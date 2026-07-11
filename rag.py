from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from projects import (
    Project, ProgressSnapshot, Milestone, MilestoneStatus, ProjectStatus,
    BlockerSeverity, SentimentScore,
)


class RAG:
    GREEN = "Green"
    AMBER = "Amber"
    RED = "Red"


WEIGHTS = {
    "schedule": 0.35,
    "budget": 0.25,
    "blockers": 0.25,
    "sentiment": 0.15,
}

SCORE_TO_RAG = {0: RAG.GREEN, 1: RAG.AMBER, 2: RAG.RED}


@dataclass
class SignalResult:
    name: str
    score: Optional[int]
    rationale: str


@dataclass
class RagResult:
    status: str
    weighted_score: Optional[float]
    signals: list[SignalResult] = field(default_factory=list)
    overrides_applied: list[str] = field(default_factory=list)
    insufficient_data: bool = False
    facts: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Individual signal scorers
# ---------------------------------------------------------------------------

def score_schedule(project: Project, snap: ProgressSnapshot, as_of: date) -> SignalResult:
    overdue = project.milestones_overdue(as_of=as_of)
    at_risk = [m for m in project.milestones if m.status == MilestoneStatus.AT_RISK]
    blocked = [m for m in project.milestones if m.status == MilestoneStatus.BLOCKED]
    days_late = max((m.days_late(as_of) for m in overdue), default=0)

    milestone_score = 0
    if blocked or days_late > 30 or len(overdue) >= 2:
        milestone_score = 2
    elif overdue or at_risk:
        milestone_score = 1

    variance_score = None
    variance = None
    if snap.percent_complete is not None:
        expected = project.schedule_progress_expected(as_of)
        variance = snap.percent_complete - expected
        if variance <= -15:
            variance_score = 2
        elif variance <= -5:
            variance_score = 1
        else:
            variance_score = 0

    if variance_score is None and not project.milestones:
        return SignalResult("schedule", None, "No milestones and no % complete reported.")

    score = max(v for v in [milestone_score, variance_score] if v is not None)
    rationale = (
        f"{len(overdue)} milestone(s) overdue (max {days_late}d late), "
        f"{len(at_risk)} flagged at-risk, {len(blocked)} blocked."
    )
    if variance is not None:
        rationale += f" Progress variance vs. schedule: {variance:+.1f} pts."
    return SignalResult("schedule", score, rationale)


def score_budget(project: Project, snap: ProgressSnapshot) -> SignalResult:
    if snap.budget_spent is None or snap.percent_complete is None or project.budget is None or project.budget <= 0:
        return SignalResult("budget", None, "Budget spent or % complete not reported.")

    burn_pct = 100 * snap.budget_spent / project.budget
    variance = burn_pct - snap.percent_complete
    if variance > 25:
        score = 2
    elif variance > 10:
        score = 1
    else:
        score = 0
    rationale = (
        f"Spent {burn_pct:.0f}% of budget vs. {snap.percent_complete:.0f}% complete "
        f"({variance:+.0f} pt variance)."
    )
    return SignalResult("budget", score, rationale)


def score_blockers(snap: ProgressSnapshot, as_of: date) -> SignalResult:
    open_blockers = snap.open_blockers()
    if not open_blockers:
        return SignalResult("blockers", 0, "No open blockers.")

    critical = [b for b in open_blockers if b.severity == BlockerSeverity.CRITICAL]
    high_aged = [b for b in open_blockers
                 if b.severity == BlockerSeverity.HIGH and b.age_days(as_of) > 14]
    high = [b for b in open_blockers if b.severity == BlockerSeverity.HIGH]
    medium = [b for b in open_blockers if b.severity == BlockerSeverity.MEDIUM]

    if critical or high_aged:
        score = 2
    elif high or len(medium) >= 2:
        score = 1
    else:
        score = 0

    rationale = (
        f"{len(open_blockers)} open blocker(s): "
        f"{len(critical)} critical, {len(high)} high, {len(medium)} medium."
    )
    return SignalResult("blockers", score, rationale)


def score_sentiment(snap: ProgressSnapshot) -> SignalResult:
    entries = [e for e in snap.stakeholder_sentiment if e.score != SentimentScore.UNKNOWN]
    if not entries:
        return SignalResult("sentiment", None, "No sentiment data reported this period.")

    negative_sources = {e.source for e in entries if e.score == SentimentScore.NEGATIVE}
    if len(negative_sources) >= 2:
        score = 2
    elif len(negative_sources) == 1:
        score = 1
    else:
        score = 0

    rationale = f"{len(negative_sources)} of {len(entries)} reporting stakeholder(s) negative."
    return SignalResult("sentiment", score, rationale)


# ---------------------------------------------------------------------------
# Combination
# ---------------------------------------------------------------------------

def compute_rag(project: Project, snap: ProgressSnapshot, as_of: Optional[date] = None) -> RagResult:
    as_of = as_of or snap.snapshot_date

    signals = [
        score_schedule(project, snap, as_of),
        score_budget(project, snap),
        score_blockers(snap, as_of),
        score_sentiment(snap),
    ]

    present = {s.name: s for s in signals if s.score is not None}
    missing_weight = sum(WEIGHTS[n] for n in WEIGHTS if n not in present)
    insufficient_data = missing_weight > 0.5

    weighted_score = None
    if present:
        total_weight = sum(WEIGHTS[n] for n in present)
        weighted_score = sum(WEIGHTS[n] * s.score for n, s in present.items()) / total_weight

    if weighted_score is None:
        status = RAG.AMBER  # no usable data at all — can't claim Green
        insufficient_data = True
    elif weighted_score < 0.66:
        status = RAG.GREEN
    elif weighted_score < 1.33:
        status = RAG.AMBER
    else:
        status = RAG.RED

    overrides = []
    if insufficient_data and status == RAG.GREEN:
        status = RAG.AMBER
        overrides.append("Capped at Amber: over half of signal weight is missing this period.")

    open_blockers = snap.open_blockers()
    if any(b.severity == BlockerSeverity.CRITICAL for b in open_blockers) and status == RAG.GREEN:
        status = RAG.AMBER
        overrides.append("Escalated to Amber: an open Critical blocker is present.")

    if project.latest and project.latest.status in (ProjectStatus.ON_HOLD, ProjectStatus.CANCELLED):
        if status != RAG.RED:
            status = RAG.RED
            overrides.append(f"Forced Red: project status is {project.latest.status.value}.")

    return RagResult(
        status=status,
        weighted_score=weighted_score,
        signals=signals,
        overrides_applied=overrides,
        insufficient_data=insufficient_data,
        facts={
            "project_name": project.name,
            "as_of": as_of.isoformat(),
            "open_blocker_count": len(open_blockers),
            "overdue_milestones": [m.name for m in project.milestones_overdue(as_of)],
        },
    )


if __name__ == "__main__":
    print("RAG")