"""Step 6 — Selection and enrichment.

Filters scored candidates by verification verdict (drops `confirmed_existing`,
promotes the next-highest near-miss) to land on a top-3 set, then makes ONE
LLM call to `mistral-large-2512` (T=0.4) to produce three customer-facing
`EnrichedUseCase` objects plus a `rejected_appendix`.

Large 3 is used here because this is the customer-facing prose — the highest
output-quality step in the pipeline.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import mistralai.workflows as workflows
from mistralai.client import Mistral

from src.config import settings
from src.models import (
    BlueprintPattern,
    CompanyContext,
    ComplexityTier,
    CostTier,
    EnrichedUseCase,
    ImpactTier,
    RejectedCandidate,
    ScoredBatch,
    ScoredCandidate,
    TimeToValue,
    VerificationBatch,
    VerificationVerdict,
)
from src.prompts import ENRICHMENT_SYSTEM

logger = logging.getLogger(__name__)


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _coerce_str_list(v: object) -> list[str]:
    """Robust coercion to list[str]. Handles the common LLM mistake of
    returning a single string for a field declared as list[str]."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, list):
        return [str(x) for x in v if x]
    return []


def filter_and_promote(
    scored: ScoredBatch, verified: VerificationBatch, k: int = 3
) -> tuple[list[ScoredCandidate], list[ScoredCandidate], dict[str, VerificationVerdict]]:
    """Return (final_top_k, near_misses_for_appendix, verdict_lookup).

    - confirmed_existing → dropped, replaced by next near-miss (still subject to verification on the next pass — at this point the verification batch is fixed, so any promoted candidate is treated as `pass` by default)
    - partial_overlap → kept, flagged in EnrichedUseCase.builds_on_existing
    - pass → kept
    """
    verdict_by_id = {r.candidate_id: r.verdict for r in verified.results}
    confirmed_ids = {
        r.candidate_id
        for r in verified.results
        if r.verdict == VerificationVerdict.CONFIRMED_EXISTING
    }

    # Walk scored in aggregate-score order; skip confirmed_existing
    final: list[ScoredCandidate] = []
    appendix: list[ScoredCandidate] = []
    for sc in scored.scored:
        if sc.candidate.id in confirmed_ids:
            appendix.append(sc)
            continue
        if len(final) < k:
            final.append(sc)
        else:
            appendix.append(sc)
        if len(final) == k and len(appendix) >= 4:
            break
    return final, appendix, verdict_by_id


def _format_top3_input(
    final: list[ScoredCandidate], verdicts: dict[str, VerificationVerdict]
) -> str:
    out: list[str] = []
    for sc in final:
        c = sc.candidate
        verdict = verdicts.get(c.id, VerificationVerdict.PASS)
        out.append(
            f"--- candidate_id: {c.id} ---\n"
            f"Title: {c.title}\n"
            f"Description: {c.description}\n"
            f"Why this company: {c.why_this_company}\n"
            f"Estimated impact (raw): {c.estimated_impact_summary}\n"
            f"Suggested Mistral products: {', '.join(c.suggested_mistral_products) or '(none)'}\n"
            f"Novelty: {c.novelty.value}\n"
            f"Verification verdict: {verdict.value}\n"
            f"Aggregate score: {sc.aggregate_score:.2f}\n"
            f"Per-criterion: relevance={sc.relevance.score} iconic={sc.iconic_potential.score} "
            f"impact={sc.estimated_impact.score} feasibility={sc.feasibility.score} "
            f"mistral_fit={sc.mistral_suitability.score}\n"
            f"Inspired_by: {', '.join(c.inspired_by) or '(empty — novel)'}\n"
            f"Grounded_in: {', '.join(c.grounded_in)}\n"
        )
    return "\n".join(out)


def _format_near_misses(appendix: list[ScoredCandidate]) -> str:
    if not appendix:
        return "(no near-misses — top-k candidates all passed)"
    return "\n".join(
        f"- {sc.candidate.id}: {sc.candidate.title} (aggregate {sc.aggregate_score:.2f})"
        for sc in appendix[:6]
    )


def _build_user_message(
    final: list[ScoredCandidate],
    appendix: list[ScoredCandidate],
    verdicts: dict[str, VerificationVerdict],
    ctx: CompanyContext,
) -> str:
    return (
        "# Target company context\n"
        + ctx.model_dump_json(indent=2)
        + "\n\n# Verified top-3 candidates to enrich\n"
        + _format_top3_input(final, verdicts)
        + "\n\n# Near-misses (for the rejected_appendix)\n"
        + _format_near_misses(appendix)
        + "\n\nReturn STRICT JSON of shape:\n"
        '{"top_use_cases": [EnrichedUseCase, EnrichedUseCase, EnrichedUseCase],'
        ' "rejected_appendix": [{"title": str, "one_line_reason": str}, ...]}'
    )


