"""Per-run pipeline action trace.

Captures every observable action across the pipeline with timestamps so we
can render a behavior blueprint per company run: which agent did what, when,
in what order, who they talked to, and how long each step took.

The trace lives behind a `ContextVar` so activities can record events without
threading the trace through every signature. Activities use the `trace_step`
context manager:

    async with trace_step("research", "wikipedia", "fetch_wikipedia_facts",
                          inputs="company=Carrefour"):
        result = await fetch_wikipedia_facts(...)

After the run, render the trace to markdown + mermaid sequence diagram for
inspection. See `RunTrace.render_markdown()` and `RunTrace.render_mermaid()`.
"""

from __future__ import annotations

import contextvars
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import uuid
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TraceEvent(BaseModel):
    """One recorded action in the pipeline."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    step: str  # high-level pipeline step: "research", "retrieve", "generate", ...
    actor: str  # what executed it: "mistral-medium-2604", "tavily", "wikipedia", ...
    action: str  # what was done: "chat.complete", "search", "fetch_html", ...
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: float | None = None
    inputs_summary: str | None = None  # short text describing the inputs
    outputs_summary: str | None = None  # short text describing the outputs
    error: str | None = None  # exception type name if the step failed
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunTrace(BaseModel):
    """Full trace for one company-run of the pipeline."""

    company_name: str
    started_at: datetime
    events: list[TraceEvent] = Field(default_factory=list)

    def add(self, event: TraceEvent) -> None:
        self.events.append(event)

    @property
    def total_duration_ms(self) -> float:
        if not self.events:
            return 0.0
        first = self.started_at
        last = max(
            (e.completed_at for e in self.events if e.completed_at is not None),
            default=first,
        )
        return (last - first).total_seconds() * 1000.0

    def render_markdown(self) -> str:
        """Render the trace as a chronological markdown timeline + per-step
        totals. Suitable for inspection alongside the report."""
        lines: list[str] = [
            f"## Execution trace — {self.company_name}",
            "",
            f"Started: `{self.started_at.isoformat()}`. "
            f"Total wall time: `{self.total_duration_ms / 1000:.1f}s` "
            f"across `{len(self.events)}` recorded actions.",
            "",
            "### Per-step time totals",
            "",
        ]
        # Aggregate per step
        per_step: dict[str, list[float]] = {}
        for e in self.events:
            if e.duration_ms is None:
                continue
            per_step.setdefault(e.step, []).append(e.duration_ms)
        lines.append("| Step | Calls | Total time | Avg time |")
        lines.append("|---|---:|---:|---:|")
        for step, durs in per_step.items():
            total_s = sum(durs) / 1000.0
            avg_ms = sum(durs) / len(durs) if durs else 0
            lines.append(f"| `{step}` | {len(durs)} | {total_s:.2f}s | {avg_ms:.0f}ms |")
        lines.append("")
        lines.append("### Chronological event log")
        lines.append("")
        for e in self.events:
            ts = e.started_at.strftime("%H:%M:%S.%f")[:-3]
            dur = f"{e.duration_ms:.0f}ms" if e.duration_ms is not None else "(in flight)"
            err_marker = " ❌" if e.error else ""
            lines.append(f"- `{ts}` **[{e.step}]** `{e.actor}.{e.action}`{err_marker} — {dur}")
            if e.inputs_summary:
                lines.append(f"   - inputs: {e.inputs_summary}")
            if e.outputs_summary:
                lines.append(f"   - outputs: {e.outputs_summary}")
            if e.error:
                lines.append(f"   - error: `{e.error}`")
        return "\n".join(lines)

    def render_blueprint_flowchart(self) -> str:
        """Render a static mermaid flowchart of the pipeline architecture.

        Doesn't depend on run data — shows the canonical step sequence and
        the agents involved at each step. Useful for orientation before
        reading the chronological event log.
        """
        return (
            "flowchart TD\n"
            "    Start([User: company name]) --> R\n"
            "    R[\"<b>1. Research</b><br/>parallel: Wikipedia · Tavily news ·<br/>existing initiatives · live verification<br/><i>mistral-medium</i> synthesis\"]\n"
            "    R --> G[\"<b>1b. Gap-fill</b><br/>identify missing fields →<br/><i>mistral-small</i> generates queries →<br/>Tavily search per field →<br/><i>mistral-small</i> per-field extraction\"]\n"
            "    G --> Conf{Confidence ≥ 0.5<br/>OR verified<br/>OR ≥ 1 existing initiative?}\n"
            "    Conf -->|no| Refuse([Refusal])\n"
            "    Conf -->|yes| Ret\n"
            "    Ret[\"<b>2. Retrieve</b><br/><i>mistral-embed</i> on company query →<br/>cosine top-K + industry/depth filter +<br/>MMR diversification\"]\n"
            "    Ret --> Gen[\"<b>3. Generate candidates</b><br/><i>mistral-medium</i> with web_search tool<br/>(8 by default, configurable) — fact citations<br/>flow into the EvidenceLedger\"]\n"
            "    Gen --> Score[\"<b>4. Score</b><br/>self-consistency: 2 parallel passes<br/><i>mistral-small</i> @ T=0.2 and T=0.4 →<br/>aggregate weighted scores\"]\n"
            "    Score --> Verify[\"<b>5. Per-candidate verify</b><br/>top-3: Tavily search + deep-read +<br/><i>mistral-small</i> verdict (pass /<br/>partial / confirmed_existing)\"]\n"
            "    Verify --> Enrich[\"<b>6. Enrich</b><br/><i>mistral-large</i> drafts customer-facing<br/>prose for 3 use cases (or<br/><i>mistral-medium</i> on fast tier)\"]\n"
            "    Enrich --> NumScrub[\"<b>6a. Numeric scrub</b><br/>flag any number not in cited content\"]\n"
            "    NumScrub --> Polish[\"<b>6b. Polish</b><br/><i>mistral-small</i> per use case (parallel):<br/>convert markers→qualitative,<br/>(ev-XXX)→[title](url)\"]\n"
            "    Polish --> Attr[\"<b>6c. Attribution check</b><br/><i>mistral-small</i> per use case (parallel):<br/>fix corpus-ID ↔ company mismatches\"]\n"
            "    Attr --> Meta[\"<b>7. Meta-evaluation</b><br/><i>mistral-medium</i> reviews report against<br/>cited precedents + ledger entries:<br/>strict claim verification\"]\n"
            "    Meta --> WV[\"<b>7c. Web-verify rescue</b><br/>2-tier credibility classifier<br/>(verified allowlist · corroborated)\"]\n"
            "    WV --> J[\"<b>7d. Source judge</b><br/><i>mistral-small</i> adjudicates each<br/>(claim, source) pair · self-correcting\"]\n"
            "    J --> FQ[\"<b>7e. Final qualify</b><br/><i>mistral-small</i> rewrites unsupported<br/>numerics into qualitative phrasing\"]\n"
            "    FQ --> QS\n"
            "    QS[\"<b>Quality signals</b><br/>LLM-graded diversity + specificity,<br/>fact-check pass rate, TTV/cost spread\"]\n"
            "    QS --> Render([Markdown report + trace])\n"
            "\n"
            "    classDef agent fill:#fef3c7,stroke:#f59e0b\n"
            "    classDef gate fill:#dbeafe,stroke:#3b82f6\n"
            "    classDef io fill:#dcfce7,stroke:#16a34a\n"
            "    class R,G,Ret,Gen,Score,Verify,Enrich,NumScrub,Polish,Attr,Meta,WV,J,FQ,QS agent\n"
            "    class Conf gate\n"
            "    class Start,Refuse,Render io\n"
        )

    def render_mermaid(self) -> str:
        """Render a mermaid sequence diagram showing the agent flow."""
        # Discover unique actors in order of first appearance
        actor_order: list[str] = []
        for e in self.events:
            if e.actor not in actor_order:
                actor_order.append(e.actor)

        lines: list[str] = ["sequenceDiagram", "    autonumber"]
        # Define participants in stable order
        participants = ["pipeline"] + actor_order
        for p in participants:
            safe = _safe_id(p)
            lines.append(f"    participant {safe} as {p}")

        for e in self.events:
            actor_id = _safe_id(e.actor)
            label_parts = [f"{e.step}: {e.action}"]
            if e.duration_ms is not None:
                label_parts.append(f"({e.duration_ms:.0f}ms)")
            if e.error:
                label_parts.append("ERR")
            label = " ".join(label_parts)
            # Truncate long labels for mermaid readability
            label = label[:80].replace("\"", "'")
            lines.append(f"    pipeline->>{actor_id}: {label}")
            if e.outputs_summary:
                short_out = e.outputs_summary[:60].replace("\"", "'")
                lines.append(f"    {actor_id}-->>pipeline: {short_out}")
        return "\n".join(lines)


def _safe_id(name: str) -> str:
    """Mermaid participant IDs need to be alphanumeric/underscore."""
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name) or "anon"


# ContextVar so activities can record events without threading the trace
# through every signature. Set by the workflow / CLI at run start.
_current_trace: contextvars.ContextVar[RunTrace | None] = contextvars.ContextVar(
    "current_run_trace", default=None
)


def get_current_trace() -> RunTrace | None:
    return _current_trace.get()


def set_current_trace(t: RunTrace | None) -> None:
    _current_trace.set(t)


def start_run_trace(company_name: str) -> RunTrace:
    """Initialize a trace for a company run and install it as current.

    If a trace is already set in the current async context (e.g. the
    FastAPI surface pre-created one so its SSE handler can read events
    in real time), reuse that one instead of overwriting. This is what
    makes live progress streaming work — `_execute_run` in api.py
    creates the RunTrace, stores it on the run state, sets it via
    set_current_trace, and then awaits execute_pipeline; if this
    function blindly overwrote the contextvar, activities would write
    into a fresh trace the API has no reference to.
    """
    existing = get_current_trace()
    if existing is not None:
        return existing
    t = RunTrace(company_name=company_name, started_at=datetime.now(timezone.utc))
    set_current_trace(t)
    return t


@asynccontextmanager
async def trace_step(
    step: str,
    actor: str,
    action: str,
    inputs_summary: str | None = None,
    metadata: dict[str, Any] | None = None,
):
    """Async context manager that records start + end of an action.

    Usage:
        async with trace_step("research", "wikipedia", "fetch_facts",
                              inputs_summary="company=Carrefour") as ev:
            result = await fetch_wikipedia_facts(...)
            ev.outputs_summary = f"found={result.found}"

    No-op if there's no active trace (e.g. the workflow runtime didn't
    initialize one).
    """
    trace = get_current_trace()
    if trace is None:
        # Yield a stub so callers can still set fields without errors
        stub = TraceEvent(
            step=step,
            actor=actor,
            action=action,
            started_at=datetime.now(timezone.utc),
            metadata=metadata or {},
            inputs_summary=inputs_summary,
        )
        yield stub
        return

    started = datetime.now(timezone.utc)
    event = TraceEvent(
        step=step,
        actor=actor,
        action=action,
        started_at=started,
        metadata=metadata or {},
        inputs_summary=inputs_summary,
    )
    # Append at START so SSE can show the event live ("running") instead of
    # only emitting it after the activity completes. The same event object is
    # mutated in place at finish; the SSE handler re-emits when completed_at
    # transitions from None → a timestamp.
    trace.add(event)
    try:
        yield event
    except Exception as exc:  # noqa: BLE001 — record & re-raise
        event.error = type(exc).__name__
        raise
    finally:
        completed = datetime.now(timezone.utc)
        event.completed_at = completed
        event.duration_ms = (completed - started).total_seconds() * 1000.0
