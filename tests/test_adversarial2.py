"""Round 2: deeper adversarial tests, project_to_domain fuzzing, cascade stress."""
import os, sys, math, json, threading, tempfile, time, sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import init, conn, _db_path, list_projects, get_project, upsert_project, delete_project
from database import upsert_milestone, delete_milestone, upsert_snapshot, delete_snapshot
from database import upsert_blocker, delete_blocker, delete_sentiment, delete_report, get_report
from database import save_report, list_reports, project_to_domain, upsert_sentiment
from models.projects import (
    Project, Milestone, Blocker, ProgressSnapshot, SentimentEntry,
    SentimentScore, ProjectStatus, BlockerSeverity, MilestoneStatus,
)
from models.rag import compute_rag, RagResult, RAG, score_schedule, score_budget, score_blockers, score_sentiment
from models.sentiment import analyze_sentiment
from reports import weekly_narrative, monthly_content, _signal_summary
from main import _enrich_sentiment, synthesize_monthly

TODAY = date.today()
_TMPDIR = None


def _db():
    global _TMPDIR
    if _TMPDIR is None:
        _TMPDIR = tempfile.mkdtemp(prefix="zycus2_")
    p = os.path.join(_TMPDIR, f"t_{os.urandom(4).hex()}.db")
    os.environ['ZYCLUS_DB'] = p; init()
    return p


def sv(p):
    return upsert_project(dict(name=p.name, stakeholders=[], budget=p.budget,
                                start_date=p.start_date.isoformat(), end_date=p.end_date.isoformat()))


# ═══════════════════════════════════════════════════════
#  project_to_domain FUZZING
# ═══════════════════════════════════════════════════════

class TestProjectToDomainFuzz:
    """project_to_domain has bare dictionary accesses that crash on missing keys."""

    def test_missing_name(self):
        with pytest.raises(KeyError):
            project_to_domain({"budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31"})

    def test_missing_budget(self):
        with pytest.raises(KeyError):
            project_to_domain({"name": "X", "start_date": "2026-01-01", "end_date": "2026-12-31"})

    def test_missing_start_date(self):
        with pytest.raises(KeyError):
            project_to_domain({"name": "X", "budget": 100, "end_date": "2026-12-31"})

    def test_bad_date_format(self):
        with pytest.raises(ValueError):
            project_to_domain({"name": "X", "budget": 100,
                               "start_date": "not-a-date", "end_date": "2026-12-31"})

    def test_year_zero_date(self):
        with pytest.raises(ValueError):
            project_to_domain({"name": "X", "budget": 100,
                               "start_date": "0000-01-01", "end_date": "2026-12-31"})

    def test_milestone_missing_name(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "milestones": [{"status": "in_progress", "due_date": "2026-06-01"}]}
        with pytest.raises(KeyError):
            project_to_domain(d)

    def test_milestone_missing_due_date(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "milestones": [{"name": "M1", "status": "in_progress"}]}
        with pytest.raises(KeyError):
            project_to_domain(d)

    def test_milestone_bad_status_enum(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "milestones": [{"name": "M1", "status": "invalid_status", "due_date": "2026-06-01"}]}
        p = project_to_domain(d)
        assert p.milestones[0].status == MilestoneStatus.NOT_STARTED

    def test_blocker_missing_description(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "snapshots": [{"snapshot_date": "2026-06-01",
                            "blockers": [{"severity": "high", "date_raised": "2026-05-01", "resolved": False}]}]}
        with pytest.raises(KeyError):
            project_to_domain(d)

    def test_blocker_missing_severity(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "snapshots": [{"snapshot_date": "2026-06-01",
                            "blockers": [{"description": "B1", "date_raised": "2026-05-01", "resolved": False}]}]}
        with pytest.raises(KeyError):
            project_to_domain(d)

    def test_blocker_missing_resolved(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "snapshots": [{"snapshot_date": "2026-06-01",
                            "blockers": [{"description": "B1", "severity": "high", "date_raised": "2026-05-01"}]}]}
        with pytest.raises(KeyError):
            project_to_domain(d)

    def test_sentiment_missing_source(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "snapshots": [{"snapshot_date": "2026-06-01",
                            "sentiment": [{"date_recorded": "2026-06-01", "comment": "ok"}]}]}
        with pytest.raises(KeyError):
            project_to_domain(d)

    def test_sentiment_missing_date_recorded(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "snapshots": [{"snapshot_date": "2026-06-01",
                            "sentiment": [{"source": "PM", "comment": "ok"}]}]}
        with pytest.raises(KeyError):
            project_to_domain(d)

    def test_sentiment_missing_comment(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "snapshots": [{"snapshot_date": "2026-06-01",
                            "sentiment": [{"source": "PM", "date_recorded": "2026-06-01"}]}]}
        with pytest.raises(KeyError):
            project_to_domain(d)

    def test_sentiment_bad_score(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "snapshots": [{"snapshot_date": "2026-06-01",
                            "sentiment": [{"source": "PM", "date_recorded": "2026-06-01",
                                           "comment": "ok", "score": "not-a-score"}]}]}
        p = project_to_domain(d)
        assert p.snapshot_history[0].stakeholder_sentiment[0].score == SentimentScore.UNKNOWN

    def test_snapshot_missing_date(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "snapshots": [{"percent_complete": 50}]}
        with pytest.raises(KeyError):
            project_to_domain(d)

    def test_stakeholders_is_string_not_list(self):
        """stakeholders default [] uses .get() — string passes through."""
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "stakeholders": "not_a_list"}
        p = project_to_domain(d)
        assert p.stakeholders == "not_a_list"  # not a list, but doesn't crash


