"""Comprehensive non-LLM test suite covering all modules and edge cases.

This suite mocks ask_llm to return None, forcing all LLM-dependent code
into its deterministic fallback paths. No external API calls are made.
"""
from __future__ import annotations
import json, os, sys, tempfile, shutil
from datetime import date, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# ── Mock LLM before any imports that reference it ──────────
import llm
llm.ask_llm = lambda *a, **kw: None

# Now safe to import modules that call ask_llm
from models.projects import (
    Project, ProgressSnapshot, Milestone, MilestoneStatus, ProjectStatus,
    Blocker, BlockerSeverity, SentimentEntry, SentimentScore,
)
from models.rag import compute_rag, RagResult, RAG, score_schedule, score_budget, score_blockers, score_sentiment, WEIGHTS
from models.sentiment import analyze_sentiment, _keyword_sentiment, _llm_sentiment, make_sentiment_entry
from reports import weekly_narrative, monthly_content, _signal_summary
# Force reports module reference too (separate from llm.ask_llm)
import reports
reports.ask_llm = lambda *a, **kw: None
from database import init, conn, list_projects, get_project, upsert_project, delete_project
from database import upsert_milestone, delete_milestone, upsert_snapshot, delete_snapshot
from database import upsert_blocker, delete_blocker, upsert_sentiment, delete_sentiment
from database import list_reports, save_report, delete_report, get_report, project_to_domain


TODAY = date(2026, 7, 11)

###############################################################################
#  PROJECTS DOMAIN (projects.py)
###############################################################################

class TestMilestone:
    def test_is_overdue_complete_not_overdue(self):
        m = Milestone("M1", date(2026, 5, 1), MilestoneStatus.COMPLETE, date(2026, 4, 28))
        assert not m.is_overdue(TODAY)

    def test_is_overdue_in_progress_past_due(self):
        m = Milestone("M1", date(2026, 5, 1), MilestoneStatus.IN_PROGRESS)
        assert m.is_overdue(TODAY)

    def test_is_overdue_future_not_overdue(self):
        m = Milestone("M1", TODAY + timedelta(days=10), MilestoneStatus.IN_PROGRESS)
        assert not m.is_overdue(TODAY)

    def test_is_overdue_defaults_to_today(self):
        m = Milestone("M1", date(2020, 1, 1), MilestoneStatus.NOT_STARTED)
        assert m.is_overdue()

    def test_days_late_completed_early_zero(self):
        m = Milestone("M1", date(2026, 5, 1), MilestoneStatus.COMPLETE, date(2026, 4, 28))
        assert m.days_late(TODAY) == 0

    def test_days_late_still_in_progress(self):
        m = Milestone("M1", date(2026, 5, 1), MilestoneStatus.IN_PROGRESS)
        assert m.days_late(TODAY) == (TODAY - date(2026, 5, 1)).days

    def test_days_late_future_not_late(self):
        m = Milestone("M1", TODAY + timedelta(days=5))
        assert m.days_late(TODAY) == 0

    def test_is_overdue_blocked_milestone(self):
        m = Milestone("M1", date(2026, 5, 1), MilestoneStatus.BLOCKED)
        assert m.is_overdue(TODAY)

    def test_is_overdue_at_risk_milestone(self):
        m = Milestone("M1", date(2026, 5, 1), MilestoneStatus.AT_RISK)
        assert m.is_overdue(TODAY)


class TestBlocker:
    def test_age_days_unresolved(self):
        b = Blocker("test", date(2026, 6, 1), BlockerSeverity.HIGH)
        assert b.age_days(TODAY) == (TODAY - date(2026, 6, 1)).days

    def test_age_days_resolved(self):
        b = Blocker("test", date(2026, 6, 1), BlockerSeverity.HIGH, resolved=True, date_resolved=date(2026, 6, 15))
        assert b.age_days(TODAY) == 14

    def test_age_days_future_raised_zero(self):
        b = Blocker("test", TODAY + timedelta(days=1), BlockerSeverity.LOW)
        assert b.age_days(TODAY) == 0


class TestProgressSnapshot:
    def test_open_blockers_none(self):
        snap = ProgressSnapshot(snapshot_date=TODAY, blockers=[
            Blocker("r1", date(2026, 6, 1), BlockerSeverity.HIGH, resolved=True),
            Blocker("r2", date(2026, 6, 1), BlockerSeverity.CRITICAL, resolved=True),
        ])
        assert snap.open_blockers() == []

    def test_open_blockers_mixed(self):
        snap = ProgressSnapshot(snapshot_date=TODAY, blockers=[
            Blocker("open", date(2026, 6, 1), BlockerSeverity.HIGH),
            Blocker("resolved", date(2026, 6, 1), BlockerSeverity.LOW, resolved=True, date_resolved=date(2026, 6, 10)),
        ])
        assert len(snap.open_blockers()) == 1
        assert snap.open_blockers()[0].description == "open"

    def test_open_blockers_empty_list(self):
        snap = ProgressSnapshot(snapshot_date=TODAY, blockers=[])
        assert snap.open_blockers() == []


class TestProject:
    def test_latest_none_when_no_snapshots(self):
        p = Project("P", [], 100_000, TODAY, TODAY)
        assert p.latest is None

    def test_latest_returns_last_snapshot(self):
        p = Project("P", [], 100_000, TODAY, TODAY)
        s1 = ProgressSnapshot(TODAY - timedelta(days=10))
        s2 = ProgressSnapshot(TODAY)
        p.add_snapshot(s1)
        p.add_snapshot(s2)
        assert p.latest is s2

    def test_add_snapshot_sorts_by_date(self):
        p = Project("P", [], 100_000, TODAY, TODAY)
        s_later = ProgressSnapshot(TODAY)
        s_earlier = ProgressSnapshot(TODAY - timedelta(days=5))
        p.add_snapshot(s_later)
        p.add_snapshot(s_earlier)
        assert p.latest is s_later

    def test_milestones_overdue_none(self):
        p = Project("P", [], 100_000, TODAY, TODAY, milestones=[
            Milestone("M1", TODAY + timedelta(days=1), MilestoneStatus.COMPLETE, TODAY),
            Milestone("M2", TODAY + timedelta(days=10)),
        ])
        assert p.milestones_overdue(TODAY) == []

    def test_milestones_overdue_some(self):
        p = Project("P", [], 100_000, TODAY, TODAY, milestones=[
            Milestone("M1", TODAY - timedelta(days=5), MilestoneStatus.IN_PROGRESS),
            Milestone("M2", TODAY + timedelta(days=10)),
            Milestone("M3", TODAY - timedelta(days=1), MilestoneStatus.NOT_STARTED),
        ])
        overdue = p.milestones_overdue(TODAY)
        assert len(overdue) == 2
        assert overdue[0].name == "M1"
        assert overdue[1].name == "M3"

    def test_schedule_progress_expected_zero_duration(self):
        p = Project("P", [], 100_000, TODAY, TODAY)
        assert p.schedule_progress_expected(TODAY) == 100.0

    def test_schedule_progress_expected_halfway(self):
        start = date(2026, 1, 1)
        end = date(2026, 7, 1)
        p = Project("P", [], 100_000, start, end)
        as_of = date(2026, 4, 1)
        expected = p.schedule_progress_expected(as_of)
        assert 49.0 <= expected <= 51.0  # ~50%

    def test_schedule_progress_expected_clamped_below_zero(self):
        p = Project("P", [], 100_000, TODAY, TODAY + timedelta(days=100))
        before_start = TODAY - timedelta(days=10)
        assert p.schedule_progress_expected(before_start) == 0.0

    def test_schedule_progress_expected_clamped_above_100(self):
        p = Project("P", [], 100_000, TODAY, TODAY + timedelta(days=100))
        after_end = TODAY + timedelta(days=200)
        assert p.schedule_progress_expected(after_end) == 100.0


