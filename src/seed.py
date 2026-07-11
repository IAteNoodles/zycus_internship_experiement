"""Seed the database with sample projects for development."""
from __future__ import annotations
import json
from datetime import date

import database
from data import sample_projects as get_sample_projects


def seed():
    database.init()
    existing = database.list_projects()
    if existing:
        print(f"Database already has {len(existing)} projects. Skipping seed.")
        return

    projects = get_sample_projects()
    for p in projects:
        pid = database.upsert_project({
            "name": p.name,
            "stakeholders": p.stakeholders,
            "budget": p.budget,
            "start_date": p.start_date.isoformat(),
            "end_date": p.end_date.isoformat(),
        })
        for m in p.milestones:
            database.upsert_milestone({
                "project_id": pid,
                "name": m.name,
                "due_date": m.due_date.isoformat(),
                "status": m.status.value,
                "actual_completion_date": m.actual_completion_date.isoformat() if m.actual_completion_date else None,
            })
        for snap in p.snapshot_history:
            sid = database.upsert_snapshot({
                "project_id": pid,
                "snapshot_date": snap.snapshot_date.isoformat(),
                "status": snap.status.value,
                "budget_spent": snap.budget_spent,
                "percent_complete": snap.percent_complete,
                "notes": snap.notes,
            })
            for b in snap.blockers:
                database.upsert_blocker({
                    "snapshot_id": sid,
                    "description": b.description,
                    "date_raised": b.date_raised.isoformat(),
                    "severity": b.severity.value,
                    "resolved": b.resolved,
                    "date_resolved": b.date_resolved.isoformat() if b.date_resolved else None,
                })
            for e in snap.stakeholder_sentiment:
                database.upsert_sentiment({
                    "snapshot_id": sid,
                    "source": e.source,
                    "date_recorded": e.date_recorded.isoformat(),
                    "comment": e.comment,
                    "score": e.score.value,
                })
    print(f"Seeded {len(projects)} projects.")


if __name__ == "__main__":
    seed()
