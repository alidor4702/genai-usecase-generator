"""Workflow activity wrapping the quality-signals computation.

The diversity signal requires an embedding call (I/O), so the whole signal
assembly lives in an activity. Pure-compute helpers are in
`src/quality_signals.py` so they can also be used standalone (e.g. tests).

Specificity is graded by an LLM (Mistral Small @ T=0.1) per use case rather
than the regex-based set overlap that turned out to massively under-score
real grounding. The LLM call is one batch — all 3 use cases in a single
JSON response.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import mistralai.workflows as workflows
from mistralai.client import Mistral

from src.config import settings
from src.models import (
    CompanyContext,
    EnrichedUseCase,
    FactCheckEntry,
    QualitySignals,
)
from src.quality_signals import assemble_quality_signals
from src.trace import trace_step

logger = logging.getLogger(__name__)


SPECIFICITY_GRADER_SYSTEM = """\
You are grading how SPECIFICALLY each use case is grounded in the target
company's actual facts vs. how generically it could be applied to any company
in the same industry.

Score 0.0-1.0 per use case where:
  0.0-0.2 — generic, "AI for any large retailer / bank / etc.", no real hooks
  0.3-0.5 — mentions company name but doesn't exploit specific company facts
  0.6-0.8 — cites company-specific data assets, named brands, named priorities,
            regional formats, or stated regulatory constraints
  0.9-1.0 — multiple deep, distinctive, hard-to-substitute company hooks

Be calibrated and honest. Most outputs land 0.4-0.7. A 0.9 should be rare —
it means the use case could not plausibly be retargeted to a competitor
without major rewrite.

Output STRICT JSON: {"scores": [{"use_case_id": str, "specificity": float, "reason": str}, ...]}
"""


DIVERSITY_GRADER_SYSTEM = """\
You are grading how TOPICALLY DIVERSE three use cases are.

Diversity is about whether the three use cases address genuinely different
business surfaces — operations vs. customer experience vs. compliance, or
different data assets, or different blueprint patterns. Stylistic similarity
in the prose does NOT count as low diversity (the same author wrote them).

Score 0.0-1.0 where:
  0.0-0.2 — three near-duplicates that just rephrase the same idea
  0.3-0.5 — two are similar (e.g. both customer-facing chatbots), one is
            different
  0.6-0.8 — three meaningfully distinct surfaces (e.g. customer experience +
            operational anomaly detection + compliance/document AI), with
            different blueprint patterns
  0.9-1.0 — three genuinely orthogonal use cases spanning operations, customer
            experience, AND compliance/strategy with different data assets
            and different blueprint patterns

Most reports should land 0.4-0.7. A 0.9 means a sales engineer could pitch
all three to different stakeholders in the same customer org without overlap.

