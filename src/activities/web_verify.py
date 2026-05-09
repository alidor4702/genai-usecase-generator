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


async def _deep_read_body(http: httpx.AsyncClient, url: str) -> str:
    """Fetch + extract body text, returning "" on any failure."""
    try:
        html = await fetch_html(http, url, timeout_s=12.0)
    except Exception:
        return ""
    if html is None:
        return ""
    try:
        return extract_main_text(html, max_chars=8000) or ""
    except Exception:
        return ""

logger = logging.getLogger(__name__)

# Hard caps to keep cost bounded.
_MAX_RESCUE_SEARCHES_PER_RUN = 12
_TAVILY_CONCURRENCY = 3
_TAVILY_TOP_K = 4


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


@workflows.activity(start_to_close_timeout=timedelta(seconds=180))
async def web_verify_unsupported_claims_activity(
    review: MetaEvalReview,
    claims: list[FactCheckEntry],
    company_name: str,
    ledger: EvidenceLedger | None = None,
) -> tuple[MetaEvalReview, list[FactCheckEntry], EvidenceLedger]:
    """Promote unsupported-but-real claims via Tavily + two-tier credibility.

    Runs AFTER `meta_evaluate_activity`. Hard-capped at
    `_MAX_RESCUE_SEARCHES_PER_RUN` Tavily calls. Returns updated review
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

    # Cap to budget.
    capped = unsupported_idxs[:_MAX_RESCUE_SEARCHES_PER_RUN]
    skipped = len(unsupported_idxs) - len(capped)
    if skipped:
        logger.warning(
            "web_verify: %d unsupported claims, capping rescue at %d (budget)",
            len(unsupported_idxs), _MAX_RESCUE_SEARCHES_PER_RUN,
        )

    tavily = AsyncTavilyClient(api_key=settings.tavily_api_key)
    sem = asyncio.Semaphore(_TAVILY_CONCURRENCY)

    async with trace_step(
        "web_verify",
        "tavily.search",
        "rescue_unsupported_claims",
        inputs_summary=f"company={company_name!r} unsupported={len(capped)} budget={_MAX_RESCUE_SEARCHES_PER_RUN}",
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
        old_pass = sum(1 for c in claims if c.passed) / max(1, len(claims))
        new_pass = sum(1 for c in new_claims if c.passed) / max(1, len(new_claims))
        delta = new_pass - old_pass
        # Bump confidence by the recall improvement, capped at +0.10. The
        # claim-list ratio is the source of truth per META_EVALUATION_SYSTEM,
        # so this keeps the review honest.
        bumped = min(1.0, review.confidence + min(0.10, delta))
        review = review.model_copy(update={"confidence": bumped})
        logger.info(
            "web_verify: rescued %d/%d claims (verified=%d, corroborated=%d); "
            "fact-check pass-rate %.2f → %.2f; confidence bumped to %.2f",
            rescued, len(capped), verified_n, corroborated_n,
            old_pass, new_pass, review.confidence,
        )
    else:
        logger.info("web_verify: no claims rescued (attempted %d)", len(capped))

    return review, new_claims, ledger
