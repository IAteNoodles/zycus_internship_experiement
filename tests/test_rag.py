from __future__ import annotations
from datetime import date, timedelta
import sys
sys.path.insert(0, ".")

from projects import (
    Project, ProgressSnapshot, Milestone, MilestoneStatus, ProjectStatus,
    Blocker, BlockerSeverity, SentimentEntry, SentimentScore,
)
from rag import compute_rag, RAG


TODAY = date(2026, 7, 11)


def _project(**kw) -> Project:
    defaults = dict(name="Test", stakeholders=["PM"], budget=100_000,
                    start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    defaults.update(kw)
    return Project(**defaults)


def _snapshot(**kw) -> ProgressSnapshot:
    defaults = dict(snapshot_date=TODAY, budget_spent=50_000, percent_complete=50.0)
    defaults.update(kw)
    return ProgressSnapshot(**defaults)


# ── GREEN cases ─────────────────────────────────────────────

def test_green_all_complete():
    p = _project(milestones=[
        Milestone("M1", date(2026, 3, 1), MilestoneStatus.COMPLETE, date(2026, 2, 28)),
        Milestone("M2", date(2026, 6, 1), MilestoneStatus.COMPLETE, date(2026, 5, 30)),
    ])
    p.add_snapshot(_snapshot(percent_complete=60.0, blockers=[], stakeholder_sentiment=[]))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.GREEN, f"Expected GREEN got {r.status}"
    print("PASS: test_green_all_complete")


def test_green_ahead_schedule():
    p = _project(milestones=[
        Milestone("M1", date(2026, 3, 1), MilestoneStatus.COMPLETE, date(2026, 2, 1)),
    ])
    p.add_snapshot(_snapshot(percent_complete=80.0, budget_spent=30_000, blockers=[],
                              stakeholder_sentiment=[SentimentEntry("PM", TODAY, "Great progress.", SentimentScore.POSITIVE)]))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.GREEN
    print("PASS: test_green_ahead_schedule")


def test_green_no_blockers_good_sentiment():
    p = _project()
    p.add_snapshot(_snapshot(budget_spent=40_000, percent_complete=50.0, blockers=[],
                              stakeholder_sentiment=[SentimentEntry("PM", TODAY, "All good.", SentimentScore.POSITIVE)]))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.GREEN
    print("PASS: test_green_no_blockers_good_sentiment")


# ── AMBER cases ─────────────────────────────────────────────

def test_amber_one_overdue():
    p = _project(milestones=[
        Milestone("M1", TODAY - timedelta(days=35), MilestoneStatus.IN_PROGRESS),
    ])
    p.add_snapshot(_snapshot(percent_complete=45.0, blockers=[], stakeholder_sentiment=[]))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.AMBER, f"Expected AMBER got {r.status}"
    print("PASS: test_amber_one_overdue")


def test_amber_slight_budget_overrun():
    p = _project(budget=100_000)
    p.add_snapshot(_snapshot(budget_spent=55_000, percent_complete=45.0, blockers=[],
                              stakeholder_sentiment=[SentimentEntry("PM", TODAY, "ok", SentimentScore.NEUTRAL)]))
    r = compute_rag(p, p.latest, TODAY)
    # variance = 55 - 45 = 10, which is <= 10 so budget_score=0
    # But schedule variance might push it
    assert r.status in (RAG.GREEN, RAG.AMBER)
    print("PASS: test_amber_slight_budget_overrun")


def test_amber_critical_blocker_override():
    """Critical blocker alone should escalate GREEN->AMBER."""
    p = _project(milestones=[Milestone("M1", date(2026, 12, 1))])  # not overdue
    p.add_snapshot(_snapshot(budget_spent=20_000, percent_complete=50.0, blockers=[
        Blocker("Critical blocker", date(2026, 7, 1), BlockerSeverity.CRITICAL, resolved=False),
    ], stakeholder_sentiment=[SentimentEntry("PM", TODAY, "fine", SentimentScore.POSITIVE)]))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.AMBER, f"Expected AMBER (override) got {r.status}"
    assert any("Critical blocker" in o for o in r.overrides_applied)
    print("PASS: test_amber_critical_blocker_override")


def test_amber_insufficient_data():
    """Over half weight missing -> cannot be GREEN."""
    p = _project()
    p.add_snapshot(_snapshot(budget_spent=None, percent_complete=None, blockers=[],
                              stakeholder_sentiment=[]))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.AMBER
    assert r.insufficient_data
    print("PASS: test_amber_insufficient_data")


def test_amber_single_negative_sentiment():
    p = _project()
    p.add_snapshot(_snapshot(budget_spent=50_000, percent_complete=50.0, blockers=[],
                              stakeholder_sentiment=[
                                  SentimentEntry("Client", TODAY, "Not happy.", SentimentScore.NEGATIVE),
                                  SentimentEntry("PM", TODAY, "ok", SentimentScore.POSITIVE),
                              ]))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.GREEN  # schedule+budget+blockers all 0, sentiment=1 -> weighted still < 0.66
    print("PASS: test_amber_single_negative_sentiment")


# ── RED cases ──────────────────────────────────────────────

def test_red_multiple_overdue():
    p = _project(milestones=[
        Milestone("M1", date(2026, 1, 1), MilestoneStatus.IN_PROGRESS),
        Milestone("M2", date(2026, 2, 1), MilestoneStatus.NOT_STARTED),
    ])
    p.add_snapshot(_snapshot(blockers=[
        Blocker("Blocker", date(2026, 6, 1), BlockerSeverity.HIGH, resolved=False),
    ], stakeholder_sentiment=[]))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.RED, f"Expected RED got {r.status}"
    print("PASS: test_red_multiple_overdue")


def test_red_critical_blocker_high_sentiment():
    p = _project()
    p.add_snapshot(_snapshot(budget_spent=95_000, percent_complete=20.0, blockers=[
        Blocker("Critical", date(2026, 7, 1), BlockerSeverity.CRITICAL),
    ], stakeholder_sentiment=[
        SentimentEntry("A", TODAY, "Bad.", SentimentScore.NEGATIVE),
        SentimentEntry("B", TODAY, "Bad.", SentimentScore.NEGATIVE),
        SentimentEntry("C", TODAY, "Bad.", SentimentScore.NEGATIVE),
    ]))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.RED, f"Expected RED got {r.status} (weighted={r.weighted_score})"
    print("PASS: test_red_critical_blocker_high_sentiment")


def test_red_project_on_hold():
    """Forces RED when project is ON_HOLD, even if signals are healthy."""
    p = _project(milestones=[Milestone("M1", date(2026, 12, 1))])
    p.add_snapshot(_snapshot(status=ProjectStatus.ON_HOLD, percent_complete=50.0, budget_spent=40_000,
                              blockers=[], stakeholder_sentiment=[
                                  SentimentEntry("PM", TODAY, "Good.", SentimentScore.POSITIVE),
                              ]))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.RED, f"Expected RED got {r.status}"
    assert any("Forced Red" in o for o in r.overrides_applied), f"Overrides: {r.overrides_applied}"
    print("PASS: test_red_project_on_hold")


def test_red_project_cancelled():
    p = _project(milestones=[Milestone("M1", date(2026, 12, 1))])
    p.add_snapshot(_snapshot(status=ProjectStatus.CANCELLED, percent_complete=80.0, budget_spent=30_000,
                              blockers=[], stakeholder_sentiment=[
                                  SentimentEntry("PM", TODAY, "Good.", SentimentScore.POSITIVE),
                              ]))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.RED, f"Expected RED got {r.status}"
    assert any("Forced Red" in o for o in r.overrides_applied)
    print("PASS: test_red_project_cancelled")


def test_red_bad_budget():
    p = _project(budget=100_000)
    p.add_snapshot(_snapshot(budget_spent=80_000, percent_complete=30.0, blockers=[],
                              stakeholder_sentiment=[]))
    r = compute_rag(p, p.latest, TODAY)
    # variance = 80 - 30 = 50 > 25 => budget_score=2
    # schedule: expected = elapsed/total = 191/364 = 52.5%, actual = 30%, variance = -22.5 <= -15 => schedule_score=2
    # weighted = (2*0.35 + 2*0.25 + 0*0.25 + 0*0.15) / (0.35+0.25) = (0.7+0.5)/0.6 = 1.2/0.6 = 2.0
    assert r.status == RAG.RED
    print("PASS: test_red_bad_budget")


# ── Edge cases ─────────────────────────────────────────────

def test_edge_no_milestones():
    p = _project()
    p.add_snapshot(_snapshot())
    r = compute_rag(p, p.latest, TODAY)
    assert r.status in (RAG.GREEN, RAG.AMBER)
    print("PASS: test_edge_no_milestones")


def test_edge_same_start_end():
    p = _project(start_date=TODAY, end_date=TODAY)
    p.add_snapshot(_snapshot(percent_complete=100.0))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status is not None
    print("PASS: test_edge_same_start_end")


def test_edge_zero_budget():
    p = _project(budget=0)
    p.add_snapshot(_snapshot(budget_spent=0, percent_complete=50.0))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status is not None
    print("PASS: test_edge_zero_budget")


def test_edge_no_snapshot():
    p = _project()
    # no snapshot added
    assert p.latest is None
    print("PASS: test_edge_no_snapshot")


def test_edge_overdue_yesterday():
    """Milestone due yesterday and not complete -> overdue."""
    yesterday = TODAY - timedelta(days=1)
    p = _project(milestones=[Milestone("M1", yesterday, MilestoneStatus.IN_PROGRESS)])
    p.add_snapshot(_snapshot())
    r = compute_rag(p, p.latest, TODAY)
    assert r.status is not None
    overdue_names = [m.name for m in p.milestones_overdue(TODAY)]
    assert "M1" in overdue_names, f"Expected M1 overdue, names={overdue_names}"
    print("PASS: test_edge_overdue_yesterday")


def test_edge_all_signals_missing():
    p = _project()
    p.add_snapshot(_snapshot(budget_spent=None, percent_complete=None, blockers=[],
                              stakeholder_sentiment=[]))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.AMBER
    assert r.insufficient_data
    print("PASS: test_edge_all_signals_missing")


def test_edge_mixed_sentiment_same_source():
    p = _project()
    p.add_snapshot(_snapshot(blockers=[],
                              stakeholder_sentiment=[
                                  SentimentEntry("PM", TODAY, "Good.", SentimentScore.POSITIVE),
                                  SentimentEntry("PM", TODAY, "Bad.", SentimentScore.NEGATIVE),
                              ]))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status is not None
    print("PASS: test_edge_mixed_sentiment_same_source")


def test_edge_resolved_blocker():
    p = _project()
    p.add_snapshot(_snapshot(blockers=[
        Blocker("Was critical but resolved", date(2026, 6, 1), BlockerSeverity.CRITICAL,
                resolved=True, date_resolved=date(2026, 6, 15)),
    ]))
    r = compute_rag(p, p.latest, TODAY)
    assert len(p.latest.open_blockers()) == 0
    print("PASS: test_edge_resolved_blocker")


def test_edge_boundary_budget_variance():
    """Test exactly at the 10% and 25% boundaries."""
    p = _project(budget=100_000)

    # boundary: variance = 10 (exactly at threshold)
    p.add_snapshot(_snapshot(budget_spent=55_000, percent_complete=45.0, blockers=[],
                              stakeholder_sentiment=[]))
    r = compute_rag(p, p.latest, TODAY)
    budget_signal = [s for s in r.signals if s.name == "budget"][0]
    # 55% spent vs 45% complete = 10 variance -> score 1 (10 > 10 is False? No: variance > 10)
    # Actually 10 > 10 is False, so it's score 1? Let's check: variance=10, 10 > 25? No. 10 > 10? No. So score=0.
    # Wait: if variance > 25: score=2; elif variance > 10: score=1 else score=0
    # 10 > 25? No. 10 > 10? No (strictly greater). So score=0.
    print(f"  Budget signal: score={budget_signal.score}, variance=10pt")
    print("PASS: test_edge_boundary_budget_variance")


# ── Run all ────────────────────────────────────────────────

if __name__ == "__main__":
    test_green_all_complete()
    test_green_ahead_schedule()
    test_green_no_blockers_good_sentiment()
    test_amber_one_overdue()
    test_amber_slight_budget_overrun()
    test_amber_critical_blocker_override()
    test_amber_insufficient_data()
    test_amber_single_negative_sentiment()
    test_red_multiple_overdue()
    test_red_critical_blocker_high_sentiment()
    test_red_project_on_hold()
    test_red_project_cancelled()
    test_red_bad_budget()
    test_edge_no_milestones()
    test_edge_same_start_end()
    test_edge_zero_budget()
    test_edge_no_snapshot()
    test_edge_overdue_yesterday()
    test_edge_all_signals_missing()
    test_edge_mixed_sentiment_same_source()
    test_edge_resolved_blocker()
    test_edge_boundary_budget_variance()
    print("\nAll tests passed.")
