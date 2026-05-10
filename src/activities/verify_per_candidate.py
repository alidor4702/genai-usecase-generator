"""Step 5 — Per-candidate verification. Targeted Tavily search + deep-read +
small-model verdict, run in parallel for the top-k scored candidates.

For each candidate we:
  1. Compose a targeted query: "{company} {candidate.title} {key entities}".
  2. Tavily search (depth=advanced, max 4 results).
  3. Deep-read top 1-2 results via httpx + selectolax.
  4. Send candidate + deep-read content to `mistral-small-2603` with the
     verification prompt. Default to `pass` on inconclusive evidence.

Concurrency-bounded so we don't burst Tavily / Mistral.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta

import httpx
import mistralai.workflows as workflows
from mistralai.client import Mistral
from tavily import AsyncTavilyClient

from scripts._fetch import extract_main_text, fetch_html
from src._clients import mistral_client
from src.config import settings
from src.evidence import from_tavily_result
from src.trace import trace_step
from src.models import (
    CompanyContext,
    EvidenceKind,
    EvidenceLedger,
    ScoredCandidate,
    SupportingSnippet,
    VerificationBatch,
    VerificationResult,
    VerificationVerdict,
)
from src.prompts import VERIFICATION_SYSTEM

logger = logging.getLogger(__name__)


from src._util import strip_fence as _strip_fence  # noqa: E402
from src._rate_limits import MISTRAL_API_RATE_LIMIT


def _candidate_query(company_name: str, sc: ScoredCandidate) -> str:
    title = sc.candidate.title
    products = " ".join(sc.candidate.suggested_mistral_products[:2])
    return f"{company_name} {title} {products}".strip()[:200]


async def _tavily_top_results(client: AsyncTavilyClient, query: str) -> list[dict[str, object]]:
    try:
        resp = await client.search(query=query, search_depth="advanced", max_results=4)
    except Exception as e:
        logger.warning("verify: Tavily search failed: %s", type(e).__name__)
        return []
    if not isinstance(resp, dict):
        return []
    return list(resp.get("results", []))


async def _deep_read_top(http: httpx.AsyncClient, urls: list[str]) -> list[tuple[str, str]]:
    """Deep-read top URLs concurrently. Was sequential pre-v9 — that was
    the choke that staggered per-candidate verify verdicts across ~5s of
    wall clock. asyncio.gather brings the deep-read step from sum-of-fetches
    down to max-of-fetches.

    Tier dispatch: max tier reads the top 5 sources per candidate (much
    more grounding density at the cost of 2-3 extra HTTP fetches per
    candidate); standard / fast stay at 2 (the configured
    `tavily_deep_read_top_n`).
    """
    top_n = settings.tavily_deep_read_top_n
    if settings.tier.value == "max":
        top_n = max(top_n, 5)
    targets = urls[:top_n]

    async def _one(url: str) -> tuple[str, str] | None:
        try:
            html = await fetch_html(http, url, timeout_s=12.0)
        except Exception:
            return None
        if html is None:
            return None
        text = extract_main_text(html, max_chars=6000)
        return (url, text) if text else None

    results = await asyncio.gather(*(_one(u) for u in targets))
    return [r for r in results if r is not None]


async def _verify_one(
    sc: ScoredCandidate,
    company_name: str,
    tavily_client: AsyncTavilyClient,
    http: httpx.AsyncClient,
    mistral: Mistral,
    sem: asyncio.Semaphore,
) -> tuple[VerificationResult, list[tuple[str, str, str]]]:
    """Verify one candidate. Returns (result, fetched_sources) where
    fetched_sources is a list of (url, title, content) tuples for every URL
    we deep-read — the caller adds these to the EvidenceLedger so meta-eval
    can verify claims against the source content."""
    async with sem:
        query = _candidate_query(company_name, sc)
        async with trace_step(
            "verify",
            "tavily",
            "search",
            inputs_summary=f"candidate={sc.candidate.id} | query={query[:60]!r}",
        ) as ev:
            results = await _tavily_top_results(tavily_client, query)
            ev.outputs_summary = f"{len(results)} results"
        urls = [str(r.get("url")) for r in results if r.get("url")]
        snippets = [str(r.get("content") or "")[:1000] for r in results]
        deep_pairs = await _deep_read_top(http, urls)
        deep_lookup = {url: body for url, body in deep_pairs}

        evidence_lines: list[str] = []
        fetched_sources: list[tuple[str, str, str]] = []
        for r, snippet in zip(results, snippets, strict=False):
            url = str(r.get("url") or "")
            title = str(r.get("title") or "")
            evidence_lines.append(f"- [{title}]({url}): {snippet}")
            # Prefer deep-read body; fall back to snippet
            body = deep_lookup.get(url) or snippet
            if url and title and body:
                fetched_sources.append((url, title, body))
        for url, body in deep_pairs:
            evidence_lines.append(f"\n--- DEEP-READ {url} ---\n{body[:4000]}")

        user_msg = (
            f"Candidate id: {sc.candidate.id}\n"
            f"Candidate title: {sc.candidate.title}\n"
            f"Candidate description: {sc.candidate.description}\n\n"
            f"Target company: {company_name}\n\n"
            "Search results:\n"
            + ("\n".join(evidence_lines) if evidence_lines else "(no results returned)")
        )

        try:
            async with trace_step(
                "verify",
                settings.mistral_verification_model,
                "chat.complete",
                inputs_summary=f"verdict for {sc.candidate.id}",
            ) as ev:
                r = await mistral.chat.complete_async(
                    model=settings.mistral_verification_model,
                    temperature=0.1,
                    # Bumped from 1500 to 2500 — output now also carries up to
                    # 5 supporting_snippets (≤300 chars each) per candidate
                    # for the v6 grounding-extraction job. 1500 was tight.
                    max_tokens=2500,
                    timeout_ms=90_000,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": VERIFICATION_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                )
                text = r.choices[0].message.content
                if isinstance(text, list):
                    text = "".join(getattr(b, "text", "") for b in text)
                data = json.loads(_strip_fence(str(text or "")))
                ev.outputs_summary = f"verdict={data.get('verdict')!r}"
        except Exception as e:
            logger.warning("verify: LLM call failed for %s: %s", sc.candidate.id, type(e).__name__)
            return (
                VerificationResult(
                    candidate_id=sc.candidate.id,
                    verdict=VerificationVerdict.PASS,
                    rationale="Verifier failed; defaulting to pass per the inconclusive-evidence rule.",
                    sources_consulted=urls,
                ),
                fetched_sources,
            )

        verdict_str = str(data.get("verdict", "pass"))
        try:
            verdict = VerificationVerdict(verdict_str)
        except ValueError:
            verdict = VerificationVerdict.PASS

        snippets_raw = data.get("supporting_snippets") or []
        snippets: list[SupportingSnippet] = []
        if isinstance(snippets_raw, list):
            for s in snippets_raw[:5]:
                if not isinstance(s, dict):
                    continue
                quote = str(s.get("quote") or "").strip()
                url = str(s.get("url") or "").strip()
                if not quote or not url:
                    continue
                snippets.append(
                    SupportingSnippet(
                        quote=quote[:400],
                        url=url,
                        title=(str(s["title"]).strip() if s.get("title") else None),
                    )
                )

        return (
            VerificationResult(
                candidate_id=sc.candidate.id,
                verdict=verdict,
                rationale=str(data.get("rationale", "")),
                sources_consulted=list(data.get("sources_consulted") or urls),
                supporting_snippets=snippets,
            ),
            fetched_sources,
        )


@workflows.activity(start_to_close_timeout=timedelta(seconds=240), rate_limit=MISTRAL_API_RATE_LIMIT)
async def verify_top_candidates_activity(
    top_candidates: list[ScoredCandidate],
    ctx: CompanyContext,
    company_name: str,
    ledger: EvidenceLedger | None = None,
) -> tuple[VerificationBatch, EvidenceLedger]:
    """Per-candidate verification via Tavily + deep-read + Mistral Small verdict.

    Every URL we deep-read for verification is appended to the EvidenceLedger
    so meta-eval can verify the candidate's claims against the same content
    the verifier saw.
    """
    if ledger is None:
        ledger = EvidenceLedger()
    if not top_candidates:
        return VerificationBatch(results=[]), ledger
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for verification")
    if not settings.tavily_api_key:
        logger.warning("verify: TAVILY_API_KEY missing; defaulting all candidates to pass")
        return (
            VerificationBatch(
                results=[
                    VerificationResult(
                        candidate_id=sc.candidate.id,
                        verdict=VerificationVerdict.PASS,
                        rationale="No Tavily key available; defaulting to pass.",
                    )
                    for sc in top_candidates
                ]
            ),
            ledger,
        )

    mistral = mistral_client()
    tavily = AsyncTavilyClient(api_key=settings.tavily_api_key)
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}) as http:
        pairs = await asyncio.gather(
            *(_verify_one(sc, company_name, tavily, http, mistral, sem) for sc in top_candidates)
        )
    results: list[VerificationResult] = []
    for verdict_result, fetched_sources in pairs:
        results.append(verdict_result)
        for url, title, content in fetched_sources:
            ledger.add(
                from_tavily_result(
                    url,
                    title,
                    content,
                    kind=EvidenceKind.PER_CANDIDATE_VERIFICATION,
                    fetched_at_step="verification",
                    confidence="medium",
                )
            )
    logger.info(
        "verify: %d candidates verified | ledger now has %d entries",
        len(results),
        len(ledger.entries),
    )
    return VerificationBatch(results=results), ledger
