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

from src._clients import mistral_client
from src.config import settings
from src.trace import trace_step
from src.models import (
    CompanyContext,
    EnrichedUseCase,
    EvidenceLedger,
    FactCheckEntry,
    MetaEvalReview,
    RejectedCandidate,
    RetrievedPrecedents,
)
from src.prompts import META_EVALUATION_SYSTEM

logger = logging.getLogger(__name__)


from src._util import strip_fence as _strip_fence  # noqa: E402


def _format_use_cases(uses: list[EnrichedUseCase]) -> str:
    """Render use cases for meta-eval. example_input and example_output are
    explicitly labeled ILLUSTRATIVE so the meta-evaluator excludes their
    contents from claim verification — they're hypothetical demonstrations
    of system behavior, not factual assertions about the company.
    """
    out: list[str] = []
    for uc in uses:
        out.append(
            f"--- {uc.id} ---\n"
            f"Title: {uc.title}\n"
            f"Description: {uc.description}\n"
            f"Why this company: {uc.why_this_company}\n"
            f"--- ILLUSTRATIVE ONLY (do NOT fact-check, do NOT include in claims) ---\n"
            f"Example input (hypothetical user query): {uc.example_input}\n"
            f"Example output (hypothetical system response with synthetic data): {uc.example_output}\n"
            f"--- end ILLUSTRATIVE block ---\n"
            f"Mistral products: {', '.join(uc.suggested_mistral_products)}\n"
            f"Blueprint pattern: {uc.blueprint_pattern.value}\n"
            f"Time-to-value: {uc.time_to_value.estimate}\n"
            f"Cost tier: {uc.operating_cost_tier.value}\n"
            f"Top implementation risk: {uc.top_implementation_risk}\n"
            f"Builds on existing: {uc.builds_on_existing}\n"
            f"Cited inspired_by: {', '.join(uc.inspired_by) or '(none)'}\n"
            f"Cited evidence_ids: {', '.join(uc.evidence_ids) or '(none)'}\n"
        )
    return "\n".join(out)


def _format_existing(ctx: CompanyContext) -> str:
    if not ctx.existing_ai_initiatives:
        return "(none discovered)"
    return "\n".join(f"- {ei.description[:600]}" for ei in ctx.existing_ai_initiatives)


def _format_cited_precedents(
    uses: list[EnrichedUseCase], retrieved: RetrievedPrecedents | None
) -> str:
    """Show only the precedents actually cited across the use cases — keeps
    the meta-eval prompt within token budget while still letting it verify
    every peer-deployment claim."""
    if retrieved is None:
        return "(no retrieved precedents available)"
    cited_ids: set[str] = set()
    for uc in uses:
        cited_ids.update(uc.inspired_by)
    if not cited_ids:
        return "(no precedents cited by any use case)"
    by_id = {p.id: p for p in retrieved.items}
    lines: list[str] = []
    for pid in sorted(cited_ids):
        p = by_id.get(pid)
        if p is None:
            lines.append(f"--- {pid} (NOT IN RETRIEVED SET — likely fabricated)")
            continue
        body = (p.deep_content or p.description or "")[:3000]
        lines.append(
            f"--- {pid} ---\n"
            f"Company: {p.company}\n"
            f"Industry: {p.industry}\n"
            f"Title: {p.title}\n"
            f"Source URL: {p.source_url or '(none)'}\n"
            f"Deep content:\n{body}\n"
        )
    return "\n".join(lines)


