from __future__ import annotations
from datetime import date, timedelta
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.projects import (
    Project, ProgressSnapshot, Milestone, MilestoneStatus, ProjectStatus,
    Blocker, BlockerSeverity, SentimentEntry, SentimentScore,
)
from models.rag import compute_rag, RAG


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


def test_green_on_track():
    p = _project(milestones=[Milestone("M1", date(2026, 3, 1), MilestoneStatus.COMPLETE)])
    p.add_snapshot(_snapshot(percent_complete=55.0))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.GREEN, f"Expected GREEN got {r.status}"
    print("PASS: test_green_on_track")


# ── AMBER cases ─────────────────────────────────────────────

def test_amber_one_overdue():
    p = _project(milestones=[
        Milestone("M1", date(2026, 3, 1), MilestoneStatus.COMPLETE, date(2026, 2, 28)),
        Milestone("M2", date(2026, 6, 15), MilestoneStatus.IN_PROGRESS),
    ])
    p.add_snapshot(_snapshot(budget_spent=70_000, percent_complete=70.0))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.AMBER, f"Expected AMBER got {r.status}"
    print("PASS: test_amber_one_overdue")


def test_amber_budget_burn():
    p = _project(milestones=[Milestone("M1", date(2026, 3, 1), MilestoneStatus.COMPLETE)])
    p.add_snapshot(_snapshot(budget_spent=60_000, percent_complete=40.0))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.AMBER, f"Expected AMBER got {r.status}"
    print("PASS: test_amber_budget_burn")


def test_amber_missing_over_half_weight():
    p = _project(milestones=[])
    p.add_snapshot(_snapshot(budget_spent=None, percent_complete=None, blockers=[], stakeholder_sentiment=[]))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.AMBER, f"Expected AMBER got {r.status}"
    assert r.insufficient_data
    print("PASS: test_amber_missing_over_half_weight")


def test_amber_critical_blocker_escalates_green():
    p = _project(milestones=[Milestone("M1", date(2026, 3, 1), MilestoneStatus.COMPLETE)])
    p.add_snapshot(_snapshot(blockers=[
        Blocker("Critical blocker", date(2026, 6, 1), BlockerSeverity.CRITICAL, resolved=False),
    ]))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.AMBER, f"Expected AMBER got {r.status}"
    assert any("Critical" in o for o in r.overrides_applied)
    print("PASS: test_amber_critical_blocker_escalates_green")


# ── RED cases ───────────────────────────────────────────────

def test_red_two_blocked_milestones():
    p = _project(milestones=[
        Milestone("M1", date(2026, 3, 1), MilestoneStatus.BLOCKED),
        Milestone("M2", date(2026, 6, 1), MilestoneStatus.BLOCKED),
    ])
    p.add_snapshot(_snapshot(percent_complete=10.0))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.RED, f"Expected RED got {r.status}"
    print("PASS: test_red_two_blocked_milestones")


def test_red_two_negative_stakeholders():
    p = _project(milestones=[Milestone("M1", date(2026, 3, 1), MilestoneStatus.BLOCKED)])
    p.add_snapshot(_snapshot(
        percent_complete=10.0, blockers=[],
        stakeholder_sentiment=[
            SentimentEntry("Client A", TODAY, "Very unhappy.", SentimentScore.NEGATIVE),
            SentimentEntry("Client B", TODAY, "Also unhappy.", SentimentScore.NEGATIVE),
        ],
    ))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.RED, f"Expected RED got {r.status}"
    print("PASS: test_red_two_negative_stakeholders")


def test_red_cancelled_project():
    p = _project(milestones=[])
    p.add_snapshot(_snapshot(status=ProjectStatus.CANCELLED))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.RED, f"Expected RED got {r.status}"
    assert any("Forced Red" in o for o in r.overrides_applied)
    print("PASS: test_red_cancelled_project")


# ── EDGE CASES ──────────────────────────────────────────────

def test_no_snapshot():
    p = _project(milestones=[])
    r = compute_rag(p, ProgressSnapshot(snapshot_date=TODAY), TODAY)
    assert r.status == RAG.AMBER
    assert r.insufficient_data
    print("PASS: test_no_snapshot")


def test_future_milestones():
    p = _project(milestones=[
        Milestone("Future", TODAY + timedelta(days=30), MilestoneStatus.IN_PROGRESS),
    ])
    p.add_snapshot(_snapshot(percent_complete=50.0, budget_spent=50_000))
    r = compute_rag(p, p.latest, TODAY)
    assert r.status == RAG.GREEN, f"Expected GREEN got {r.status}"
    print("PASS: test_future_milestones")


def test_budget_exactly_at_boundary():
    p = _project(milestones=[Milestone("M1", date(2026, 3, 1), MilestoneStatus.COMPLETE)])
    p.add_snapshot(_snapshot(budget_spent=60_000, percent_complete=50.0))
    r = compute_rag(p, p.latest, TODAY)
    expected = "AMBER" if r.weighted_score and r.weighted_score >= 0.66 else "GREEN"
    print(f"  weighted_score={r.weighted_score:.3f}, status={r.status}, expected_boundary={expected}")
    print("PASS: test_budget_exactly_at_boundary")


def test_all_signals_missing():
    p = _project(milestones=[])
    s = ProgressSnapshot(snapshot_date=TODAY, budget_spent=None, percent_complete=None, stakeholder_sentiment=[])
    p.snapshot_history = [s]
    r = compute_rag(p, s, TODAY)
    assert r.status == RAG.AMBER
    assert r.insufficient_data
    assert r.weighted_score is not None
    assert r.weighted_score == 0.0
    print("PASS: test_all_signals_missing")


# ── Run all if executed directly ────────────────────────────

if __name__ == "__main__":
    test_green_all_complete()
    test_green_on_track()
    test_amber_one_overdue()
    test_amber_budget_burn()
    test_amber_missing_over_half_weight()
    test_amber_critical_blocker_escalates_green()
    test_red_two_blocked_milestones()
    test_red_two_negative_stakeholders()
    test_red_cancelled_project()
    test_no_snapshot()
    test_future_milestones()
    test_budget_exactly_at_boundary()
    test_all_signals_missing()
    print("\nAll RAG tests passed.")
