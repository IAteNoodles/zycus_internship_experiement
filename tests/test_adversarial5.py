import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import json
import math
import os
import sqlite3
import threading
import time
from decimal import Decimal
from unittest import mock

import pytest

from tests.test_adversarial_api import _db


@pytest.fixture
def cli():
    _db()
    with mock.patch("reports.ask_llm", return_value=None):
        from api import app
        app.debug = False
        from starlette.testclient import TestClient
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


# ── 1. Fake exception in api_list_projects ─────────────────────

@mock.patch("api.compute_rag", side_effect=ValueError("fake RAG error"))
def test_list_projects_fake_exception_has_empty_error(mock_rag, cli):
    """app.py:147 — error now captures real exception (fixed)."""
    pid = cli.post("/api/projects", json={
        "name": "boom", "stakeholders": "", "budget": 100,
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    }).json()["id"]
    import database
    database.upsert_snapshot({
        "project_id": pid, "snapshot_date": "2026-06-01",
        "budget_spent": 1e308, "percent_complete": 1e308,
    })
    resp = cli.get("/api/projects")
    assert resp.status_code == 200
    data = resp.json()
    proj = next((p for p in data if p["id"] == pid), None)
    assert proj is not None, f"Expected project {pid} in list, got {data}"
    assert "error" in proj, f"Expected error key, got {proj}"
    assert isinstance(proj["error"], str) and len(proj["error"]) > 0, f"Expected non-empty error string, got {proj['error']!r}"


# ── 2. Concurrent API call hammer ────────────────────────────

def _ham(pid):
    import os
    os.environ['ZYCLUS_DB'] = os.environ.get('ZYCLUS_DB', '')
    from api import app
    from starlette.testclient import TestClient
    with TestClient(app) as c:
        for _ in range(30):
            try:
                c.get(f"/api/projects/{pid}")
                c.get("/api/projects")
            except Exception:
                pass


