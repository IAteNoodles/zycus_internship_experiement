from __future__ import annotations
import json
from datetime import date
from typing import Optional

from models.projects import Project, SentimentScore
from models.rag import RagResult, RAG
from llm import ask_llm


def _signal_summary(project: Project, result: RagResult) -> str:
    snap = project.latest
    lines = [f"Project: {project.name}", f"RAG: {result.status}"]
    lines.append(f"Weighted score: {result.weighted_score:.2f}" if result.weighted_score is not None else "Weighted score: N/A")
    for s in result.signals:
        score = f"{s.score}/2" if s.score is not None else "N/A"
        lines.append(f"  {s.name}: {score} — {s.rationale}")
        if s.name == "blockers" and snap:
            open_b = snap.open_blockers()
            for b in open_b:
                lines.append(f"    - {b.description}")
    if result.overrides_applied:
        lines.append("  Overrides applied: " + "; ".join(result.overrides_applied))
    if result.insufficient_data:
        lines.append("  ⚠ Insufficient data — over half of signal weight is missing.")
    return "\n".join(lines)


_WEEKLY_SYSTEM = (
    "You are a PM writing a weekly project update. "
    "Given a project summary and RAG status, write 3-4 sentences. "
    "Say what's going well, what's not, and what to do next. "
    "Use plain simple language. No jargon. No markdown. No greetings."
)


def weekly_narrative(project: Project, result: RagResult) -> str:
    try:
        summary_text = _signal_summary(project, result)
        user = f"Write a narrative for:\n{summary_text}"
        llm_out = ask_llm(_WEEKLY_SYSTEM, user, max_tokens=300, temperature=0.3)
        if llm_out:
            return llm_out.strip()
    except Exception:
        pass
    return _fallback_narrative(project, result)


def _fallback_narrative(project: Project, result: RagResult) -> str:
    snap = project.latest
    if not snap:
        return f"{project.name}: {result.status}. No data available."
    as_of_raw = result.facts.get("as_of", date.today().isoformat())
    if isinstance(as_of_raw, str):
        try:
            as_of = date.fromisoformat(as_of_raw)
        except ValueError:
            from database import parse_date
            as_of = parse_date(as_of_raw)
    else:
        as_of = as_of_raw
    over = project.milestones_overdue(as_of)
    open_b = snap.open_blockers()
    neg_sent = sum(1 for e in snap.stakeholder_sentiment if e.score == SentimentScore.NEGATIVE)
    parts = [f"[{result.status}] {project.name}"]
    if result.insufficient_data:
        parts.append("Not enough data to assess.")
    else:
        parts.append(f"Score: {result.weighted_score:.2f}/2.")
    if over:
        parts.append(f"{len(over)} overdue milestone(s).")
    if open_b:
        parts.append(f"{len(open_b)} open issue(s).")
    if neg_sent:
        parts.append(f"{neg_sent} unhappy stakeholder(s).")
    return " ".join(parts)


_MONTHLY_SYSTEM = (
    "You are a PM writing a monthly summary for non-technical readers. "
    "Given the list of projects with their RAG status, produce: "
    "1) An executive summary (3-4 sentences, plain language). "
    "2) Key trends (what changed this month, simply put). "
    "3) Risks (what could go wrong, stated clearly). "
    "4) Recommendations (what to do next, actionable). "
    "Use plain simple language. No jargon. No markdown. "
    "Return ONLY valid JSON with keys: executive_summary (string), trends (list of strings), risks (list of strings), recommendations (list of strings)."
)


def monthly_content(results: list[tuple[Project, RagResult]], as_of: Optional[date] = None) -> dict:
    as_of = as_of or date.today()
    fallback = _monthly_fallback(results)
    try:
        summaries = [_signal_summary(p, r) for p, r in results]
        user = f"As of {as_of.isoformat()}:\n" + "\n---\n".join(summaries)
        llm_out = ask_llm(_MONTHLY_SYSTEM, user, max_tokens=800, temperature=0.3, json_mode=True)
        if llm_out:
            parsed = json.loads(llm_out)
            if all(k in parsed for k in ("executive_summary", "trends", "recommendations")):
                for key in ("trends", "risks", "recommendations"):
                    parsed[key] = [str(item) for item in parsed.get(key, [])]
                return parsed
    except Exception:
        pass
    return fallback


def _monthly_fallback(results: list[tuple[Project, RagResult]]) -> dict:
    green = [p.name for p, r in results if r.status == RAG.GREEN]
    amber = [p.name for p, r in results if r.status == RAG.AMBER]
    red = [p.name for p, r in results if r.status == RAG.RED]
    parts = []
    if green:
        parts.append(f"Green: {', '.join(green)}.")
    if amber:
        parts.append(f"Amber: {', '.join(amber)} — needs attention.")
    if red:
        parts.append(f"Red: {', '.join(red)} — escalation required.")
    summary = "Monthly portfolio review. " + " ".join(parts) if parts else "No project data available."

    trends = []
    risks = []
    recommendations = []
    for p, r in results:
        trends.append(f"{p.name} ({r.status}, score {r.weighted_score:.2f}/2)")
        for s in r.signals:
            if s.score is not None and s.score >= 2:
                risks.append(f"{p.name}: {s.name} needs attention ({s.rationale[:80]})")
        if r.status == RAG.RED:
            recommendations.append(f"Escalate {p.name} — immediate intervention needed.")
        elif r.status == RAG.AMBER:
            recommendations.append(f"Review {p.name} — risks need monitoring.")
    if not risks:
        risks.append("No significant risks identified.")
    if not recommendations:
        recommendations.append("Continue current trajectory.")

    return {
        "executive_summary": summary,
        "trends": trends,
        "risks": risks,
        "recommendations": recommendations,
    }
