"""Step 7 — Meta-evaluation. Single LLM call (`mistral-medium-2604`, T=0.1)
that grades the complete report and returns a structured `MetaEvalReview`
plus a per-claim fact-check list (used to compute the fact-check pass rate).

Per the methodology, if the meta-eval `confidence < 0.6` AND the workflow
hasn't already done one regeneration, the workflow triggers a targeted
regeneration of the weakest use case. That branching lives in `workflow.py`;
this activity just reports.
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
    MetaEvalReview,
    RejectedCandidate,
)
from src.prompts import META_EVALUATION_SYSTEM

logger = logging.getLogger(__name__)


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _format_use_cases(uses: list[EnrichedUseCase]) -> str:
    out: list[str] = []
    for uc in uses:
        out.append(
            f"--- {uc.id} ---\n"
            f"Title: {uc.title}\n"
            f"Description: {uc.description}\n"
            f"Why this company: {uc.why_this_company}\n"
            f"Example input: {uc.example_input}\n"
            f"Example output: {uc.example_output}\n"
            f"Mistral products: {', '.join(uc.suggested_mistral_products)}\n"
            f"Blueprint pattern: {uc.blueprint_pattern.value}\n"
            f"Time-to-value: {uc.time_to_value.estimate}\n"
            f"Cost tier: {uc.operating_cost_tier.value}\n"
            f"Top implementation risk: {uc.top_implementation_risk}\n"
            f"Builds on existing: {uc.builds_on_existing}\n"
        )
    return "\n".join(out)


def _format_existing(ctx: CompanyContext) -> str:
    if not ctx.existing_ai_initiatives:
        return "(none discovered)"
    return "\n".join(f"- {ei.description[:600]}" for ei in ctx.existing_ai_initiatives)


def _build_user_message(
    uses: list[EnrichedUseCase],
    rejected: list[RejectedCandidate],
    ctx: CompanyContext,
) -> str:
    rej_lines = "\n".join(f"- {r.title}: {r.one_line_reason}" for r in rejected) or "(none)"
    return (
        "# Target company context\n"
        + ctx.model_dump_json(indent=2)
        + "\n\n# Existing AI initiatives at this company\n"
        + _format_existing(ctx)
        + "\n\n# Three enriched use cases to review\n"
        + _format_use_cases(uses)
        + "\n\n# Rejected appendix (near-misses)\n"
        + rej_lines
        + "\n\nReturn STRICT JSON per the system spec."
    )


@workflows.activity(start_to_close_timeout=timedelta(seconds=180))
async def meta_evaluate_activity(
    uses: list[EnrichedUseCase],
    rejected: list[RejectedCandidate],
    ctx: CompanyContext,
) -> tuple[MetaEvalReview, list[FactCheckEntry]]:
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for meta-evaluation")

    client = Mistral(api_key=settings.mistral_api_key)
    user_msg = _build_user_message(uses, rejected, ctx)
    r = await client.chat.complete_async(
        model=settings.mistral_meta_eval_model,
        temperature=0.1,
        max_tokens=4000,
        timeout_ms=180_000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": META_EVALUATION_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
    )
    text = r.choices[0].message.content
    if isinstance(text, list):
        text = "".join(getattr(b, "text", "") for b in text)
    data = json.loads(_strip_fence(str(text or "")))

    review = MetaEvalReview(
        confidence=float(data.get("confidence", 0.5)),
        sales_engineer_ready=bool(data.get("sales_engineer_ready", False)),
        weakest_use_case_id=data.get("weakest_use_case_id"),
        weakness_reason=data.get("weakness_reason"),
        cross_cutting_concern=data.get("cross_cutting_concern"),
        duplicate_flag=data.get("duplicate_flag"),
    )
    raw_claims = data.get("claims") or []
    claims: list[FactCheckEntry] = []
    if isinstance(raw_claims, list):
        for c in raw_claims:
            if not isinstance(c, dict):
                continue
            claims.append(
                FactCheckEntry(
                    claim=str(c.get("claim", "")),
                    use_case_id=str(c.get("use_case_id", "")),
                    passed=bool(c.get("supported", c.get("passed", False))),
                    rationale=c.get("supporting_signal") or c.get("rationale"),
                )
            )
    logger.info(
        "meta_eval: ready=%s confidence=%.2f weakest=%s claims_total=%d (passed=%d)",
        review.sales_engineer_ready,
        review.confidence,
        review.weakest_use_case_id,
        len(claims),
        sum(1 for c in claims if c.passed),
    )
    return review, claims
