# Publishing the workflow as a Le Chat assistant

The whole point of building on Mistral Workflows is the Le Chat surface:
once the workflow is registered with Mistral's runtime and exposed as an
assistant, anyone with a Le Chat account can search for it, install it,
and use it inline in chat. This document walks through the publication
flow.

## Prerequisites

1. **Mistral AI Studio account** with workflow access enabled. If your
   org doesn't have it, email
   [paul.devillers@mistral.ai](mailto:paul.devillers@mistral.ai) /
   [maxime.langelier@mistral.ai](mailto:maxime.langelier@mistral.ai)
   referencing the take-home.
2. **`MISTRAL_API_KEY`** in `.env` — the key authenticates the worker
   when it registers the workflow with the runtime.
3. **`TAVILY_API_KEY`** in `.env` — the workflow uses Tavily for news +
   per-candidate verification + web_search tool calls.

## 1 — Run the worker

The worker is what actually executes pipeline steps when the assistant is
invoked. It registers `GenAIUseCaseWorkflow` with Mistral's runtime and
then polls for task assignments.

```bash
uv run python -m scripts.run_worker
```

You should see something like:

```
INFO  run_worker  starting Mistral Workflows worker for 'GenAIUseCaseWorkflow' (tier=standard)
INFO  mistralai.workflows.core.worker  worker registered, polling for tasks…
```

While this is running, the workflow is invokable. Stop with Ctrl-C; the
assistant becomes unresponsive until the worker comes back.

For production, the worker is deployed alongside the FastAPI service on
Render (see [`docs/deploy.md`](deploy.md) — extend the `render.yaml`
`services:` block with a second `worker` service running
`scripts.run_worker` as its `startCommand`).

## 2 — Publish to Le Chat

With the worker running:

1. **Mistral AI Studio** → Workflows → find `genai-usecase-generator`
   in the list (the name comes from the `@workflow.define(name=...)`
   decorator in `src/workflow.py`).
2. Click **Publish to Le Chat**. Set:
   - **Display name:** "GenAI Use Case Generator"
   - **Description:** "Three relevant, iconic, high-impact GenAI use
     cases for any company, grounded in 2,150+ peer deployments."
   - **Visibility:** Public (or Private to your org if you want a
     limited demo)
3. Save. The assistant appears in Le Chat's assistants directory within
   a few minutes.

## 3 — Use it in Le Chat

1. Open Le Chat → Assistants → search "GenAI Use Case Generator" → Install.
2. In a new chat, the assistant appears in the assistant picker.
3. Type a company name (e.g. `Carrefour`).
4. The assistant runs Step 0 first (`ConfirmationInput` with default
   vs custom config). Click "Use defaults" to proceed, or "Customize"
   to type a custom focus + weight string.
5. The pipeline executes — research → retrieve → generate → score →
   verify → enrich → meta-eval → render — and Le Chat displays the
   final report as a Rich UI Component composition (Cards + Badges +
   PieChart + mermaid blueprints).

End-to-end wall time: ~3-5 minutes at `tier=standard`. Le Chat shows the
worker's TodoListItem progress while it runs.

## Updating the published assistant

Any commit that changes `src/workflow.py`, `src/activities/*`, or
`src/prompts.py` requires a worker restart to pick up the changes. The
assistant in Le Chat continues to work — Mistral routes new invocations
to whatever worker version is currently registered.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "Workflow not found" in Le Chat | Worker isn't running or registered against the wrong API key |
| Assistant hangs at "research" | `TAVILY_API_KEY` missing — research sub-tasks (news, verification) silently no-op without it |
| Assistant returns immediately with refusal | Research confidence below threshold for an obscure company — try the legal name or parent brand |
| Worker logs `WORKFLOW_EXECUTION_TIMED_OUT` | Pipeline exceeded the 15-minute `execution_timeout` (set in `@workflow.define`). Usually means an activity is stuck — check `docs/limits.md` for per-activity timeouts |
