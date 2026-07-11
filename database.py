from __future__ import annotations
import json
import sqlite3
import os
from contextlib import contextmanager
from datetime import date
from typing import Optional, Any

def _db_path():
    return os.getenv("ZYCLUS_DB", os.path.join(os.path.dirname(__file__), "zycus.db"))


@contextmanager
def conn():
    db = None
    try:
        db = sqlite3.connect(_db_path(), timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        yield db
        db.commit()
    except sqlite3.OperationalError:
        if db:
            db.rollback()
        raise
    except sqlite3.Error as e:
        if db:
            db.rollback()
        raise RuntimeError(f"Database error: {e}") from e
    finally:
        if db:
            db.close()


def init():
    with conn() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                stakeholders TEXT NOT NULL DEFAULT '[]',
                budget REAL NOT NULL DEFAULT 0,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (date('now')),
                updated_at TEXT NOT NULL DEFAULT (date('now'))
            );

            CREATE TABLE IF NOT EXISTS milestones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                due_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'not_started',
                actual_completion_date TEXT
            );

            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                snapshot_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'in_progress',
                budget_spent REAL,
                percent_complete REAL,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS blockers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
                description TEXT NOT NULL,
                date_raised TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'medium',
                resolved INTEGER NOT NULL DEFAULT 0,
                date_resolved TEXT
            );

            CREATE TABLE IF NOT EXISTS sentiment_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
                source TEXT NOT NULL,
                date_recorded TEXT NOT NULL,
                comment TEXT NOT NULL,
                score TEXT NOT NULL DEFAULT 'unknown'
            );

            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                report_date TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                file_path TEXT,
                summary TEXT
            );
        """)


# ── Projects ───────────────────────────────────────────────

def list_projects() -> list[dict]:
    with conn() as db:
        rows = db.execute("SELECT * FROM projects ORDER BY name").fetchall()
        return [dict(r) for r in rows]


def get_project(pid: int) -> Optional[dict]:
    with conn() as db:
        r = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
        if not r:
            return None
        proj = dict(r)
        proj["stakeholders"] = json.loads(proj["stakeholders"])
        proj["milestones"] = [dict(r) for r in db.execute(
            "SELECT * FROM milestones WHERE project_id=? ORDER BY due_date", (pid,))]
        proj["snapshots"] = [dict(r) for r in db.execute(
            "SELECT * FROM snapshots WHERE project_id=? ORDER BY snapshot_date DESC", (pid,))]
        for s in proj["snapshots"]:
            s["blockers"] = [dict(r) for r in db.execute(
                "SELECT * FROM blockers WHERE snapshot_id=?", (s["id"],))]
            s["sentiment"] = [dict(r) for r in db.execute(
                "SELECT * FROM sentiment_entries WHERE snapshot_id=?", (s["id"],))]
        return proj


def upsert_project(data: dict) -> int:
    stakeholders = json.dumps(data.get("stakeholders", []), ensure_ascii=False)
    pid = data.get("id")
    with conn() as db:
        if pid:
            db.execute("""UPDATE projects SET name=?,stakeholders=?,budget=?,start_date=?,end_date=?,updated_at=date('now')
                          WHERE id=?""",
                       (data["name"], stakeholders, data["budget"], data["start_date"], data["end_date"], pid))
        else:
            cur = db.execute("""INSERT INTO projects (name,stakeholders,budget,start_date,end_date)
                                VALUES (?,?,?,?,?)""",
                             (data["name"], stakeholders, data["budget"], data["start_date"], data["end_date"]))
            pid = cur.lastrowid
    return pid


def delete_project(pid: int) -> None:
    with conn() as db:
        db.execute("DELETE FROM projects WHERE id=?", (pid,))


# ── Milestones ─────────────────────────────────────────────

def upsert_milestone(data: dict) -> int:
    with conn() as db:
        mid = data.get("id")
        if mid:
            db.execute("""UPDATE milestones SET name=?,due_date=?,status=?,actual_completion_date=?
                          WHERE id=?""",
                       (data["name"], data["due_date"], data["status"], data.get("actual_completion_date"), mid))
        else:
            cur = db.execute("""INSERT INTO milestones (project_id,name,due_date,status,actual_completion_date)
                                VALUES (?,?,?,?,?)""",
                             (data["project_id"], data["name"], data["due_date"],
                              data.get("status", "not_started"), data.get("actual_completion_date")))
            mid = cur.lastrowid
        return mid


def delete_milestone(mid: int) -> None:
    with conn() as db:
        db.execute("DELETE FROM milestones WHERE id=?", (mid,))


# ── Snapshots ──────────────────────────────────────────────

def upsert_snapshot(data: dict) -> int:
    with conn() as db:
        sid = data.get("id")
        if sid:
            db.execute("""UPDATE snapshots SET snapshot_date=?,status=?,budget_spent=?,percent_complete=?,notes=?
                          WHERE id=?""",
                       (data["snapshot_date"], data.get("status", "in_progress"),
                        data.get("budget_spent"), data.get("percent_complete"), data.get("notes"), sid))
        else:
            cur = db.execute("""INSERT INTO snapshots (project_id,snapshot_date,status,budget_spent,percent_complete,notes)
                                VALUES (?,?,?,?,?,?)""",
                             (data["project_id"], data["snapshot_date"], data.get("status", "in_progress"),
                              data.get("budget_spent"), data.get("percent_complete"), data.get("notes")))
            sid = cur.lastrowid
        return sid


def delete_snapshot(sid: int) -> None:
    with conn() as db:
        db.execute("DELETE FROM snapshots WHERE id=?", (sid,))


# ── Blockers ───────────────────────────────────────────────

def upsert_blocker(data: dict) -> int:
    with conn() as db:
        bid = data.get("id")
        if bid:
            db.execute("""UPDATE blockers SET description=?,date_raised=?,severity=?,resolved=?,date_resolved=?
                          WHERE id=?""",
                       (data["description"], data["date_raised"], data["severity"],
                        int(data.get("resolved", False)), data.get("date_resolved"), bid))
        else:
            cur = db.execute("""INSERT INTO blockers (snapshot_id,description,date_raised,severity,resolved,date_resolved)
                                VALUES (?,?,?,?,?,?)""",
                             (data["snapshot_id"], data["description"], data["date_raised"],
                              data["severity"], int(data.get("resolved", False)), data.get("date_resolved")))
            bid = cur.lastrowid
        return bid


def delete_blocker(bid: int) -> None:
    with conn() as db:
        db.execute("DELETE FROM blockers WHERE id=?", (bid,))


# ── Sentiment ──────────────────────────────────────────────

def upsert_sentiment(data: dict) -> int:
    with conn() as db:
        eid = data.get("id")
        if eid:
            db.execute("""UPDATE sentiment_entries SET source=?,date_recorded=?,comment=?,score=?
                          WHERE id=?""",
                       (data["source"], data["date_recorded"], data["comment"], data.get("score", "unknown"), eid))
        else:
            cur = db.execute("""INSERT INTO sentiment_entries (snapshot_id,source,date_recorded,comment,score)
                                VALUES (?,?,?,?,?)""",
                             (data["snapshot_id"], data["source"], data["date_recorded"],
                              data["comment"], data.get("score", "unknown")))
            eid = cur.lastrowid
        return eid


def delete_sentiment(eid: int) -> None:
    with conn() as db:
        db.execute("DELETE FROM sentiment_entries WHERE id=?", (eid,))


# ── Internal helpers for app ───────────────────────────────

def _get_milestone(mid: int) -> Optional[dict]:
    with conn() as db:
        r = db.execute("SELECT * FROM milestones WHERE id=?", (mid,)).fetchone()
        return dict(r) if r else None


def _get_snapshot(sid: int) -> Optional[dict]:
    with conn() as db:
        r = db.execute("SELECT * FROM snapshots WHERE id=?", (sid,)).fetchone()
        return dict(r) if r else None


def _get_blocker(bid: int) -> Optional[dict]:
    with conn() as db:
        r = db.execute("SELECT * FROM blockers WHERE id=?", (bid,)).fetchone()
        return dict(r) if r else None


def get_report(rid: int) -> Optional[dict]:
    with conn() as db:
        r = db.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
        return dict(r) if r else None


# ── Reports ────────────────────────────────────────────────

def list_reports(type_filter: Optional[str] = None) -> list[dict]:
    with conn() as db:
        if type_filter:
            rows = db.execute("SELECT * FROM reports WHERE type=? ORDER BY created_at DESC", (type_filter,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM reports ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def save_report(type_: str, report_date: str, file_path: Optional[str] = None, summary: Optional[str] = None) -> int:
    with conn() as db:
        cur = db.execute("""INSERT INTO reports (type,report_date,file_path,summary)
                            VALUES (?,?,?,?)""", (type_, report_date, file_path, summary))
        return cur.lastrowid


def delete_report(rid: int) -> None:
    with conn() as db:
        db.execute("DELETE FROM reports WHERE id=?", (rid,))


# ── Build domain objects from DB ───────────────────────────

def project_to_domain(proj: dict):
    from projects import Project, Milestone, MilestoneStatus, ProgressSnapshot, Blocker, BlockerSeverity, SentimentEntry, SentimentScore, ProjectStatus
    from datetime import date as dt_date

    p = Project(
        name=proj["name"],
        stakeholders=proj.get("stakeholders", []),
        budget=proj["budget"],
        start_date=dt_date.fromisoformat(proj["start_date"]),
        end_date=dt_date.fromisoformat(proj["end_date"]),
    )
    for m in proj.get("milestones", []):
        status = m["status"]
        try:
            ms = MilestoneStatus(status)
        except ValueError:
            ms = MilestoneStatus.NOT_STARTED
        p.milestones.append(Milestone(
            name=m["name"],
            due_date=dt_date.fromisoformat(m["due_date"]),
            status=ms,
            actual_completion_date=dt_date.fromisoformat(m["actual_completion_date"]) if m.get("actual_completion_date") else None,
        ))
    for s in proj.get("snapshots", []):
        status = s.get("status", "in_progress")
        try:
            ps = ProjectStatus(status)
        except ValueError:
            ps = ProjectStatus.IN_PROGRESS
        snap = ProgressSnapshot(
            snapshot_date=dt_date.fromisoformat(s["snapshot_date"]),
            status=ps,
            budget_spent=s.get("budget_spent"),
            percent_complete=s.get("percent_complete"),
            notes=s.get("notes"),
        )
        for b in s.get("blockers", []):
            severity = b["severity"]
            try:
                bs = BlockerSeverity(severity)
            except ValueError:
                bs = BlockerSeverity.MEDIUM
            snap.blockers.append(Blocker(
                description=b["description"],
                date_raised=dt_date.fromisoformat(b["date_raised"]),
                severity=bs,
                resolved=bool(b["resolved"]),
                date_resolved=dt_date.fromisoformat(b["date_resolved"]) if b.get("date_resolved") else None,
            ))
        for e in s.get("sentiment", []):
            score = e.get("score", "unknown")
            try:
                ss = SentimentScore(score)
            except ValueError:
                ss = SentimentScore.UNKNOWN
            snap.stakeholder_sentiment.append(SentimentEntry(
                source=e["source"],
                date_recorded=dt_date.fromisoformat(e["date_recorded"]),
                comment=e["comment"],
                score=ss,
            ))
        p.add_snapshot(snap)
    return p
