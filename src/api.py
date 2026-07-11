from __future__ import annotations
import math
import os
from datetime import date
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import JSONResponse

import database
from database import project_to_domain
from models.rag import compute_rag, RagResult, RAG
from models.sentiment import analyze_sentiment
from schedule import start_scheduler, stop_scheduler
from seed import seed

load_dotenv()


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


class SafeJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return super().render(_sanitize(content))


app = FastAPI(title="Zycus API", default_response_class=SafeJSONResponse)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

os.makedirs(os.path.join(os.path.dirname(__file__), "output"), exist_ok=True)


@app.on_event("startup")
def startup():
    database.init()
    seed()
    start_scheduler()


@app.on_event("shutdown")
def shutdown():
    stop_scheduler()



def _enrich_sentiment(proj_dict: dict, llm: bool = False):
    proj = project_to_domain(proj_dict)
    snap = proj.latest
    if not snap:
        return proj, None, None
    for entry in snap.stakeholder_sentiment:
        entry.score = analyze_sentiment(entry.comment, mode="llm" if llm else "keyword")
    result = compute_rag(proj, snap)
    return proj, snap, result


def _project_row(proj: dict):
    proj_domain, _, rag_result = _enrich_sentiment(proj)
    return {
        "id": proj["id"],
        "name": proj["name"],
        "budget": proj["budget"],
        "start_date": proj["start_date"],
        "end_date": proj["end_date"],
        "stakeholders": proj.get("stakeholders", []),
        "milestone_count": len(proj.get("milestones", [])),
        "snapshot_count": len(proj.get("snapshots", [])),
        "rag": rag_result.status if rag_result else "N/A",
    }


def _project_detail(proj: dict):
    proj_domain, snap, rag_result = _enrich_sentiment(proj)
    return {
        "id": proj["id"],
        "name": proj["name"],
        "budget": proj["budget"],
        "start_date": proj["start_date"],
        "end_date": proj["end_date"],
        "stakeholders": proj.get("stakeholders", []),
        "milestones": proj.get("milestones", []),
        "snapshots": proj.get("snapshots", []),
        "rag": rag_result.status if rag_result else "N/A",
        "rag_detail": {
            "status": rag_result.status,
            "weighted_score": rag_result.weighted_score,
            "signals": [{"name": s.name, "score": s.score, "rationale": s.rationale} for s in rag_result.signals],
            "overrides": rag_result.overrides_applied,
            "facts": rag_result.facts,
        } if rag_result else None,
    }



class ProjectIn(BaseModel):
    name: str
    stakeholders: str = ""
    budget: float = 0
    start_date: str
    end_date: str

class MilestoneIn(BaseModel):
    name: str
    due_date: str
    status: str = "not_started"
    actual_completion_date: Optional[str] = None

class SnapshotIn(BaseModel):
    snapshot_date: str
    budget_spent: Optional[float] = None
    percent_complete: Optional[float] = None
    notes: Optional[str] = None

class BlockerIn(BaseModel):
    description: str
    date_raised: str
    severity: str = "medium"

class SentimentIn(BaseModel):
    source: str
    comment: str



@app.get("/api/projects")
def api_list_projects():
    rows = database.list_projects()
    out = []
    for row in rows:
        proj = database.get_project(row["id"])
        if not proj:
            out.append({"id": None, "name": "? (deleted)", "rag": "N/A", "error": "Project deleted between list and fetch"})
            continue
        try:
            out.append(_project_row(proj))
        except Exception as e:
            out.append({"id": proj["id"], "name": proj["name"], "rag": "N/A", "error": str(e)})
    return out


@app.get("/api/projects/{pid}")
def api_get_project(pid: int):
    proj = database.get_project(pid)
    if not proj:
        raise HTTPException(404, "Project not found")
    try:
        return _project_detail(proj)
    except Exception as e:
        return {**proj, "rag": "N/A", "error": str(e)}


