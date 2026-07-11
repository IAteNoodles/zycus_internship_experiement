"""Adversarial API tests — try to break the FastAPI endpoints."""
import os, sys, math, json, tempfile
from datetime import date, timedelta
from unittest import mock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

TODAY = date.today()
_TMPDIR = None


def _db():
    global _TMPDIR
    if _TMPDIR is None:
        _TMPDIR = tempfile.mkdtemp(prefix="zycus_api_")
    path = os.path.join(_TMPDIR, f"t_{os.urandom(4).hex()}.db")
    os.environ['ZYCLUS_DB'] = path
    import database
    database.init()
    return path


@pytest.fixture
def client():
    _db()
    with mock.patch("reports.ask_llm", return_value=None):
        from api import app
        app.debug = False
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


# ═══════════════════════════════════════════════════════
#  CREATE PROJECT EDGE CASES
# ═══════════════════════════════════════════════════════

class TestCreateProject:
    def test_empty_name(self, client):
        r = client.post("/api/projects", json={
            "name": "", "stakeholders": "", "budget": 100,
            "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        assert r.status_code == 200
        assert r.json()["ok"]

    def test_unicode_name(self, client):
        r = client.post("/api/projects", json={
            "name": "Zoë 🦄 项目 ⚠️", "stakeholders": "PM,Client",
            "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        assert r.status_code == 200
        pid = r.json()["id"]
        r2 = client.get(f"/api/projects/{pid}")
        assert r2.status_code == 200
        assert r2.json()["name"] == "Zoë 🦄 项目 ⚠️"

    def test_10000_char_name(self, client):
        r = client.post("/api/projects", json={
            "name": "X" * 10000, "stakeholders": "",
            "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        assert r.status_code == 200

    def test_negative_budget(self, client):
        r = client.post("/api/projects", json={
            "name": "Neg", "stakeholders": "",
            "budget": -5000, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        assert r.status_code == 200
        assert r.json()["ok"]

    def test_zero_budget(self, client):
        r = client.post("/api/projects", json={
            "name": "Zero", "stakeholders": "",
            "budget": 0, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        assert r.status_code == 200

    def test_huge_budget(self, client):
        r = client.post("/api/projects", json={
            "name": "Huge", "stakeholders": "",
            "budget": 1e15, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        assert r.status_code == 200

    def test_inf_budget(self, client):
        # float("inf") is not JSON-compliant; sanitized to None by SafeJSONResponse
        import database
        pid = database.upsert_project(dict(name="Inf", stakeholders=[], budget=float("inf"),
                                            start_date="2026-01-01", end_date="2026-12-31"))
        r = client.get(f"/api/projects/{pid}")
        assert r.status_code == 200
        assert r.json()["budget"] is None

    def test_invalid_dates(self, client):
        r = client.post("/api/projects", json={
            "name": "P", "stakeholders": "",
            "budget": 100, "start_date": "not-a-date", "end_date": "2026-12-31",
        })
        # should still create (DB stores as text, no validation)
        assert r.status_code == 200

    def test_same_start_end_date(self, client):
        r = client.post("/api/projects", json={
            "name": "P", "stakeholders": "",
            "budget": 100, "start_date": "2026-06-01", "end_date": "2026-06-01",
        })
        assert r.status_code == 200

    def test_empty_stakeholders(self, client):
        r = client.post("/api/projects", json={
            "name": "P", "stakeholders": "",
            "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        assert r.status_code == 200

    def test_stakeholders_with_special_chars(self, client):
        r = client.post("/api/projects", json={
            "name": "P", "stakeholders": "alice,bob,charlie with spaces,<script>,日本語,😀",
            "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        assert r.status_code == 200
        pid = r.json()["id"]
        r2 = client.get(f"/api/projects/{pid}")
        data = r2.json()
        assert len(data["stakeholders"]) == 6

    def test_missing_required_fields(self, client):
        r = client.post("/api/projects", json={})
        assert r.status_code == 422  # pydantic validation

    def test_extra_fields_ignored(self, client):
        r = client.post("/api/projects", json={
            "name": "P", "stakeholders": "",
            "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
            "extra_field": "should be ignored",
        })
        assert r.status_code == 200

    def test_control_chars_in_name(self, client):
        r = client.post("/api/projects", json={
            "name": "\x00\r\n\t\x07", "stakeholders": "",
            "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════
#  GET PROJECT EDGE CASES
# ═══════════════════════════════════════════════════════

class TestGetProject:
    def test_nonexistent(self, client):
        r = client.get("/api/projects/999999")
        assert r.status_code == 404

    def test_negative_id(self, client):
        r = client.get("/api/projects/-1")
        assert r.status_code == 404

    def test_zero_id(self, client):
        r = client.get("/api/projects/0")
        assert r.status_code == 404

    def test_string_id(self, client):
        r = client.get("/api/projects/abc")
        assert r.status_code == 422  # FastAPI path validation

    def test_huge_id(self, client):
        r = client.get(f"/api/projects/{2**31}")
        assert r.status_code in (404, 500)  # may overflow on some SQLite builds

    def test_list_projects_empty(self, client):
        r = client.get("/api/projects")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_then_get(self, client):
        r = client.post("/api/projects", json={
            "name": "Test", "stakeholders": "PM",
            "budget": 50000, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        pid = r.json()["id"]
        r2 = client.get(f"/api/projects/{pid}")
        assert r2.status_code == 200
        data = r2.json()
        assert data["name"] == "Test"
        assert data["rag"] in ("Green", "Amber", "Red", "N/A")

    def test_get_project_with_no_snapshots(self, client):
        """Project with no snapshots — _enrich_sentiment returns None for rag."""
        r = client.post("/api/projects", json={
            "name": "NoSnap", "stakeholders": "",
            "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        pid = r.json()["id"]
        r2 = client.get(f"/api/projects/{pid}")
        assert r2.status_code == 200
        data = r2.json()
        assert data["rag"] == "N/A"

    def test_list_projects_with_no_snapshots(self, client):
        """_project_row handles projects with no snapshots."""
        r = client.post("/api/projects", json={
            "name": "ListNoSnap", "stakeholders": "",
            "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        assert r.status_code == 200
        r2 = client.get("/api/projects")
        assert r2.status_code == 200
        data = r2.json()
        assert len(data) >= 1
        for p in data:
            assert "rag" in p


# ═══════════════════════════════════════════════════════
#  UPDATE PROJECT
# ═══════════════════════════════════════════════════════

class TestUpdateProject:
    def test_update_nonexistent(self, client):
        """Updating a non-existent project should not crash."""
        r = client.put("/api/projects/999999", json={
            "name": "Ghost", "stakeholders": "",
            "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        assert r.status_code == 200  # silently succeeds (0 rows affected)

    def test_update_with_empty_name(self, client):
        r = client.post("/api/projects", json={
            "name": "Orig", "stakeholders": "",
            "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        pid = r.json()["id"]
        r2 = client.put(f"/api/projects/{pid}", json={
            "name": "", "stakeholders": "",
            "budget": 200, "start_date": "2026-06-01", "end_date": "2026-12-31",
        })
        assert r2.status_code == 200

    def test_update_no_changes(self, client):
        r = client.post("/api/projects", json={
            "name": "Same", "stakeholders": "",
            "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        pid = r.json()["id"]
        r2 = client.put(f"/api/projects/{pid}", json={
            "name": "Same", "stakeholders": "",
            "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        assert r2.status_code == 200


# ═══════════════════════════════════════════════════════
#  DELETE PROJECT
# ═══════════════════════════════════════════════════════

class TestDeleteProject:
    def test_delete_nonexistent(self, client):
        r = client.delete("/api/projects/999999")
        assert r.status_code == 200  # database.delete_project is idempotent

    def test_delete_twice(self, client):
        r = client.post("/api/projects", json={
            "name": "DelMe", "stakeholders": "",
            "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        pid = r.json()["id"]
        r2 = client.delete(f"/api/projects/{pid}")
        assert r2.status_code == 200
        r3 = client.delete(f"/api/projects/{pid}")
        assert r3.status_code == 200

    def test_delete_negative_id(self, client):
        r = client.delete("/api/projects/-1")
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════
#  MILESTONE EDGE CASES
# ═══════════════════════════════════════════════════════

class TestMilestones:
    def _mkproj(self, client):
        r = client.post("/api/projects", json={
            "name": "MS", "stakeholders": "",
            "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        return r.json()["id"]

    def test_add_milestone_nonexistent_project(self, client):
        r = client.post("/api/projects/999999/milestones", json={
            "name": "M", "due_date": "2026-06-01",
        })
        # FOREIGN KEY constraint fails — should return 500
        assert r.status_code == 500

    def test_add_milestone_empty_name(self, client):
        pid = self._mkproj(client)
        r = client.post(f"/api/projects/{pid}/milestones", json={
            "name": "", "due_date": "2026-06-01",
        })
        assert r.status_code == 200

    def test_add_milestone_bad_status(self, client):
        pid = self._mkproj(client)
        r = client.post(f"/api/projects/{pid}/milestones", json={
            "name": "M", "due_date": "2026-06-01", "status": "INVALID_STATUS",
        })
        assert r.status_code == 200
        # Check detail — should show the milestone
        r2 = client.get(f"/api/projects/{pid}")
        assert r2.status_code == 200
        assert len(r2.json()["milestones"]) >= 1

    def test_add_milestone_bad_date(self, client):
        pid = self._mkproj(client)
        r = client.post(f"/api/projects/{pid}/milestones", json={
            "name": "M", "due_date": "not-a-date",
        })
        assert r.status_code == 200

    def test_add_milestone_unicode(self, client):
        pid = self._mkproj(client)
        r = client.post(f"/api/projects/{pid}/milestones", json={
            "name": "里程碑 🔥", "due_date": "2026-06-01",
        })
        assert r.status_code == 200

    def test_delete_milestone_nonexistent(self, client):
        r = client.delete("/api/milestones/999999")
        assert r.status_code == 200

    def test_delete_milestone_twice(self, client):
        pid = self._mkproj(client)
        r = client.post(f"/api/projects/{pid}/milestones", json={
            "name": "M", "due_date": "2026-06-01",
        })
        mid = r.json()["id"]
        client.delete(f"/api/milestones/{mid}")
        r2 = client.delete(f"/api/milestones/{mid}")
        assert r2.status_code == 200


# ═══════════════════════════════════════════════════════
#  SNAPSHOT EDGE CASES
# ═══════════════════════════════════════════════════════

class TestSnapshots:
    def _mkproj(self, client):
        r = client.post("/api/projects", json={
            "name": "SS", "stakeholders": "",
            "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        return r.json()["id"]

    def test_add_snapshot_no_budget(self, client):
        pid = self._mkproj(client)
        r = client.post(f"/api/projects/{pid}/snapshots", json={
            "snapshot_date": "2026-06-01",
            # budget_spent and percent_complete optional
        })
        assert r.status_code == 200

    def test_add_snapshot_negative_percent(self, client):
        pid = self._mkproj(client)
        r = client.post(f"/api/projects/{pid}/snapshots", json={
            "snapshot_date": "2026-06-01",
            "percent_complete": -50, "budget_spent": 100,
        })
        assert r.status_code == 200

    def test_add_snapshot_overflow_percent(self, client):
        pid = self._mkproj(client)
        r = client.post(f"/api/projects/{pid}/snapshots", json={
            "snapshot_date": "2026-06-01",
            "percent_complete": 999, "budget_spent": 100,
        })
        assert r.status_code == 200

    def test_add_snapshot_negative_budget_spent(self, client):
        pid = self._mkproj(client)
        r = client.post(f"/api/projects/{pid}/snapshots", json={
            "snapshot_date": "2026-06-01",
            "percent_complete": 50, "budget_spent": -1000,
        })
        assert r.status_code == 200

    def test_add_snapshot_nan_complete(self, client):
        # float("nan") is not JSON-compliant; test via DB directly
        import database
        pid = database.upsert_project(dict(name="NaN", stakeholders=[], budget=100,
                                           start_date="2026-01-01", end_date="2026-12-31"))
        sid = database.upsert_snapshot(dict(project_id=pid, snapshot_date="2026-06-01",
                                             percent_complete=float("nan"), budget_spent=100))
        r = client.get(f"/api/projects/{pid}")
        assert r.status_code == 200

    def test_add_snapshot_invalid_date(self, client):
        pid = self._mkproj(client)
        r = client.post(f"/api/projects/{pid}/snapshots", json={
            "snapshot_date": "not-a-date",
        })
        assert r.status_code == 200

    def test_add_snapshot_on_nonexistent_project(self, client):
        r = client.post("/api/projects/999999/snapshots", json={
            "snapshot_date": "2026-06-01",
        })
        assert r.status_code == 500  # FOREIGN KEY constraint fails

    def test_delete_snapshot_nonexistent(self, client):
        r = client.delete("/api/snapshots/999999")
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════
#  BLOCKER EDGE CASES
# ═══════════════════════════════════════════════════════

class TestBlockers:
    def _mksnap(self, client):
        r = client.post("/api/projects", json={
            "name": "B", "stakeholders": "",
            "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        pid = r.json()["id"]
        r2 = client.post(f"/api/projects/{pid}/snapshots", json={
            "snapshot_date": "2026-06-01",
        })
        return r2.json()["id"]

    def test_add_blocker_on_nonexistent_snapshot(self, client):
        r = client.post("/api/snapshots/999999/blockers", json={
            "description": "B", "date_raised": "2026-05-01", "severity": "high",
        })
        assert r.status_code == 500  # FOREIGN KEY constraint fails

    def test_add_blocker_empty_description(self, client):
        sid = self._mksnap(client)
        r = client.post(f"/api/snapshots/{sid}/blockers", json={
            "description": "", "date_raised": "2026-05-01",
        })
        assert r.status_code == 200

    def test_add_blocker_bad_severity(self, client):
        sid = self._mksnap(client)
        r = client.post(f"/api/snapshots/{sid}/blockers", json={
            "description": "B", "date_raised": "2026-05-01", "severity": "MEGA",
        })
        assert r.status_code == 200

    def test_add_blocker_future_date(self, client):
        sid = self._mksnap(client)
        r = client.post(f"/api/snapshots/{sid}/blockers", json={
            "description": "B", "date_raised": (TODAY + timedelta(days=365)).isoformat(),
        })
        assert r.status_code == 200

    def test_resolve_nonexistent_blocker(self, client):
        r = client.post("/api/blockers/999999/resolve")
        assert r.status_code == 404

    def test_resolve_blocker_twice(self, client):
        sid = self._mksnap(client)
        r = client.post(f"/api/snapshots/{sid}/blockers", json={
            "description": "B", "date_raised": "2026-05-01",
        })
        bid = r.json()["id"]
        r2 = client.post(f"/api/blockers/{bid}/resolve")
        assert r2.status_code == 200
        r3 = client.post(f"/api/blockers/{bid}/resolve")
        assert r3.status_code == 200  # already resolved, still OK

    def test_delete_blocker_nonexistent(self, client):
        r = client.delete("/api/blockers/999999")
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════
#  SENTIMENT EDGE CASES
# ═══════════════════════════════════════════════════════

class TestSentiment:
    def _mksnap(self, client):
        r = client.post("/api/projects", json={
            "name": "S", "stakeholders": "",
            "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        pid = r.json()["id"]
        r2 = client.post(f"/api/projects/{pid}/snapshots", json={
            "snapshot_date": "2026-06-01",
        })
        return r2.json()["id"]

    def test_add_sentiment_empty_source(self, client):
        sid = self._mksnap(client)
        r = client.post(f"/api/snapshots/{sid}/sentiment", json={
            "source": "", "comment": "great progress",
        })
        assert r.status_code == 200

    def test_add_sentiment_empty_comment(self, client):
        sid = self._mksnap(client)
        r = client.post(f"/api/snapshots/{sid}/sentiment", json={
            "source": "PM", "comment": "",
        })
        assert r.status_code == 200

    def test_add_sentiment_unicode(self, client):
        sid = self._mksnap(client)
        r = client.post(f"/api/snapshots/{sid}/sentiment", json={
            "source": "PM", "comment": "反馈很好 😊 继续加油",
        })
        assert r.status_code == 200

    def test_add_sentiment_very_long(self, client):
        sid = self._mksnap(client)
        r = client.post(f"/api/snapshots/{sid}/sentiment", json={
            "source": "PM", "comment": "x" * 100000,
        })
        assert r.status_code == 200

    def test_add_sentiment_on_nonexistent_snapshot(self, client):
        r = client.post("/api/snapshots/999999/sentiment", json={
            "source": "PM", "comment": "ok",
        })
        assert r.status_code == 500  # FOREIGN KEY constraint fails


# ═══════════════════════════════════════════════════════
#  REPORTS EDGE CASES
# ═══════════════════════════════════════════════════════

class TestReports:
    def test_list_reports_empty(self, client):
        r = client.get("/api/reports")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_reports_with_type(self, client):
        r = client.get("/api/reports?type=weekly")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_reports_with_invalid_type(self, client):
        r = client.get("/api/reports?type=invalid_type_xyz")
        assert r.status_code == 200
        assert r.json() == []

    def test_delete_report_nonexistent(self, client):
        r = client.delete("/api/reports/999999")
        assert r.status_code == 200

    def test_download_nonexistent_report(self, client):
        r = client.get("/api/reports/999999/download")
        assert r.status_code == 404

    def test_download_report_no_file_path(self, client):
        import database
        rid = database.save_report("test", TODAY.isoformat(), None, None)
        r = client.get(f"/api/reports/{rid}/download")
        assert r.status_code == 404

    def test_download_report_missing_file(self, client):
        import database
        rid = database.save_report("test", TODAY.isoformat(), "/nonexistent/report.txt", "summary")
        r = client.get(f"/api/reports/{rid}/download")
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════
#  REPORT GENERATION
# ═══════════════════════════════════════════════════════

class TestReportGeneration:
    def test_generate_weekly_empty(self, client):
        r = client.post("/api/reports/generate/weekly")
        assert r.status_code == 200

    def test_generate_monthly_empty(self, client):
        r = client.post("/api/reports/generate/monthly")
        assert r.status_code == 200

    def test_generate_weekly_with_data(self, client):
        r = client.post("/api/projects", json={
            "name": "GenTest", "stakeholders": "PM",
            "budget": 100000, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        pid = r.json()["id"]
        client.post(f"/api/projects/{pid}/snapshots", json={
            "snapshot_date": TODAY.isoformat(),
            "percent_complete": 50, "budget_spent": 50000,
        })
        r2 = client.post("/api/reports/generate/weekly")
        assert r2.status_code == 200

    def test_generate_monthly_with_data(self, client):
        r = client.post("/api/projects", json={
            "name": "MonthlyGen", "stakeholders": "PM",
            "budget": 100000, "start_date": "2026-01-01", "end_date": "2026-12-31",
        })
        pid = r.json()["id"]
        client.post(f"/api/projects/{pid}/snapshots", json={
            "snapshot_date": TODAY.isoformat(),
            "percent_complete": 50, "budget_spent": 50000,
        })
        r2 = client.post("/api/reports/generate/monthly")
        assert r2.status_code == 200


# ═══════════════════════════════════════════════════════
#  CONCURRENT API REQUESTS
# ═══════════════════════════════════════════════════════

class TestConcurrentAPI:
    def test_concurrent_create_and_read(self, client):
        import concurrent.futures
        errors = []

        def create(i):
            r = client.post("/api/projects", json={
                "name": f"C-{i}", "stakeholders": "",
                "budget": float(i * 1000),
                "start_date": "2026-01-01", "end_date": "2026-12-31",
            })
            if r.status_code != 200:
                errors.append(f"create {i}: {r.status_code}")

        def read():
            for _ in range(20):
                r = client.get("/api/projects")
                if r.status_code != 200:
                    errors.append(f"read: {r.status_code}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            fs = [ex.submit(create, i) for i in range(10)]
            fs.append(ex.submit(read))
            concurrent.futures.wait(fs)
        assert len(errors) == 0, f"errors: {errors}"


# ═══════════════════════════════════════════════════════
#  HEALTH CHECK
# ═══════════════════════════════════════════════════════

class TestHealth:
    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