Output STRICT JSON: {"diversity": float, "reason": str}
"""


async def _llm_diversity_grade(
    client: Mistral, uses: list[EnrichedUseCase]
) -> float | None:
    """Grade topical diversity of the 3 final use cases via Mistral Small.

    Returns None on failure so the caller can fall back to embedding-based
    diversity. Scores titles + blueprint patterns + a 1-line core, NOT the
    full descriptions, since stylistic similarity in long prose was the
    main reason the embedding-based metric collapsed to 0.10-0.15 across
    all reports.
    """
    if len(uses) < 2:
        return 1.0
    user_msg = "## Use cases to grade for diversity\n\n" + "\n\n".join(
        f"--- use_case_id: {uc.id} ---\n"
        f"Title: {uc.title}\n"
        f"Blueprint pattern: {uc.blueprint_pattern.value}\n"
        f"Core (first sentence of description): {uc.description[:240]}"
        for uc in uses
    )
    try:
        async with trace_step(
            "quality_signals",
            settings.mistral_scoring_model,
            "chat.complete",
            inputs_summary="diversity grade",
        ) as ev:
            r = await client.chat.complete_async(
                model=settings.mistral_scoring_model,
                temperature=0.1,
                max_tokens=400,
                timeout_ms=30_000,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": DIVERSITY_GRADER_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
            )
            text = r.choices[0].message.content
            if isinstance(text, list):
                text = "".join(getattr(b, "text", "") for b in text)
            data = json.loads(str(text or "{}"))
            ev.outputs_summary = f"diversity={data.get('diversity')}"
    except Exception as e:
        logger.warning("diversity grader failed: %s — falling back to embedding", type(e).__name__)
        return None
    raw = data.get("diversity")
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return None
    if 0.0 <= score <= 1.0:
        reason = data.get("reason")
        logger.info("diversity (LLM): %.2f — %s", score, str(reason or "")[:140])
        return score
    return None


async def _llm_specificity_per_use_case(
    client: Mistral, uses: list[EnrichedUseCase], ctx: CompanyContext
) -> list[float]:
    if not uses:
        return []
    user_msg = (
        "## Target company context\n"
        + ctx.model_dump_json(indent=2)
        + "\n\n## Use cases to grade\n"
        + "\n\n".join(
            f"--- use_case_id: {uc.id} ---\n"
            f"Title: {uc.title}\n"
            f"Description: {uc.description}\n"
            f"Why this company: {uc.why_this_company}\n"
            for uc in uses
        )
    )
    try:
        async with trace_step(
            "quality_signals",
            settings.mistral_scoring_model,
            "chat.complete",
            inputs_summary=f"specificity grade ({len(uses)} use cases)",
        ) as ev:
            r = await client.chat.complete_async(
                model=settings.mistral_scoring_model,
                temperature=0.1,
                max_tokens=2500,
                timeout_ms=90_000,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SPECIFICITY_GRADER_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
            )
            text = r.choices[0].message.content
            if isinstance(text, list):
                text = "".join(getattr(b, "text", "") for b in text)
            data = json.loads(str(text or "{}"))
            ev.outputs_summary = f"scored {len(data.get('scores', []))} use cases"
    except Exception as e:
        logger.warning("specificity grader failed: %s — falling back to 0.5", type(e).__name__)
        return [0.5] * len(uses)

    scores_by_id: dict[str, float] = {}
    for s in data.get("scores", []):
        if isinstance(s, dict) and "use_case_id" in s:
            try:
                scores_by_id[str(s["use_case_id"])] = float(s.get("specificity", 0.5))
            except (TypeError, ValueError):
                pass
    out: list[float] = [scores_by_id.get(uc.id, 0.5) for uc in uses]
    return out


@workflows.activity(start_to_close_timeout=timedelta(seconds=60))
async def compute_quality_signals_activity(
    uses: list[EnrichedUseCase],
    ctx: CompanyContext,
    fact_check: list[FactCheckEntry],
) -> QualitySignals:
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for quality-signals embedding")
    client = Mistral(api_key=settings.mistral_api_key)
    descriptions = [uc.description for uc in uses]
    if descriptions:
        resp = await client.embeddings.create_async(
            model=settings.mistral_embedding_model,
            inputs=descriptions,
        )
        embeds = [list(d.embedding) for d in resp.data]
    else:
        embeds = []

    signals = assemble_quality_signals(uses, ctx, embeds, fact_check)

    # Override regex-based specificity with LLM-graded specificity (the regex
    # under-scores real grounding because most company-specific hooks aren't
    # capitalized phrases).
    llm_specificity = await _llm_specificity_per_use_case(client, uses, ctx)
    if llm_specificity:
        signals.specificity_per_use_case = llm_specificity

    # Override embedding-based diversity with LLM-graded diversity. The
    # embedding metric was clustering on stylistic similarity (the same
    # enrichment LLM wrote all 3 descriptions) and collapsed to 0.10-0.15
    # across reports regardless of actual topical spread. The LLM grader
    # scores titles + blueprint patterns + 1-line cores instead.
    llm_diversity = await _llm_diversity_grade(client, uses)
    if llm_diversity is not None:
        signals.diversity = llm_diversity

    logger.info(
        "quality_signals: diversity=%.2f specificity=%s mistral_products=%d fact_pass=%.2f",
        signals.diversity,
        [round(s, 2) for s in signals.specificity_per_use_case],
        signals.mistral_product_diversity,
        signals.fact_check_pass_rate,
    )
    return signals