# ═══════════════════════════════════════════════════════
#  CASCADE DELETE STRESS
# ═══════════════════════════════════════════════════════

class TestCascadeStress:
    def test_delete_project_with_1000_children(self):
        _db()
        pid = upsert_project(dict(name="Big", stakeholders=[], budget=100,
                                   start_date="2026-01-01", end_date="2026-12-31"))
        for i in range(1000):
            sid = upsert_snapshot(dict(project_id=pid, snapshot_date="2026-06-01",
                                       percent_complete=50, budget_spent=50))
            upsert_milestone(dict(project_id=pid, name=f"M-{i}", due_date="2026-06-01"))
            upsert_blocker(dict(snapshot_id=sid, description=f"B-{i}",
                                date_raised="2026-05-01", severity="low"))
            upsert_sentiment(dict(snapshot_id=sid, source="PM",
                                  date_recorded="2026-06-01", comment="ok"))
        delete_project(pid)
        # Verify cascade: get_project should return None
        assert get_project(pid) is None  # operational error handled by conn()
        # Actually: OperationalError raised because get_project calls conn() after init()
        # SQLite cascade deletes when FK is ON — verify by checking no orphan milestones
        with conn() as db:
            orphans = db.execute("SELECT COUNT(*) FROM milestones WHERE project_id=?", (pid,)).fetchone()[0]
            assert orphans == 0

    def test_delete_nonexistent_twice(self):
        _db()
        delete_project(1)
        delete_project(1)  # second delete of same id — should not crash


# ═══════════════════════════════════════════════════════
#  EDGE MATH
# ═══════════════════════════════════════════════════════

