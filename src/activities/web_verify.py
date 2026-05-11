"""Step 7b — post-meta-eval web-verify rescue layer.

Runs after `meta_evaluate_activity` produces its claim list. For every claim
the meta-eval flagged `passed=False`, this activity issues a fresh Tavily
search scoped to the company and the claim text, then applies the two-tier
credibility classifier in `src/web_verify.py` to decide whether to promote
the claim back to `passed=True`.

Tier 1 (`verified`): allowlist domain (FT, Reuters, Bloomberg, Le Monde,
company-official, government/EU regulator, …). Auto-promote.

Tier 2 (`corroborated`): non-allowlist domain whose body contains an
entity / number anchor present in the original claim. Promote with a
distinguishing flag so the report can render "corroborated" differently.

Both promote the claim AND append the rescue source to the EvidenceLedger
under `EvidenceKind.CLAIM_VERIFICATION` so subsequent reads see the source.

Why this exists: ~85% of claims the meta-eval flagged "fabricated" in v5
were actually real and verifiable from public sources — Carrefour's
Centric Software partnership, L'Oréal's 10-PB data platform, Veolia's
GreenUp program, etc. The fact-checker has too narrow an evidence pool;
this layer widens it to live web search at the cost of ~5-10 Tavily calls
per run (~$0.05-0.10, +15-30s).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import httpx
import mistralai.workflows as workflows
from tavily import AsyncTavilyClient

from src.config import settings
from src.evidence import from_tavily_result
from src.models import (
    EvidenceKind,
    EvidenceLedger,
    FactCheckEntry,
    MetaEvalReview,
)
from scripts._fetch import extract_main_text, fetch_html  # noqa: I001
from src.trace import trace_step
from src.web_verify import claim_anchor_present, classify_source
from src._rate_limits import TAVILY_API_RATE_LIMIT


async def _deep_read_body(http: httpx.AsyncClient, url: str) -> str:
    """Fetch + extract body text, returning "" on any failure."""
    try:
        html = await fetch_html(http, url, timeout_s=12.0)
    except Exception as e:
        logger.warning("web_verify._deep_read_body: fetch failed (%s) — %s", url, type(e).__name__)
        return ""
    if html is None:
        return ""
    try:
        return extract_main_text(html, max_chars=12000) or ""
    except Exception as e:
        logger.warning("web_verify._deep_read_body: extract failed (%s) — %s", url, type(e).__name__)
        return ""

logger = logging.getLogger(__name__)

# Hard caps to keep cost bounded. Max tier bumps the rescue cap to give
# claim-dense reports more headroom; only fires when there's actually
# that many unsupported claims, so most runs don't hit it.
_MAX_RESCUE_SEARCHES_STANDARD = 12
_MAX_RESCUE_SEARCHES_MAX_TIER = 18
_TAVILY_CONCURRENCY = 3
_TAVILY_TOP_K = 4


def _rescue_cap() -> int:
    from src.config import settings
    return (
        _MAX_RESCUE_SEARCHES_MAX_TIER
        if settings.tier.value == "max"
        else _MAX_RESCUE_SEARCHES_STANDARD
    )


async def _rescue_one_claim(
    claim: FactCheckEntry,
    company_name: str,
    tavily: AsyncTavilyClient,
    http: httpx.AsyncClient,
    sem: asyncio.Semaphore,
) -> tuple[FactCheckEntry, list[tuple[str, str, str]]] | None:
    """Run one rescue search for a single unsupported claim.

    Returns (updated_claim, fetched_sources) on success — fetched_sources is
    a list of (url, title, body) tuples to be appended to the ledger by the
    caller. Returns None if the rescue couldn't be attempted (e.g., empty
    claim, network error).
    """
    if not claim.claim or not claim.claim.strip():
        return None
    query = f"{company_name} {claim.claim}"[:380]
    async with sem:
        try:
            search = await tavily.search(
                query=query,
                search_depth="basic",
                max_results=_TAVILY_TOP_K,
            )
        except Exception as e:
            logger.warning("web_verify: tavily search failed for claim: %s — %s", claim.claim[:80], type(e).__name__)
            return None

    results = search.get("results") if isinstance(search, dict) else None
    if not isinstance(results, list) or not results:
        return None

    # Tier 1 sweep: any allowlisted/government/company-official source wins.
    fetched: list[tuple[str, str, str]] = []
    for r in results:
        url = str(r.get("url") or "")
        title = str(r.get("title") or "")
        snippet = str(r.get("content") or "")
        if not url:
            continue
        tier = classify_source(url, company_name)
        if tier == "verified":
            # We trust the snippet for tier-1; fetching the full body is nice
            # to have but not required for the rescue verdict.
            fetched.append((url, title, snippet))
            updated = claim.model_copy(
                update={
                    "passed": True,
                    "rationale": f"Rescued via web search (verified source): {snippet[:240]}",
                    "rescue_tier": "verified",
                    "rescue_url": url,
                }
            )
            return updated, fetched

    # Tier 2 sweep: corroborated requires entity/number anchor match. Try the
    # snippet first; if no anchor, deep-read the URL once and try again.
    for r in results:
        url = str(r.get("url") or "")
        title = str(r.get("title") or "")
        snippet = str(r.get("content") or "")
        if not url:
            continue
        if classify_source(url, company_name) != "corroborated":
            continue
        if claim_anchor_present(claim.claim, snippet):
            fetched.append((url, title, snippet))
            updated = claim.model_copy(
                update={
                    "passed": True,
                    "rationale": f"Corroborated via web search: {snippet[:240]}",
                    "rescue_tier": "corroborated",
                    "rescue_url": url,
                }
            )
            return updated, fetched
        # Deep-read once for tier-2 — sometimes the snippet is just the page
        # title and the anchor lives in the body.
        body = await _deep_read_body(http, url)
        if body and claim_anchor_present(claim.claim, body):
            fetched.append((url, title, body))
            updated = claim.model_copy(
                update={
                    "passed": True,
                    "rationale": f"Corroborated via web search: {body[:240]}",
                    "rescue_tier": "corroborated",
                    "rescue_url": url,
                }
            )
            return updated, fetched

    return None


@workflows.activity(start_to_close_timeout=timedelta(seconds=180), rate_limit=TAVILY_API_RATE_LIMIT)
async def web_verify_unsupported_claims_activity(
    review: MetaEvalReview,
    claims: list[FactCheckEntry],
    company_name: str,
    ledger: EvidenceLedger | None = None,
) -> tuple[MetaEvalReview, list[FactCheckEntry], EvidenceLedger]:
    """Promote unsupported-but-real claims via Tavily + two-tier credibility.

    Runs AFTER `meta_evaluate_activity`. Hard-capped at
    `_rescue_cap()` Tavily calls (12 standard, 18 on max tier). Returns updated review
    (confidence bumped if recall improved meaningfully), updated claims
    list, and updated ledger with new CLAIM_VERIFICATION entries.

    No-ops gracefully if Tavily isn't configured.
    """
    if ledger is None:
        ledger = EvidenceLedger()
    if not settings.tavily_api_key:
        logger.info("web_verify: TAVILY_API_KEY missing, skipping rescue")
        return review, claims, ledger

    unsupported_idxs = [i for i, c in enumerate(claims) if not c.passed]
    if not unsupported_idxs:
        logger.info("web_verify: no unsupported claims to rescue")
        return review, claims, ledger

    # Cap to budget — max tier gets a higher cap (18 vs 12) for
    # claim-dense reports.
    cap = _rescue_cap()
    capped = unsupported_idxs[:cap]
    skipped = len(unsupported_idxs) - len(capped)
    if skipped:
        logger.warning(
            "web_verify: %d unsupported claims, capping rescue at %d (budget)",
            len(unsupported_idxs), cap,
        )

    tavily = AsyncTavilyClient(api_key=settings.tavily_api_key)
    sem = asyncio.Semaphore(_TAVILY_CONCURRENCY)

    async with trace_step(
        "web_verify",
        "tavily.search",
        "rescue_unsupported_claims",
        inputs_summary=f"company={company_name!r} unsupported={len(capped)} budget={cap}",
    ) as ev:
        async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}, timeout=20.0) as http:
            tasks = [_rescue_one_claim(claims[i], company_name, tavily, http, sem) for i in capped]
            results = await asyncio.gather(*tasks)

        verified_n = corroborated_n = 0
        new_claims = list(claims)
        for idx, outcome in zip(capped, results, strict=True):
            if outcome is None:
                continue
            updated, fetched = outcome
            new_claims[idx] = updated
            if updated.rescue_tier == "verified":
                verified_n += 1
            elif updated.rescue_tier == "corroborated":
                corroborated_n += 1
            for url, title, body in fetched:
                ledger.add(
                    from_tavily_result(
                        url, title, body,
                        kind=EvidenceKind.CLAIM_VERIFICATION,
                        fetched_at_step="web_verify",
                        confidence="medium",
                    )
                )
        ev.outputs_summary = (
            f"rescued: verified={verified_n} corroborated={corroborated_n} "
            f"of {len(capped)} attempted"
        )

    rescued = verified_n + corroborated_n
    if rescued > 0:
        # Use in-scope (non-qualified-out) claims for the rate. Web-verify
        # runs before final_qualify in the pipeline so qualified_out is
        # always False here; the filter is defense-in-depth in case the
        # call order changes.
        all_active = [c for c in claims if not c.qualified_out]
        all_active_new = [c for c in new_claims if not c.qualified_out]
        old_pass = sum(1 for c in all_active if c.passed) / max(1, len(all_active))
        new_pass = sum(1 for c in all_active_new if c.passed) / max(1, len(all_active_new))

        # Re-anchor confidence on the new pass-rate, preserve meta-eval's
        # qualitative delta — but CLAMP the delta to bounded influence.
        # v8 had Mistral 91% pass / 0.55 confidence: meta-eval applied a
        # -0.36 qualitative penalty for a soft cross-cutting concern that
        # carried through every chain step and inverted the relationship
        # between pass-rate and confidence. Clamping to [-0.15, +0.10]
        # keeps meta-eval's structural judgment in play (a real cross-
        # cutting concern can still pull confidence down by up to 15
        # points) while preventing the inversion. Defensible principle:
        # qualitative judgment has bounded influence on top of the
        # supported-fraction baseline.
        qual_delta_raw = review.confidence - old_pass
        qual_delta = max(-0.15, min(0.10, qual_delta_raw))
        new_confidence = max(0.0, min(1.0, new_pass + qual_delta))

        # Rescue-share cap: if rescues are doing >50% of the support work,
        # the report is structurally less robust than one whose support
        # comes from the original ledger. Cap at 0.85 so it can't read as
        # "customer-ready" purely on rescue-derived evidence.
        passed_count = sum(1 for c in new_claims if c.passed)
        rescue_share = rescued / max(1, passed_count)
        capped_by_share = False
        if rescue_share > 0.50:
            capped_at = 0.85
            if new_confidence > capped_at:
                new_confidence = capped_at
                capped_by_share = True

        review = review.model_copy(update={"confidence": new_confidence})
        logger.info(
            "web_verify: rescued %d/%d claims (verified=%d, corroborated=%d); "
            "pass-rate %.2f → %.2f; qual_delta=%+.2f; rescue_share=%.0f%%%s; "
            "confidence → %.2f",
            rescued, len(capped), verified_n, corroborated_n,
            old_pass, new_pass, qual_delta, rescue_share * 100,
            " (capped at 0.85)" if capped_by_share else "",
            review.confidence,
        )
    else:
        logger.info("web_verify: no claims rescued (attempted %d)", len(capped))

    return review, new_claims, ledger