def test_concurrent_reads_no_crash(cli):
    pid = cli.post("/api/projects", json={
        "name": "conc", "stakeholders": "", "budget": 100,
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    }).json()["id"]
    threads = [threading.Thread(target=_ham, args=(pid,), daemon=True) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    resp = cli.get(f"/api/projects/{pid}")
    assert resp.status_code == 200


# ── 3. Null bytes in string fields ──────────────────────────

def test_null_bytes_in_project_name(cli):
    resp = cli.post("/api/projects", json={
        "name": "foo\x00bar", "stakeholders": "", "budget": 100,
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    })
    assert resp.status_code == 200, f"Expected 200 got {resp.status_code}: {resp.text}"
    pid = resp.json()["id"]
    r2 = cli.get(f"/api/projects/{pid}")
    assert r2.status_code == 200


# ── 4. Deeply nested JSON in report body ─────────────────────

def test_deeply_nested_json_project(cli):
    deep = {"key": "v"}
    for _ in range(100):
        deep = {"nested": deep}
    resp = cli.post("/api/projects", json={
        "name": str(deep)[:100], "stakeholders": "", "budget": 100,
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    })
    assert resp.status_code == 200, f"Deep nesting: {resp.status_code} {resp.text}"


# ── 5. Non-JSON body on POST ────────────────────────────────

def test_non_json_body_on_post(cli):
    resp = cli.post("/api/projects", data="this is not json", headers={"Content-Type": "application/json"})
    assert resp.status_code == 422


# ── 6. Delete non-existent ─────────────────────────────────

def test_delete_non_existent(cli):
    resp = cli.delete("/api/projects/999999")
    assert resp.status_code == 200, f"Expected 200 got {resp.status_code}"
    resp = cli.delete("/api/milestones/999999")
    assert resp.status_code == 200
    resp = cli.delete("/api/snapshots/999999")
    assert resp.status_code == 200
    resp = cli.delete("/api/blockers/999999")
    assert resp.status_code == 200


# ── 7. Missing required fields ──────────────────────────────

def test_missing_required_fields(cli):
    resp = cli.post("/api/projects", json={"name": "x"})
    assert resp.status_code == 422, f"Expected 422 got {resp.status_code}: {resp.text}"


# ── 8. Wrong types ──────────────────────────────────────────

def test_wrong_types(cli):
    resp = cli.post("/api/projects", json={
        "name": 123, "stakeholders": "", "budget": "not-a-number",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    })
    assert resp.status_code == 422, f"Expected 422 got {resp.status_code}: {resp.text}"

    resp = cli.post("/api/projects", json={
        "name": "test", "stakeholders": "", "budget": -100,
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    })
    assert resp.status_code == 200


# ── 9. Unsigned integer overflow ────────────────────────────

def test_huge_budget_not_make_json_fail(cli):
    resp = cli.post("/api/projects", json={
        "name": "big", "stakeholders": "", "budget": 1e308,
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    })
    assert resp.status_code == 200, f"Expected 200 got {resp.status_code}: {resp.text}"
    pid = resp.json()["id"]
    import database
    database.upsert_snapshot({
        "project_id": pid, "snapshot_date": "2026-06-01",
        "budget_spent": 1e308, "percent_complete": 1e308,
    })
    r2 = cli.get(f"/api/projects/{pid}")
    assert r2.status_code == 200
    data = r2.json()
    assert data.get("rag") in ("Green", "Amber", "Red", "N/A"), f"Unexpected RAG: {data.get('rag')}"


# ── 10. Empty string stakeholders ───────────────────────────

def test_empty_stakeholders(cli):
    resp = cli.post("/api/projects", json={
        "name": "empty", "stakeholders": "", "budget": 0,
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    })
    assert resp.status_code == 200
    pid = resp.json()["id"]
    r2 = cli.get(f"/api/projects/{pid}")
    assert r2.status_code == 200
    assert r2.json()["stakeholders"] == []


# ── 11. Duplicate project names ─────────────────────────────

def test_duplicate_names_ok(cli):
    r1 = cli.post("/api/projects", json={
        "name": "dup", "stakeholders": "", "budget": 100,
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    })
    r2 = cli.post("/api/projects", json={
        "name": "dup", "stakeholders": "", "budget": 200,
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    })
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["id"] != r2.json()["id"]


# ── 12. Resolve already resolved blocker ────────────────────

def test_resolve_already_resolved_blocker(cli):
    pid = cli.post("/api/projects", json={
        "name": "res", "stakeholders": "", "budget": 100,
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    }).json()["id"]
    sid = cli.post(f"/api/projects/{pid}/snapshots", json={
        "snapshot_date": "2026-06-01", "budget_spent": 10, "percent_complete": 10,
    }).json()["id"]
    bid = cli.post(f"/api/snapshots/{sid}/blockers", json={
        "description": "test", "date_raised": "2026-06-01",
    }).json()["id"]
    r1 = cli.post(f"/api/blockers/{bid}/resolve")
    assert r1.status_code == 200
    r2 = cli.post(f"/api/blockers/{bid}/resolve")
    assert r2.status_code == 200


# ── 13. Report download non-existent ────────────────────────

def test_download_non_existent_report(cli):
    resp = cli.get("/api/reports/999999/download")
    assert resp.status_code == 404


# ── 14. Generate reports with no data ───────────────────────

def test_generate_weekly_no_data(cli):
    resp = cli.post("/api/reports/generate/weekly")
    assert resp.status_code == 200


def test_generate_monthly_no_data(cli):
    resp = cli.post("/api/reports/generate/monthly")
    assert resp.status_code == 200


# ── 15. Create milestone with missing fields ────────────────

def test_create_milestone_missing_required(cli):
    pid = cli.post("/api/projects", json={
        "name": "ms", "stakeholders": "", "budget": 100,
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    }).json()["id"]
    resp = cli.post(f"/api/projects/{pid}/milestones", json={})
    assert resp.status_code == 422


# ── 16. Very long strings ──────────────────────────────────

def test_very_long_project_name(cli):
    name = "A" * 10_000
    resp = cli.post("/api/projects", json={
        "name": name, "stakeholders": "", "budget": 100,
        "start_date": "2026-01-01", "end_date": "2026-12-31",
    })
    assert resp.status_code == 200
    pid = resp.json()["id"]
    r2 = cli.get(f"/api/projects/{pid}")
    assert r2.status_code == 200
    assert len(r2.json()["name"]) == 10_000


# ── 17. Simultaneous read/write ─────────────────────────────

def _writer():
    import os
    os.environ['ZYCLUS_DB'] = os.environ.get('ZYCLUS_DB', '')
    from api import app
    from starlette.testclient import TestClient
    with TestClient(app) as c:
        for _ in range(20):
            try:
                pid = c.post("/api/projects", json={
                    "name": "rw", "stakeholders": "", "budget": 100,
                    "start_date": "2026-01-01", "end_date": "2026-12-31",
                }).json()["id"]
                c.delete(f"/api/projects/{pid}")
            except Exception:
                pass


def test_concurrent_read_write(cli):
    threads = [threading.Thread(target=_writer, daemon=True) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    resp = cli.get("/api/projects")
    assert resp.status_code == 200


# ── 18. Invalid date format ─────────────────────────────────

def test_invalid_date_format(cli):
    resp = cli.post("/api/projects", json={
        "name": "date", "stakeholders": "", "budget": 100,
        "start_date": "not-a-date", "end_date": "2026-12-31",
    })
    assert resp.status_code == 200, f"Expected 200 got {resp.status_code}"

    pid = resp.json()["id"]
    r2 = cli.get(f"/api/projects/{pid}")
    assert r2.status_code == 200
    assert "error" in r2.json(), f"Expected error for bad date, got {r2.json()}"


# ── 19. CORS headers present ───────────────────────────────

def test_cors_headers(cli):
    resp = cli.options("/api/projects", headers={
        "Origin": "http://evil.com",
        "Access-Control-Request-Method": "GET",
    })
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "*"


# ── 20. Health endpoint ─────────────────────────────────────

def test_health_always_ok(cli):
    for _ in range(100):
        resp = cli.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
