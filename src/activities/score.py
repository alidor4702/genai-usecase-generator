"""Step 4 — Scoring activity. Self-consistency: 2 LLM passes, scores averaged.

Runs `mistral-small-2603` twice (T=0.2 and T=0.4) in parallel over the 12
candidates, then averages numeric scores per candidate per criterion. Rationales
are kept from the lower-temperature pass (more deterministic). Aggregate score
per candidate is computed using the user-provided `CriteriaWeights`.

Per CLAUDE.md, scoring is the most quality-sensitive step; self-consistency
is applied here specifically because the small model is cheap enough to call
twice.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta

import mistralai.workflows as workflows
from mistralai.client import Mistral

from src._clients import mistral_client
from src.config import settings
from src.criteria import render_criteria_for_prompt
from src.trace import trace_step
from src.models import (
    Candidate,
    CandidateBatch,
    CompanyContext,
    CriteriaWeights,
    CriterionScore,
    ScoredBatch,
    ScoredCandidate,
)
from src.prompts import SCORING_SYSTEM

logger = logging.getLogger(__name__)


def _format_existing_initiatives(ctx: CompanyContext) -> str:
    if not ctx.existing_ai_initiatives:
        return "(none discovered)"
    return "\n".join(f"- {ei.description[:600]}" for ei in ctx.existing_ai_initiatives)


def _format_candidates(candidates: list[Candidate]) -> str:
    out: list[str] = []
    for c in candidates:
        out.append(
            f"--- candidate_id: {c.id} ---\n"
            f"Title: {c.title}\n"
            f"Description: {c.description}\n"
            f"Why this company: {c.why_this_company}\n"
            f"Estimated impact: {c.estimated_impact_summary}\n"
            f"Suggested Mistral products: {', '.join(c.suggested_mistral_products) or '(none)'}\n"
            f"Novelty: {c.novelty.value}\n"
        )
    return "\n".join(out)


def _build_user_message(batch: CandidateBatch, ctx: CompanyContext) -> str:
    return (
        "# Five scoring criteria (with positive and negative anchors)\n"
        + render_criteria_for_prompt()
        + "\n\n# Company existing AI initiatives (for the iconic hard-gate)\n"
        + _format_existing_initiatives(ctx)
        + "\n\n# Candidates to score\n"
        + _format_candidates(batch.candidates)
        + "\n\nReturn STRICT JSON per the system spec."
    )


from src._util import strip_fence as _strip_fence  # noqa: E402


async def _score_pass(
    client: Mistral, user_msg: str, temperature: float
) -> dict[str, dict[str, dict[str, object]]]:
    """Return mapping candidate_id -> criterion_key -> {score: int, rationale: str}."""
    async with trace_step(
        "score",
        settings.mistral_scoring_model,
        "chat.complete",
        inputs_summary=f"self-consistency pass T={temperature}",
    ) as ev:
        r = await client.chat.complete_async(
            model=settings.mistral_scoring_model,
            temperature=temperature,
            max_tokens=10_000,
            timeout_ms=180_000,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SCORING_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
        )
        text = r.choices[0].message.content
        if isinstance(text, list):
            text = "".join(getattr(b, "text", "") for b in text)
        data = json.loads(_strip_fence(str(text or "")))
        ev.outputs_summary = f"scored {len(data.get('scored', []))} candidates"
    scored_list = data.get("scored", [])

    out: dict[str, dict[str, dict[str, object]]] = {}
    if not isinstance(scored_list, list):
        return out
    for item in scored_list:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("candidate_id") or "")
        if not cid:
            continue
        per_crit: dict[str, dict[str, object]] = {}
        for key in (
            "relevance",
            "iconic_potential",
            "estimated_impact",
            "feasibility",
            "mistral_suitability",
        ):
            block = item.get(key)
            if isinstance(block, dict):
                per_crit[key] = {
                    "score": int(block.get("score", 5)),
                    "rationale": str(block.get("rationale", "")),
                }
        if per_crit:
            out[cid] = per_crit
    return out


def _avg_scores(
    pass_a: dict[str, dict[str, dict[str, object]]],
    pass_b: dict[str, dict[str, dict[str, object]]],
) -> dict[str, dict[str, dict[str, object]]]:
    """Average numeric scores; keep rationales from pass_a (lower temperature)."""
    merged: dict[str, dict[str, dict[str, object]]] = {}
    for cid, crits_a in pass_a.items():
        crits_b = pass_b.get(cid, {})
        merged_crits: dict[str, dict[str, object]] = {}
        for key, va in crits_a.items():
            sa = int(va.get("score", 5))
            sb = int(crits_b.get(key, {}).get("score", sa))
            merged_crits[key] = {
                "score": int(round((sa + sb) / 2.0)),
                "rationale": va.get("rationale", ""),
            }
        merged[cid] = merged_crits
    return merged


def _build_scored_candidates(
    candidates: list[Candidate],
    merged: dict[str, dict[str, dict[str, object]]],
    weights: CriteriaWeights,
) -> list[ScoredCandidate]:
    out: list[ScoredCandidate] = []
    for c in candidates:
        crits = merged.get(c.id)
        if crits is None:
            # Skip candidates the scorer dropped — log and continue
            logger.warning("score: no scores returned for candidate %s", c.id)
            continue
        sc = ScoredCandidate(
            candidate=c,
            relevance=CriterionScore(
                score=int(crits.get("relevance", {}).get("score", 5)),
                rationale=str(crits.get("relevance", {}).get("rationale", "")),
            ),
            iconic_potential=CriterionScore(
                score=int(crits.get("iconic_potential", {}).get("score", 5)),
                rationale=str(crits.get("iconic_potential", {}).get("rationale", "")),
            ),
            estimated_impact=CriterionScore(
                score=int(crits.get("estimated_impact", {}).get("score", 5)),
                rationale=str(crits.get("estimated_impact", {}).get("rationale", "")),
            ),
            feasibility=CriterionScore(
                score=int(crits.get("feasibility", {}).get("score", 5)),
                rationale=str(crits.get("feasibility", {}).get("rationale", "")),
            ),
            mistral_suitability=CriterionScore(
                score=int(crits.get("mistral_suitability", {}).get("score", 5)),
                rationale=str(crits.get("mistral_suitability", {}).get("rationale", "")),
            ),
        )
        sc.aggregate_score = sc.aggregate_with_weights(weights)
        out.append(sc)
    out.sort(key=lambda s: s.aggregate_score, reverse=True)
    return out


@workflows.activity(start_to_close_timeout=timedelta(seconds=180))
async def score_candidates_activity(
    batch: CandidateBatch, ctx: CompanyContext, weights: CriteriaWeights
) -> ScoredBatch:
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for scoring")
    client = mistral_client()
    user_msg = _build_user_message(batch, ctx)

    if settings.enable_single_pass_score:
        # Phase 3e ablation path — one call at T=0.3 instead of the
        # parallel two-pass average. Cuts ~17s of wall clock at the cost
        # of less stable scores at the margin. Off by default.
        merged = await _score_pass(client, user_msg, temperature=0.3)
    else:
        pass_a, pass_b = await asyncio.gather(
            _score_pass(client, user_msg, temperature=0.2),
            _score_pass(client, user_msg, temperature=0.4),
        )
        merged = _avg_scores(pass_a, pass_b)
    scored = _build_scored_candidates(batch.candidates, merged, weights)
    logger.info(
        "score: %d / %d candidates scored | top aggregate %.2f",
        len(scored),
        len(batch.candidates),
        scored[0].aggregate_score if scored else 0.0,
    )
    return ScoredBatch(
        scored=scored,
        weights_used=weights,
        self_consistency_passes=2,
    )