def _coerce_enriched(
    raw: dict[str, object], scored: ScoredCandidate, verdict: VerificationVerdict
) -> EnrichedUseCase:
    blueprint_str = str(raw.get("blueprint_pattern") or "rag")
    try:
        bp = BlueprintPattern(blueprint_str)
    except ValueError:
        bp = BlueprintPattern.RAG

    cost_str = str(raw.get("operating_cost_tier") or "unknown")
    try:
        cost = CostTier(cost_str)
    except ValueError:
        cost = CostTier.UNKNOWN

    # Impact tier from aggregate score buckets
    if scored.estimated_impact.score >= 8:
        impact = ImpactTier.HIGH
    elif scored.estimated_impact.score >= 5:
        impact = ImpactTier.MEDIUM
    else:
        impact = ImpactTier.LOW

    # Complexity heuristic from feasibility (inverse-ish)
    if scored.feasibility.score >= 8:
        complexity = ComplexityTier.LOW
    elif scored.feasibility.score >= 5:
        complexity = ComplexityTier.MEDIUM
    else:
        complexity = ComplexityTier.HIGH

    ttv_raw = raw.get("time_to_value")
    if isinstance(ttv_raw, dict):
        ttv = TimeToValue(
            estimate=str(ttv_raw.get("estimate", "unknown")),
            anchored_to=list(ttv_raw.get("anchored_to") or []),
        )
    else:
        ttv = TimeToValue(estimate=str(ttv_raw or "unknown"))

    builds_on = verdict == VerificationVerdict.PARTIAL_OVERLAP
    return EnrichedUseCase(
        id=str(raw.get("id", scored.candidate.id)),
        title=str(raw.get("title", scored.candidate.title)),
        description=str(raw.get("description", scored.candidate.description)),
        why_this_company=str(raw.get("why_this_company", scored.candidate.why_this_company)),
        example_input=str(raw.get("example_input", "")),
        example_output=str(raw.get("example_output", "")),
        suggested_mistral_products=(
            _coerce_str_list(raw.get("suggested_mistral_products"))
            or scored.candidate.suggested_mistral_products
        ),
        blueprint_pattern=bp,
        blueprint_mermaid=str(raw.get("blueprint_mermaid", "")),
        time_to_value=ttv,
        operating_cost_tier=cost,
        impact_tier=impact,
        complexity_tier=complexity,
        top_implementation_risk=str(raw.get("top_implementation_risk", "")),
        inspired_by=_coerce_str_list(raw.get("inspired_by")) or scored.candidate.inspired_by,
        grounded_in=_coerce_str_list(raw.get("grounded_in")) or scored.candidate.grounded_in,
        builds_on_existing=builds_on,
        builds_on_note=(
            "Builds on an existing initiative at this company (partial overlap detected by verifier)."
            if builds_on
            else None
        ),
    )


@workflows.activity(start_to_close_timeout=timedelta(seconds=180))
async def select_and_enrich_activity(
    scored: ScoredBatch,
    verified: VerificationBatch,
    ctx: CompanyContext,
) -> tuple[list[EnrichedUseCase], list[RejectedCandidate]]:
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for enrichment")

    final, appendix, verdicts = filter_and_promote(scored, verified, k=3)
    if len(final) < 3:
        # Pad from appendix if needed (shouldn't usually happen)
        while len(final) < 3 and appendix:
            final.append(appendix.pop(0))

    client = Mistral(api_key=settings.mistral_api_key)
    user_msg = _build_user_message(final, appendix, verdicts, ctx)
    r = await client.chat.complete_async(
        model=settings.mistral_enrichment_model,
        temperature=0.4,
        max_tokens=8000,
        timeout_ms=240_000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": ENRICHMENT_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
    )
    text = r.choices[0].message.content
    if isinstance(text, list):
        text = "".join(getattr(b, "text", "") for b in text)
    data = json.loads(_strip_fence(str(text or "")))

    raw_uses = data.get("top_use_cases", [])
    enriched: list[EnrichedUseCase] = []
    if isinstance(raw_uses, list):
        for raw, sc in zip(raw_uses, final, strict=False):
            if not isinstance(raw, dict):
                continue
            enriched.append(
                _coerce_enriched(raw, sc, verdicts.get(sc.candidate.id, VerificationVerdict.PASS))
            )

    # Pad if the model returned fewer than 3 enriched outputs
    while len(enriched) < len(final):
        sc = final[len(enriched)]
        enriched.append(
            _coerce_enriched(
                {"id": sc.candidate.id}, sc, verdicts.get(sc.candidate.id, VerificationVerdict.PASS)
            )
        )

    raw_rej = data.get("rejected_appendix") or []
    rejected: list[RejectedCandidate] = []
    if isinstance(raw_rej, list):
        for r_item in raw_rej:
            if not isinstance(r_item, dict):
                continue
            rejected.append(
                RejectedCandidate(
                    title=str(r_item.get("title", "")),
                    one_line_reason=str(r_item.get("one_line_reason", "")),
                )
            )

    logger.info(
        "select_enrich: enriched %d use cases | %d rejected appendix entries",
        len(enriched),
        len(rejected),
    )
    return enriched[:3], rejected
