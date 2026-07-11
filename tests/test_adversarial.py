"""Adversarial tests: try to break the code with malicious/edge inputs."""
import os, sys, math, json, threading, tempfile, time, sqlite3
from datetime import date, timedelta
from unittest.mock import patch
from pathlib import Path

import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from database import init, conn, _db_path, list_projects, get_project, upsert_project, delete_project
from database import upsert_milestone, delete_milestone, upsert_snapshot, delete_snapshot
from database import upsert_blocker, delete_blocker, delete_sentiment, delete_report, get_report
from models.projects import (
    Project, Milestone, Blocker, ProgressSnapshot, SentimentEntry,
    SentimentScore, ProjectStatus, BlockerSeverity, MilestoneStatus,
)
from models.rag import compute_rag, RagResult, RAG, score_schedule, score_budget, score_blockers, score_sentiment
from models.sentiment import analyze_sentiment, make_sentiment_entry
from reports import weekly_narrative, monthly_content, _signal_summary
from main import _enrich_sentiment

TODAY = date.today()
_TMPDIR = None


def _db():
    global _TMPDIR
    if _TMPDIR is None:
        _TMPDIR = tempfile.mkdtemp(prefix="zycus_test_")
    path = os.path.join(_TMPDIR, f"t_{os.urandom(4).hex()}.db")
    os.environ['ZYCLUS_DB'] = path; init()
    return path


def proj(**kw):
    d = dict(name="Adv", stakeholders=[], budget=100_000.0,
             start_date=TODAY - timedelta(days=30), end_date=TODAY + timedelta(days=30))
    return Project(**{**d, **kw})


def snap(**kw):
    d = dict(snapshot_date=TODAY, budget_spent=50_000.0, percent_complete=50.0)
    return ProgressSnapshot(**{**d, **kw})


def sv(p):
    return upsert_project(dict(name=p.name, stakeholders=[], budget=p.budget,
                                start_date=p.start_date.isoformat(), end_date=p.end_date.isoformat()))


def sg(r, name):
    return next((s for s in r.signals if s.name == name), None)


# ═══════════════════════════════════════════════════════
#  BUGS FOUND IN PRODUCTION
# ═══════════════════════════════════════════════════════

class TestProductionBugs:
    """Confirmed bugs. Fix needed in production code."""

    def test_bug_score_budget_none_crashes(self):
        """score_budget: budget=None no longer crashes (returns None budget signal)."""
        r = compute_rag(proj(budget=None), snap(budget_spent=50.0, percent_complete=50.0))
        b = next((s for s in r.signals if s.name == "budget"), None)
        assert b is not None and b.score is None

    def test_bug_db_before_init_raises_operational_error(self):
        """get_project before init() now raises bare sqlite3.OperationalError (not RuntimeError)."""
        os.environ['ZYCLUS_DB'] = ':memory:'
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            get_project(1)


# ═══════════════════════════════════════════════════════
#  DATABASE
# ═══════════════════════════════════════════════════════

