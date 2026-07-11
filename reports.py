from __future__ import annotations
import json
from datetime import date
from typing import Optional

from projects import Project, SentimentScore
from rag import RagResult, RAG
from llm import ask_llm


def _signal_summary(project: Project, result: RagResult) -> str:
    snap = project.latest
    lines = [f"Project: {project.name}", f"RAG: {result.status}"]
    lines.append(f"Weighted score: {result.weighted_score:.2f}" if result.weighted_score is not None else "Weighted score: N/A")
    for s in result.signals:
        score_str = f"{s.score}/2" if s.score is not None else "N/A"
        lines.append(f"  {s.name}: {score_str} - {s.rationale}")
    if result.overrides_applied:
        for o in result.overrides_applied:
            lines.append(f"  Override: {o}")
    if snap:
        ob = snap.open_blockers()
        if ob:
            lines.append(f"  Open blockers: {len(ob)}")
            for b in ob:
                lines.append(f"    [{b.severity.value}] {b.description}")
        neg = [e for e in snap.stakeholder_sentiment if e.score == SentimentScore.NEGATIVE]
        if neg:
            lines.append(f"  Negative stakeholders: {len(neg)}")
            for e in neg:
                lines.append(f"    {e.source}: {e.comment}")
    return "\n".join(lines)


_WEEKLY_SYSTEM = (
    "You are a senior program manager writing a weekly project health update. "
    "Given the project data, write 2-3 clear sentences explaining: "
    "(1) the current RAG status and why, "
    "(2) key risks or blockers, "
    "(3) recommended actions. "
    "Be direct and specific. Do not use markdown."
)


def weekly_narrative(project: Project, result: RagResult) -> str:
    data = _signal_summary(project, result)
    narrative = ask_llm(_WEEKLY_SYSTEM, data, max_tokens=250, temperature=0.3)
    if narrative:
        return narrative.strip()
    snap = project.latest
    parts = [f"{project.name} is {result.status}."]

    sigs = {s.name: s for s in result.signals}
    schedule = sigs.get("schedule")
    budget = sigs.get("budget")
    blockers_i = sigs.get("blockers")
    sentiment = sigs.get("sentiment")

    issues = []
    if schedule and schedule.score and schedule.score >= 1:
        late = sum(1 for m in project.milestones if m.is_overdue())
        issues.append(f"{late} milestone(s) overdue")
    if budget and budget.score and budget.score >= 1:
        issues.append("budget variance exceeds threshold")
    if blockers_i and blockers_i.score and blockers_i.score >= 1:
        n = len(snap.open_blockers()) if snap and snap.open_blockers() else 0
        issues.append(f"{n} open blocker(s)")
    if sentiment and sentiment.score and sentiment.score >= 1:
        neg = len([e for e in snap.stakeholder_sentiment if e.score == SentimentScore.NEGATIVE]) if snap else 0
        if neg:
            issues.append(f"{neg} stakeholder(s) negative")
    if result.overrides_applied:
        issues.append("overrides applied: " + "; ".join(result.overrides_applied))

    if issues:
        parts.append("Issues: " + ", ".join(issues) + ".")
    else:
        parts.append("No significant issues detected.")

    if result.status == RAG.RED:
        parts.append("Immediate attention required.")
    elif result.status == RAG.AMBER:
        parts.append("Monitor closely.")
    return " ".join(parts)


_MONTHLY_SYSTEM = (
    "You are a VP preparing an executive monthly portfolio review. "
    "Given the list of projects with their RAG status and signal data, "
    "generate a JSON object with exactly these fields:\n"
    '  "executive_summary": 2-3 sentence portfolio overview,\n'
    '  "trends": list of 2-3 observed patterns across projects,\n'
    '  "risks": list of 2-3 emerging risks requiring attention,\n'
    '  "recommendations": list of 3-4 actionable executive recommendations.\n'
    "Be specific and data-driven. Do not hedge. Output ONLY JSON."
)


def monthly_content(results: list[tuple[Project, RagResult]], as_of: date) -> dict:
    data_lines = [f"Report date: {as_of.isoformat()}", ""]
    for proj, result in results:
        data_lines.append(_signal_summary(proj, result))
        data_lines.append("")

    raw = ask_llm(_MONTHLY_SYSTEM, "\n".join(data_lines), max_tokens=800, temperature=0.3, json_mode=True)
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

    green = sum(1 for _, r in results if r.status == RAG.GREEN)
    amber_n = sum(1 for _, r in results if r.status == RAG.AMBER)
    red_n = sum(1 for _, r in results if r.status == RAG.RED)
    total = len(results)
    exec_summary = f"Portfolio review for {as_of.strftime('%B %Y')}: {total} project(s) tracked. "
    exec_summary += f"{green} Green, {amber_n} Amber, {red_n} Red."
    non_green = [p.name for p, r in results if r.status != RAG.GREEN]
    if non_green:
        exec_summary += f" Projects requiring attention: {', '.join(non_green)}."

    risks = []
    for p, r in results:
        if r.status != RAG.GREEN:
            risks.append(f"{p.name} ({r.status}): {r.weighted_score:.2f} weighted score" if r.weighted_score is not None else f"{p.name} ({r.status})")
            for o in r.overrides_applied:
                risks.append(f"  Override: {o}")

    recs = []
    if red_n:
        recs.append(f"Schedule steering committee for {red_n} Red project(s) immediately.")
    if amber_n:
        recs.append(f"Review recovery plans for {amber_n} Amber project(s).")
    if non_green:
        recs.append("Assess resource allocation and adjust priorities.")
    recs.append("Continue monitoring Green projects for early warning signs.")

    return {
        "executive_summary": exec_summary,
        "trends": [f"{p.name}: {r.status} (score {r.weighted_score:.2f})" if r.weighted_score is not None else f"{p.name}: {r.status}" for p, r in results],
        "risks": risks or ["No significant risks identified."],
        "recommendations": recs,
    }
