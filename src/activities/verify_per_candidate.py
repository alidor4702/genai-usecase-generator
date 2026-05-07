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
from src.config import settings
from src.models import (
    CompanyContext,
    ScoredCandidate,
    VerificationBatch,
    VerificationResult,
    VerificationVerdict,
)
from src.prompts import VERIFICATION_SYSTEM

logger = logging.getLogger(__name__)


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


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
    out: list[tuple[str, str]] = []
    for url in urls[: settings.tavily_deep_read_top_n]:
        html = await fetch_html(http, url, timeout_s=12.0)
        if html is None:
            continue
        text = extract_main_text(html, max_chars=6000)
        if text:
            out.append((url, text))
    return out


async def _verify_one(
    sc: ScoredCandidate,
    company_name: str,
    tavily_client: AsyncTavilyClient,
    http: httpx.AsyncClient,
    mistral: Mistral,
    sem: asyncio.Semaphore,
) -> VerificationResult:
    async with sem:
        query = _candidate_query(company_name, sc)
        results = await _tavily_top_results(tavily_client, query)
        urls = [str(r.get("url")) for r in results if r.get("url")]
        snippets = [str(r.get("content") or "")[:1000] for r in results]
        deep_pairs = await _deep_read_top(http, urls)

        evidence_lines: list[str] = []
        for r, snippet in zip(results, snippets, strict=False):
            evidence_lines.append(f"- [{r.get('title')}]({r.get('url')}): {snippet}")
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
            r = await mistral.chat.complete_async(
                model=settings.mistral_verification_model,
                temperature=0.1,
                max_tokens=1200,
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
        except Exception as e:
            logger.warning("verify: LLM call failed for %s: %s", sc.candidate.id, type(e).__name__)
            return VerificationResult(
                candidate_id=sc.candidate.id,
                verdict=VerificationVerdict.PASS,
                rationale="Verifier failed; defaulting to pass per the inconclusive-evidence rule.",
                sources_consulted=urls,
            )

        verdict_str = str(data.get("verdict", "pass"))
        try:
            verdict = VerificationVerdict(verdict_str)
        except ValueError:
            verdict = VerificationVerdict.PASS
        return VerificationResult(
            candidate_id=sc.candidate.id,
            verdict=verdict,
            rationale=str(data.get("rationale", "")),
            sources_consulted=list(data.get("sources_consulted") or urls),
        )


@workflows.activity(start_to_close_timeout=timedelta(seconds=240))
async def verify_top_candidates_activity(
    top_candidates: list[ScoredCandidate],
    ctx: CompanyContext,
    company_name: str,
) -> VerificationBatch:
    if not top_candidates:
        return VerificationBatch(results=[])
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for verification")
    if not settings.tavily_api_key:
        logger.warning("verify: TAVILY_API_KEY missing; defaulting all candidates to pass")
        return VerificationBatch(
            results=[
                VerificationResult(
                    candidate_id=sc.candidate.id,
                    verdict=VerificationVerdict.PASS,
                    rationale="No Tavily key available; defaulting to pass.",
                )
                for sc in top_candidates
            ]
        )

    mistral = Mistral(api_key=settings.mistral_api_key)
    tavily = AsyncTavilyClient(api_key=settings.tavily_api_key)
    sem = asyncio.Semaphore(3)
    async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}) as http:
        results = await asyncio.gather(
            *(_verify_one(sc, company_name, tavily, http, mistral, sem) for sc in top_candidates)
        )
    return VerificationBatch(results=list(results))
