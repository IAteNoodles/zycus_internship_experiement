from __future__ import annotations
import os
from datetime import date, datetime
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Form, Query, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import database
from database import project_to_domain
from rag import compute_rag, RAG, RagResult
from sentiment import analyze_sentiment
from reports import weekly_narrative, monthly_content
from schedule import start_scheduler, stop_scheduler, _run_weekly, _run_monthly

load_dotenv()

app = FastAPI(title="Zycus - Project Health Reporting Agent")

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
os.makedirs(os.path.join(os.path.dirname(__file__), "output"), exist_ok=True)

STATUS_ORDER = {"Red": 0, "Amber": 1, "Green": 2}


@app.on_event("startup")
def startup():
    database.init()
    start_scheduler()


@app.on_event("shutdown")
def shutdown():
    stop_scheduler()


# ── Helpers ────────────────────────────────────────────────

def compute_current(proj_dict: dict) -> tuple[Optional[RagResult], Optional[str]]:
    proj = project_to_domain(proj_dict)
    snap = proj.latest
    if not snap:
        return None, None
    for entry in snap.stakeholder_sentiment:
        entry.score = analyze_sentiment(entry.comment)
    result = compute_rag(proj, snap)
    narrative = weekly_narrative(proj, result)
    return result, narrative


# ── Dashboard ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    rows = database.list_projects()
    projects = []
    for row in rows:
        proj = database.get_project(row["id"])
        result, narrative = compute_current(proj)
        projects.append({
            "id": proj["id"],
            "name": proj["name"],
            "budget": proj["budget"],
            "start_date": proj["start_date"],
            "end_date": proj["end_date"],
            "milestone_count": len(proj.get("milestones", [])),
            "snapshot_count": len(proj.get("snapshots", [])),
            "rag": result.status if result else "N/A",
            "narrative": narrative or "No data",
        })
    projects.sort(key=lambda p: STATUS_ORDER.get(p["rag"], 99))
    return templates.TemplateResponse(request, "dashboard.html", {"projects": projects})


# ── Project CRUD ───────────────────────────────────────────

@app.get("/project/new", response_class=HTMLResponse)
def new_project_form(request: Request):
    return templates.TemplateResponse(request, "project_form.html", {"project": None})


@app.post("/project/new")
def create_project(name: str = Form(...), stakeholders: str = Form(""),
                   budget: float = Form(0), start_date: str = Form(...), end_date: str = Form(...)):
    pid = database.upsert_project({
        "name": name,
        "stakeholders": [s.strip() for s in stakeholders.split(",") if s.strip()],
        "budget": budget,
        "start_date": start_date,
        "end_date": end_date,
    })
    return RedirectResponse(f"/project/{pid}", status_code=303)


@app.get("/project/{pid}", response_class=HTMLResponse)
def project_detail(request: Request, pid: int):
    proj = database.get_project(pid)
    if not proj:
        raise HTTPException(404, "Project not found")
    result, narrative = compute_current(proj)
    return templates.TemplateResponse(request, "project.html", {
        "proj": proj, "result": result, "narrative": narrative,
    })


@app.post("/project/{pid}/update")
def update_project(pid: int, name: str = Form(...), stakeholders: str = Form(""),
                   budget: float = Form(0), start_date: str = Form(...), end_date: str = Form(...)):
    database.upsert_project({
        "id": pid, "name": name,
        "stakeholders": [s.strip() for s in stakeholders.split(",") if s.strip()],
        "budget": budget, "start_date": start_date, "end_date": end_date,
    })
    return RedirectResponse(f"/project/{pid}", status_code=303)


@app.post("/project/{pid}/delete")
def delete_project(pid: int):
    database.delete_project(pid)
    return RedirectResponse("/", status_code=303)


# ── Milestones ─────────────────────────────────────────────

@app.post("/project/{pid}/milestone")
def add_milestone(pid: int, name: str = Form(...), due_date: str = Form(...),
                  status: str = Form("not_started"), actual_completion_date: Optional[str] = Form(None)):
    database.upsert_milestone({
        "project_id": pid, "name": name, "due_date": due_date,
        "status": status, "actual_completion_date": actual_completion_date or None,
    })
    return RedirectResponse(f"/project/{pid}", status_code=303)


@app.post("/milestone/{mid}/delete")
def delete_milestone(mid: int):
    from database import get_project
    m = database._get_milestone(mid)
    pid = m["project_id"] if m else 0
    database.delete_milestone(mid)
    return RedirectResponse(f"/project/{pid}", status_code=303)


# ── Snapshots ──────────────────────────────────────────────

