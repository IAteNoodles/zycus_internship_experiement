from __future__ import annotations
from datetime import date
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class MilestoneStatus(Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    AT_RISK = "at_risk"
    BLOCKED = "blocked"


class ProjectStatus(Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    ON_HOLD = "on_hold"
    COMPLETE = "complete"
    CANCELLED = "cancelled"


class BlockerSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SentimentScore(Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    UNKNOWN = "unknown"          


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Milestone:
    name: str
    due_date: date
    status: MilestoneStatus = MilestoneStatus.NOT_STARTED
    actual_completion_date: Optional[date] = None

    def is_overdue(self, as_of: Optional[date] = None) -> bool:
        as_of = as_of or date.today()
        if self.status == MilestoneStatus.COMPLETE:
            return False
        return as_of > self.due_date

    def days_late(self, as_of: date) -> int:
        end = self.actual_completion_date or as_of
        return max(0, (end - self.due_date).days)


@dataclass
class Blocker:
    description: str
    date_raised: date
    severity: BlockerSeverity
    resolved: bool = False
    date_resolved: Optional[date] = None

    def age_days(self, as_of: date) -> int:
        end = self.date_resolved or as_of
        return max(0, (end - self.date_raised).days)


@dataclass
class SentimentEntry:
    source: str                        # which stakeholder
    date_recorded: date
    comment: str                       # raw comment by stakeholder
    score: SentimentScore = SentimentScore.UNKNOWN
    notes: Optional[str] = None        # notes used by us


@dataclass
class ProgressSnapshot:
    snapshot_date: date
    status: ProjectStatus = ProjectStatus.IN_PROGRESS
    budget_spent: Optional[float] = None
    percent_complete: Optional[float] = None
    blockers: list[Blocker] = field(default_factory=list)
    stakeholder_sentiment: list[SentimentEntry] = field(default_factory=list)
    notes: Optional[str] = None        # notes by us

    def open_blockers(self) -> list[Blocker]:
        return [blocker for blocker in self.blockers if not blocker.resolved]


@dataclass
class Project:
    name: str
    stakeholders: list[str]
    budget: float
    start_date: date
    end_date: date
    milestones: list[Milestone] = field(default_factory=list)
    snapshot_history: list[ProgressSnapshot] = field(default_factory=list)

    @property
    def latest(self) -> Optional[ProgressSnapshot]:
        return self.snapshot_history[-1] if self.snapshot_history else None

    def add_snapshot(self, snapshot: ProgressSnapshot) -> None:
        self.snapshot_history.append(snapshot)
        self.snapshot_history.sort(key=lambda s: s.snapshot_date)

    def milestones_overdue(self, as_of: date) -> list[Milestone]:
        return [m for m in self.milestones if m.is_overdue(as_of)]

    def schedule_progress_expected(self, as_of: Optional[date] = None) -> float:
        as_of = as_of or date.today()
        total = (self.end_date - self.start_date).days
        if total <= 0:
            return 100.0
        elapsed = (as_of - self.start_date).days
        return max(0, min(100, 100 * elapsed / total))


if __name__ == "__main__":
    print("Main")