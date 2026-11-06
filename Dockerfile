FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    ETF_HOST=0.0.0.0 \
    ETF_PORT=8787 \
    ETF_DB_PATH=/data/etf_premium.db \
    ETF_TZ=Asia/Shanghai \
    ETF_LOG_FILE=/data/collect.log

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
RUN mkdir -p /data \
    && useradd -m -u 10001 app \
    && chown -R app:app /app /data

USER app
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
  CMD python3 -c "import sys,urllib.request;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/api/health',timeout=3).status==200 else 1)"

CMD ["python3", "server.py"]
