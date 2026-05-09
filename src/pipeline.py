"""End-to-end pipeline runner — single source of truth shared by the CLI
(`scripts/run_example.py`) and the FastAPI surface (`src/api.py`).

The Mistral Workflows runtime invokes activities directly via the
`GenAIUseCaseWorkflow` orchestrator class. This module provides the SAME
seven-step sequence as a plain async function, with no Workflows runtime
dependency, so:

  - `run_example.py` can run end-to-end locally without a Workflows worker
  - `src/api.py` can spawn pipelines as background asyncio tasks for the
    standalone web app

The Workflows orchestrator (`src/workflow.py`) and this function are
deliberately parallel implementations: the workflow class adds replay-safe
state tracking + interactive Step 0 + Le Chat output rendering, while this
module is pure pipeline execution. Activity logic is shared — both call
the same `*_activity` functions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.activities.compute_signals import compute_quality_signals_activity
from src.activities.generate import generate_candidates_activity
from src.activities.meta_evaluate import meta_evaluate_activity
from src.activities.web_verify import web_verify_unsupported_claims_activity
from src.activities.research import (
    enrich_company_context_activity,
    gather_bundle_for_company,
    research_company_activity,
)
from src.activities.retrieve import retrieve_precedents_activity
from src.activities.score import score_candidates_activity
from src.activities.select_enrich import (
    regen_one_use_case_activity,
    select_and_enrich_activity,
)
from src.activities.verify_per_candidate import verify_top_candidates_activity
from src.config import settings
from src.models import (
    EvidenceLedger,
    RejectedCandidate,
    Report,
    WorkflowInput,
)
from src.trace import RunTrace, start_run_trace

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Outcome of one end-to-end pipeline execution.

    `report` is None iff `refused` is True. `trace` and `ledger` are always
    populated — even on refusal — so callers can render diagnostics.
    """

    refused: bool
    report: Report | None
    trace: RunTrace
    ledger: EvidenceLedger
    refusal_reason: str | None = None
    regenerated_use_case_id: str | None = None
    original_confidence: float | None = None


