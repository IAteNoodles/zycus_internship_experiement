# Zycus - Project Health Reporting Agent

Automated project health monitoring with RAG (Red/Amber/Green) scoring, LLM-generated weekly narratives, monthly PPTX reports, a Streamlit UI, and a FastAPI API.

**AI usage**: Narrative generation (weekly/monthly reports) and sentiment scoring use LLMs (Groq/OpenRouter). The RAG scoring model (`rag.py`), data models (`models/projects.py`), and sentiment logic (`models/sentiment.py`) are **hand-crafted** — no LLM inference in the critical path — to ensure deterministic, auditable assessments and a stable backbone for coordination. The surrounding application code (UI, API, scheduling, Docker) was developed with AI assistance.

## Quick Start

```bash
uv sync
uv run streamlit run src/ui.py
```

The UI initializes and seeds the local SQLite database when it is empty. Run the API separately when needed:

```bash
uv run uvicorn --app-dir src api:app --host 0.0.0.0 --port 8000
```

## Docker

Create a root `.env` with at least one LLM key:

```env
GROQ_API_KEY=...
# or
OPENAI_API_KEY=...
```

Build and start both services:

```bash
docker compose up --build
```

- UI: http://localhost:8501
- API health: http://localhost:8000/api/health
- SQLite data persists in the `zycus_data` volume.
- Generated reports persist in the `zycus_output` volume.

The Docker image copies `.env` at build time. Do not push or share the resulting image because it contains the LLM credentials.

## Scheduled Reports

The API owns one APScheduler instance while the container is running:

- Weekly report: Monday at 09:00 UTC
- Monthly report: first day of each month at 08:00 UTC

Scheduled reports use Groq first, then OpenRouter, when the corresponding key is configured. They are not generated while the container is stopped.
Weekly reports are generated as downloadable PDFs; monthly reports include a PPTX.

## CLI

```bash
uv run python src/seed.py                  # seed 4 sample projects
uv run python src/main.py                  # weekly analysis
uv run python src/main.py --synthesize     # monthly PPTX
```

## Architecture

| Layer | Tech |
|-------|------|
| UI | Streamlit |
| API | FastAPI + Uvicorn |
| Schedule | APScheduler |
| LLM | Groq (primary) / OpenRouter (fallback) |
| DB | SQLite (WAL mode) |
| Reports | python-pptx |

## API

- `GET /api/projects` - all projects with RAG data
- `GET /api/projects/{id}` - project detail
- `GET /api/reports` - generated reports
- `POST /api/reports/generate/weekly` - generate a weekly report
- `POST /api/reports/generate/monthly` - generate a monthly report
- `GET /api/health` - health check
