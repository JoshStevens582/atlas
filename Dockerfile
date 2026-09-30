# Atlas API image (FastAPI + the ingest worker that runs inside it).
# The React app is built and served by the web image (deploy/Dockerfile.web).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN pip install --no-cache-dir uv

WORKDIR /app

# Dependencies first, so editing code does not reinstall everything.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY sample_docs ./sample_docs
RUN uv sync --frozen --no-dev

# Run as a normal user, not root. /app/data holds SQLite, Chroma and uploads;
# it is a volume in docker-compose.yml so it survives rebuilds.
RUN useradd --system --uid 10001 --home-dir /app atlas \
    && mkdir -p /app/data \
    && chown -R atlas:atlas /app/data
USER atlas

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8787

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/health')"

# --forwarded-allow-ips "*": behind the proxy every request arrives from the
# proxy's address. Trusting X-Forwarded-For lets rate limits see the real client.
# Safe because this port is never published: only the web container can reach it.
CMD ["uvicorn", "atlas.main:app", "--host", "0.0.0.0", "--port", "8787", "--app-dir", "src", "--proxy-headers", "--forwarded-allow-ips", "*"]
