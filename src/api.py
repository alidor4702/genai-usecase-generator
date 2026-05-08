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
from src.trace import RunTrace
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

# Permissive CORS for local dev. Tighten for production deploy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


@app.get("/events/{run_id}")
async def events(run_id: str) -> StreamingResponse:
    """Server-Sent Events stream of trace events as they arrive.

    Each event is `data: <JSON>\n\n` for a TraceEvent (step, actor, action,
    started_at, duration_ms, inputs/outputs summary). Final event is
    `event: done\ndata: <status>\n\n` and the stream closes.
    """

    async def event_generator() -> AsyncIterator[bytes]:
        last_idx = 0
        while True:
            state = _runs.get(run_id)
            if state is None:
                yield b"event: error\ndata: run not found\n\n"
                return
            # Emit any new trace events
            if state.trace is not None:
                trace_events = state.trace.events
                while last_idx < len(trace_events):
                    ev = trace_events[last_idx]
                    payload = json.dumps(
                        {
                            "step": ev.step,
                            "actor": ev.actor,
                            "action": ev.action,
                            "started_at": ev.started_at.isoformat(),
                            "completed_at": (
                                ev.completed_at.isoformat() if ev.completed_at else None
                            ),
                            "duration_ms": ev.duration_ms,
                            "inputs_summary": ev.inputs_summary,
                            "outputs_summary": ev.outputs_summary,
                            "error": ev.error,
                        }
                    )
                    yield f"data: {payload}\n\n".encode("utf-8")
                    last_idx += 1
            # Emit step + progress every poll so the UI can render a progress bar
            yield (
                f"event: progress\ndata: "
                f'{{"step":"{state.current_step}","progress":{state.progress_percent}}}\n\n'
            ).encode("utf-8")
            if state.status in ("completed", "failed", "refused"):
                yield f"event: done\ndata: {state.status}\n\n".encode("utf-8")
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ----------------------------------------------------------------------------
# Background pipeline runner
# ----------------------------------------------------------------------------


async def _execute_run(run_id: str) -> None:
    state = _runs[run_id]
    state.status = "running"
    state.started_at = datetime.now(timezone.utc)
    state.current_step = "research"
    state.progress_percent = 5.0
    try:
        result = await execute_pipeline(state.params)
        state.trace = result.trace  # populated, even on refusal
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
