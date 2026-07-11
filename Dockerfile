FROM python:3.11-slim
WORKDIR /app

ENV ZYCLUS_DB=/app/data/zycus.db \
    ZYCLUS_OUTPUT_DIR=/app/output

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir . && rm -rf /root/.cache

COPY .env ./

RUN mkdir -p /app/output /app/data

EXPOSE 8000
EXPOSE 8501

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