@app.post("/project/{pid}/snapshot")
def add_snapshot(pid: int, snapshot_date: str = Form(...),
                 budget_spent: Optional[float] = Form(None),
                 percent_complete: Optional[float] = Form(None),
                 notes: Optional[str] = Form("")):
    database.upsert_snapshot({
        "project_id": pid, "snapshot_date": snapshot_date,
        "budget_spent": budget_spent, "percent_complete": percent_complete, "notes": notes or None,
    })
    return RedirectResponse(f"/project/{pid}", status_code=303)


@app.post("/snapshot/{sid}/delete")
def delete_snapshot(sid: int):
    from database import get_project
    s = database._get_snapshot(sid)
    pid = s["project_id"] if s else 0
    database.delete_snapshot(sid)
    return RedirectResponse(f"/project/{pid}", status_code=303)


@app.post("/snapshot/{sid}/blocker")
def add_blocker(sid: int, description: str = Form(...), date_raised: str = Form(...),
                severity: str = Form("medium")):
    from database import get_project
    database.upsert_blocker({
        "snapshot_id": sid, "description": description,
        "date_raised": date_raised, "severity": severity,
    })
    s = database._get_snapshot(sid)
    return RedirectResponse(f"/project/{s['project_id']}", status_code=303)


@app.post("/blocker/{bid}/resolve")
def resolve_blocker(bid: int):
    from database import get_project
    b = database._get_blocker(bid)
    database.upsert_blocker({
        "id": bid, "snapshot_id": b["snapshot_id"],
        "description": b["description"], "date_raised": b["date_raised"],
        "severity": b["severity"], "resolved": True,
        "date_resolved": date.today().isoformat(),
    })
    s = database._get_snapshot(b["snapshot_id"])
    return RedirectResponse(f"/project/{s['project_id']}", status_code=303)


@app.post("/blocker/{bid}/delete")
def delete_blocker(bid: int):
    from database import get_project
    b = database._get_blocker(bid)
    database.delete_blocker(bid)
    s = database._get_snapshot(b["snapshot_id"])
    return RedirectResponse(f"/project/{s['project_id']}", status_code=303)


# ── Sentiment ──────────────────────────────────────────────

@app.post("/snapshot/{sid}/sentiment")
def add_sentiment(sid: int, source: str = Form(...), comment: str = Form(...)):
    from database import get_project
    database.upsert_sentiment({
        "snapshot_id": sid, "source": source,
        "date_recorded": date.today().isoformat(), "comment": comment,
    })
    s = database._get_snapshot(sid)
    return RedirectResponse(f"/project/{s['project_id']}", status_code=303)


# ── Reports ────────────────────────────────────────────────

@app.get("/reports", response_class=HTMLResponse)
def reports_list(request: Request, type: Optional[str] = Query(None)):
    rows = database.list_reports(type)
    return templates.TemplateResponse(request, "reports.html", {"reports": rows, "filter": type})


@app.post("/reports/generate/weekly")
def generate_weekly():
    _run_weekly()
    return RedirectResponse("/reports", status_code=303)


@app.post("/reports/generate/monthly")
def generate_monthly():
    _run_monthly()
    return RedirectResponse("/reports", status_code=303)


@app.get("/reports/download/{rid}")
def download_report(rid: int):
    from database import get_report
    r = database.get_report(rid)
    if not r or not r.get("file_path") or not os.path.exists(r["file_path"]):
        raise HTTPException(404, "Report file not found")
    return FileResponse(r["file_path"], filename=os.path.basename(r["file_path"]))


@app.post("/reports/{rid}/delete")
def delete_report(rid: int):
    database.delete_report(rid)
    return RedirectResponse("/reports", status_code=303)


# ── API for programmatic access ────────────────────────────

@app.get("/api/projects")
def api_projects():
    rows = database.list_projects()
    out = []
    for row in rows:
        proj = database.get_project(row["id"])
        result, narrative = compute_current(proj)
        out.append({
            "id": proj["id"], "name": proj["name"],
            "rag": result.status if result else "N/A",
            "narrative": narrative,
        })
    return out


@app.get("/api/projects/{pid}/rag")
def api_project_rag(pid: int):
    proj = database.get_project(pid)
    if not proj:
        raise HTTPException(404)
    result, narrative = compute_current(proj)
    if not result:
        return {"status": "N/A", "reason": "No snapshot data"}
    return {
        "status": result.status,
        "weighted_score": result.weighted_score,
        "signals": [{"name": s.name, "score": s.score, "rationale": s.rationale} for s in result.signals],
        "overrides": result.overrides_applied,
        "narrative": narrative,
        "facts": result.facts,
    }
