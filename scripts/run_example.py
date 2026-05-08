"""CLI entry point — run the full pipeline against a company name.

Usage:
    uv run python -m scripts.run_example "Carrefour"
    uv run python -m scripts.run_example "BNP Paribas" --focus operations
    uv run python -m scripts.run_example "Veolia" --depth high
    uv run python -m scripts.run_example "L'Oreal" --weights 0.15,0.30,0.20,0.15,0.20

This bypasses the Mistral Workflows runtime and calls each activity directly
in sequence — useful for fast end-to-end testing and for the CLI demo. The
real workflow class lives in `src/workflow.py` and is what would be registered
with a Workflows worker for the Le Chat publish.

The activity functions are decorated with `@workflows.activity` but are also
plain awaitable async functions, so they can be called directly here.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from src.config import Tier, settings
from src.models import (
    CriteriaWeights,
    FocusArea,
    ResearchDepth,
    WorkflowInput,
)
from src.pipeline import execute_pipeline
from src.ui.render import render_report_to_markdown

logger = logging.getLogger(__name__)


def _parse_weights(s: str | None) -> CriteriaWeights:
    if not s:
        return CriteriaWeights()
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) != 5:
        raise argparse.ArgumentTypeError(
            "weights must be 5 comma-separated floats: relevance,iconic,impact,feasibility,mistral"
        )
    try:
        f = [float(p) for p in parts]
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"weights must be floats: {e}") from e
    return CriteriaWeights(
        relevance=f[0],
        iconic_potential=f[1],
        estimated_impact=f[2],
        feasibility=f[3],
        mistral_suitability=f[4],
    )


async def run_pipeline(params: WorkflowInput, write_md: Path | None) -> int:
    log = logging.getLogger("run_example")

    result = await execute_pipeline(params)

    if result.refused:
        log.warning("Refusal: %s", result.refusal_reason)
        print(f"\nREFUSAL: {result.refusal_reason}")
        return 2

    assert result.report is not None  # narrowing for type-checker
    md = render_report_to_markdown(result.report)
    trace = result.trace
    if write_md:
        write_md.parent.mkdir(parents=True, exist_ok=True)
        write_md.write_text(md, encoding="utf-8")
        log.info("wrote markdown report to %s", write_md)
        # Render the trace alongside the report
        trace_path = write_md.with_name(write_md.stem + "_trace.md")
        trace_md = (
            "# Pipeline blueprint (architecture)\n\n"
            "Static view of the pipeline regardless of run timing — shows agents,\n"
            "models, and gates. The chronological execution log follows below.\n\n"
            "```mermaid\n"
            + trace.render_blueprint_flowchart()
            + "```\n\n"
            + trace.render_markdown()
            + "\n\n## Mermaid sequence diagram (execution)\n\n```mermaid\n"
            + trace.render_mermaid()
            + "\n```\n"
        )
        trace_path.write_text(trace_md, encoding="utf-8")
        log.info("wrote trace to %s", trace_path)
    print()
    print(md)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the GenAI Use Case Generator pipeline end-to-end"
    )
    parser.add_argument("company_name")
    parser.add_argument(
        "--focus",
        choices=[f.value for f in FocusArea],
        default=FocusArea.GENERAL.value,
    )
    parser.add_argument(
        "--depth",
        choices=[d.value for d in ResearchDepth],
        default=ResearchDepth.MEDIUM.value,
    )
    parser.add_argument(
        "--weights",
        type=_parse_weights,
        default=None,
        help="comma-separated weights: relevance,iconic,impact,feasibility,mistral (default 0.2 each)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="optional markdown output path (e.g. docs/examples/carrefour.md)",
    )
    parser.add_argument(
        "--tier",
        choices=[t.value for t in Tier],
        default=Tier.STANDARD.value,
        help="performance tier: fast (~60-90s), standard (~2-3 min, default), max (~5-7 min)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if not args.verbose else logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Quiet a few noisy ones
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("mistralai").setLevel(logging.WARNING)

    # Apply tier override at startup so activities see the right setting
    settings.tier = Tier(args.tier)
    logger.info("tier set to %s", settings.tier.value)

    params = WorkflowInput(
        company_name=args.company_name,
        focus_area=FocusArea(args.focus),
        weights=args.weights or CriteriaWeights(),
        research_depth=ResearchDepth(args.depth),
    )
    rc = asyncio.run(run_pipeline(params, args.out))
    return rc


if __name__ == "__main__":
    sys.exit(main())
