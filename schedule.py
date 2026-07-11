from __future__ import annotations
import os
from datetime import date, datetime

from apscheduler.schedulers.background import BackgroundScheduler

from database import list_projects, get_project, save_report, project_to_domain
from rag import compute_rag
from sentiment import analyze_sentiment
from reports import weekly_narrative, monthly_content

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")


def _run_weekly():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = date.today()
    rows = list_projects()
    summaries = []
    for row in rows:
        proj = get_project(row["id"])
        if not proj:
            continue
        p = project_to_domain(proj)
        snap = p.latest
        if not snap:
            continue
        for entry in snap.stakeholder_sentiment:
            entry.score = analyze_sentiment(entry.comment)
        result = compute_rag(p, snap, today)
        narrative = weekly_narrative(p, result)
        summaries.append(f"[{result.status}] {p.name}: {narrative}")

    summary_text = "\n\n".join(summaries)
    fname = f"weekly_{today.isoformat()}.txt"
    fpath = os.path.join(OUTPUT_DIR, fname)
    with open(fpath, "w") as f:
        f.write(f"Weekly Report - {today}\n{'='*50}\n\n")
        f.write(summary_text)
    save_report("weekly", today.isoformat(), fpath, summary_text[:500])
    print(f"[scheduler] Weekly report saved: {fpath}")


def _run_monthly():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = date.today()
    rows = list_projects()
    results = []
    for row in rows:
        proj = get_project(row["id"])
        if not proj:
            continue
        p = project_to_domain(proj)
        snap = p.latest
        if not snap:
            continue
        for entry in snap.stakeholder_sentiment:
            entry.score = analyze_sentiment(entry.comment)
        result = compute_rag(p, snap, today)
        results.append((p, result))

    content = monthly_content(results, today)
    summary_text = content.get("executive_summary", "")

    fname = f"monthly_{today.isoformat()}.txt"
    fpath_txt = os.path.join(OUTPUT_DIR, fname)
    with open(fpath_txt, "w") as f:
        f.write(f"Monthly Report - {today}\n{'='*50}\n\n")
        for p, r in results:
            f.write(f"[{r.status}] {p.name}\n")
            f.write(weekly_narrative(p, r) + "\n\n")
        f.write("--- Executive Summary ---\n")
        f.write(content.get("executive_summary", ""))
        f.write("\n\nTrends:\n")
        for t in content.get("trends", []):
            f.write(f"  - {t}\n")
        f.write("\nRecommendations:\n")
        for rec in content.get("recommendations", []):
            f.write(f"  - {rec}\n")

    try:
        from main import synthesize_monthly
        fpath_pptx = synthesize_monthly(results, today, os.path.join(OUTPUT_DIR, f"monthly_{today.isoformat()}.pptx"))
        save_report("monthly", today.isoformat(), fpath_pptx, summary_text[:500])
        print(f"[scheduler] Monthly PPTX saved: {fpath_pptx}")
    except Exception as e:
        print(f"[scheduler] PPTX generation failed: {e}")
        save_report("monthly", today.isoformat(), fpath_txt, summary_text[:500])

    print(f"[scheduler] Monthly report saved: {fpath_txt}")


scheduler = BackgroundScheduler()


def start_scheduler(weekly_hour: int = 9, weekly_minute: int = 0, weekly_day: str = "mon"):
    if not scheduler.get_jobs():
        scheduler.add_job(_run_weekly, "cron", day_of_week=weekly_day, hour=weekly_hour, minute=weekly_minute,
                          id="weekly_report", name="Weekly report", replace_existing=True)
        scheduler.add_job(_run_monthly, "cron", day=1, hour=8, minute=0,
                          id="monthly_report", name="Monthly report", replace_existing=True)
        scheduler.start()
        print(f"[scheduler] Started (weekly={weekly_day} {weekly_hour}:{weekly_minute:02d}, monthly=1st 8:00)")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
