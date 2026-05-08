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

from src.activities.compute_signals import compute_quality_signals_activity
from src.activities.generate import generate_candidates_activity
from src.activities.meta_evaluate import meta_evaluate_activity
from src.activities.research import (
    enrich_company_context_activity,
    gather_bundle_for_company,
    research_company_activity,
)
from src.activities.retrieve import retrieve_precedents_activity
from src.activities.score import score_candidates_activity
from src.activities.select_enrich import select_and_enrich_activity
from src.activities.verify_per_candidate import verify_top_candidates_activity
from src.config import settings
from src.models import (
    CriteriaWeights,
    FocusArea,
    Report,
    ResearchDepth,
    VerificationBatch,
    VerificationResult,
    VerificationVerdict,
    WorkflowInput,
)
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

    log.info("=== Step 1: Research ===")
    ctx = await research_company_activity(params.company_name, params.research_depth)
    log.info(
        "research_confidence=%.2f | is_verified=%s | industry=%s | sources=%s",
        ctx.meta.research_confidence,
        ctx.meta.is_verified,
        ctx.classification.industry,
        ctx.meta.research_sources,
    )

    log.info("=== Step 1b: Context completion (gap-fill) ===")
    ctx = await enrich_company_context_activity(ctx)
    log.info(
        "after enrich: confidence=%.2f | industry=%s | priorities=%d | data_assets=%d",
        ctx.meta.research_confidence,
        ctx.classification.industry,
        len(ctx.strategic_context.stated_priorities),
        len(ctx.data_and_tech.likely_data_assets),
    )

    confidence_ok = (
        ctx.meta.research_confidence >= settings.research_confidence_threshold
        or ctx.meta.is_verified
        or len(ctx.existing_ai_initiatives) > 0
    )
    if not confidence_ok:
        log.warning("Refusal: research signal too sparse for %s", params.company_name)
        print(f"\nREFUSAL: Couldn't find enough information about {params.company_name}.")
        return 2

    log.info("=== Step 2: Retrieve precedents ===")
    retrieved = await retrieve_precedents_activity(ctx, settings.top_k_precedents)
    log.info("retrieved %d precedents", len(retrieved.items))

    log.info("=== Step 3: Generate 12 candidates ===")
    bundle = await gather_bundle_for_company(params.company_name, params.research_depth)
    batch = await generate_candidates_activity(
        ctx, retrieved, params.focus_area.value, True, raw_bundle=bundle
    )
    log.info(
        "generated %d candidates | diversity=%.3f | regenerated=%s",
        len(batch.candidates),
        batch.diversity_score,
        batch.regenerated_for_diversity,
    )

    log.info("=== Step 4: Score (self-consistency, 2 passes) ===")
    scored = await score_candidates_activity(batch, ctx, params.weights)
    log.info("top-3 aggregate scores: %s", [round(s.aggregate_score, 2) for s in scored.scored[:3]])

    log.info("=== Step 5: Per-candidate verification of top-3 ===")
    top_3 = scored.scored[:3]
    verified = await verify_top_candidates_activity(top_3, ctx, params.company_name)
    log.info("verdicts: %s", [(r.candidate_id, r.verdict.value) for r in verified.results])

    log.info("=== Step 6: Select and enrich ===")
    enriched_uses, rejected = await select_and_enrich_activity(scored, verified, ctx)
    log.info("enriched %d use cases | %d rejected", len(enriched_uses), len(rejected))

    log.info("=== Step 7: Meta-evaluation ===")
    review, fact_claims = await meta_evaluate_activity(enriched_uses, rejected, ctx)
    log.info(
        "ready=%s | confidence=%.2f | weakest=%s",
        review.sales_engineer_ready,
        review.confidence,
        review.weakest_use_case_id,
    )

    # Targeted regeneration of the weakest use case if confidence below
    # threshold (one round only — bounded cost, matches architecture spec).
    original_confidence = review.confidence
    regenerated_use_case_id: str | None = None
    if review.confidence < settings.meta_eval_confidence_threshold and review.weakest_use_case_id:
        log.info(
            "=== Step 7b: Regeneration (weakest use case = %s) ===",
            review.weakest_use_case_id,
        )
        weak_id = review.weakest_use_case_id
        # Find the weakest use case in the current top-3 and re-enrich JUST that one
        weak_idx = next((i for i, u in enumerate(enriched_uses) if u.id == weak_id), None)
        if weak_idx is not None:
            # Force the weakest one OUT of the verified set so select_and_enrich
            # promotes the next-highest near-miss in its place. Mark it as
            # confirmed_existing — that's the verdict select_and_enrich uses to drop.
            forced_drop = VerificationBatch(
                results=[
                    r
                    if r.candidate_id != weak_id
                    else VerificationResult(
                        candidate_id=weak_id,
                        verdict=VerificationVerdict.CONFIRMED_EXISTING,
                        rationale="Marked for regen — meta-eval flagged as weakest.",
                    )
                    for r in verified.results
                ]
            )
            new_uses, new_rejected = await select_and_enrich_activity(scored, forced_drop, ctx)
            review2, fact_claims2 = await meta_evaluate_activity(new_uses, new_rejected, ctx)
            log.info(
                "regen result: confidence %.2f → %.2f | ready=%s",
                original_confidence,
                review2.confidence,
                review2.sales_engineer_ready,
            )
            # Keep the regen if it actually improved confidence
            if review2.confidence > original_confidence:
                enriched_uses = new_uses
                rejected = new_rejected
                review = review2
                fact_claims = fact_claims2
                regenerated_use_case_id = weak_id
            else:
                log.info("regen did not improve confidence, keeping original")

    log.info("=== Quality signals ===")
    signals = await compute_quality_signals_activity(enriched_uses, ctx, fact_claims)
    if regenerated_use_case_id:
        log.info(
            "regen footer: original_confidence=%.2f, regenerated=%s, final_confidence=%.2f",
            original_confidence,
            regenerated_use_case_id,
            review.confidence,
        )

    intro = (
        f"GenAI use cases for {ctx.identity.name}: three customer-ready proposals "
        f"scored against the five-criteria rubric and verified against "
        f"{len(ctx.existing_ai_initiatives)} existing AI initiatives discovered during research."
    )
    report = Report(
        company=ctx,
        weights_used=params.weights,
        focus_area=params.focus_area,
        research_depth=params.research_depth,
        top_use_cases=enriched_uses,
        rejected_appendix=rejected,
        quality=signals,
        meta_review=review,
        intro_text=intro,
    )

    md = render_report_to_markdown(report)
    if write_md:
        write_md.parent.mkdir(parents=True, exist_ok=True)
        write_md.write_text(md, encoding="utf-8")
        log.info("wrote markdown report to %s", write_md)
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
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if not args.verbose else logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Quiet a few noisy ones
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("mistralai").setLevel(logging.WARNING)

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