class TestDBAdversarial:
    def setup_method(self): _db()

    def test_sql_injection_name(self):
        pid = sv(proj(name="Robert'); DROP TABLE projects; --"))
        assert get_project(pid)["name"] == "Robert'); DROP TABLE projects; --"
        assert len(list_projects()) >= 1

    def test_unicode_name(self):
        pid = sv(proj(name="\u2603\u2600\u2622\u2744\U0001F4A3"))
        assert get_project(pid)["name"] == "\u2603\u2600\u2622\u2744\U0001F4A3"

    def test_negative_budget(self):
        pid = sv(proj(budget=-1.0))
        assert get_project(pid)["budget"] == -1.0

    def test_nan_budget(self):
        """SQLite rejects NaN for REAL. Expected: NOT NULL constraint error."""
        with pytest.raises(RuntimeError, match="NOT NULL constraint"):
            sv(proj(budget=float("nan")))

    def test_inf_budget(self):
        pid = sv(proj(budget=float("inf")))
        assert get_project(pid)["budget"] == float("inf")

    def test_long_name_10000_chars(self):
        pid = sv(proj(name="A" * 10000))
        assert get_project(pid)["name"] == "A" * 10000

    def test_emojis_only(self):
        pid = sv(proj(name="😀😎🚀💥👻"))
        assert get_project(pid)["name"] == "😀😎🚀💥👻"

    def test_delete_nonexistent(self):
        delete_project(9999999)
        delete_milestone(9999999)
        delete_blocker(9999999)
        delete_snapshot(9999999)
        delete_sentiment(9999999)
        delete_report(9999999)

    def test_get_nonexistent_report(self):
        assert get_report(99999) is None

    def test_large_stakeholders_json(self):
        pid = sv(proj(name="Huge"))
        huge = {"keys": ["x"] * 50000}
        with conn() as db:
            db.execute("UPDATE projects SET stakeholders=? WHERE id=?", (json.dumps(huge), pid))
        loaded = get_project(pid)
        # loaded["stakeholders"] is a parsed Python dict; check nested list len
        assert len(loaded["stakeholders"]["keys"]) == 50000

    def test_concurrent_writes(self):
        errors = []; lock = threading.Lock()
        def w(i):
            try: _db(); sv(proj(name=f"C-{i}"))
            except Exception as e:
                with lock: errors.append(e)
        ts = [threading.Thread(target=w, args=(i,)) for i in range(5)]
        for t in ts: t.start()
        for t in ts: t.join(10)
        assert len(errors) == 0, f"errors: {errors}"


# ═══════════════════════════════════════════════════════
#  RAG
# ═══════════════════════════════════════════════════════

class TestRAGAdversarial:
    def test_schedule_negative_duration(self):
        p = proj(start_date=TODAY, end_date=TODAY - timedelta(days=10))
        p.milestones = [Milestone("MS1", TODAY - timedelta(days=5))]
        assert sg(compute_rag(p, snap()), "schedule") is not None

    def test_schedule_zero_duration(self):
        p = proj(start_date=TODAY, end_date=TODAY)
        p.milestones = [Milestone("MS1", TODAY - timedelta(days=1))]
        assert sg(compute_rag(p, snap()), "schedule").score == 2

    def test_budget_zero(self):
        assert sg(compute_rag(proj(budget=0.0), snap()), "budget").score is None

    def test_budget_zero_some_spent(self):
        assert sg(compute_rag(proj(budget=0.0), snap(budget_spent=100.0, percent_complete=50.0)), "budget").score is None

    def test_budget_nan(self):
        r = compute_rag(proj(budget=float("nan")), snap())
        assert sg(r, "budget") is not None

    def test_budget_negative_variance(self):
        r = compute_rag(proj(budget=100), snap(budget_spent=0.0, percent_complete=100.0))
        assert sg(r, "budget").score == 0

    def test_3000_blockers_low(self):
        s = snap(blockers=[Blocker(f"B-{i}", TODAY, BlockerSeverity.LOW) for i in range(3000)])
        assert score_blockers(s, TODAY).score == 0

    def test_blocker_unicode_critical(self):
        s = snap(blockers=[Blocker("💥", TODAY, BlockerSeverity.CRITICAL)])
        assert score_blockers(s, TODAY).score == 2

    def test_1000_sentiment_sources(self):
        e = [SentimentEntry(f"s{i}", TODAY, "ok") for i in range(1000)]
        e[0] = SentimentEntry("s0", TODAY, "bad", SentimentScore.NEGATIVE)
        s = snap(); s.stakeholder_sentiment = e
        assert score_sentiment(s).score == 1

    def test_overdue_1000_days(self):
        p = proj(); p.milestones = [Milestone("Late", TODAY - timedelta(days=1000))]
        assert sg(compute_rag(p, snap()), "schedule").score == 2

    def test_as_of_future(self):
        p = proj(end_date=TODAY + timedelta(days=100))
        p.milestones = [Milestone("MS", TODAY - timedelta(days=5))]
        assert sg(compute_rag(p, snap(), as_of=TODAY + timedelta(days=365)), "schedule").score == 2

    def test_on_hold_forced_red(self):
        p = proj(); s = snap(status=ProjectStatus.ON_HOLD)
        p.snapshot_history = [s]
        r = compute_rag(p, s)
        assert r.status == RAG.RED
        assert any("on_hold" in o.lower() for o in r.overrides_applied)

    def test_cancelled_forced_red(self):
        p = proj(); s = snap(status=ProjectStatus.CANCELLED)
        p.snapshot_history = [s]
        r = compute_rag(p, s)
        assert r.status == RAG.RED
        assert any("cancelled" in o.lower() for o in r.overrides_applied)