###############################################################################
#  RAG SIGNAL SCORERS (rag.py)
###############################################################################

class TestRagScoreSchedule:
    def test_no_milestones_no_percent(self):
        p = Project("P", [], 100_000, TODAY, TODAY + timedelta(days=100))
        snap = ProgressSnapshot(TODAY)
        r = score_schedule(p, snap, TODAY)
        assert r.score is None
        assert "No milestones" in r.rationale

    def test_all_complete_score_zero(self):
        p = Project("P", [], 100_000, TODAY, TODAY + timedelta(days=100), milestones=[
            Milestone("M1", TODAY + timedelta(days=10), MilestoneStatus.COMPLETE, TODAY),
        ])
        snap = ProgressSnapshot(TODAY, percent_complete=50)
        r = score_schedule(p, snap, TODAY)
        assert r.score == 0

    def test_one_overdue_score_one(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31), milestones=[
            Milestone("M1", TODAY - timedelta(days=5), MilestoneStatus.IN_PROGRESS),
        ])
        snap = ProgressSnapshot(TODAY, percent_complete=50)
        r = score_schedule(p, snap, TODAY)
        assert r.score == 1

    def test_two_overdue_score_two(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31), milestones=[
            Milestone("M1", date(2026, 5, 1), MilestoneStatus.NOT_STARTED),
            Milestone("M2", date(2026, 6, 1), MilestoneStatus.IN_PROGRESS),
        ])
        snap = ProgressSnapshot(TODAY, percent_complete=50)
        r = score_schedule(p, snap, TODAY)
        assert r.score == 2

    def test_blocked_milestone_score_two(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31), milestones=[
            Milestone("M1", date(2026, 5, 1), MilestoneStatus.BLOCKED),
        ])
        snap = ProgressSnapshot(TODAY, percent_complete=50)
        r = score_schedule(p, snap, TODAY)
        assert r.score == 2

    def test_variance_negative_15_score_two(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        snap = ProgressSnapshot(TODAY, percent_complete=10.0)
        r = score_schedule(p, snap, TODAY)
        assert r.score == 2  # expected ~52%, actual 10%, variance -42 <= -15

    def test_variance_negative_6_score_one(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        snap = ProgressSnapshot(TODAY, percent_complete=46.0)
        r = score_schedule(p, snap, TODAY)
        # expected = 191/364*100 = 52.5, actual = 46, variance = -6.5, -6.5 > -15? Yes. -6.5 > -5? Yes, so > -15 means <= -5 check: -6.5 <= -5 => score 1
        assert r.score == 1

    def test_variance_exactly_at_minus_5_score_zero(self):
        """-5 variance boundary: <= -5 triggers score 1, so use -4.9 for score 0."""
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        elapsed = (TODAY - date(2026, 1, 1)).days / (date(2026, 12, 31) - date(2026, 1, 1)).days * 100
        snap = ProgressSnapshot(TODAY, percent_complete=elapsed - 4.9)
        r = score_schedule(p, snap, TODAY)
        assert r.score == 0, f"Expected 0 got {r.score}"

    def test_variance_15_plus_score_two(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        # expected ~52%, actual 30% = variance -22
        snap = ProgressSnapshot(TODAY, percent_complete=30.0)
        r = score_schedule(p, snap, TODAY)
        assert r.score == 2


class TestRagScoreBudget:
    def test_no_budget_data_score_none(self):
        p = Project("P", [], 0, TODAY, TODAY)
        snap = ProgressSnapshot(TODAY, budget_spent=0, percent_complete=50)
        r = score_budget(p, snap)
        assert r.score is None

    def test_no_percent_complete_score_none(self):
        p = Project("P", [], 100_000, TODAY, TODAY)
        snap = ProgressSnapshot(TODAY, budget_spent=50_000, percent_complete=None)
        r = score_budget(p, snap)
        assert r.score is None

    def test_no_budget_spent_score_none(self):
        p = Project("P", [], 100_000, TODAY, TODAY)
        snap = ProgressSnapshot(TODAY, budget_spent=None, percent_complete=50)
        r = score_budget(p, snap)
        assert r.score is None

    def test_variance_11_score_one(self):
        p = Project("P", [], 100_000, TODAY, TODAY)
        snap = ProgressSnapshot(TODAY, budget_spent=56_000, percent_complete=45)
        r = score_budget(p, snap)
        # 56% spent vs 45% complete = 11 variance > 10 => score 1
        assert r.score == 1

    def test_variance_26_score_two(self):
        p = Project("P", [], 100_000, TODAY, TODAY)
        snap = ProgressSnapshot(TODAY, budget_spent=75_000, percent_complete=35)
        r = score_budget(p, snap)
        # 75% spent vs 35% complete = 40 variance > 25 => score 2
        assert r.score == 2

    def test_variance_exactly_10_score_zero(self):
        p = Project("P", [], 100_000, TODAY, TODAY)
        snap = ProgressSnapshot(TODAY, budget_spent=55_000, percent_complete=45)
        r = score_budget(p, snap)
        # 55% spent vs 45% complete = 10 variance. 10 > 25? No. 10 > 10? No (strict). => score 0
        assert r.score == 0

    def test_variance_exactly_25_score_one(self):
        p = Project("P", [], 100_000, TODAY, TODAY)
        snap = ProgressSnapshot(TODAY, budget_spent=70_000, percent_complete=45)
        r = score_budget(p, snap)
        # 70% spent vs 45% complete = 25 variance. 25 > 25? No. 25 > 10? Yes => score 1
        assert r.score == 1

    def test_under_budget_score_zero(self):
        p = Project("P", [], 1_000_000, TODAY, TODAY)
        snap = ProgressSnapshot(TODAY, budget_spent=100_000, percent_complete=50)
        r = score_budget(p, snap)
        # 10% spent vs 50% complete = -40 variance => nothing triggered => score 0
        assert r.score == 0


class TestRagScoreBlockers:
    def test_no_blockers_score_zero(self):
        snap = ProgressSnapshot(TODAY, blockers=[])
        r = score_blockers(snap, TODAY)
        assert r.score == 0

    def test_critical_blocker_score_two(self):
        snap = ProgressSnapshot(TODAY, blockers=[
            Blocker("c", date(2026, 7, 1), BlockerSeverity.CRITICAL),
        ])
        r = score_blockers(snap, TODAY)
        assert r.score == 2

    def test_high_aged_over_14_days_score_two(self):
        snap = ProgressSnapshot(TODAY, blockers=[
            Blocker("h", date(2026, 6, 20), BlockerSeverity.HIGH),
        ])
        r = score_blockers(snap, TODAY)
        assert r.score == 2

    def test_high_not_aged_score_one(self):
        snap = ProgressSnapshot(TODAY, blockers=[
            Blocker("h", TODAY - timedelta(days=5), BlockerSeverity.HIGH),
        ])
        r = score_blockers(snap, TODAY)
        assert r.score == 1

    def test_two_medium_blockers_score_one(self):
        snap = ProgressSnapshot(TODAY, blockers=[
            Blocker("m1", date(2026, 7, 1), BlockerSeverity.MEDIUM),
            Blocker("m2", date(2026, 7, 2), BlockerSeverity.MEDIUM),
        ])
        r = score_blockers(snap, TODAY)
        assert r.score == 1

    def test_single_medium_blocker_score_zero(self):
        snap = ProgressSnapshot(TODAY, blockers=[
            Blocker("m", date(2026, 7, 1), BlockerSeverity.MEDIUM),
        ])
        r = score_blockers(snap, TODAY)
        assert r.score == 0

    def test_low_blocker_only_score_zero(self):
        snap = ProgressSnapshot(TODAY, blockers=[
            Blocker("l", date(2026, 7, 1), BlockerSeverity.LOW),
        ])
        r = score_blockers(snap, TODAY)
        assert r.score == 0

    def test_all_types_mixed(self):
        snap = ProgressSnapshot(TODAY, blockers=[
            Blocker("low", date(2026, 7, 1), BlockerSeverity.LOW),
            Blocker("med", date(2026, 7, 1), BlockerSeverity.MEDIUM),
            Blocker("high", date(2026, 7, 1), BlockerSeverity.HIGH),
        ])
        r = score_blockers(snap, TODAY)
        assert r.score == 1  # high present = 1, not aged >14


class TestRagScoreSentiment:
    def test_no_entries_score_none(self):
        snap = ProgressSnapshot(TODAY, stakeholder_sentiment=[])
        r = score_sentiment(snap)
        assert r.score is None

    def test_all_unknown_score_none(self):
        snap = ProgressSnapshot(TODAY, stakeholder_sentiment=[
            SentimentEntry("A", TODAY, "?", SentimentScore.UNKNOWN),
        ])
        r = score_sentiment(snap)
        assert r.score is None

    def test_one_negative_source_score_one(self):
        snap = ProgressSnapshot(TODAY, stakeholder_sentiment=[
            SentimentEntry("A", TODAY, "Bad", SentimentScore.NEGATIVE),
            SentimentEntry("B", TODAY, "Good", SentimentScore.POSITIVE),
        ])
        r = score_sentiment(snap)
        assert r.score == 1

    def test_two_negative_sources_score_two(self):
        snap = ProgressSnapshot(TODAY, stakeholder_sentiment=[
            SentimentEntry("A", TODAY, "Bad", SentimentScore.NEGATIVE),
            SentimentEntry("B", TODAY, "Bad", SentimentScore.NEGATIVE),
            SentimentEntry("C", TODAY, "Good", SentimentScore.POSITIVE),
        ])
        r = score_sentiment(snap)
        assert r.score == 2

    def test_all_positive_score_zero(self):
        snap = ProgressSnapshot(TODAY, stakeholder_sentiment=[
            SentimentEntry("A", TODAY, "Great", SentimentScore.POSITIVE),
            SentimentEntry("B", TODAY, "Good", SentimentScore.POSITIVE),
        ])
        r = score_sentiment(snap)
        assert r.score == 0

    def test_same_source_multiple_negative_counts_as_one_source(self):
        """Same source with multiple negative entries should still be 1 source."""
        snap = ProgressSnapshot(TODAY, stakeholder_sentiment=[
            SentimentEntry("A", TODAY, "Bad1", SentimentScore.NEGATIVE),
            SentimentEntry("A", TODAY, "Bad2", SentimentScore.NEGATIVE),
        ])
        r = score_sentiment(snap)
        # Both from source "A" => 1 unique negative source => score 1
        assert r.score == 1


###############################################################################
#  COMPUTE RAG (rag.py)
###############################################################################

class TestComputeRag:
    def test_all_green(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        p.add_snapshot(ProgressSnapshot(TODAY, budget_spent=30_000, percent_complete=60.0, blockers=[],
                                         stakeholder_sentiment=[SentimentEntry("A", TODAY, "Great", SentimentScore.POSITIVE)]))
        r = compute_rag(p, p.latest, TODAY)
        assert r.status == RAG.GREEN

    def test_no_data_at_all_amber(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        p.add_snapshot(ProgressSnapshot(TODAY, budget_spent=None, percent_complete=None, blockers=[], stakeholder_sentiment=[]))
        r = compute_rag(p, p.latest, TODAY)
        assert r.status == RAG.AMBER
        assert r.insufficient_data
        # blockers=0 contributes weight 0.25, so weighted=0*0.25/0.25=0.0
        assert r.weighted_score is not None
        assert r.weighted_score == 0.0

    def test_on_hold_forces_red(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        p.add_snapshot(ProgressSnapshot(TODAY, budget_spent=0, percent_complete=0, status=ProjectStatus.ON_HOLD))
        r = compute_rag(p, p.latest, TODAY)
        assert r.status == RAG.RED
        assert any("Forced Red" in o for o in r.overrides_applied)

    def test_cancelled_forces_red(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        p.add_snapshot(ProgressSnapshot(TODAY, budget_spent=0, percent_complete=0, status=ProjectStatus.CANCELLED))
        r = compute_rag(p, p.latest, TODAY)
        assert r.status == RAG.RED

    def test_critical_blocker_override_amber(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        p.add_snapshot(ProgressSnapshot(TODAY, budget_spent=50_000, percent_complete=50.0, blockers=[
            Blocker("c", date(2026, 7, 1), BlockerSeverity.CRITICAL),
        ], stakeholder_sentiment=[SentimentEntry("A", TODAY, "ok", SentimentScore.POSITIVE)]))
        r = compute_rag(p, p.latest, TODAY)
        # Would be Green but critical blocker overrides to Amber
        assert r.status == RAG.AMBER
        assert any("Critical blocker" in o for o in r.overrides_applied)

    def test_insufficient_data_amber_override(self):
        """Even if weighted score suggests Green, insufficient data caps at Amber."""
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        # Only sentiment data (weight 0.15). Budget/schedule/blockers all None
        p.add_snapshot(ProgressSnapshot(TODAY, budget_spent=None, percent_complete=None, blockers=[],
                                         stakeholder_sentiment=[SentimentEntry("A", TODAY, "Great", SentimentScore.POSITIVE)]))
        r = compute_rag(p, p.latest, TODAY)
        # Sentiment score 0 with weight 0.15, total missing = 0.35+0.25+0.25 = 0.85 > 0.5
        assert r.insufficient_data
        assert r.status == RAG.AMBER

    def test_weighted_score_calculation(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        p.add_snapshot(ProgressSnapshot(TODAY, budget_spent=50_000, percent_complete=50.0, blockers=[
            Blocker("b", date(2026, 7, 1), BlockerSeverity.MEDIUM),
        ], stakeholder_sentiment=[
            SentimentEntry("A", TODAY, "Bad", SentimentScore.NEGATIVE),
        ]))
        r = compute_rag(p, p.latest, TODAY)
        # schedule=0, budget=0, blockers=0, sentiment=1
        # weighted = (0*0.35 + 0*0.25 + 0*0.25 + 1*0.15) / 1.0 = 0.15 => Green
        assert r.status == RAG.GREEN
        assert r.weighted_score == 0.15

    def test_facts_include_overdue_names(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31), milestones=[
            Milestone("OverdueM", date(2026, 5, 1), MilestoneStatus.IN_PROGRESS),
        ])
        p.add_snapshot(ProgressSnapshot(TODAY))
        r = compute_rag(p, p.latest, TODAY)
        assert "OverdueM" in r.facts["overdue_milestones"]

    def test_facts_open_blocker_count(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        p.add_snapshot(ProgressSnapshot(TODAY, blockers=[
            Blocker("b1", date(2026, 7, 1), BlockerSeverity.HIGH),
        ]))
        r = compute_rag(p, p.latest, TODAY)
        assert r.facts["open_blocker_count"] == 1

    def test_all_signals_present_weights_sum_to_one(self):
        """Verify that when all 4 signals present, the weights sum correctly."""
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        p.add_snapshot(ProgressSnapshot(TODAY, budget_spent=50_000, percent_complete=50.0, blockers=[],
                                         stakeholder_sentiment=[SentimentEntry("A", TODAY, "Good", SentimentScore.POSITIVE)]))
        r = compute_rag(p, p.latest, TODAY)
        assert abs(sum(WEIGHTS.values()) - 1.0) < 0.001
        assert r.weighted_score is not None

    def test_rag_boundary_green_amber(self):
        """weighted_score < 0.66 is Green, >= 0.66 is Amber."""
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        # force schedule=1 (weight 0.35), budget=0 (0.25), blockers=0 (0.25), sentiment=0 (0.15)
        # weighted = 0.35 / 1.0 = 0.35 => Green
        p.add_snapshot(ProgressSnapshot(TODAY, budget_spent=50_000, percent_complete=50.0, blockers=[],
                                         stakeholder_sentiment=[SentimentEntry("A", TODAY, "Good", SentimentScore.POSITIVE)],
                                         ))
        p2 = Project("P2", [], 100_000, date(2026, 1, 1), date(2026, 12, 31), milestones=[
            Milestone("M1", date(2026, 5, 1), MilestoneStatus.IN_PROGRESS),
        ])
        p2.add_snapshot(ProgressSnapshot(TODAY, budget_spent=50_000, percent_complete=50.0, blockers=[],
                                          stakeholder_sentiment=[SentimentEntry("A", TODAY, "Good", SentimentScore.POSITIVE)]))
        r_green = compute_rag(p, p.latest, TODAY)
        r_amber = compute_rag(p2, p2.latest, TODAY)
        assert r_green.status == RAG.GREEN
        assert r_amber.status == RAG.AMBER

    def test_as_of_defaults_to_snapshot_date(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        snap = ProgressSnapshot(TODAY)
        r = compute_rag(p, snap)  # no as_of passed
        assert r.facts["as_of"] == TODAY.isoformat()

    def test_overdue_30_days_score_two(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31), milestones=[
            Milestone("M1", TODAY - timedelta(days=31), MilestoneStatus.IN_PROGRESS),
        ])
        p.add_snapshot(ProgressSnapshot(TODAY))
        r = compute_rag(p, p.latest, TODAY)
        schedule_sig = [s for s in r.signals if s.name == "schedule"][0]
        assert schedule_sig.score == 2


###############################################################################
#  SENTIMENT ANALYSIS (sentiment.py)
###############################################################################

class TestKeywordSentiment:
    def test_negative_keywords_win(self):
        assert _keyword_sentiment("This is a big problem and I'm concerned") == SentimentScore.NEGATIVE

    def test_positive_keywords_win(self):
        assert _keyword_sentiment("I'm pleased and confident we are on track") == SentimentScore.POSITIVE

    def test_neutral_when_tied(self):
        assert _keyword_sentiment("Happy and concerned at the same time") == SentimentScore.NEUTRAL

    def test_empty_string(self):
        assert _keyword_sentiment("") == SentimentScore.NEUTRAL

    def test_no_keywords(self):
        assert _keyword_sentiment("The forecast is for tomorrow") == SentimentScore.NEUTRAL

    def test_neg_keyword_list(self):
        for word in ["frustrated", "concerned", "worried", "delay", "behind",
                      "issue", "problem", "poor", "failed", "risk", "blocked"]:
            assert _keyword_sentiment(f"This is a {word} situation") == SentimentScore.NEGATIVE, f"Failed for '{word}'"

    def test_pos_keyword_list(self):
        for word in ["pleased", "happy", "confident", "great", "smooth", "ahead",
                      "impressed", "satisfied", "strong", "positive"]:
            assert _keyword_sentiment(f"I am {word} with progress") == SentimentScore.POSITIVE, f"Failed for '{word}'"

    def test_case_insensitive(self):
        assert _keyword_sentiment("CONCERNED about the DELAY") == SentimentScore.NEGATIVE

    def test_substring_not_matched(self):
        """'delayed' should match because 'delay' is a substring."""
        assert _keyword_sentiment("delayed delivery") == SentimentScore.NEGATIVE


class TestAnalyzeSentiment:
    def test_empty_text_unknown(self):
        assert analyze_sentiment("", mode="keyword") == SentimentScore.UNKNOWN
        assert analyze_sentiment(None, mode="keyword") == SentimentScore.UNKNOWN
        assert analyze_sentiment("   ", mode="keyword") == SentimentScore.UNKNOWN

    def test_keyword_mode_only(self):
        assert analyze_sentiment("I am happy", mode="keyword") == SentimentScore.POSITIVE

    def test_auto_mode_falls_through_to_keyword(self):
        """With ask_llm mocked to return None, 'auto' falls through to keyword."""
        assert analyze_sentiment("I am frustrated", mode="auto") == SentimentScore.NEGATIVE

    def test_llm_mode_falls_through_to_keyword(self):
        """With ask_llm mocked to return None, 'llm' falls through to keyword."""
        assert analyze_sentiment("I am happy", mode="llm") == SentimentScore.POSITIVE


class TestMakeSentimentEntry:
    def test_creates_entry_with_keyword(self):
        e = make_sentiment_entry("PM", "Great progress", TODAY, mode="keyword")
        assert e.source == "PM"
        assert e.score == SentimentScore.POSITIVE
        assert e.date_recorded == TODAY
        assert e.notes is None

    def test_creates_entry_with_notes(self):
        e = make_sentiment_entry("PM", "This is a problem", TODAY, mode="keyword", notes="needs follow-up")
        assert e.notes == "needs follow-up"
        assert e.score == SentimentScore.NEGATIVE


###############################################################################
#  REPORTS (reports.py)
###############################################################################

class TestSignalSummary:
    def test_basic_summary(self):
        p = Project("P", [], 100_000, TODAY, TODAY)
        p.add_snapshot(ProgressSnapshot(TODAY))
        r = compute_rag(p, p.latest, TODAY)
        out = _signal_summary(p, r)
        assert "Project: P" in out
        assert "RAG:" in out
        assert "schedule" in out
        assert "budget" in out
        assert "blockers" in out
        assert "sentiment" in out

    def test_summary_includes_overrides(self):
        p = Project("P", [], 100_000, TODAY, TODAY)
        p.add_snapshot(ProgressSnapshot(TODAY, status=ProjectStatus.ON_HOLD))
        r = compute_rag(p, p.latest, TODAY)
        out = _signal_summary(p, r)
        assert "Override" in out or "Forced Red" in out

    def test_summary_includes_open_blockers(self):
        p = Project("P", [], 100_000, TODAY, TODAY)
        p.add_snapshot(ProgressSnapshot(TODAY, blockers=[
            Blocker("b1", TODAY, BlockerSeverity.HIGH),
        ]))
        r = compute_rag(p, p.latest, TODAY)
        out = _signal_summary(p, r)
        assert "open blocker(s)" in out
        assert "b1" in out

    def test_summary_includes_negative_stakeholders(self):
        p = Project("P", [], 100_000, TODAY, TODAY)
        p.add_snapshot(ProgressSnapshot(TODAY, stakeholder_sentiment=[
            SentimentEntry("Client", TODAY, "Bad", SentimentScore.NEGATIVE),
        ]))
        r = compute_rag(p, p.latest, TODAY)
        out = _signal_summary(p, r)
        assert "stakeholder(s) negative" in out


class TestWeeklyNarrative:
    def test_fallback_red(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        p.add_snapshot(ProgressSnapshot(TODAY, budget_spent=90_000, percent_complete=10.0, blockers=[
            Blocker("c", TODAY, BlockerSeverity.CRITICAL),
        ], stakeholder_sentiment=[
            SentimentEntry("A", TODAY, "Bad", SentimentScore.NEGATIVE),
            SentimentEntry("B", TODAY, "Bad", SentimentScore.NEGATIVE),
        ]))
        r = compute_rag(p, p.latest, TODAY)
        narrative = weekly_narrative(p, r)
        assert "[Red] P" in narrative
        assert "open blocker(s)" in narrative

    def test_fallback_green(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        p.add_snapshot(ProgressSnapshot(TODAY, budget_spent=30_000, percent_complete=60.0, blockers=[],
                                         stakeholder_sentiment=[SentimentEntry("A", TODAY, "Great", SentimentScore.POSITIVE)]))
        r = compute_rag(p, p.latest, TODAY)
        narrative = weekly_narrative(p, r)
        assert "[Green] P" in narrative

    def test_fallback_amber_with_issues(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31), milestones=[
            Milestone("M1", date(2026, 5, 1), MilestoneStatus.IN_PROGRESS),
        ])
        p.add_snapshot(ProgressSnapshot(TODAY, budget_spent=60_000, percent_complete=40.0, blockers=[
            Blocker("m", TODAY, BlockerSeverity.MEDIUM),
        ], stakeholder_sentiment=[
            SentimentEntry("A", TODAY, "Bad", SentimentScore.NEGATIVE),
        ]))
        r = compute_rag(p, p.latest, TODAY)
        narrative = weekly_narrative(p, r)
        assert "[Amber] P" in narrative
        assert "open blocker(s)" in narrative

    def test_fallback_mentions_overrides(self):
        p = Project("P", [], 100_000, TODAY, TODAY)
        p.add_snapshot(ProgressSnapshot(TODAY, status=ProjectStatus.CANCELLED))
        r = compute_rag(p, p.latest, TODAY)
        narrative = weekly_narrative(p, r)
        assert "overrides" in narrative or "Override" in narrative or "Red" in narrative


class TestMonthlyContent:
    def test_fallback_with_mixed_statuses(self):
        results = []
        for name, budget, start, end, score_pct in [
            ("GreenP", 100_000, date(2026, 1, 1), date(2026, 12, 31), 60.0),
            ("AmberP", 100_000, date(2026, 1, 1), date(2026, 12, 31), 45.0),
            ("RedP", 100_000, date(2026, 1, 1), date(2026, 12, 31), 10.0),
        ]:
            p = Project(name, [], budget, start, end, milestones=[
                Milestone("M1", date(2026, 5, 1), MilestoneStatus.IN_PROGRESS if name != "GreenP" else MilestoneStatus.COMPLETE),
            ])
            p.add_snapshot(ProgressSnapshot(TODAY, budget_spent=budget * score_pct / 100, percent_complete=score_pct,
                                             blockers=[] if name == "GreenP" else [Blocker("b", TODAY, BlockerSeverity.HIGH)],
                                             stakeholder_sentiment=[SentimentEntry("A", TODAY, "Bad" if name == "RedP" else "Good", SentimentScore.NEGATIVE if name == "RedP" else SentimentScore.POSITIVE)]))
            results.append((p, compute_rag(p, p.latest, TODAY)))
        content = monthly_content(results, TODAY)
        assert "executive_summary" in content
        assert "trends" in content
        assert "risks" in content
        assert "recommendations" in content
        assert "GreenP" in content["executive_summary"] or "Green" in content["executive_summary"]

    def test_fallback_red_recommends_steering_committee(self):
        p = Project("RedOnly", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        p.add_snapshot(ProgressSnapshot(TODAY, budget_spent=100_000, percent_complete=10.0, blockers=[
            Blocker("c", TODAY, BlockerSeverity.CRITICAL),
        ], stakeholder_sentiment=[SentimentEntry("A", TODAY, "Bad", SentimentScore.NEGATIVE)]))
        results = [(p, compute_rag(p, p.latest, TODAY))]
        content = monthly_content(results, TODAY)
        assert "executive_summary" in content
        assert "recommendations" in content

    def test_fallback_all_green_no_risks(self):
        p = Project("GoodP", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        p.add_snapshot(ProgressSnapshot(TODAY, budget_spent=30_000, percent_complete=60.0, blockers=[],
                                         stakeholder_sentiment=[SentimentEntry("A", TODAY, "Great", SentimentScore.POSITIVE)]))
        results = [(p, compute_rag(p, p.latest, TODAY))]
        content = monthly_content(results, TODAY)
        assert "executive_summary" in content
        assert isinstance(content.get("risks"), list)

    def test_fallback_recommendations_structure(self):
        projects = []
        p_g = Project("GreenP", [], 100_000, date(2026, 1, 1), date(2026, 12, 31))
        snap_g = ProgressSnapshot(TODAY, budget_spent=30_000, percent_complete=60.0, blockers=[],
                                   stakeholder_sentiment=[SentimentEntry("X", TODAY, "Great", SentimentScore.POSITIVE)])
        projects.append((p_g, compute_rag(p_g, snap_g, TODAY)))

        p_a = Project("AmberP", [], 100_000, date(2026, 1, 1), date(2026, 12, 31), milestones=[
            Milestone("M1", TODAY - timedelta(days=35), MilestoneStatus.IN_PROGRESS),
        ])
        snap_a = ProgressSnapshot(TODAY, budget_spent=60_000, percent_complete=40.0, blockers=[],
                                   stakeholder_sentiment=[SentimentEntry("X", TODAY, "Concerned", SentimentScore.NEGATIVE)])
        projects.append((p_a, compute_rag(p_a, snap_a, TODAY)))

        p_r = Project("RedP", [], 100_000, date(2026, 1, 1), date(2026, 12, 31), milestones=[
            Milestone("M1", TODAY - timedelta(days=40), MilestoneStatus.IN_PROGRESS),
            Milestone("M2", TODAY - timedelta(days=10), MilestoneStatus.NOT_STARTED),
        ])
        snap_r = ProgressSnapshot(TODAY, budget_spent=80_000, percent_complete=15.0, blockers=[
            Blocker("critical", TODAY, BlockerSeverity.CRITICAL),
        ], stakeholder_sentiment=[
            SentimentEntry("X", TODAY, "This is a problem", SentimentScore.NEGATIVE),
            SentimentEntry("Y", TODAY, "Very concerned", SentimentScore.NEGATIVE),
        ])
        projects.append((p_r, compute_rag(p_r, snap_r, TODAY)))

        content = monthly_content(projects, TODAY)
        assert "executive_summary" in content
        assert isinstance(content.get("recommendations"), list)


###############################################################################
#  DATABASE (database.py)
###############################################################################

class TestDatabaseCRUD:
    db_path = None

    @classmethod
    def setup_class(cls):
        cls.db_dir = tempfile.mkdtemp()
        cls.orig_path = os.environ.get("ZYCLUS_DB")
        cls.db_path = os.path.join(cls.db_dir, "test.db")
        os.environ["ZYCLUS_DB"] = cls.db_path
        init()

    @classmethod
    def teardown_class(cls):
        if cls.orig_path:
            os.environ["ZYCLUS_DB"] = cls.orig_path
        else:
            os.environ.pop("ZYCLUS_DB", None)
        shutil.rmtree(cls.db_dir, ignore_errors=True)

    def setup_method(self):
        # Clear all data between tests
        with conn() as db:
            for table in ["sentiment_entries", "blockers", "snapshots", "milestones", "reports", "projects"]:
                db.execute(f"DELETE FROM {table}")

    def _make_project(self, name="TestProj") -> int:
        return upsert_project({
            "name": name,
            "stakeholders": ["PM", "Eng"],
            "budget": 100_000,
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
        })

    def test_create_project(self):
        pid = self._make_project()
        assert pid > 0
        p = get_project(pid)
        assert p is not None
        assert p["name"] == "TestProj"

    def test_get_project_nonexistent(self):
        assert get_project(99999) is None

    def test_update_project(self):
        pid = self._make_project("Original")
        upsert_project({"id": pid, "name": "Updated", "stakeholders": ["PM"], "budget": 200_000,
                        "start_date": "2026-01-01", "end_date": "2026-12-31"})
        p = get_project(pid)
        assert p["name"] == "Updated"
        assert p["budget"] == 200_000

    def test_delete_project_cascades(self):
        pid = self._make_project()
        mid = upsert_milestone({"project_id": pid, "name": "M1", "due_date": "2026-06-01"})
        sid = upsert_snapshot({"project_id": pid, "snapshot_date": "2026-06-01"})
        bid = upsert_blocker({"snapshot_id": sid, "description": "b", "date_raised": "2026-06-01", "severity": "high"})
        eid = upsert_sentiment({"snapshot_id": sid, "source": "PM", "date_recorded": "2026-06-01", "comment": "ok"})
        delete_project(pid)
        assert get_project(pid) is None

    def test_create_milestone(self):
        pid = self._make_project()
        mid = upsert_milestone({"project_id": pid, "name": "M1", "due_date": "2026-06-01",
                                "status": "in_progress", "actual_completion_date": None})
        assert mid > 0

    def test_update_milestone(self):
        pid = self._make_project()
        mid = upsert_milestone({"project_id": pid, "name": "M1", "due_date": "2026-06-01"})
        upsert_milestone({"id": mid, "project_id": pid, "name": "M1-Updated", "due_date": "2026-06-01",
                          "status": "completed", "actual_completion_date": "2026-05-30"})
        # Re-read project to verify
        proj = get_project(pid)
        ms = [m for m in proj["milestones"] if m["id"] == mid]
        assert len(ms) == 1
        assert ms[0]["status"] == "completed"

    def test_delete_milestone(self):
        pid = self._make_project()
        mid = upsert_milestone({"project_id": pid, "name": "M1", "due_date": "2026-06-01"})
        delete_milestone(mid)
        proj = get_project(pid)
        assert len(proj["milestones"]) == 0

    def test_create_snapshot_with_all_fields(self):
        pid = self._make_project()
        sid = upsert_snapshot({"project_id": pid, "snapshot_date": "2026-06-01", "status": "in_progress",
                               "budget_spent": 50_000, "percent_complete": 50.0, "notes": "on track"})
        assert sid > 0
        proj = get_project(pid)
        snaps = [s for s in proj["snapshots"] if s["id"] == sid]
        assert len(snaps) == 1
        assert snaps[0]["budget_spent"] == 50_000

    def test_delete_snapshot(self):
        pid = self._make_project()
        sid = upsert_snapshot({"project_id": pid, "snapshot_date": "2026-06-01"})
        delete_snapshot(sid)
        proj = get_project(pid)
        assert len(proj["snapshots"]) == 0

    def test_create_blocker(self):
        pid = self._make_project()
        sid = upsert_snapshot({"project_id": pid, "snapshot_date": "2026-06-01"})
        bid = upsert_blocker({"snapshot_id": sid, "description": "Critical bug", "date_raised": "2026-06-01",
                              "severity": "critical", "resolved": False})
        assert bid > 0

    def test_update_blocker_resolve(self):
        pid = self._make_project()
        sid = upsert_snapshot({"project_id": pid, "snapshot_date": "2026-06-01"})
        bid = upsert_blocker({"snapshot_id": sid, "description": "Bug", "date_raised": "2026-06-01", "severity": "high"})
        upsert_blocker({"id": bid, "snapshot_id": sid, "description": "Bug", "date_raised": "2026-06-01",
                        "severity": "high", "resolved": True, "date_resolved": "2026-06-15"})
        proj = get_project(pid)
        blockers = [b for s in proj["snapshots"] for b in s["blockers"] if b["id"] == bid]
        assert len(blockers) == 1
        assert blockers[0]["resolved"] == 1

    def test_delete_blocker(self):
        pid = self._make_project()
        sid = upsert_snapshot({"project_id": pid, "snapshot_date": "2026-06-01"})
        bid = upsert_blocker({"snapshot_id": sid, "description": "Bug", "date_raised": "2026-06-01", "severity": "high"})
        delete_blocker(bid)
        proj = get_project(pid)
        all_blockers = [b for s in proj["snapshots"] for b in s["blockers"]]
        assert len(all_blockers) == 0

    def test_create_sentiment_entry(self):
        pid = self._make_project()
        sid = upsert_snapshot({"project_id": pid, "snapshot_date": "2026-06-01"})
        eid = upsert_sentiment({"snapshot_id": sid, "source": "Client", "date_recorded": "2026-06-01",
                                "comment": "Very happy", "score": "positive"})
        assert eid > 0

    def test_delete_sentiment(self):
        pid = self._make_project()
        sid = upsert_snapshot({"project_id": pid, "snapshot_date": "2026-06-01"})
        eid = upsert_sentiment({"snapshot_id": sid, "source": "Client", "date_recorded": "2026-06-01",
                                "comment": "ok", "score": "neutral"})
        delete_sentiment(eid)
        proj = get_project(pid)
        all_sentiment = [e for s in proj["snapshots"] for e in s["sentiment"]]
        assert len(all_sentiment) == 0

    def test_list_projects_empty(self):
        assert list_projects() == []

    def test_list_projects_after_creation(self):
        self._make_project("A")
        self._make_project("B")
        assert len(list_projects()) == 2

    def test_save_and_list_reports(self):
        save_report("weekly", "2026-07-11", "/tmp/test.txt", "summary text")
        save_report("monthly", "2026-07-11", "/tmp/test.pptx")
        reports = list_reports()
        assert len(reports) == 2
        assert reports[0]["type"] in ("weekly", "monthly")

    def test_list_reports_filtered(self):
        save_report("weekly", "2026-07-04")
        save_report("weekly", "2026-07-11")
        save_report("monthly", "2026-07-11")
        weekly = list_reports("weekly")
        monthly = list_reports("monthly")
        assert len(weekly) == 2
        assert len(monthly) == 1

    def test_get_report(self):
        rid = save_report("weekly", "2026-07-11", "/tmp/test.txt")
        r = get_report(rid)
        assert r is not None
        assert r["type"] == "weekly"
        assert get_report(99999) is None

    def test_delete_report(self):
        rid = save_report("weekly", "2026-07-11")
        delete_report(rid)
        assert get_report(rid) is None


class TestProjectToDomain:
    def test_basic_conversion(self):
        data = {
            "id": 1,
            "name": "Test",
            "stakeholders": ["PM", "Eng"],
            "budget": 100_000,
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "milestones": [],
            "snapshots": [],
        }
        p = project_to_domain(data)
        assert p.name == "Test"
        assert p.budget == 100_000
        assert p.latest is None

    def test_with_milestones_and_snapshot(self):
        data = {
            "name": "Test",
            "stakeholders": ["PM"],
            "budget": 100_000,
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "milestones": [
                {"name": "M1", "due_date": "2026-06-01", "status": "in_progress", "actual_completion_date": None},
            ],
            "snapshots": [
                {"id": 1, "snapshot_date": "2026-06-15", "status": "in_progress", "budget_spent": 50_000,
                 "percent_complete": 50.0, "notes": None, "blockers": [], "sentiment": []},
            ],
        }
        p = project_to_domain(data)
        assert len(p.milestones) == 1
        assert p.latest is not None
        assert p.latest.budget_spent == 50_000

    def test_with_blockers_and_sentiment(self):
        data = {
            "name": "Test",
            "stakeholders": ["PM"],
            "budget": 100_000,
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "milestones": [],
            "snapshots": [{
                "snapshot_date": "2026-06-15",
                "status": "in_progress",
                "budget_spent": 50_000,
                "percent_complete": 50.0,
                "notes": "ok",
                "blockers": [
                    {"description": "Bug", "date_raised": "2026-06-01", "severity": "high",
                     "resolved": 0, "date_resolved": None},
                ],
                "sentiment": [
                    {"source": "PM", "date_recorded": "2026-06-15", "comment": "Good", "score": "positive"},
                ],
            }],
        }
        p = project_to_domain(data)
        assert len(p.latest.blockers) == 1
        assert p.latest.blockers[0].severity.value == "high"
        assert not p.latest.blockers[0].resolved
        assert len(p.latest.stakeholder_sentiment) == 1
        assert p.latest.stakeholder_sentiment[0].score == SentimentScore.POSITIVE

    def test_invalid_enum_falls_back_safely(self):
        data = {
            "name": "Test",
            "stakeholders": [],
            "budget": 0,
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "milestones": [{"name": "M1", "due_date": "2026-06-01", "status": "garbage_status", "actual_completion_date": None}],
            "snapshots": [{
                "snapshot_date": "2026-06-15",
                "status": "garbage_status",
                "budget_spent": None,
                "percent_complete": None,
                "notes": None,
                "blockers": [{"description": "b", "date_raised": "2026-06-01", "severity": "garbage_severity",
                              "resolved": 0, "date_resolved": None}],
                "sentiment": [{"source": "PM", "date_recorded": "2026-06-15", "comment": "?", "score": "garbage_score"}],
            }],
        }
        p = project_to_domain(data)
        assert p.milestones[0].status.value == "not_started"  # fallback
        assert p.latest.status.value == "in_progress"  # fallback
        assert p.latest.blockers[0].severity.value == "medium"  # fallback
        assert p.latest.stakeholder_sentiment[0].score == SentimentScore.UNKNOWN  # fallback

    def test_float_resolved_to_bool(self):
        data = {
            "name": "Test",
            "stakeholders": [],
            "budget": 0,
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "milestones": [],
            "snapshots": [{
                "snapshot_date": "2026-06-15",
                "status": "in_progress",
                "budget_spent": None,
                "percent_complete": None,
                "notes": None,
                "blockers": [{"description": "b", "date_raised": "2026-06-01", "severity": "high",
                              "resolved": 1, "date_resolved": "2026-06-10"}],
                "sentiment": [],
            }],
        }
        p = project_to_domain(data)
        assert p.latest.blockers[0].resolved is True
        assert p.latest.blockers[0].date_resolved == date(2026, 6, 10)


###############################################################################
#  MAIN (main.py)
###############################################################################

class TestMainFunctions:
    def test_enrich_sentiment_without_snapshot(self):
        from main import _enrich_sentiment
        p = Project("P", [], 100_000, TODAY, TODAY)
        _enrich_sentiment(p)  # should not crash
        assert p.latest is None

    def test_enrich_sentiment_with_snapshot(self):
        from main import _enrich_sentiment
        p = Project("P", [], 100_000, TODAY, TODAY)
        p.add_snapshot(ProgressSnapshot(TODAY, stakeholder_sentiment=[
            SentimentEntry("PM", TODAY, "Great progress"),
            SentimentEntry("Client", TODAY, "I'm concerned about delays"),
        ]))
        _enrich_sentiment(p)
        assert p.latest.stakeholder_sentiment[0].score is not None

    def test_run_weekly_skips_no_snapshot(self, capsys):
        from main import run_weekly
        projects = [
            Project("WithSnap", [], 100_000, TODAY, TODAY),
            Project("NoSnap", [], 100_000, TODAY, TODAY),
        ]
        projects[0].add_snapshot(ProgressSnapshot(TODAY, budget_spent=50_000, percent_complete=50.0))
        results = run_weekly(projects, TODAY)
        assert len(results) == 1
        assert results[0][0].name == "WithSnap"
        captured = capsys.readouterr()
        assert "NoSnap" in captured.out or "[SKIP]" in captured.out


###############################################################################
#  SCHEDULE (schedule.py)
###############################################################################

class TestSchedule:
    @classmethod
    def setup_class(cls):
        cls.db_dir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.db_dir, "test.db")
        os.environ["ZYCLUS_DB"] = cls.db_path
        init()
        from seed import seed
        seed()

    @classmethod
    def teardown_class(cls):
        os.environ.pop("ZYCLUS_DB", None)
        shutil.rmtree(cls.db_dir, ignore_errors=True)

    def test_run_weekly_without_llm(self):
        from schedule import _run_weekly
        _run_weekly()
        reports = list_reports()
        assert len(reports) > 0

    def test_run_monthly_without_llm(self):
        from schedule import _run_monthly
        _run_monthly()
        reports = list_reports()
        monthly = [r for r in reports if r["type"] == "monthly"]
        assert len(monthly) > 0


###############################################################################
#  INTEGRATION: DB → Domain → RAG → Report Pipeline
###############################################################################

class TestFullPipeline:
    """End-to-end test of the data pipeline: DB → domain → RAG → report,
    without any LLM calls. All fallback paths are exercised."""

    @classmethod
    def setup_class(cls):
        cls.db_dir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.db_dir, "test.db")
        cls.orig_path = os.environ.get("ZYCLUS_DB")
        os.environ["ZYCLUS_DB"] = cls.db_path
        init()
        with conn() as db:
            for table in ["sentiment_entries", "blockers", "snapshots", "milestones", "reports", "projects"]:
                db.execute(f"DELETE FROM {table}")
        from seed import seed
        seed()

    @classmethod
    def teardown_class(cls):
        if cls.orig_path:
            os.environ["ZYCLUS_DB"] = cls.orig_path
        else:
            os.environ.pop("ZYCLUS_DB", None)
        shutil.rmtree(cls.db_dir, ignore_errors=True)

    def setup_method(self):
        pass

    def test_seed_produce_valid_rag(self):
        from data import sample_projects
        projects = sample_projects()
        for proj in projects:
            assert proj.latest is not None, f"{proj.name} has no snapshot"
            snap = proj.latest
            for e in snap.stakeholder_sentiment:
                e.score = analyze_sentiment(e.comment, mode="keyword")
            r = compute_rag(proj, snap, TODAY)
            assert r.status in ("Green", "Amber", "Red")
            assert len(r.signals) == 4
            assert r.facts["project_name"] == proj.name

    def test_seed_projects_all_have_expected_rag(self):
        from data import sample_projects
        projects = sample_projects()
        for proj in projects:
            snap = proj.latest
            for e in snap.stakeholder_sentiment:
                e.score = analyze_sentiment(e.comment, mode="keyword")
            r = compute_rag(proj, snap, TODAY)
            if proj.name == "Project Alpha":
                assert r.status == "Amber"  # schedule variance -26.6 drives to amber regardless of sentiment
            elif proj.name == "Project Beta":
                assert r.status == "Red"
            elif proj.name == "Project Gamma":
                assert r.status == "Red"
            elif proj.name == "Project Delta":
                assert r.status == "Green"

    def test_signal_summary_for_each_seed(self):
        from data import sample_projects
        from reports import _signal_summary
        projects = sample_projects()
        for proj in projects:
            snap = proj.latest
            for e in snap.stakeholder_sentiment:
                e.score = analyze_sentiment(e.comment, mode="keyword")
            r = compute_rag(proj, snap, TODAY)
            summary = _signal_summary(proj, r)
            assert proj.name in summary
            assert r.status in summary

    def test_weekly_narrative_for_each_seed(self):
        from data import sample_projects
        projects = sample_projects()
        for proj in projects:
            snap = proj.latest
            for e in snap.stakeholder_sentiment:
                e.score = analyze_sentiment(e.comment, mode="keyword")
            r = compute_rag(proj, snap, TODAY)
            narrative = weekly_narrative(proj, r)
            assert narrative
            assert proj.name in narrative
            assert r.status in narrative

    def test_monthly_content_from_seeds(self):
        from data import sample_projects
        projects = sample_projects()
        results = []
        for proj in projects:
            snap = proj.latest
            for e in snap.stakeholder_sentiment:
                e.score = analyze_sentiment(e.comment, mode="keyword")
            results.append((proj, compute_rag(proj, snap, TODAY)))
        content = monthly_content(results, TODAY)
        assert len(content["trends"]) == 4
        assert content["executive_summary"]
        assert content["recommendations"]
        assert content["risks"]
        for p, r in results:
            assert any(p.name in t for t in content["trends"]), f"{p.name} missing from trends"

    def test_pptx_generation_from_seeds(self):
        from main import synthesize_monthly
        from data import sample_projects
        import tempfile
        projects = sample_projects()
        results = []
        for proj in projects:
            snap = proj.latest
            for e in snap.stakeholder_sentiment:
                e.score = analyze_sentiment(e.comment, mode="keyword")
            results.append((proj, compute_rag(proj, snap, TODAY)))
        path = synthesize_monthly(results, TODAY, os.path.join(tempfile.gettempdir(), "test_monthly.pptx"))
        assert os.path.exists(path)
        from pptx import Presentation
        prs = Presentation(path)
        # Title + Executive Summary + per-project slides + Trends + Recommendations
        assert len(prs.slides) >= 7  # at least 1+1+4+1+1
        os.remove(path)

    def test_run_weekly_generates_txt(self):
        from main import run_weekly
        from data import sample_projects
        projects = sample_projects()
        for proj in projects:
            snap = proj.latest
            for e in snap.stakeholder_sentiment:
                e.score = analyze_sentiment(e.comment, mode="keyword")
        results = run_weekly(projects, TODAY)
        assert len(results) == 4

    def test_schedule_weekly_from_seeds(self):
        from schedule import _run_weekly
        before = len(list_reports())
        _run_weekly()
        after = len(list_reports())
        assert after >= before + 1

    def test_schedule_monthly_from_seeds(self):
        from schedule import _run_monthly
        before = len(list_reports())
        _run_monthly()
        after = len(list_reports())
        assert after >= before + 1

    def test_weekly_report_files_have_all_projects(self):
        from schedule import _run_weekly
        _run_weekly()
        reports = list_reports("weekly")
        assert len(reports) > 0
        latest = reports[0]
        fp = latest.get("file_path", "")
        if fp and os.path.exists(fp):
            with open(fp, encoding="utf-8") as f:
                content = f.read()
            for name in ["Alpha", "Beta", "Delta", "Gamma"]:
                assert name in content, f"{name} missing from weekly report"

    def test_monthly_pptx_has_per_project_slides(self):
        from schedule import _run_monthly
        _run_monthly()
        reports = list_reports("monthly")
        assert len(reports) > 0
        latest = reports[0]
        fp = latest.get("file_path", "")
        if fp and os.path.exists(fp) and fp.endswith(".pptx"):
            from pptx import Presentation
            prs = Presentation(fp)
            # Monthly: 1 title + 1 exec summary + 4 project slides + 1 trends + 1 recommendations = 8
            assert len(prs.slides) >= 7, f"Expected >=7 slides, got {len(prs.slides)}"


###############################################################################
#  EDGE CASE BONUS: extreme / adversarial inputs
###############################################################################

class TestEdgeCases:
    def test_project_name_empty(self):
        p = Project("", [], 0, TODAY, TODAY)
        assert p.name == ""

    def test_milestone_due_date_in_past(self):
        m = Milestone("Old", date(2000, 1, 1))
        assert m.is_overdue()
        assert m.days_late(TODAY) > 1000

    def test_blocker_raised_today_age_zero(self):
        b = Blocker("fresh", TODAY, BlockerSeverity.LOW)
        assert b.age_days(TODAY) == 0

    def test_snapshot_with_no_blockers_or_sentiment(self):
        p = Project("Minimal", [], 0, TODAY, TODAY)
        p.add_snapshot(ProgressSnapshot(TODAY))
        r = compute_rag(p, p.latest, TODAY)
        assert r.status is not None

    def test_budget_negative_variance(self):
        """Under budget (spent less than expected) should not trigger warnings."""
        p = Project("P", [], 100_000, TODAY, TODAY)
        snap = ProgressSnapshot(TODAY, budget_spent=1000, percent_complete=50)
        r = score_budget(p, snap)
        # 1% spent vs 50% complete = -49 variance => score 0
        assert r.score == 0

    def test_sentiment_mixed_tie_keyword(self):
        """Equal pos and neg keywords = neutral."""
        assert _keyword_sentiment("happy and concerned") == SentimentScore.NEUTRAL

    def test_sentiment_only_punctuation(self):
        assert _keyword_sentiment("!!! ???") == SentimentScore.NEUTRAL

    def test_large_number_of_milestones(self):
        milestones = [Milestone(f"M{i}", date(2026, 1, 1) if i < 50 else date(2027, 1, 1))
                       for i in range(100)]
        p = Project("Big", [], 100_000, date(2026, 1, 1), date(2027, 12, 31), milestones=milestones)
        overdue = p.milestones_overdue(TODAY)
        assert len(overdue) == 50  # first 50 are past due

    def test_zero_budget_no_crash(self):
        p = Project("P", [], 0, TODAY, TODAY)
        p.add_snapshot(ProgressSnapshot(TODAY, budget_spent=0, percent_complete=50))
        r = compute_rag(p, p.latest, TODAY)
        assert r.status is not None
        budget_sig = [s for s in r.signals if s.name == "budget"][0]
        assert budget_sig.score is None  # budget=0 => not reportable

    def test_all_enums_invalid_fallback(self):
        data = {
            "name": "Chaos",
            "stakeholders": [],
            "budget": 0,
            "start_date": "bad-date",  # will crash fromisoformat
            "end_date": "bad-date",
            "milestones": [],
            "snapshots": [],
        }
        import pytest
        with pytest.raises((ValueError, TypeError)):
            project_to_domain(data)

    def test_100_percent_complete_no_overdue(self):
        p = Project("P", [], 100_000, date(2026, 1, 1), date(2026, 12, 31), milestones=[
            Milestone("M1", date(2026, 6, 1), MilestoneStatus.COMPLETE, date(2026, 5, 30)),
        ])
        p.add_snapshot(ProgressSnapshot(TODAY, percent_complete=100.0))
        r = compute_rag(p, p.latest, TODAY)
        assert r.status == RAG.GREEN

    def test_budget_exactly_at_boundary_25(self):
        p = Project("P", [], 100_000, TODAY, TODAY)
        snap = ProgressSnapshot(TODAY, budget_spent=75_000, percent_complete=50)
        r = score_budget(p, snap)
        # 75% spent vs 50% complete = 25 variance. 25 > 25? No. 25 > 10? Yes => score 1
        assert r.score == 1

    def test_days_late_negative_when_future(self):
        m = Milestone("Future", TODAY + timedelta(days=10))
        assert m.days_late(TODAY) == 0

    def test_snapshots_out_of_order(self):
        p = Project("P", [], 100_000, TODAY, TODAY)
        s1 = ProgressSnapshot(TODAY - timedelta(days=20))
        s2 = ProgressSnapshot(TODAY - timedelta(days=10))
        s3 = ProgressSnapshot(TODAY)
        p.add_snapshot(s3)  # add latest first
        p.add_snapshot(s1)  # then earliest
        p.add_snapshot(s2)  # then middle
        assert p.latest is s3  # after sort, latest should still be most recent
        assert len(p.snapshot_history) == 3
        assert p.snapshot_history[0].snapshot_date == s1.snapshot_date


###############################################################################
#  RUNNER
###############################################################################

import pytest

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "--no-header", "-x"])
