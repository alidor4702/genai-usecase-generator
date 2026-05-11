# Deployment

Two-surface deploy: FastAPI backend on **Render**, Next.js frontend on
**Vercel**. Both have free tiers that comfortably handle the demo load.

## Backend — Render

1. **Fork or clone** this repo to your GitHub account.
2. **Render dashboard** → "New" → "Blueprint" → connect the GitHub repo.
   Render reads [`render.yaml`](../render.yaml) and provisions a free-tier
   web service in Frankfurt (close to Mistral's EU infra).
3. **Set secrets in Render** (the dashboard's Environment tab):
   - `MISTRAL_API_KEY` — required, from Mistral AI Studio
   - `TAVILY_API_KEY` — required, from tavily.com (free tier sufficient)
   - `TIER` — optional, defaults to `standard` (alternatives: `fast`, `max`)
4. **First deploy**: Render builds the Docker image (or uses the Python
   buildpack with `uv sync --frozen`), then runs uvicorn on `$PORT`.
   Health check at `/healthz` confirms boot. Cold-start is ~20s on the
   free tier.
5. **Public URL**: Render assigns `https://genai-usecase-generator-api.onrender.com`
   (or your chosen subdomain).

Free-tier note: Render free services sleep after 15 min idle. First
request after sleep takes ~30s to wake. Acceptable for a demo;
upgrade to a paid plan for production traffic.

## Frontend — Vercel

1. **Vercel dashboard** → "New Project" → import the same GitHub repo.
2. **Configure**:
   - Root directory: `standalone`
   - Framework preset: Next.js (auto-detected)
   - Install command: `npm install` (default)
   - Build command: `npm run build` (default)
3. **Environment variables**:
   - `API_URL` — set to your Render backend URL (e.g.
     `https://genai-usecase-generator-api.onrender.com`).
     The Next.js app's `next.config.mjs` uses this to rewrite `/api/*`
     requests to the backend, avoiding CORS in the browser.
4. **Deploy**. Vercel assigns `https://genai-usecase-generator.vercel.app`
   on first deploy. Subsequent commits to `main` auto-deploy.

## Local Docker (optional)

```bash
docker build -t genai-api .
docker run -p 8000:8000 \
  -e MISTRAL_API_KEY=$MISTRAL_API_KEY \
  -e TAVILY_API_KEY=$TAVILY_API_KEY \
  genai-api
```

Then `cd standalone && API_URL=http://localhost:8000 npm run dev`.

## What deploys where

| Component | Location | Why |
|---|---|---|
| FastAPI surface (`src/api.py`) | Render | Persistent server, async background tasks for pipeline runs, SSE streaming endpoint |
| Next.js standalone web app (`standalone/`) | Vercel | Native Next.js support, edge CDN, free-tier generous |
| Mistral Workflows worker (`src/workflow.py`) | Mistral AI Studio | Workers register with the Mistral runtime via `mistralai workflows register`; not deployed by us |
| SQLite data layer (`data/genai_usecases.db`) | Render container | Persistent across requests within a single container; production migration path is Postgres + pgvector |

## Production hardening (out of scope for this MVP)

- Tighten CORS origins in `src/api.py` to the actual Vercel URL
- Replace in-memory `_runs` dict with Redis for multi-worker scaling
- Add structured logging + per-request traces (OpenTelemetry — Mistral
  Workflows runtime already integrates with OTLP)
- Migrate SQLite → Postgres + pgvector (the cache + corpus tables)
- Add a rate limiter (`slowapi`) on `/generate` to bound Mistral API spend
- Health check that pings the Mistral API and the cache, not just liveness
