from __future__ import annotations
from datetime import date

from models.projects import (
    Project, ProgressSnapshot, Milestone, MilestoneStatus,
    Blocker, BlockerSeverity, SentimentEntry, SentimentScore,
)


def sample_projects() -> list[Project]:
    today = date(2026, 7, 11)

    alpha = Project(
        name="Project Alpha",
        stakeholders=["Client COO", "PM", "Engineering Lead"],
        budget=500_000,
        start_date=date(2026, 1, 15),
        end_date=date(2026, 9, 30),
        milestones=[
            Milestone("Kickoff", date(2026, 1, 20), MilestoneStatus.COMPLETE, date(2026, 1, 18)),
            Milestone("Requirements Sign-off", date(2026, 3, 1), MilestoneStatus.COMPLETE, date(2026, 2, 28)),
            Milestone("Design Complete", date(2026, 5, 1), MilestoneStatus.COMPLETE, date(2026, 5, 5)),
            Milestone("Core Build", date(2026, 7, 15), MilestoneStatus.IN_PROGRESS),
            Milestone("UAT", date(2026, 8, 30), MilestoneStatus.NOT_STARTED),
            Milestone("Go-Live", date(2026, 9, 30), MilestoneStatus.NOT_STARTED),
        ],
    )
    alpha.add_snapshot(ProgressSnapshot(
        snapshot_date=today,
        budget_spent=210_000,
        percent_complete=42.0,
        blockers=[
            Blocker("Vendor API key delay", date(2026, 7, 1), BlockerSeverity.MEDIUM, resolved=False),
        ],
        stakeholder_sentiment=[
            SentimentEntry("Client COO", today, "Overall satisfied but would like to see more velocity."),
            SentimentEntry("PM", today, "Team is performing well, minor vendor hiccup."),
            SentimentEntry("Engineering Lead", today, "Integration is taking longer than expected."),
        ],
        notes="Solid progress. Vendor delay is minor, being tracked.",
    ))

    beta = Project(
        name="Project Beta",
        stakeholders=["Client VP", "Program Manager", "Tech Lead"],
        budget=750_000,
        start_date=date(2026, 2, 1),
        end_date=date(2026, 11, 30),
        milestones=[
            Milestone("Discovery", date(2026, 3, 1), MilestoneStatus.COMPLETE, date(2026, 3, 5)),
            Milestone("Architecture", date(2026, 5, 1), MilestoneStatus.COMPLETE, date(2026, 5, 20)),
            Milestone("Phase 1 Dev", date(2026, 7, 1), MilestoneStatus.AT_RISK),
            Milestone("Phase 2 Dev", date(2026, 9, 1), MilestoneStatus.NOT_STARTED),
            Milestone("Deployment", date(2026, 11, 30), MilestoneStatus.NOT_STARTED),
        ],
    )
    beta.add_snapshot(ProgressSnapshot(
        snapshot_date=today,
        budget_spent=520_000,
        percent_complete=35.0,
        blockers=[
            Blocker("Key developer on leave", date(2026, 6, 15), BlockerSeverity.HIGH, resolved=False),
            Blocker("Third-party library incompatibility", date(2026, 6, 20), BlockerSeverity.MEDIUM, resolved=False),
        ],
        stakeholder_sentiment=[
            SentimentEntry("Client VP", today, "Concerned about the timeline given the budget spent."),
            SentimentEntry("Program Manager", today, "We're burning through budget faster than planned."),
            SentimentEntry("Tech Lead", today, "The team is working hard but the scope is challenging."),
        ],
        notes="Budget overrun due to scope creep. Two blockers need attention.",
    ))

    gamma = Project(
        name="Project Gamma",
        stakeholders=["Client CIO", "Delivery Director", "Security Lead"],
        budget=1_200_000,
        start_date=date(2026, 3, 1),
        end_date=date(2027, 2, 28),
        milestones=[
            Milestone("Initiation", date(2026, 4, 1), MilestoneStatus.COMPLETE, date(2026, 4, 10)),
            Milestone("Security Review", date(2026, 6, 1), MilestoneStatus.BLOCKED),
            Milestone("Data Migration", date(2026, 8, 1), MilestoneStatus.NOT_STARTED),
            Milestone("Integration", date(2026, 11, 1), MilestoneStatus.NOT_STARTED),
            Milestone("Go-Live", date(2027, 2, 28), MilestoneStatus.NOT_STARTED),
        ],
    )
    gamma.add_snapshot(ProgressSnapshot(
        snapshot_date=today,
        budget_spent=180_000,
        percent_complete=10.0,
        blockers=[
            Blocker("Security compliance audit blocked by external regulator", date(2026, 5, 1), BlockerSeverity.CRITICAL, resolved=False),
            Blocker("Data center migration paused due to vendor negotiations", date(2026, 6, 1), BlockerSeverity.HIGH, resolved=False),
        ],
        stakeholder_sentiment=[
            SentimentEntry("Client CIO", today, "This is not acceptable. We need a recovery plan immediately."),
            SentimentEntry("Delivery Director", today, "I'm escalating this to steering committee."),
            SentimentEntry("Security Lead", today, "Cannot proceed until the external audit clears."),
        ],
        notes="Critical external dependency on regulator. Recovery plan needed urgently.",
    ))

    delta = Project(
        name="Project Delta",
        stakeholders=["Client Director", "Team Lead"],
        budget=300_000,
        start_date=date(2026, 4, 1),
        end_date=date(2026, 10, 31),
        milestones=[
            Milestone("Kickoff", date(2026, 4, 7), MilestoneStatus.COMPLETE, date(2026, 4, 7)),
            Milestone("Spec Complete", date(2026, 5, 15), MilestoneStatus.COMPLETE, date(2026, 5, 10)),
            Milestone("Dev Sprint 1", date(2026, 6, 30), MilestoneStatus.COMPLETE, date(2026, 6, 25)),
            Milestone("Dev Sprint 2", date(2026, 8, 15), MilestoneStatus.IN_PROGRESS),
            Milestone("Handover", date(2026, 10, 31), MilestoneStatus.NOT_STARTED),
        ],
    )
    delta.add_snapshot(ProgressSnapshot(
        snapshot_date=today,
        budget_spent=95_000,
        percent_complete=55.0,
        blockers=[],
        stakeholder_sentiment=[
            SentimentEntry("Client Director", today, "Very happy with the team's pace and communication."),
            SentimentEntry("Team Lead", today, "Everything is on track, team morale is high."),
        ],
        notes="Running ahead of schedule. No blockers.",
    ))

    return [alpha, beta, gamma, delta]