def _format_full_evidence_pool(
    uses: list[EnrichedUseCase], ledger: EvidenceLedger | None
) -> str:
    """Render the FULL evidence pool the meta-eval can use to verify claims.

    Earlier versions only showed entries cited via `evidence_ids` — this
    artificially narrowed support, causing meta-eval to mark real claims
    like Veolia's GreenUp program or L'Oréal's ModiFace face-scan as
    unsupported just because the use case didn't explicitly cite the
    gap-fill ledger entry where they were retrieved.

    Now we show every ledger entry (Wikipedia summary, news bodies,
    gap-fill Tavily results, generation_tool web_search results,
    per-candidate verification deep-reads, existing-initiative source
    URLs, company-verification URLs). Each entry's content is capped at
    1200 chars so the full pool fits in the meta-eval's token budget.
    Cited entries are flagged inline so meta-eval can prioritize them
    while still being free to find support elsewhere.
    """
    if ledger is None or not ledger.entries:
        return "(no ledger entries)"
    cited_ids: set[str] = set()
    for uc in uses:
        cited_ids.update(uc.evidence_ids)
    lines: list[str] = []
    # Order: prefer entries the model actually cited first (model already
    # thought these mattered), then the rest by source-kind priority.
    _kind_priority = {
        "gap_fill": 0,
        "news": 1,
        "company_verification": 2,
        "generation_tool": 3,
        "wikipedia": 4,
        "existing_initiative": 5,
        "per_candidate_verification": 6,
        "tavily": 7,
        "precedent": 8,
        "jobs": 9,
    }
    sorted_entries = sorted(
        ledger.entries,
        key=lambda e: (
            0 if e.id in cited_ids else 1,
            _kind_priority.get(e.source_kind.value, 99),
        ),
    )
    for item in sorted_entries:
        cited_marker = " [CITED IN A USE CASE]" if item.id in cited_ids else ""
        body = (item.content or "")[:1200]
        lines.append(
            f"--- {item.id} ({item.source_kind.value}, "
            f"confidence={item.confidence}){cited_marker} ---\n"
            f"URL: {item.url or '(none)'}\n"
            f"Title: {item.title}\n"
            f"Content:\n{body}\n"
        )
    return "\n".join(lines)


def _build_user_message(
    uses: list[EnrichedUseCase],
    rejected: list[RejectedCandidate],
    ctx: CompanyContext,
    retrieved: RetrievedPrecedents | None,
    ledger: EvidenceLedger | None,
) -> str:
    rej_lines = "\n".join(f"- {r.title}: {r.one_line_reason}" for r in rejected) or "(none)"
    return (
        "# Target company context\n"
        + ctx.model_dump_json(indent=2)
        + "\n\n# Existing AI initiatives at this company\n"
        + _format_existing(ctx)
        + "\n\n# Three enriched use cases to review\n"
        + _format_use_cases(uses)
        + "\n\n# Cited precedents (deep content for verifying peer-deployment claims)\n"
        + _format_cited_precedents(uses, retrieved)
        + "\n\n# Full evidence pool (ALL ledger entries — use any to verify claims)\n"
        + _format_full_evidence_pool(uses, ledger)
        + "\n\n# Rejected appendix (near-misses)\n"
        + rej_lines
        + "\n\nReturn STRICT JSON per the system spec. Remember: every claim's "
        "supporting_signal must be a literal quote from the cited source."
    )


@workflows.activity(start_to_close_timeout=timedelta(seconds=300))
async def meta_evaluate_activity(
    uses: list[EnrichedUseCase],
    rejected: list[RejectedCandidate],
    ctx: CompanyContext,
    retrieved: RetrievedPrecedents | None = None,
    ledger: EvidenceLedger | None = None,
) -> tuple[MetaEvalReview, list[FactCheckEntry]]:
    """Step 7 — meta-evaluation. Reviews the enriched report and grades it.

    With `retrieved` and `ledger` populated, the LLM also verifies each
    factual claim against cited precedent deep_content and cited ledger
    entries. Pass rate now reflects whether claims are LITERALLY supported
    by source content, not just whether the CompanyContext mentions them.
    """
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for meta-evaluation")

    client = mistral_client()
    user_msg = _build_user_message(uses, rejected, ctx, retrieved, ledger)
    async with trace_step(
        "meta_eval",
        settings.mistral_meta_eval_model,
        "chat.complete",
        inputs_summary=f"reviewing {len(uses)} use cases",
    ) as ev:
        r = await client.chat.complete_async(
            model=settings.mistral_meta_eval_model,
            temperature=0.1,
            max_tokens=10_000,
            timeout_ms=240_000,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": META_EVALUATION_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
        )
        ev.outputs_summary = "review + claims"
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
