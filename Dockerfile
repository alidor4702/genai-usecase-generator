# Dockerfile for the FastAPI backend (and optionally the workflow worker).
#
# Render uses this when its native Python buildpack isn't a perfect fit;
# Fly.io / Railway / generic Kubernetes also build from it directly.
# Vercel deploys the Next.js app separately via its own pipeline.

FROM python:3.12-slim

# uv is the package manager (locked in pyproject.toml + uv.lock).
RUN pip install --no-cache-dir uv

# System packages selectolax wants for HTML parsing.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first (cached layer) — pyproject + lock only.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Then the source.
COPY src ./src
COPY scripts ./scripts
COPY data ./data

# uvicorn binds to $PORT for Render / Railway / Fly.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uv run uvicorn src.api:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips '*'"]
