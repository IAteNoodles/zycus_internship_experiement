#!/bin/bash
set -e

echo "[entrypoint] Starting API server..."
uvicorn --app-dir src api:app --host 0.0.0.0 --port 8000 &

echo "[entrypoint] Starting Streamlit UI..."
exec streamlit run src/ui.py --server.port 8501 --server.address 0.0.0.0