async def execute_pipeline(params: WorkflowInput) -> PipelineResult:
    """Run the full seven-step pipeline for one company. Returns
    PipelineResult with the structured Report (or refusal) plus the
    RunTrace and EvidenceLedger so callers can render diagnostics."""
    log = logger
    trace = start_run_trace(params.company_name)
    log.info("pipeline: started for %s at %s", params.company_name, trace.started_at.isoformat())

    # Step 1 — Research
    log.info("=== Step 1: Research ===")
    ctx, ledger, bundle = await research_company_activity(params.company_name, params.research_depth)
    log.info(
        "research_confidence=%.2f | is_verified=%s | industry=%s | sources=%s | ledger=%d",
        ctx.meta.research_confidence,
        ctx.meta.is_verified,
        ctx.classification.industry,
        ctx.meta.research_sources,
        len(ledger.entries),
    )

    # Step 1b — Context completion (gap-fill). Pass the original bundle through
    # so re-synthesis uses the same depth signal instead of re-fetching at LOW.
    log.info("=== Step 1b: Context completion (gap-fill) ===")
    ctx, ledger = await enrich_company_context_activity(ctx, ledger, bundle)
    log.info(
        "after enrich: confidence=%.2f | industry=%s | priorities=%d | data_assets=%d | ledger=%d",
        ctx.meta.research_confidence,
        ctx.classification.industry,
        len(ctx.strategic_context.stated_priorities),
        len(ctx.data_and_tech.likely_data_assets),
        len(ledger.entries),
    )

    # Confidence gate — graceful refusal
    confidence_ok = (
        ctx.meta.research_confidence >= settings.research_confidence_threshold
        or ctx.meta.is_verified
        or len(ctx.existing_ai_initiatives) > 0
    )
    if not confidence_ok:
        log.warning("Refusal: research signal too sparse for %s", params.company_name)
        return PipelineResult(
            refused=True,
            report=None,
            trace=trace,
            ledger=ledger,
            refusal_reason=(
                f"Couldn't find enough information about {params.company_name} "
                f"to confidently generate use cases. Try the legal name or parent brand."
            ),
        )

    # Step 2 — Retrieve precedents
    log.info("=== Step 2: Retrieve precedents ===")
    retrieved = await retrieve_precedents_activity(ctx, settings.top_k_precedents)
    log.info("retrieved %d precedents", len(retrieved.items))

    # Step 3 — Generate candidates
    log.info("=== Step 3: Generate 12 candidates ===")
    bundle = await gather_bundle_for_company(params.company_name, params.research_depth)
    batch, ledger = await generate_candidates_activity(
        ctx, retrieved, params.focus_area.value, True, raw_bundle=bundle, ledger=ledger
    )
    log.info(
        "generated %d candidates | diversity=%.3f | regenerated=%s | ledger=%d",
        len(batch.candidates),
        batch.diversity_score,
        batch.regenerated_for_diversity,
        len(ledger.entries),
    )

    # Step 4 — Score
    log.info("=== Step 4: Score (self-consistency, 2 passes) ===")
    scored = await score_candidates_activity(batch, ctx, params.weights)
    log.info("top-3 aggregate scores: %s", [round(s.aggregate_score, 2) for s in scored.scored[:3]])

    # Step 5 — Per-candidate verification
    log.info("=== Step 5: Per-candidate verification of top-3 ===")
    top_3 = scored.scored[:3]
    verified, ledger = await verify_top_candidates_activity(
        top_3, ctx, params.company_name, ledger=ledger
    )
    log.info(
        "verdicts: %s | ledger=%d",
        [(r.candidate_id, r.verdict.value) for r in verified.results],
        len(ledger.entries),
    )

    # Step 6 — Select + enrich
    log.info("=== Step 6: Select and enrich ===")
    enriched_uses, rejected = await select_and_enrich_activity(
        scored, verified, ctx, retrieved=retrieved, ledger=ledger
    )
    log.info("enriched %d use cases | %d rejected", len(enriched_uses), len(rejected))

    # Step 7 — Meta-evaluate
    log.info("=== Step 7: Meta-evaluation ===")
    review, fact_claims = await meta_evaluate_activity(
        enriched_uses, rejected, ctx, retrieved=retrieved, ledger=ledger
    )
    log.info(
        "ready=%s | confidence=%.2f | weakest=%s",
        review.sales_engineer_ready,
        review.confidence,
        review.weakest_use_case_id,
    )

    # Step 7b — Targeted regen on low confidence (skipped on fast tier)
    original_confidence = review.confidence
    regenerated_use_case_id: str | None = None
    if (
        settings.tier.value != "fast"
        and review.confidence < settings.meta_eval_confidence_threshold
        and review.weakest_use_case_id
    ):
        log.info(
            "=== Step 7b: Regeneration (weakest use case = %s) ===",
            review.weakest_use_case_id,
        )
        weak_id = review.weakest_use_case_id
        weak_idx = next((i for i, u in enumerate(enriched_uses) if u.id == weak_id), None)
        in_top_3 = {u.id for u in enriched_uses}
        replacement_sc = next(
            (sc for sc in scored.scored if sc.candidate.id not in in_top_3),
            None,
        )
        if weak_idx is not None and replacement_sc is not None:
            replacement_enriched = await regen_one_use_case_activity(
                weakest_id=weak_id,
                replacement=replacement_sc,
                ctx=ctx,
                retrieved=retrieved,
                ledger=ledger,
                weakness_reason=review.weakness_reason,
            )
            if replacement_enriched is not None:
                new_uses = list(enriched_uses)
                dropped_uc = new_uses[weak_idx]
                # Defensive: if regen returned essentially the same content
                # as what we dropped (the LLM "fixing" by re-emitting), the
                # 2nd meta-eval will produce the same result. Skip it and
                # keep the original — saves ~9s.
                desc_old = (dropped_uc.description or "").strip()
                desc_new = (replacement_enriched.description or "").strip()
                if desc_old and desc_old == desc_new:
                    log.info(
                        "regen: produced identical description to dropped use case, "
                        "skipping 2nd meta-eval and keeping original"
                    )
                else:
                    new_uses[weak_idx] = replacement_enriched
                    new_rejected = list(rejected) + [
                        RejectedCandidate(
                            title=dropped_uc.title,
                            one_line_reason="Replaced by regen — meta-eval flagged as weakest.",
                        )
                    ]
                    review2, fact_claims2 = await meta_evaluate_activity(
                        new_uses, new_rejected, ctx, retrieved=retrieved, ledger=ledger
                    )
                    log.info(
                        "regen result: confidence %.2f → %.2f | ready=%s",
                        original_confidence,
                        review2.confidence,
                        review2.sales_engineer_ready,
                    )
                    if review2.confidence > original_confidence:
                        enriched_uses = new_uses
                        rejected = new_rejected
                        review = review2
                        fact_claims = fact_claims2
                        regenerated_use_case_id = replacement_sc.candidate.id
                    else:
                        log.info("regen did not improve confidence, keeping original")

    # Step 7c — Web-verify rescue: rescue claims the meta-eval flagged
    # unsupported but are real and verifiable from public sources. Two-tier
    # credibility (verified allowlist / corroborated entity-anchor match).
    log.info("=== Step 7c: Web-verify rescue ===")
    review, fact_claims, ledger = await web_verify_unsupported_claims_activity(
        review, fact_claims, ctx.identity.name, ledger
    )

    # Quality signals
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
    return PipelineResult(
        refused=False,
        report=report,
        trace=trace,
        ledger=ledger,
        regenerated_use_case_id=regenerated_use_case_id,
        original_confidence=original_confidence,
    )
