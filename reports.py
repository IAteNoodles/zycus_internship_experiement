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
    # fallback if LLM unavailable
    status = result.status
    signals = {s.name: s for s in result.signals}
    parts = [f"{project.name} is {status}."]
    if status == RAG.GREEN:
        parts.append("All signals are healthy. No immediate action needed.")
    elif status == RAG.RED:
        parts.append("Immediate attention required.")
    else:
        parts.append("Monitor closely.")
    snap = project.latest
    if snap and snap.open_blockers():
        parts.append(f"Open blockers: {len(snap.open_blockers())}.")
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

    # fallback
    green = sum(1 for _, r in results if r.status == RAG.GREEN)
    amber = sum(1 for _, r in results if r.status == RAG.AMBER)
    red = sum(1 for _, r in results if r.status == RAG.RED)
    return {
        "executive_summary": f"Portfolio: {green} Green, {amber} Amber, {red} Red.",
        "trends": [f"{p.name}: {r.status}" for p, r in results],
        "risks": [f"{p.name} requires monitoring" for p, r in results if r.status != RAG.GREEN],
        "recommendations": ["Review resource allocation.", "Schedule steering committee for Red projects."],
    }