@app.post("/api/projects")
def api_create_project(body: ProjectIn):
    pid = database.upsert_project({
        "name": body.name,
        "stakeholders": [s.strip() for s in body.stakeholders.split(",") if s.strip()],
        "budget": body.budget,
        "start_date": body.start_date,
        "end_date": body.end_date,
    })
    return {"ok": True, "id": pid}


@app.put("/api/projects/{pid}")
def api_update_project(pid: int, body: ProjectIn):
    database.upsert_project({
        "id": pid, "name": body.name,
        "stakeholders": [s.strip() for s in body.stakeholders.split(",") if s.strip()],
        "budget": body.budget, "start_date": body.start_date, "end_date": body.end_date,
    })
    return {"ok": True, "id": pid}


@app.delete("/api/projects/{pid}")
def api_delete_project(pid: int):
    database.delete_project(pid)
    return {"ok": True}



@app.post("/api/projects/{pid}/milestones")
def api_add_milestone(pid: int, body: MilestoneIn):
    mid = database.upsert_milestone({
        "project_id": pid, "name": body.name, "due_date": body.due_date,
        "status": body.status, "actual_completion_date": body.actual_completion_date,
    })
    return {"ok": True, "id": mid}


@app.delete("/api/milestones/{mid}")
def api_delete_milestone(mid: int):
    database.delete_milestone(mid)
    return {"ok": True}



@app.post("/api/projects/{pid}/snapshots")
def api_add_snapshot(pid: int, body: SnapshotIn):
    sid = database.upsert_snapshot({
        "project_id": pid, "snapshot_date": body.snapshot_date,
        "budget_spent": body.budget_spent, "percent_complete": body.percent_complete,
        "notes": body.notes,
    })
    return {"ok": True, "id": sid}


@app.delete("/api/snapshots/{sid}")
def api_delete_snapshot(sid: int):
    database.delete_snapshot(sid)
    return {"ok": True}



@app.post("/api/snapshots/{sid}/blockers")
def api_add_blocker(sid: int, body: BlockerIn):
    bid = database.upsert_blocker({
        "snapshot_id": sid, "description": body.description,
        "date_raised": body.date_raised, "severity": body.severity,
    })
    return {"ok": True, "id": bid}


@app.post("/api/blockers/{bid}/resolve")
def api_resolve_blocker(bid: int):
    b = database._get_blocker(bid)
    if not b:
        raise HTTPException(404, "Blocker not found")
    database.upsert_blocker({
        "id": bid, "snapshot_id": b["snapshot_id"],
        "description": b["description"], "date_raised": b["date_raised"],
        "severity": b["severity"], "resolved": True,
        "date_resolved": date.today().isoformat(),
    })
    return {"ok": True}


@app.delete("/api/blockers/{bid}")
def api_delete_blocker(bid: int):
    database.delete_blocker(bid)
    return {"ok": True}



@app.post("/api/snapshots/{sid}/sentiment")
def api_add_sentiment(sid: int, body: SentimentIn):
    database.upsert_sentiment({
        "snapshot_id": sid, "source": body.source,
        "date_recorded": date.today().isoformat(), "comment": body.comment,
    })
    return {"ok": True}



@app.get("/api/reports")
def api_list_reports(type: Optional[str] = None):
    return database.list_reports(type)


@app.post("/api/reports/generate/weekly")
def api_generate_weekly():
    try:
        _run_weekly()
        return {"ok": True, "message": "Weekly report generated"}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/reports/generate/monthly")
def api_generate_monthly():
    try:
        _run_monthly()
        return {"ok": True, "message": "Monthly report generated"}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.delete("/api/reports/{rid}")
def api_delete_report(rid: int):
    database.delete_report(rid)
    return {"ok": True}


@app.get("/api/reports/{rid}/download")
def api_download_report(rid: int):
    from fastapi.responses import FileResponse
    r = database.get_report(rid)
    if not r or not r.get("file_path") or not os.path.exists(r["file_path"]):
        raise HTTPException(404, "Report file not found")
    return FileResponse(r["file_path"], filename=os.path.basename(r["file_path"]))



@app.get("/api/health")
def health():
    return {"status": "ok"}
