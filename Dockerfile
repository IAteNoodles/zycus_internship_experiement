FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY *.py ./
COPY database.py llm.py rag.py reports.py schedule.py seed.py sentiment.py ./
COPY projects.py ./

RUN pip install --no-cache-dir . && rm -rf /root/.cache

RUN mkdir -p /app/output /app/data && python seed.py 2>/dev/null || echo "Seed skipped (DB exists)"

EXPOSE 8000
EXPOSE 8501

CMD /bin/sh -c "uvicorn app:app --host 0.0.0.0 --port 8000 & sleep 1 && streamlit run app_ui.py --server.port 8501 --server.headless true --server.address 0.0.0.0"