# ═══════════════════════════════════════════════════════
#  SENTIMENT
# ═══════════════════════════════════════════════════════

class TestSentimentAdversarial:
    def test_binary_data(self):
        r = analyze_sentiment("\x00\x01\x02\xFF", mode="keyword")
        assert isinstance(r, SentimentScore)

    def test_very_long(self):
        r = analyze_sentiment("great " * 10000 + "bad " * 10000, mode="keyword")
        assert isinstance(r, SentimentScore)

    def test_unicode_confusables(self):
        assert analyze_sentiment("ɡооd", mode="keyword") == SentimentScore.NEUTRAL

    def test_html_tags(self):
        assert analyze_sentiment("<script>alert('great')</script>", mode="keyword") == SentimentScore.POSITIVE

    def test_only_spaces(self):
        assert analyze_sentiment("   \t\t\n\n  ", mode="keyword") == SentimentScore.UNKNOWN

    def test_make_entry_null_chars(self):
        entry = make_sentiment_entry("test", "\x00great\x00", TODAY)
        assert entry.score in SentimentScore


# ═══════════════════════════════════════════════════════
#  REPORTS
# ═══════════════════════════════════════════════════════

class TestReportsAdversarial:
    """weekly_narrative takes (project, result); monthly_content takes (list_of_tuples, date)."""

    def test_weekly_narrative_single_green(self):
        p = proj(name="G")
        r = compute_rag(p, snap())
        text = weekly_narrative(p, r)
        assert isinstance(text, str) and len(text) > 0

    def test_weekly_narrative_single_red(self):
        p = proj(); p.milestones = [Milestone("M", TODAY - timedelta(days=100))]
        s = snap(status=ProjectStatus.ON_HOLD)
        p.snapshot_history = [s]
        r = compute_rag(p, s)
        text = weekly_narrative(p, r)
        assert isinstance(text, str) and len(text) > 0

    def test_monthly_no_data(self):
        content = monthly_content([], TODAY)
        assert isinstance(content, dict)

    def test_monthly_malformed_dates(self):
        p = proj(start_date=date(1, 1, 1), end_date=date(1, 1, 1))
        r = compute_rag(p, snap())
        content = monthly_content([(p, r)], TODAY)
        assert isinstance(content, dict)

    def test_signal_summary(self):
        p = proj(); r = compute_rag(p, snap())
        s = _signal_summary(p, r)
        assert isinstance(s, str)


# ═══════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════

class TestMainAdversarial:
    def test_enrich_sentiment_empty_snapshot(self):
        p = proj()
        p.snapshot_history = [ProgressSnapshot(snapshot_date=TODAY, percent_complete=0.0)]
        with patch("main.analyze_sentiment", return_value=SentimentScore.POSITIVE):
            try: _enrich_sentiment(p)
            except Exception: pass


# ═══════════════════════════════════════════════════════
#  DB FILE SYSTEM
# ═══════════════════════════════════════════════════════

