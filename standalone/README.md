# Standalone web app

Next.js 14 (App Router, TypeScript strict, Tailwind) thin client over the
FastAPI surface defined in `src/api.py`. Renders the same Mistral
Workflows pipeline output the Le Chat assistant produces, with live
progress streaming and architecture-blueprint diagrams.

## Stack

- Next.js 14 + React 18 + TypeScript strict
- Tailwind CSS for styling (dark theme, Mistral-orange accent)
- `react-markdown` + `remark-gfm` for the report body
- `mermaid` (lazy-loaded client-side) for blueprint diagrams
- `EventSource` for SSE progress streaming
- `recharts` reserved for cost-distribution pie chart (post-MVP)

## Run locally

```bash
# 1. start the FastAPI backend (in repo root)
uv run uvicorn src.api:app --reload --port 8000

# 2. start the Next dev server (in standalone/)
cd standalone
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Type a company name,
optionally tweak focus area / weights, click **Generate**. The progress feed
streams pipeline activity live; the report renders when complete.

`next.config.mjs` rewrites `/api/*` to `http://localhost:8000` by default.
Override via `API_URL=https://my-deployed-backend.com npm run dev`.
