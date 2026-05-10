"""Activity wrapper around src.ui.render — for the Mistral Workflows
sandbox compat path.

Why this exists
---------------
The Temporal sandbox the workflow class runs in re-imports `src.models`
in its own namespace. Pydantic v2 does strict `isinstance` checks. So:

    # In the workflow sandbox
    ctx: CompanyContext = await research_activity(...)   # CompanyContext_A
    Report(company=ctx, ...)                              # validates against CompanyContext_B
    # → ValidationError: "Input should be a valid dictionary or instance of CompanyContext"

Even though both are nominally the same class, they're loaded in
different namespaces (sandbox vs activity worker) so identity-check
fails. The fix is to do the typed-Report construction OUTSIDE the
sandbox — i.e. in an activity, where `src.models` is loaded once and
the imports survive across all activity invocations.

This activity takes the typed pieces, builds the Report, renders to
markdown, and returns the string. The workflow class never touches
the typed Report directly; it just gets back a `str` (which is
sandbox-safe to handle).
"""

from __future__ import annotations

import logging
from datetime import timedelta

import mistralai.workflows as workflows

from src.models import (
    CompanyContext,
    CriteriaWeights,
    EnrichedUseCase,
    FactCheckEntry,
    FocusArea,
    MetaEvalReview,
    QualitySignals,
    RejectedCandidate,
    Report,
    ResearchDepth,
)
from src.trace import trace_step

logger = logging.getLogger(__name__)


@workflows.activity(start_to_close_timeout=timedelta(seconds=30))
async def render_report_activity(
    ctx: CompanyContext,
    weights: CriteriaWeights,
    focus_area: FocusArea,
    research_depth: ResearchDepth,
    top_use_cases: list[EnrichedUseCase],
    rejected_appendix: list[RejectedCandidate],
    quality: QualitySignals,
    meta_review: MetaEvalReview | None,
    intro_text: str,
) -> dict[str, object]:
    """Construct the typed Report + render it. Pure compute, no I/O, but
    lives in an activity to dodge the workflow sandbox's Pydantic
    isinstance issue.

    Returns a plain dict (not a typed model) so the result crosses the
    activity boundary cleanly:

      full_markdown: str  — single-document Markdown for CLI / web /
                            persistence (mermaid blocks inline)
      chunks:        dict — structured pieces for per-use-case canvas
                            rendering on Le Chat (executive_summary,
                            list of use_cases each with body_md +
                            diagram_mermaid, verification_md)
    """
    from src.ui.render import render_report_to_chunks, render_report_to_markdown

    async with trace_step(
        "render",
        "render_report",
        "render_report",
        inputs_summary=f"company={ctx.identity.name!r} top_3={len(top_use_cases)}",
    ) as ev:
        report = Report(
            company=ctx,
            weights_used=weights,
            focus_area=focus_area,
            research_depth=research_depth,
            top_use_cases=top_use_cases,
            rejected_appendix=rejected_appendix,
            quality=quality,
            meta_review=meta_review,
            intro_text=intro_text,
        )
        full_md = render_report_to_markdown(report)
        chunks = render_report_to_chunks(report)
        ev.outputs_summary = f"{len(full_md)} bytes · {len(chunks['use_cases'])} use cases"  # type: ignore[arg-type]
    return {
        "full_markdown": full_md,
        "chunks": chunks,
    }


def _unused_imports() -> None:  # noqa: D401
    """Silences ruff F401 — these are used by Pydantic at activity-call
    serialisation time even when not referenced in this file's body."""
    _ = (FactCheckEntry,)
