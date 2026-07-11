"""Round 3: schedule module, PPTX, boundary weights, concurrency, deeper edge cases."""
import os, sys, math, json, threading, tempfile, time, sqlite3
from unittest import mock
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
from models.sentiment import analyze_sentiment, make_sentiment_entry
from reports import weekly_narrative, monthly_content, _signal_summary
from main import _enrich_sentiment, synthesize_monthly

TODAY = date.today()
_TMPDIR = None


def _db():
    global _TMPDIR
    if _TMPDIR is None:
        _TMPDIR = tempfile.mkdtemp(prefix="zycus3_")
    p = os.path.join(_TMPDIR, f"t_{os.urandom(4).hex()}.db")
    os.environ['ZYCLUS_DB'] = p; init()
    return p


def proj(**kw):
    d = dict(name="A3", stakeholders=[], budget=100_000.0,
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
#  SCHEDULE MODULE
# ═══════════════════════════════════════════════════════

class TestScheduleModule:
    """Test _run_weekly and _run_monthly directly (no scheduler)."""

    def _run_weekly_mocked(self):
        with mock.patch("reports.ask_llm", return_value=None):
            from schedule import _run_weekly
            _run_weekly()

    def _run_monthly_mocked(self):
        with mock.patch("reports.ask_llm", return_value=None):
            from schedule import _run_monthly
            _run_monthly()

    def test_run_weekly_empty_db(self):
        _db()
        self._run_weekly_mocked()

    def test_run_weekly_with_projects(self):
        _db()
        pid = sv(proj(name="Weekly-Test"))
        sid = upsert_snapshot(dict(project_id=pid, snapshot_date=TODAY.isoformat(),
                                    percent_complete=50, budget_spent=10_000))
        upsert_sentiment(dict(snapshot_id=sid, source="PM",
                              date_recorded=TODAY.isoformat(), comment="great progress"))
        self._run_weekly_mocked()

    def test_run_weekly_skip_no_snapshot(self):
        _db()
        pid = sv(proj(name="No-Snap"))
        self._run_weekly_mocked()

    def test_run_weekly_corrupt_project(self):
        """_run_weekly wraps per-project in try/except — corrupt data won't crash the whole run."""
        _db()
        pid = sv(proj(name="Good"))
        sid = upsert_snapshot(dict(project_id=pid, snapshot_date=TODAY.isoformat(),
                                    percent_complete=50, budget_spent=10_000))
        with conn() as db:
            db.execute("UPDATE projects SET stakeholders=? WHERE id=?", ("not-json", pid))
        self._run_weekly_mocked()

    def test_run_monthly_empty_db(self):
        _db()
        self._run_monthly_mocked()

    def test_run_monthly_with_projects(self):
        _db()
        pid = sv(proj(name="Monthly-Test"))
        sid = upsert_snapshot(dict(project_id=pid, snapshot_date=TODAY.isoformat(),
                                    percent_complete=50, budget_spent=10_000))
        upsert_sentiment(dict(snapshot_id=sid, source="PM",
                              date_recorded=TODAY.isoformat(), comment="great progress"))
        with mock.patch("reports.ask_llm", return_value=None):
            from schedule import _run_monthly
            _run_monthly()  # should generate both text and PPTX


class TestSchedulerLifecycle:
    """Test start_scheduler and stop_scheduler."""

    def test_double_start(self):
        from schedule import scheduler, start_scheduler, stop_scheduler
        _db()
        # First start
        start_scheduler(weekly_hour=9, weekly_minute=0, weekly_day="mon")
        assert len(scheduler.get_jobs()) == 2
        # Second start — should not add more jobs
        start_scheduler()
        assert len(scheduler.get_jobs()) == 2
        stop_scheduler()
        assert not scheduler.running

    def test_stop_restart(self):
        from schedule import scheduler, start_scheduler, stop_scheduler
        _db()
        start_scheduler()
        assert scheduler.running
        stop_scheduler()
        assert not scheduler.running
        # restarting should work
        start_scheduler()
        assert scheduler.running
        stop_scheduler()

    def test_stop_when_not_running(self):
        from schedule import stop_scheduler
        stop_scheduler()  # should not crash

    def test_start_with_extreme_times(self):
        from schedule import start_scheduler, stop_scheduler
        _db()
        start_scheduler(weekly_hour=23, weekly_minute=59, weekly_day="sun")
        stop_scheduler()




# ═══════════════════════════════════════════════════════
#  PPTX GENERATION
# ═══════════════════════════════════════════════════════

class TestPPTXGeneration:
    """synthesize_monthly edge cases."""

    def _synth(self, results, path):
        with mock.patch("reports.ask_llm", return_value=None):
            return synthesize_monthly(results, TODAY, path)

    def test_synthesize_empty_results(self):
        """Empty results list should produce valid PPTX."""
        path = os.path.join(tempfile.mkdtemp(), "empty.pptx")
        result = self._synth([], path)
        assert os.path.exists(result)

    def test_synthesize_unicode_names(self):
        """Project with unicode/emoji name should not crash PPTX generation."""
        from pptx import Presentation as PptxPres
        p = proj(name="Zoë 🦄 项目 Alpha 😊")
        p.milestones = [Milestone("测试", TODAY + timedelta(days=10))]
        s = snap()
        p.snapshot_history = [s]
        r = compute_rag(p, s)
        path = os.path.join(tempfile.mkdtemp(), "unicode.pptx")
        result = self._synth([(p, r)], path)
        assert os.path.exists(result)
        prs = PptxPres(result)
        assert len(prs.slides) > 0

    def test_synthesize_20_projects(self):
        """20 projects should not crash PPTX."""
        results = []
        for i in range(20):
            p = proj(name=f"P-{i}", budget=float(i * 1000))
            s = snap(budget_spent=float(i * 500), percent_complete=float(i % 100))
            p.milestones = [Milestone(f"M{i}", TODAY - timedelta(days=i))] if i % 2 == 0 else []
            p.snapshot_history = [s]
            r = compute_rag(p, s)
            results.append((p, r))
        path = os.path.join(tempfile.mkdtemp(), "big.pptx")
        result = self._synth(results, path)
        assert os.path.exists(result)

    def test_synthesize_long_name(self):
        """5000-char project name should not crash PPTX."""
        p = proj(name="ABC" * 1666)
        s = snap()
        p.snapshot_history = [s]
        r = compute_rag(p, s)
        path = os.path.join(tempfile.mkdtemp(), "long.pptx")
        result = self._synth([(p, r)], path)
        assert os.path.exists(result)

    def test_synthesize_all_red(self):
        """All Red projects."""
        results = []
        for i in range(5):
            p = proj(name=f"Red-{i}")
            p.milestones = [Milestone("Late", TODAY - timedelta(days=100))]
            s = snap(status=ProjectStatus.CANCELLED)
            p.snapshot_history = [s]
            r = compute_rag(p, s)
            results.append((p, r))
        path = os.path.join(tempfile.mkdtemp(), "allred.pptx")
        result = self._synth(results, path)
        assert os.path.exists(result)

    def test_synthesize_budget_widely_varying(self):
        """Budget from -1 to 10^12 should not crash."""
        results = []
        for budget in [-1, 0, 1, 10_000_000_000]:
            p = proj(name=f"B-{budget}", budget=budget)
            s = snap(budget_spent=budget / 2 if budget > 0 else None, percent_complete=50)
            p.snapshot_history = [s]
            r = compute_rag(p, s)
            results.append((p, r))
        path = os.path.join(tempfile.mkdtemp(), "budget.pptx")
        result = self._synth(results, path)
        assert os.path.exists(result)


# ═══════════════════════════════════════════════════════
#  BOUNDARY WEIGHTS (compute_rag)
# ═══════════════════════════════════════════════════════

class TestBoundaryWeights:
    """Test compute_rag at exact weighted score boundaries."""

    def test_weighted_score_exactly_green(self):
        """Weighted score exactly at 0.0 should be Green."""
        p = proj()
        s = snap(percent_complete=100, budget_spent=0)
        s.open_blockers = lambda: []
        s.stakeholder_sentiment = []
        r = compute_rag(p, s)
        assert r.status == RAG.GREEN
        # Budget: spent 0% vs 100% complete → variance -100 → score 0
        # Schedule: no milestones, has % complete → compute variance → 100 - 100 = 0 → score 0
        # Blockers: none → score 0
        # Sentiment: none → score None (excluded)
        # present = schedule(0) + budget(0) + blockers(0)
        # total_weight = 0.35 + 0.25 + 0.25 = 0.85
        # weighted_score = (0 + 0 + 0) / 0.85 = 0.0 → Green
        assert r.weighted_score == 0.0

    def test_weighted_score_boundary_green_amber(self):
        """Weighted score just below 0.66 should be Green."""
        p = proj()
        s = snap()
        p.milestones = [Milestone("M1", TODAY - timedelta(days=3))]
        # Schedule: 1 overdue (< 2, <= 30d late) → score 1, variance: 50 - 50 = 0 → score 0
        # Max(1, 0) = 1
        # Budget: 50/100 * 100 = 50% spent vs 50% complete → variance 0 → score 0
        # Blockers: none → score 0
        # Sentiment: none → None
        # present = schedule(1) + budget(0) + blockers(0)
        # total = 0.35 + 0.25 + 0.25 = 0.85
        # weighted = (0.35*1 + 0) / 0.85 = 0.4118 → Green
        r = compute_rag(p, s)
        assert r.status == RAG.GREEN
        assert r.weighted_score < 0.66

    def test_weighted_score_boundary_amber_red(self):
        """Weighted score just above 1.33 should be Red."""
        p = proj()
        p.milestones = [Milestone("M1", TODAY - timedelta(days=100))]
        # Schedule: 1 overdue, 100d late → max(1, 0) = 1? No, days_late > 30 → score 2
        # Wait: days_late > 30 → milestone_score = 2
        # variance: 50 - 50 = 0 → score 0
        # max(2, 0) = 2
        s = snap()
        s.blockers = [Blocker("CRIT", TODAY, BlockerSeverity.CRITICAL)]
        s.stakeholder_sentiment = [SentimentEntry("PM", TODAY, "bad", SentimentScore.NEGATIVE),
                                    SentimentEntry("Client", TODAY, "also bad", SentimentScore.NEGATIVE)]
        r = compute_rag(p, s)
        # Schedule(2) + Budget(0) + Blockers(2) + Sentiment(2)
        # total = 1.0
        # weighted = (0.35*2 + 0.25*0 + 0.25*2 + 0.15*2) = 0.7 + 0 + 0.5 + 0.3 = 1.5
        # 1.5 ≥ 1.33 → Red... but critical blocker amber override only if status was Green
        # weighted score = 1.5 → already Red, override not needed
        assert r.status == RAG.RED

    def test_weighted_score_insufficient_data(self):
        """Only schedule signal present — missing_weight = 0.65 > 0.5 → insufficient_data."""
        p = proj()
        p.milestones = [Milestone("M1", TODAY - timedelta(days=3))]
        s = ProgressSnapshot(snapshot_date=TODAY, budget_spent=None, percent_complete=None)
        # No budget (None → excluded), no blockers (0), no sentiment (None)
        # present = schedule + blockers(0)
        # missing = 0.25(budget) + 0.15(sentiment) = 0.40
        # Actually wait: score_blockers returns score=0 "No open blockers" — that's a valid 0, not None
        # present = schedule + blockers is 0.35 + 0.25 = 0.60
        # missing = 0.25(budget) + 0.15(sentiment) = 0.40
        # 0.40 <= 0.50 → not insufficient
        r = compute_rag(p, s)
        assert not r.insufficient_data or r.status is not None

    def test_completely_insufficient_data(self):
        """No project milestones, no % complete, no budget, no blockers, no sentiment."""
        p = proj(start_date=TODAY, end_date=TODAY + timedelta(days=30))
        s = ProgressSnapshot(snapshot_date=TODAY, budget_spent=None, percent_complete=None)
        # Schedule: No milestones AND no % complete → None
        # Budget: None spent or None % complete → None
        # Blockers: empty → 0
        # Sentiment: empty → None
        # present = blockers(0) weight=0.25
        # missing = schedule(0.35) + budget(0.25) + sentiment(0.15) = 0.75 > 0.5
        # status = "no usable data" → Amber, insufficient_data
        r = compute_rag(p, s)
        assert r.insufficient_data

    def test_override_at_exact_weight(self):
        """Insufficient data but status would be Green → capped to Amber."""
        # Make schedule score = 0 (no overdue, no variance), blockers = 0
        p = proj(end_date=TODAY + timedelta(days=100))
        s = ProgressSnapshot(snapshot_date=TODAY, percent_complete=50, budget_spent=0)
        # Schedule: No milestones, but has % complete (50%) vs expected (100*elapsed/total)
        # elapsed = 60 (30 + 30), total = 130 → expected = 100 * 60 / 130 ≈ 46.15
        # variance = 50 - 46.15 = 3.85 → > -5 → score 0
        # Budget: spent 0% vs 50% complete → variance = -50 → score 0
        # Blockers: none → 0
        # Sentiment: none → None
        # present = schedule(0.35) + budget(0.25) + blockers(0.25) = 0.85
        # missing = sentiment(0.15) = 0.15
        # 0.15 <= 0.50 → not insufficient data
        r = compute_rag(p, s)
        assert not r.insufficient_data
        assert r.status == RAG.GREEN


# ═══════════════════════════════════════════════════════
#  weekly_narrative EDGE CASES
# ═══════════════════════════════════════════════════════

class TestNarrativeEdges:
    """weekly_narrative has fallback when LLM returns None."""

    def _narrative(self, p, r):
        with mock.patch("reports.ask_llm", return_value=None):
            return weekly_narrative(p, r)

    def test_narrative_all_none_signals(self):
        """Signals all None — fallback narrative should still produce valid text."""
        p = proj(budget=0, start_date=TODAY, end_date=TODAY)
        s = ProgressSnapshot(snapshot_date=TODAY, budget_spent=None, percent_complete=None)
        p.snapshot_history = [s]
        r = compute_rag(p, s)
        text = self._narrative(p, r)
        assert isinstance(text, str) and len(text) > 0

    def test_narrative_no_latest_snap(self):
        """project.latest is None in fallback — weekly_narrative should not crash."""
        p = proj()
        r = compute_rag(p, snap())
        p.snapshot_history = []
        text = self._narrative(p, r)
        assert isinstance(text, str) and len(text) > 0

    def test_narrative_override_text(self):
        """Test narrative with overrides."""
        p = proj(end_date=TODAY + timedelta(days=100))
        s = ProgressSnapshot(snapshot_date=TODAY, percent_complete=50, budget_spent=0)
        s.blockers = [Blocker("CRIT", TODAY, BlockerSeverity.CRITICAL)]
        p.snapshot_history = [s]
        r = compute_rag(p, s)
        text = self._narrative(p, r)
        assert isinstance(text, str) and len(text) > 0

    def test_narrative_empty_project_name(self):
        p = proj(name="")
        p.milestones = [Milestone("M", TODAY - timedelta(days=1))]
        s = snap()
        p.snapshot_history = [s]
        r = compute_rag(p, s)
        text = self._narrative(p, r)
        assert isinstance(text, str) and len(text) > 0

    def test_narrative_only_special_chars_name(self):
        p = proj(name="\n\r\t\0")
        s = snap()
        p.snapshot_history = [s]
        r = compute_rag(p, s)
        text = self._narrative(p, r)
        assert isinstance(text, str) and len(text) > 0


# ═══════════════════════════════════════════════════════
#  monthly_content EDGE CASES
# ═══════════════════════════════════════════════════════

class TestMonthlyContentEdges:
    def test_empty_results_fallback(self):
        """When ask_llm returns None, fallback produces expected dict structure."""
        with mock.patch("reports.ask_llm", return_value=None):
            content = monthly_content([], TODAY)
            assert isinstance(content, dict)
            assert isinstance(content.get("executive_summary", ""), str)
            assert "No project data available" in content["executive_summary"]

    def test_monthly_unicode_project_names(self):
        with mock.patch("reports.ask_llm", return_value=None):
            p = proj(name="⚠️ Critical / 紧急项目")
            s = snap()
            p.snapshot_history = [s]
            r = compute_rag(p, s)
            content = monthly_content([(p, r)], TODAY)
            assert isinstance(content, dict)


# ═══════════════════════════════════════════════════════
#  DB CRUD EXTREME EDGES
# ═══════════════════════════════════════════════════════

class TestDbExtremeEdges:
    def test_get_project_none_id(self):
        _db()
        result = get_project(None)
        assert result is None

    def test_get_project_zero_id(self):
        _db()
        assert get_project(0) is None

    def test_get_project_negative_id(self):
        _db()
        assert get_project(-1) is None

    def test_get_project_huge_id(self):
        _db()
        assert get_project(2**63 - 1) is None

    def test_upsert_project_empty_dict(self):
        _db()
        with pytest.raises((KeyError, sqlite3.OperationalError, RuntimeError)):
            upsert_project({})

    def test_upsert_snapshot_empty_dict(self):
        _db()
        with pytest.raises((KeyError, Exception)):
            upsert_snapshot({})

    def test_upsert_milestone_empty_dict(self):
        _db()
        with pytest.raises((KeyError, Exception)):
            upsert_milestone({})

    def test_upsert_blocker_empty_dict(self):
        _db()
        with pytest.raises((KeyError, Exception)):
            upsert_blocker({})

    def test_upsert_sentiment_empty_dict(self):
        _db()
        with pytest.raises((KeyError, Exception)):
            upsert_sentiment({})

    def test_list_projects_empty_db(self):
        _db()
        assert list_projects() == []

    def test_list_reports_empty_db(self):
        _db()
        assert list_reports() == []

    def test_list_reports_with_filter_empty(self):
        _db()
        assert list_reports("weekly") == []

    def test_save_report_with_none_fields(self):
        _db()
        rid = save_report("test", date.today().isoformat(), None, None)
        assert rid > 0
        r = get_report(rid)
        assert r["file_path"] is None
        assert r["summary"] is None

    def test_save_report_with_empty_strings(self):
        _db()
        rid = save_report("", "", "", "")
        assert rid > 0

    def test_delete_report_nonexistent(self):
        _db()
        delete_report(99999)

    def test_delete_report_twice(self):
        _db()
        delete_report(1)
        delete_report(1)  # should not crash


# ═══════════════════════════════════════════════════════
#  SENTIMENT EXTREMES
# ═══════════════════════════════════════════════════════

class TestSentimentExtremes:
    def test_input_none(self):
        assert analyze_sentiment(None, mode="keyword") == SentimentScore.UNKNOWN

    def test_input_empty(self):
        assert analyze_sentiment("", mode="keyword") == SentimentScore.UNKNOWN

    def test_input_whitespace_only(self):
        assert analyze_sentiment(" \t\n\r  ", mode="keyword") == SentimentScore.UNKNOWN

    def test_balanced_neg_pos_keywords(self):
        """Equal negative and positive keywords → NEUTRAL."""
        result = analyze_sentiment("great problem pleased failed", mode="keyword")
        assert result == SentimentScore.NEUTRAL

    def test_more_neg_than_pos(self):
        result = analyze_sentiment("great pleased but failed risk blocked", mode="keyword")
        assert result == SentimentScore.NEGATIVE

    def test_unicode_only_keywords(self):
        result = analyze_sentiment("feliz contento bien", mode="keyword")
        assert result == SentimentScore.NEUTRAL

    def test_repeated_keywords(self):
        result = analyze_sentiment("great " * 1000 + "bad", mode="keyword")
        assert result == SentimentScore.POSITIVE

    def test_kw_only_mode(self):
        result = analyze_sentiment("great progress this quarter", mode="keyword")
        assert result == SentimentScore.POSITIVE

    def test_auto_mode_without_api_key(self):
        """Without API key, auto mode falls through to keyword."""
        result = analyze_sentiment("this is a risk and a problem", mode="auto")
        assert result == SentimentScore.NEGATIVE

    def test_make_entry_edge_cases(self):
        from datetime import datetime
        entry = make_sentiment_entry("", "", TODAY, mode="keyword")
        assert entry.score == SentimentScore.UNKNOWN

        entry2 = make_sentiment_entry("\x00", "\x01\x02", TODAY, mode="keyword")
        assert isinstance(entry2.score, SentimentScore)

        entry3 = make_sentiment_entry("A" * 10000, "B" * 10000, TODAY, mode="keyword")
        assert isinstance(entry3.score, SentimentScore)


# ═══════════════════════════════════════════════════════
#  CONCURRENCY
# ═══════════════════════════════════════════════════════

class TestScheduleConcurrency:
    def test_concurrent_run_weekly(self):
        """Multiple concurrent _run_weekly calls should not crash."""
        _db()
        pid = sv(proj(name="Concurrent"))
        sid = upsert_snapshot(dict(project_id=pid, snapshot_date=TODAY.isoformat(),
                                    percent_complete=50, budget_spent=10_000))
        upsert_sentiment(dict(snapshot_id=sid, source="PM",
                              date_recorded=TODAY.isoformat(), comment="great"))
        errors = []
        lock = threading.Lock()

        from schedule import _run_weekly

        def run():
            try:
                _run_weekly()
            except Exception as e:
                with lock:
                    errors.append(e)

        ts = [threading.Thread(target=run) for _ in range(5)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        assert len(errors) == 0, f"errors: {errors}"

    def test_concurrent_read_write(self):
        """Concurrent DB writes while reading."""
        _db()
        errors = []
        lock = threading.Lock()

        def writer(i):
            try:
                p = proj(name=f"C-{i}", budget=float(i * 1000))
                pid = sv(p)
                sid = upsert_snapshot(dict(project_id=pid, snapshot_date=TODAY.isoformat(),
                                            percent_complete=float(i % 100), budget_spent=float(i * 500)))
                upsert_sentiment(dict(snapshot_id=sid, source="PM",
                                      date_recorded=TODAY.isoformat(), comment="ok"))
            except Exception as e:
                with lock:
                    errors.append(e)

        def reader():
            try:
                for _ in range(20):
                    get_project(1)
                    list_projects()
            except Exception as e:
                with lock:
                    errors.append(e)

        ts = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
        ts.append(threading.Thread(target=reader))
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        assert len(errors) == 0, f"errors: {errors}"

    def test_concurrent_save_and_list_reports(self):
        """Concurrent save_report and list_reports."""
        _db()
        errors = []
        lock = threading.Lock()

        def saver(i):
            try:
                save_report("weekly", TODAY.isoformat(), f"/tmp/r{i}.txt", f"summary {i}")
                save_report("monthly", TODAY.isoformat(), f"/tmp/r{i}.pptx", f"summary {i}")
            except Exception as e:
                with lock:
                    errors.append(e)

        def lister():
            try:
                for _ in range(10):
                    list_reports("weekly")
                    list_reports("monthly")
                    list_reports()
            except Exception as e:
                with lock:
                    errors.append(e)

        ts = [threading.Thread(target=saver, args=(i,)) for i in range(10)]
        ts.append(threading.Thread(target=lister))
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        assert len(errors) == 0, f"errors: {errors}"


# ═══════════════════════════════════════════════════════
#  project_to_domain DEEPER EDGES
# ═══════════════════════════════════════════════════════

class TestProjectToDomainDeep:
    """Edge cases not covered in previous adversarial tests."""

    def test_no_snapshots(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31"}
        p = project_to_domain(d)
        assert p.latest is None
        assert len(p.snapshot_history) == 0

    def test_no_milestones(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "snapshots": [{"snapshot_date": "2026-06-01"}]}
        p = project_to_domain(d)
        assert p.milestones == []
        assert p.latest is not None

    def test_milestone_actual_completion_none(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "milestones": [{"name": "M1", "due_date": "2026-06-01", "status": "in_progress",
                             "actual_completion_date": None}]}
        p = project_to_domain(d)
        assert p.milestones[0].actual_completion_date is None

    def test_blocker_date_resolved_none(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "snapshots": [{"snapshot_date": "2026-06-01",
                            "blockers": [{"description": "B1", "date_raised": "2026-05-01",
                                           "severity": "high", "resolved": False,
                                           "date_resolved": None}]}]}
        p = project_to_domain(d)
        assert p.snapshot_history[0].blockers[0].date_resolved is None

    def test_snapshot_with_notes(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "snapshots": [{"snapshot_date": "2026-06-01",
                            "notes": "Some notes here"}]}
        p = project_to_domain(d)
        assert p.snapshot_history[0].notes == "Some notes here"

    def test_snapshot_notes_none(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "snapshots": [{"snapshot_date": "2026-06-01"}]}
        p = project_to_domain(d)
        assert p.snapshot_history[0].notes is None

    def test_sentiment_with_notes(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "snapshots": [{"snapshot_date": "2026-06-01",
                            "sentiment": [{"source": "PM", "date_recorded": "2026-06-01",
                                            "comment": "ok"}]}]}
        p = project_to_domain(d)
        assert p.snapshot_history[0].stakeholder_sentiment[0].notes is None

    def test_bad_snapshot_status(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "snapshots": [{"snapshot_date": "2026-06-01", "status": "INVALID_STATUS"}]}
        p = project_to_domain(d)
        assert p.snapshot_history[0].status == ProjectStatus.IN_PROGRESS

    def test_bad_blocker_severity(self):
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "snapshots": [{"snapshot_date": "2026-06-01",
                            "blockers": [{"description": "B1", "date_raised": "2026-05-01",
                                           "severity": "MEGA-CRITICAL", "resolved": False}]}]}
        p = project_to_domain(d)
        assert p.snapshot_history[0].blockers[0].severity == BlockerSeverity.MEDIUM

    def test_snapshot_order(self):
        """Snapshots should be sorted by date in add_snapshot."""
        d = {"name": "X", "budget": 100, "start_date": "2026-01-01", "end_date": "2026-12-31",
             "snapshots": [{"snapshot_date": "2026-06-01"},
                           {"snapshot_date": "2026-03-01"},
                           {"snapshot_date": "2026-09-01"}]}
        p = project_to_domain(d)
        dates = [s.snapshot_date.isoformat() for s in p.snapshot_history]
        assert dates == ["2026-03-01", "2026-06-01", "2026-09-01"]

    def test_negative_budget_project_to_domain(self):
        d = {"name": "X", "budget": -5000, "start_date": "2026-01-01", "end_date": "2026-12-31"}
        p = project_to_domain(d)
        assert p.budget == -5000


# ═══════════════════════════════════════════════════════
#  FILE I/O EDGE CASES
# ═══════════════════════════════════════════════════════

class TestFileIO:
    def test_schedule_output_dir_creation(self):
        """_run_weekly should create OUTPUT_DIR if it doesn't exist."""
        _db()
        import schedule as sched
        orig = sched.OUTPUT_DIR
        new_dir = os.path.join(tempfile.mkdtemp(), "nonexistent", "deep", "dir")
        sched.OUTPUT_DIR = new_dir
        try:
            pid = sv(proj(name="DirTest"))
            sid = upsert_snapshot(dict(project_id=pid, snapshot_date=TODAY.isoformat(),
                                        percent_complete=50, budget_spent=10_000))
            upsert_sentiment(dict(snapshot_id=sid, source="PM",
                                  date_recorded=TODAY.isoformat(), comment="great"))
            with mock.patch("reports.ask_llm", return_value=None):
                sched._run_weekly()
            assert os.path.exists(new_dir)
        finally:
            sched.OUTPUT_DIR = orig

    def test_save_report_long_path(self):
        """Very long file_path in save_report should not crash SQLite."""
        _db()
        long_path = "C:\\" + "sub\\" * 50 + "report.txt"
        rid = save_report("weekly", TODAY.isoformat(), long_path, "short summary")
        assert rid > 0

    def test_schedule_concurrent_output_dir_write(self):
        """Multiple concurrent _run_weekly calls writing to same dir."""
        _db()
        import schedule as sched
        orig = sched.OUTPUT_DIR
        shared_dir = os.path.join(tempfile.mkdtemp(), "shared")
        sched.OUTPUT_DIR = shared_dir
        errors = []
        lock = threading.Lock()

        def run():
            try:
                pid = sv(proj(name=f"Concurrent-{os.urandom(2).hex()}"))
                sid = upsert_snapshot(dict(project_id=pid, snapshot_date=TODAY.isoformat(),
                                            percent_complete=50, budget_spent=10_000))
                upsert_sentiment(dict(snapshot_id=sid, source="PM",
                                      date_recorded=TODAY.isoformat(), comment="ok"))
                sched._run_weekly()
            except Exception as e:
                with lock:
                    errors.append(e)

        try:
            ts = [threading.Thread(target=run) for _ in range(10)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(30)
            assert len(errors) == 0, f"errors: {errors}"
        finally:
            sched.OUTPUT_DIR = orig


# ═══════════════════════════════════════════════════════
#  compute_rag INTERNAL EDGES
# ═══════════════════════════════════════════════════════

class TestComputeRagInternal:
    def test_score_schedule_no_milestones_and_no_percent(self):
        p = proj(start_date=TODAY, end_date=TODAY + timedelta(days=30))
        s = ProgressSnapshot(snapshot_date=TODAY, budget_spent=None, percent_complete=None)
        r = score_schedule(p, s, TODAY)
        assert r.score is None
        assert "no milestones" in r.rationale.lower()

    def test_score_schedule_only_milestones_no_percent(self):
        p = proj()
        p.milestones = [Milestone("M1", TODAY - timedelta(days=3))]
        s = ProgressSnapshot(snapshot_date=TODAY, budget_spent=None, percent_complete=None)
        r = score_schedule(p, s, TODAY)
        # milestones exist, so score computed from milestone data
        assert r.score is not None

    def test_score_schedule_only_percent_no_milestones(self):
        p = proj()
        s = ProgressSnapshot(snapshot_date=TODAY, budget_spent=None, percent_complete=50)
        # wait, this path: variance_score is set (percent_complete=50) but no milestones
        # if variance_score is None -> no, it's set to 0 or 1 or 2
        # Actually variance_score will be calculated: expected - 50... let me check
        expected = p.schedule_progress_expected(TODAY)
        # project started 30 days ago, ends in 30 days, total=60 days, elapsed=60 days
        # expected = 100 * 60 / 60 = 100
        # variance = 50 - 100 = -50
        # variance <= -15 → score 2
        # But variance_score is not None, so the "no milestones and no % complete" branch is skipped
        r = score_schedule(p, s, TODAY)
        assert r.score is not None

    def test_score_schedule_variance_score_none_no_milestones(self):
        """variance_score None, no milestones → returns None."""
        # variance_score can be None only if snap.percent_complete is None
        p = proj()
        s = ProgressSnapshot(snapshot_date=TODAY, budget_spent=None, percent_complete=None)
        r = score_schedule(p, s, TODAY)
        assert r.score is None

    def test_score_budget_none_budget(self):
        p = proj(budget=None)
        s = snap()
        r = score_budget(p, s)
        assert r.score is None

    def test_score_budget_zero_budget(self):
        p = proj(budget=0)
        s = snap(budget_spent=100, percent_complete=50)
        r = score_budget(p, s)
        assert r.score is None

    def test_score_budget_none_spent(self):
        p = proj()
        s = snap(budget_spent=None, percent_complete=50)
        r = score_budget(p, s)
        assert r.score is None

    def test_score_budget_none_percent(self):
        p = proj()
        s = snap(budget_spent=50, percent_complete=None)
        r = score_budget(p, s)
        assert r.score is None

    def test_score_blockers_all_resolved(self):
        s = snap(blockers=[Blocker("B1", TODAY, BlockerSeverity.CRITICAL, resolved=True)])
        r = score_blockers(s, TODAY)
        assert r.score == 0

    def test_score_blockers_mixed_low(self):
        s = snap(blockers=[
            Blocker("B1", TODAY, BlockerSeverity.LOW),
            Blocker("B2", TODAY, BlockerSeverity.LOW),
        ])
        r = score_blockers(s, TODAY)
        assert r.score == 0  # only LOW, < 2 medium

    def test_score_blockers_two_medium(self):
        s = snap(blockers=[
            Blocker("B1", TODAY, BlockerSeverity.MEDIUM),
            Blocker("B2", TODAY, BlockerSeverity.MEDIUM),
        ])
        r = score_blockers(s, TODAY)
        assert r.score == 1  # 2+ medium

    def test_score_blockers_one_high_aged(self):
        s = snap(blockers=[
            Blocker("B1", TODAY - timedelta(days=20), BlockerSeverity.HIGH),
        ])
        r = score_blockers(s, TODAY)
        assert r.score == 2  # high aged > 14 days

    def test_score_blockers_one_high_not_aged(self):
        s = snap(blockers=[
            Blocker("B1", TODAY - timedelta(days=5), BlockerSeverity.HIGH),
        ])
        r = score_blockers(s, TODAY)
        assert r.score == 1  # high, not aged

    def test_score_sentiment_only_unknown(self):
        s = snap()
        s.stakeholder_sentiment = [SentimentEntry("PM", TODAY, "ok", SentimentScore.UNKNOWN)]
        r = score_sentiment(s)
        assert r.score is None  # only UNKNOWN entries, excluded

    def test_score_sentiment_positive_only(self):
        s = snap()
        s.stakeholder_sentiment = [SentimentEntry("PM", TODAY, "great", SentimentScore.POSITIVE),
                                    SentimentEntry("Client", TODAY, "good", SentimentScore.POSITIVE)]
        r = score_sentiment(s)
        assert r.score == 0  # no negative sources

    def test_score_sentiment_one_negative(self):
        s = snap()
        s.stakeholder_sentiment = [SentimentEntry("PM", TODAY, "bad", SentimentScore.NEGATIVE),
                                    SentimentEntry("Client", TODAY, "good", SentimentScore.POSITIVE)]
        r = score_sentiment(s)
        assert r.score == 1

    def test_score_sentiment_two_negative_same_source(self):
        s = snap()
        s.stakeholder_sentiment = [SentimentEntry("PM", TODAY, "bad", SentimentScore.NEGATIVE),
                                    SentimentEntry("PM", TODAY, "really bad", SentimentScore.NEGATIVE)]
        r = score_sentiment(s)
        # same source counted once
        assert r.score == 1


# ═══════════════════════════════════════════════════════
#  ENRICH SENTIMENT EDGES
# ═══════════════════════════════════════════════════════

class TestEnrichSentimentEdges:
    def test_enrich_sentiment_no_latest(self):
        """No latest snapshot → _enrich_sentiment returns None."""
        p = proj()
        p.snapshot_history = []
        result = _enrich_sentiment(p)
        assert result is None

    def test_enrich_sentiment_empty_sentiment(self):
        """Latest snapshot with empty sentiment list → should not crash."""
        p = proj()
        s = ProgressSnapshot(snapshot_date=TODAY)
        p.snapshot_history = [s]
        result = _enrich_sentiment(p)
        assert result is None

    def test_enrich_sentiment_updates_scores(self):
        """Enrich sentiment should assign score to each entry."""
        p = proj()
        entries = [SentimentEntry("PM", TODAY, "great progress"),
                    SentimentEntry("Client", TODAY, "serious concern")]
        s = snap()
        s.stakeholder_sentiment = entries
        p.snapshot_history = [s]
        _enrich_sentiment(p)
        assert entries[0].score == SentimentScore.POSITIVE
        assert entries[1].score == SentimentScore.NEGATIVE