class TestEdgeMath:
    def test_schedule_progress_negative_total_days(self):
        """end_date before start_date already handled (returns 100)."""
        p = Project("X", [], 100, TODAY, TODAY - timedelta(days=10))
        assert p.schedule_progress_expected(TODAY) == 100.0

    def test_budget_spent_exceeds_budget_100x(self):
        """spent 10M on 100K budget."""
        p = Project("X", [], 100_000, TODAY - timedelta(days=30), TODAY + timedelta(days=30))
        s = ProgressSnapshot(TODAY, budget_spent=10_000_000, percent_complete=50)
        r = compute_rag(p, s)
        b = next((s for s in r.signals if s.name == "budget"), None)
        assert b is not None and b.score in (1, 2)

    def test_percent_complete_negative(self):
        p = Project("X", [], 100_000, TODAY - timedelta(days=30), TODAY + timedelta(days=30))
        s = ProgressSnapshot(TODAY, budget_spent=50_000, percent_complete=-5)
        r = compute_rag(p, s)
        assert r.status in (RAG.GREEN, RAG.AMBER, RAG.RED)

    def test_percent_complete_over_100(self):
        p = Project("X", [], 100_000, TODAY - timedelta(days=30), TODAY + timedelta(days=30))
        s = ProgressSnapshot(TODAY, budget_spent=50_000, percent_complete=150)
        r = compute_rag(p, s)
        b = next((s for s in r.signals if s.name == "budget"), None)
        assert b.score == 0  # spent 50% vs 150% complete = 100 * 50/100 - 150 = -100 → no overrun

    def test_all_scores_none(self):
        """All signals return None score."""
        p = Project("X", [], 0, TODAY, TODAY)
        s = ProgressSnapshot(TODAY, budget_spent=None, percent_complete=None)
        r = compute_rag(p, s)
        assert r.status == RAG.AMBER  # no usable data
        assert r.insufficient_data

    def test_budget_spent_zero(self):
        p = Project("X", [], 100, TODAY, TODAY + timedelta(days=10))
        s = ProgressSnapshot(TODAY, budget_spent=0, percent_complete=50)
        r = compute_rag(p, s)
        b = next((s for s in r.signals if s.name == "budget"), None)
        assert b.score == 0

    def test_budget_spent_none_with_data(self):
        p = Project("X", [], 100, TODAY, TODAY + timedelta(days=10))
        s = ProgressSnapshot(TODAY, budget_spent=None, percent_complete=50)
        r = compute_rag(p, s)
        b = next((s for s in r.signals if s.name == "budget"), None)
        assert b.score is None

    def test_1_milestone_overdue_today(self):
        """Due today, as_of today → should be overdue (as_of > due_date is False, so not overdue)."""
        ms = Milestone("M", TODAY, MilestoneStatus.NOT_STARTED)
        assert ms.is_overdue(TODAY) is False  # as_of > due_date → False

    def test_1_milestone_overdue_tomorrow(self):
        ms = Milestone("M", TODAY, MilestoneStatus.NOT_STARTED)
        assert ms.is_overdue(TODAY + timedelta(days=1))

    def test_blocker_age_raised_future(self):
        b = Blocker("B", TODAY + timedelta(days=5), BlockerSeverity.HIGH)
        assert b.age_days(TODAY) == 0


# ═══════════════════════════════════════════════════════
#  DB FUNCTION SIGNATURES
# ═══════════════════════════════════════════════════════

class TestDbSignatures:
    """Check upsert_* functions accept dicts with missing optional keys."""

    def test_upsert_snapshot_no_budget_spent(self):
        _db()
        pid = upsert_project(dict(name="X", stakeholders=[], budget=100,
                                   start_date="2026-01-01", end_date="2026-12-31"))
        sid = upsert_snapshot(dict(project_id=pid, snapshot_date="2026-06-01"))
        assert sid > 0

    def test_upsert_snapshot_no_percent(self):
        _db()
        pid = upsert_project(dict(name="X", stakeholders=[], budget=100,
                                   start_date="2026-01-01", end_date="2026-12-31"))
        sid = upsert_snapshot(dict(project_id=pid, snapshot_date="2026-06-01"))
        assert sid > 0

    def test_upsert_blocker_no_resolved(self):
        _db()
        pid = upsert_project(dict(name="X", stakeholders=[], budget=100,
                                   start_date="2026-01-01", end_date="2026-12-31"))
        sid = upsert_snapshot(dict(project_id=pid, snapshot_date="2026-06-01"))
        bid = upsert_blocker(dict(snapshot_id=sid, description="B",
                                   date_raised="2026-05-01", severity="low"))
        assert bid > 0

    def test_upsert_sentiment_no_score(self):
        _db()
        pid = upsert_project(dict(name="X", stakeholders=[], budget=100,
                                   start_date="2026-01-01", end_date="2026-12-31"))
        sid = upsert_snapshot(dict(project_id=pid, snapshot_date="2026-06-01"))
        eid = upsert_sentiment(dict(snapshot_id=sid, source="PM",
                                     date_recorded="2026-06-01", comment="ok"))
        assert eid > 0


# ═══════════════════════════════════════════════════════
#  LIST_PROJECTS DOES NOT INCLUDE NESTED DATA
# ═══════════════════════════════════════════════════════

class TestListProjects:
    def test_list_projects_no_nested_data(self):
        _db()
        pid = upsert_project(dict(name="X", stakeholders=[], budget=100,
                                   start_date="2026-01-01", end_date="2026-12-31"))
        upsert_milestone(dict(project_id=pid, name="M", due_date="2026-06-01"))
        plist = list_projects()
        assert len(plist) == 1
        assert "milestones" not in plist[0]  # list_projects doesn't join children
        assert "snapshots" not in plist[0]
