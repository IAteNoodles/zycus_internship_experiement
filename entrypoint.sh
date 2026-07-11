#!/bin/bash
set -e

echo "[entrypoint] Starting API server..."
uvicorn api:app --host 0.0.0.0 --port 8000 &

echo "[entrypoint] Starting Streamlit UI (with scheduler)..."
exec streamlit run src/ui.py --server.port 8501 --server.address 0.0.0.0
