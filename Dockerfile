FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --root-user-action=ignore . \
    && useradd --system --uid 10001 --home /data monitor \
    && mkdir -p /data && chown monitor /data

USER monitor
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

# 0.0.0.0 inside the container only; compose.yaml publishes it on 127.0.0.1.
CMD ["uptime-monitor", "--config", "/config/config.toml", "--host", "0.0.0.0", "--port", "8000"]
