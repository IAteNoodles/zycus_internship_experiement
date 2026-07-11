from __future__ import annotations
import argparse
import os
from datetime import date
from typing import Optional

from dotenv import load_dotenv

from data import sample_projects
from projects import Project, SentimentScore
from rag import compute_rag, RagResult
from sentiment import analyze_sentiment
from reports import weekly_narrative, monthly_content

load_dotenv()


def _enrich_sentiment(project: Project) -> None:
    snap = project.latest
    if not snap:
        return
    for entry in snap.stakeholder_sentiment:
        entry.score = analyze_sentiment(entry.comment)


ANALYSIS_HEADER = "=" * 60


def run_weekly(projects: list[Project], as_of: date) -> list[tuple[Project, RagResult]]:
    results = []
    for proj in projects:
        if not proj.latest:
            print(f"[SKIP] {proj.name}: no snapshot.")
            continue
        _enrich_sentiment(proj)
        result = compute_rag(proj, proj.latest, as_of)
        results.append((proj, result))
        print(ANALYSIS_HEADER)
        print(weekly_narrative(proj, result))
        print()
    return results


def synthesize_monthly(results: list[tuple[Project, RagResult]], as_of: date, path: str) -> str:
    from pptx import Presentation
    from pptx.util import Inches, Pt

    content = monthly_content(results, as_of)

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    W = prs.slide_width

    def slide():
        return prs.slides.add_slide(prs.slide_layouts[6])

    def title(sl, text: str, top=Inches(0.3), left=Inches(0.5), size=Pt(28)):
        tb = sl.shapes.add_textbox(left, top, W - Inches(1), Inches(0.7)).text_frame
        tb.word_wrap = True
        p = tb.paragraphs[0]
        p.text = text
        p.font.size = size
        p.font.bold = True

    def body(sl, text: str, top=Inches(1.3), left=Inches(0.5),
             width=None, height=None, size=Pt(16)):
        width = width or W - Inches(1)
        height = height or prs.slide_height - top - Inches(0.5)
        tb = sl.shapes.add_textbox(left, top, width, height).text_frame
        tb.word_wrap = True
        p = tb.paragraphs[0]
        p.text = text
        p.font.size = size

    # Title
    sl = slide()
    title(sl, "Monthly Project Health Report", top=Inches(2.5), size=Pt(36))
    body(sl, as_of.strftime("%B %d, %Y"), top=Inches(3.5), size=Pt(20))

    # Executive summary
    sl = slide()
    title(sl, "Executive Summary")
    body(sl, content.get("executive_summary", ""))

    # Per-project slides
    for proj, result in results:
        sl = slide()
        title(sl, f"{result.status.upper()}  {proj.name}")
        lines = [
            f"Budget: ${proj.budget:,.0f}",
            f"Period: {proj.start_date} -> {proj.end_date}",
            "",
        ]
        for s in result.signals:
            score = f"{s.score}/2" if s.score is not None else "N/A"
            lines.append(f"{s.name}: {score}  {s.rationale}")
        if result.overrides_applied:
            for o in result.overrides_applied:
                lines.append(f"Override: {o}")
        body(sl, "\n".join(lines))

    # Trends
    sl = slide()
    title(sl, "Trends & Emerging Risks")
    trends = "\n\n".join(filter(None, [
        "\n".join(content.get("trends", [])),
        "\n".join(content.get("risks", [])),
    ]))
    body(sl, trends or "No significant trends detected.")

    # Recommendations
    sl = slide()
    title(sl, "Recommendations")
    body(sl, "\n".join(content.get("recommendations", [])))

    prs.save(path)
    return path


def main():
    parser = argparse.ArgumentParser(description="Project Health Reporting Agent")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD")
    parser.add_argument("--synthesize", action="store_true", help="Generate monthly PPTX")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    as_of = date.fromisoformat(args.date) if args.date else date.today()

    projects = sample_projects()
    results = run_weekly(projects, as_of)

    weekly_path = os.path.join(args.output_dir, f"weekly_{as_of.isoformat()}.txt")
    with open(weekly_path, "w") as f:
        for proj, result in results:
            f.write(weekly_narrative(proj, result) + "\n\n")
    print(f"Weekly: {weekly_path}")

    if args.synthesize:
        pptx = synthesize_monthly(results, as_of, os.path.join(args.output_dir, f"monthly_{as_of.isoformat()}.pptx"))
        print(f"Monthly: {pptx}")


if __name__ == "__main__":
    main()
