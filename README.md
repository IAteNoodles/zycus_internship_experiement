# Zycus — Project Health Reporting Agent

Automated project health monitoring with RAG (Red/Amber/Green) scoring, LLM-generated weekly narratives, monthly PPTX synthesis, and a FastAPI web dashboard.

## Quick Start

```bash
uv sync
uv run python seed.py                   # seed 4 sample projects
uv run uvicorn app:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000

## Docker

```bash
docker compose up --build
```

## CLI

```bash
uv run python main.py                    # weekly analysis
uv run python main.py --synthesize       # + monthly PPTX
```

## Architecture

| Layer | Tech |
|-------|------|
| Web | FastAPI + Jinja2 + Bootstrap 5 |
| Schedule | APScheduler (weekly Mon 9AM, monthly 1st 8AM) |
| LLM | Groq (primary) / OpenRouter (fallback) |
| DB | SQLite (WAL mode) |
| CLI | argparse + python-pptx |

## API

- `GET /api/projects` — all projects with RAG
- `GET /api/projects/{id}/rag` — detailed RAG signals
