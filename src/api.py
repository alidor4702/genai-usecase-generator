"""FastAPI surface for the standalone web app.

Exposes the pipeline as an async-job HTTP API:

    POST /generate         → spawn a pipeline run, return run_id
    GET  /status/{run_id}  → poll current step + progress + final report
    GET  /events/{run_id}  → SSE stream of trace events as the pipeline runs
    GET  /report/{run_id}  → final Report (404 until completed)
    GET  /healthz          → liveness check

Per the locked stack (CLAUDE.md), this is the standalone-app surface only —
the Mistral Workflows runtime invokes the workflow class directly without
going through this HTTP layer.

Run state is held in-memory; production deployment with multiple workers
would migrate to Redis. For the take-home prototype, single-process is
sufficient — the standalone app and the API run on the same host.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.models import (
    CriteriaWeights,
    FocusArea,
    Report,
    ResearchDepth,
    WorkflowInput,
)
from src.pipeline import execute_pipeline
from src.trace import RunTrace, set_current_trace
from src.ui.render import render_report_to_markdown

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# Request / response schemas
# ----------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    """POST /generate body."""

    company_name: str = Field(min_length=1, max_length=200)
    focus_area: FocusArea = FocusArea.GENERAL
    weights: CriteriaWeights | None = None
    research_depth: ResearchDepth = ResearchDepth.MEDIUM


class GenerateResponse(BaseModel):
    run_id: str
    status: Literal["queued"]


class StatusResponse(BaseModel):
    run_id: str
    company_name: str
    status: Literal["queued", "running", "completed", "failed", "refused"]
    current_step: str
    progress_percent: float
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    refusal_reason: str | None = None
    event_count: int = 0
    fact_check_pass_rate: float | None = None
    meta_eval_confidence: float | None = None


class ReportResponse(BaseModel):
    run_id: str
    report: Report
    markdown: str


# ----------------------------------------------------------------------------
# In-memory run registry
# ----------------------------------------------------------------------------


class RunState:
    """One pipeline run's state. Mutated by the background task and read
    by the HTTP handlers. ContextVar threading inside execute_pipeline
    keeps each run's RunTrace isolated when multiple runs share the
    process."""

    def __init__(self, run_id: str, params: WorkflowInput) -> None:
        self.run_id = run_id
        self.params = params
        self.status: str = "queued"
        self.current_step = "initialized"
        self.progress_percent = 0.0
        self.started_at: datetime | None = None
        self.completed_at: datetime | None = None
        self.trace: RunTrace | None = None
        self.report: Report | None = None
        self.report_markdown: str | None = None
        # Append-only evidence ledger captured for the /grounding/<run_id>
        # endpoint. Same object the pipeline mutated; we hold a reference
        # after execute_pipeline returns so the FE can render every cited
        # source with title + URL + content excerpt.
        self.ledger: object | None = None
        self.error: str | None = None
        self.refusal_reason: str | None = None


_runs: dict[str, RunState] = {}


# ----------------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------------


app = FastAPI(
    title="GenAI Use Case Generator API",
    version="0.1.0",
    description=(
        "Thin HTTP shell over the Mistral Workflows pipeline. The Next.js "
        "standalone web app is the primary client; everything is also "
        "reachable via curl for ops + debugging."
    ),
)

# CORS — driven by CORS_ORIGINS env var (comma-separated) so deployment
# can lock to the Vercel frontend URL. Local dev with no env var falls
# back to "*" so curl + npm-dev work without ceremony.
import os as _os  # local import keeps the top-of-file imports tidy
_cors_env = _os.environ.get("CORS_ORIGINS", "").strip()
_cors_origins: list[str] = (
    [o.strip() for o in _cors_env.split(",") if o.strip()] if _cors_env else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,  # cookie-free FE; CORS_ORIGINS=["*"] requires this False
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest) -> GenerateResponse:
    run_id = str(uuid.uuid4())
    params = WorkflowInput(
        company_name=req.company_name,
        focus_area=req.focus_area,
        weights=req.weights or CriteriaWeights(),
        research_depth=req.research_depth,
    )
    _runs[run_id] = RunState(run_id, params)
    asyncio.create_task(_execute_run(run_id))
    logger.info("api: queued run %s for %s", run_id, params.company_name)
    return GenerateResponse(run_id=run_id, status="queued")


@app.get("/status/{run_id}", response_model=StatusResponse)
async def status(run_id: str) -> StatusResponse:
    state = _runs.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    fact_pass: float | None = None
    meta_conf: float | None = None
    if state.report is not None:
        fact_pass = state.report.quality.fact_check_pass_rate
        if state.report.meta_review is not None:
            meta_conf = state.report.meta_review.confidence
    return StatusResponse(
        run_id=run_id,
        company_name=state.params.company_name,
        status=state.status,  # type: ignore[arg-type]
        current_step=state.current_step,
        progress_percent=state.progress_percent,
        started_at=state.started_at,
        completed_at=state.completed_at,
        error=state.error,
        refusal_reason=state.refusal_reason,
        event_count=len(state.trace.events) if state.trace else 0,
        fact_check_pass_rate=fact_pass,
        meta_eval_confidence=meta_conf,
    )


@app.get("/report/{run_id}", response_model=ReportResponse)
async def report(run_id: str) -> ReportResponse:
    state = _runs.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    if state.report is None or state.report_markdown is None:
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id} not complete (status={state.status})",
        )
    return ReportResponse(
        run_id=run_id,
        report=state.report,
        markdown=state.report_markdown,
    )


@app.get("/grounding/{run_id}")
async def grounding(run_id: str) -> dict[str, object]:
    """Return the run's full evidence ledger so the FE can render an
    explorable grounding-data view. Each entry is the same EvidenceItem
    the pipeline cited from — id, source_kind, url, title, content,
    fetched_at_step.

    The FE links a use case's `evidence_ids` / `inspired_by` references
    here so reviewers can click a citation and see exactly what content
    the system grounded a claim in.
    """
    state = _runs.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    ledger = state.ledger
    entries: list[dict[str, object]] = []
    if ledger is not None and hasattr(ledger, "entries"):
        for ent in ledger.entries:  # type: ignore[attr-defined]
            entries.append(
                {
                    "id": ent.id,
                    "source_kind": ent.source_kind.value if hasattr(ent.source_kind, "value") else str(ent.source_kind),
                    "url": ent.url,
                    "title": ent.title,
                    "content": ent.content,
                    "fetched_at_step": ent.fetched_at_step,
                    "confidence": getattr(ent, "confidence", None),
                }
            )
    # Per-use-case index: list which evidence_ids each use case cited so
    # the FE can group entries under the use case that referenced them.
    by_use_case: list[dict[str, object]] = []
    if state.report is not None:
        for uc in state.report.top_use_cases:
            by_use_case.append(
                {
                    "id": uc.id,
                    "title": uc.title,
                    "evidence_ids": list(uc.evidence_ids),
                    "inspired_by": list(uc.inspired_by),
                }
            )
    return {
        "run_id": run_id,
        "company_name": state.params.company_name,
        "entries": entries,
        "by_use_case": by_use_case,
    }


@app.get("/events/{run_id}")
async def events(run_id: str) -> StreamingResponse:
    """Server-Sent Events stream of trace events as they arrive.

    Each event is `data: <JSON>\n\n` for a TraceEvent (step, actor, action,
    started_at, duration_ms, inputs/outputs summary). Final event is
    `event: done\ndata: <status>\n\n` and the stream closes.
    """

    async def event_generator() -> AsyncIterator[bytes]:
        last_idx = 0
        completed_emitted: set[str] = set()
        while True:
            state = _runs.get(run_id)
            if state is None:
                yield b"event: error\ndata: run not found\n\n"
                return
            # Emit trace events as they arrive AND when they complete.
            # `last_idx` advances past events we've emitted "start" for; the
            # `completed_emitted` set tracks events whose "complete" we've
            # already pushed. Net effect: each step produces a `step_start`
            # event when it begins, and a `step_complete` event when it ends —
            # so the live feed updates during work, not just after it.
            if state.trace is not None:
                trace_events = state.trace.events
                while last_idx < len(trace_events):
                    ev = trace_events[last_idx]
                    payload = json.dumps(
                        {
                            "id": ev.id,
                            "step": ev.step,
                            "actor": ev.actor,
                            "action": ev.action,
                            "started_at": ev.started_at.isoformat(),
                            "inputs_summary": ev.inputs_summary,
                        }
                    )
                    yield f"event: step_start\ndata: {payload}\n\n".encode("utf-8")
                    last_idx += 1
                # Re-scan the list for any completed events we haven't emitted
                # the completion for yet. Each complete carries the same `id`
                # as the start, so the frontend can update the in-progress
                # card in place.
                for ev in trace_events:
                    if ev.completed_at is not None and ev.id not in completed_emitted:
                        payload = json.dumps(
                            {
                                "id": ev.id,
                                "step": ev.step,
                                "actor": ev.actor,
                                "action": ev.action,
                                "started_at": ev.started_at.isoformat(),
                                "completed_at": ev.completed_at.isoformat(),
                                "duration_ms": ev.duration_ms,
                                "inputs_summary": ev.inputs_summary,
                                "outputs_summary": ev.outputs_summary,
                                "error": ev.error,
                            }
                        )
                        yield f"event: step_complete\ndata: {payload}\n\n".encode("utf-8")
                        completed_emitted.add(ev.id)
            # Emit step + progress every poll so the UI can render a progress
            # bar. Derive `step` from the latest trace event when possible —
            # state.current_step is set once at run start (api.py runs the
            # pipeline directly, not the workflow class, so nothing updates
            # it during execution).
            live_step = state.current_step
            if state.trace is not None and state.trace.events:
                live_step = state.trace.events[-1].step
            yield (
                f"event: progress\ndata: "
                f'{{"step":"{live_step}","progress":{state.progress_percent}}}\n\n'
            ).encode("utf-8")
            if state.status in ("completed", "failed", "refused"):
                yield f"event: done\ndata: {state.status}\n\n".encode("utf-8")
                return
            await asyncio.sleep(0.5)

    # Headers that disable buffering across the chain:
    #   X-Accel-Buffering: no   — tells nginx / Render proxy not to buffer
    #   Cache-Control: no-cache — prevents intermediaries from caching
    #   Connection: keep-alive  — long-lived connection
    # Combined with `text/event-stream` content type, this ensures every
    # `yield` flushes immediately to the browser.
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ----------------------------------------------------------------------------
# Background pipeline runner
# ----------------------------------------------------------------------------


async def _execute_run(run_id: str) -> None:
    state = _runs[run_id]
    state.status = "running"
    state.started_at = datetime.now(timezone.utc)
    state.current_step = "research"
    state.progress_percent = 5.0

    # Pre-create the run trace and store it on state BEFORE the pipeline
    # runs. This is what makes the SSE event stream live — the
    # /events/{run_id} handler reads state.trace.events as they're appended
    # by activities. If we waited for execute_pipeline to return and only
    # then assigned state.trace, the SSE handler would see no events for
    # the entire ~4-minute run and dump them all at once at the end.
    #
    # set_current_trace installs it in this task's ContextVar; the
    # execute_pipeline coroutine inherits the same context, and
    # start_run_trace() inside it will reuse our pre-created trace
    # instead of overwriting (see src/trace.py).
    state.trace = RunTrace(
        company_name=state.params.company_name,
        started_at=state.started_at,
    )
    set_current_trace(state.trace)

    try:
        result = await execute_pipeline(state.params)
        # result.trace is the same object we created above (start_run_trace
        # reused it via the contextvar), so events are already on state.trace.
        state.ledger = result.ledger
        if result.refused:
            state.status = "refused"
            state.refusal_reason = result.refusal_reason
            state.current_step = "refused"
            state.progress_percent = 100.0
        else:
            assert result.report is not None
            state.report = result.report
            state.report_markdown = render_report_to_markdown(result.report)
            state.status = "completed"
            state.current_step = "complete"
            state.progress_percent = 100.0
    except Exception as e:  # noqa: BLE001
        logger.exception("api: run %s failed", run_id)
        state.status = "failed"
        state.error = f"{type(e).__name__}: {e}"
        state.current_step = "failed"
    finally:
        state.completed_at = datetime.now(timezone.utc)