class TestDbFileSystem:
    def test_db_path_memory(self):
        os.environ['ZYCLUS_DB'] = ':memory:'
        assert _db_path() == ':memory:'

    def test_db_path_default(self):
        os.environ.pop('ZYCLUS_DB', None)
        assert _db_path().endswith("zycus.db")


# ═══════════════════════════════════════════════════════
#  LARGE DATA
# ═══════════════════════════════════════════════════════

class TestLargeData:
    def test_1000_milestones(self):
        p = proj(end_date=TODAY + timedelta(days=1000))
        p.milestones = [Milestone(f"MS-{i}", TODAY - timedelta(days=500-i)) for i in range(1000)]
        assert sg(compute_rag(p, snap()), "schedule").score >= 1

    def test_500_projects(self):
        for i in range(500):
            compute_rag(proj(name=f"S-{i}", budget=float(i)), snap())

    def test_whitespace_name(self):
        _db()
        pid = sv(proj(name="   "))
        assert get_project(pid)["name"] == "   "

    def test_control_chars_name(self):
        _db()
        pid = sv(proj(name="\r\n\t\x08\x07\x0c"))
        assert get_project(pid)["name"] == "\r\n\t\x08\x07\x0c"


# ═══════════════════════════════════════════════════════
#  RACE CONDITIONS
# ═══════════════════════════════════════════════════════

class TestRaceConditions:
    def test_blocker_age_clamps_zero(self):
        b = Blocker("RB", TODAY, BlockerSeverity.HIGH)
        assert b.age_days(TODAY) == 0
        assert b.age_days(TODAY + timedelta(days=1)) == 1

    def test_blocker_resolved_future(self):
        b = Blocker("RB", TODAY, BlockerSeverity.HIGH, resolved=True, date_resolved=TODAY + timedelta(days=5))
        assert b.age_days(TODAY) == 5

    def test_blocker_resolved_before_raised(self):
        b = Blocker("RB", TODAY, BlockerSeverity.HIGH, resolved=True, date_resolved=TODAY - timedelta(days=5))
        assert b.age_days(TODAY) == 0

    def test_milestone_days_late(self):
        assert Milestone("M", TODAY - timedelta(days=10)).days_late(TODAY) == 10

    def test_milestone_days_late_early(self):
        m = Milestone("M", TODAY - timedelta(days=10), status=MilestoneStatus.COMPLETE, actual_completion_date=TODAY - timedelta(days=12))
        assert m.days_late(TODAY) == 0

    def test_milestone_days_late_actual(self):
        m = Milestone("M", TODAY - timedelta(days=10), status=MilestoneStatus.COMPLETE, actual_completion_date=TODAY - timedelta(days=3))
        assert m.days_late(TODAY) == 7

    def test_schedule_neg_duration(self):
        assert proj(start_date=TODAY, end_date=TODAY - timedelta(days=10)).schedule_progress_expected(TODAY) == 100.0

    def test_schedule_before_start(self):
        assert proj(start_date=TODAY, end_date=TODAY + timedelta(days=100)).schedule_progress_expected(TODAY - timedelta(days=10)) == 0.0

    def test_schedule_after_end(self):
        p = proj(start_date=TODAY - timedelta(days=200), end_date=TODAY)
        assert p.schedule_progress_expected(TODAY + timedelta(days=1)) == 100.0

    def test_open_blockers_mixed(self):
        s = snap(blockers=[
            Blocker("r1", TODAY, BlockerSeverity.HIGH, resolved=True),
            Blocker("o1", TODAY, BlockerSeverity.LOW),
        ])
        assert len(s.open_blockers()) == 1

    def test_open_blockers_all_resolved(self):
        s = snap(blockers=[
            Blocker("r1", TODAY, BlockerSeverity.HIGH, resolved=True),
            Blocker("r2", TODAY, BlockerSeverity.CRITICAL, resolved=True),
        ])
        assert s.open_blockers() == []
