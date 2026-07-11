FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY . .

RUN python seed.py

EXPOSE 8000

ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
