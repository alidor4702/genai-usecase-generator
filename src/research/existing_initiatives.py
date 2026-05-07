"""Discover the company's already-deployed AI / GenAI initiatives.

This is the broad sweep that complements per-candidate verification. It feeds
the iconic-potential hard-gate at scoring time — the system must NOT recommend
something the company is already doing.

Sources, in order:
  1. Tavily search across "<company> AI", "<company> generative AI",
     "<company> machine learning". Top results deep-read via httpx + selectolax.
  2. Direct query of the local precedent corpus for entries where company name
     matches — these are pre-vetted public deployments already known to us.

Returns a list of `ExistingInitiative` with description, source, and a
confidence label. Cached for 7 days by default.
"""

from __future__ import annotations

import logging
import re

import httpx
from tavily import AsyncTavilyClient

from scripts._fetch import extract_main_text, fetch_html
from src.cache import cache
from src.config import settings
from src.db import connect
from src.models import ExistingInitiative, ExistingInitiativeSource

logger = logging.getLogger(__name__)


SEARCH_QUERIES = (
    "{name} AI deployment OR launch",
    "{name} generative AI OR GenAI use case",
    "{name} machine learning announcement",
)


async def _query_corpus(company_name: str) -> list[ExistingInitiative]:
    """Return ExistingInitiative entries from our precedent corpus matching the company."""
    pattern = f"%{company_name.lower()}%"
    out: list[ExistingInitiative] = []
    async with connect() as db:
        async with db.execute(
            """SELECT title, description, source_url FROM precedents
            WHERE LOWER(company) LIKE ? LIMIT 8""",
            (pattern,),
        ) as cur:
            async for r in cur:
                out.append(
                    ExistingInitiative(
                        description=str(r["description"])[:600],
                        source=ExistingInitiativeSource.PRECEDENT_CORPUS,
                        source_url=r["source_url"],
                        confidence="high",
                    )
                )
    return out


async def _tavily_initiatives(company_name: str) -> list[ExistingInitiative]:
    if not settings.tavily_api_key:
        return []
    client = AsyncTavilyClient(api_key=settings.tavily_api_key)
    out: list[ExistingInitiative] = []
    async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}) as http:
        for q in SEARCH_QUERIES:
            try:
                resp = await client.search(
                    query=q.format(name=company_name),
                    max_results=3,
                    search_depth="advanced",
                )
            except Exception as e:
                logger.warning("existing_initiatives: search failed: %s", type(e).__name__)
                continue
            if not isinstance(resp, dict):
                continue
            for r in resp.get("results", [])[:2]:
                url = str(r.get("url") or "")
                title = str(r.get("title") or "")
                snippet = str(r.get("content") or "")
                if not url:
                    continue
                # Deep-read the page body to be confident about claims
                html = await fetch_html(http, url, timeout_s=10.0)
                body = extract_main_text(html, max_chars=4000) if html else None
                desc = (body or snippet)[:1000]
                if not desc:
                    continue
                out.append(
                    ExistingInitiative(
                        description=f"{title}: {desc}".strip(": "),
                        source=_classify_source(url),
                        source_url=url,
                        confidence="medium",
                    )
                )
                if len(out) >= 5:
                    break
            if len(out) >= 5:
                break
    return out


_OFFICIAL_DOMAINS_RE = re.compile(
    r"^(www\.)?[\w-]+\.(com|io|ai|net|org|co|gov|edu)$", re.IGNORECASE
)


def _classify_source(url: str) -> ExistingInitiativeSource:
    if "blog" in url.lower() or "engineering" in url.lower() or "tech" in url.lower():
        return ExistingInitiativeSource.ENGINEERING_BLOG
    if "press" in url.lower() or "newsroom" in url.lower() or "about" in url.lower():
        return ExistingInitiativeSource.OFFICIAL_ANNOUNCEMENT
    return ExistingInitiativeSource.NEWS


async def fetch_existing_initiatives(company_name: str) -> list[ExistingInitiative]:
    cached = await cache.get(company_name, "existing_initiatives")
    if cached is not None:
        return [ExistingInitiative.model_validate(c) for c in cached]

    corpus_hits = await _query_corpus(company_name)
    web_hits = await _tavily_initiatives(company_name)

    # De-duplicate by (description first 100 chars) lower
    seen: set[str] = set()
    out: list[ExistingInitiative] = []
    for it in corpus_hits + web_hits:
        key = it.description[:100].lower().strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)

    await cache.set(
        company_name,
        "existing_initiatives",
        [it.model_dump() for it in out],
        ttl_seconds=settings.cache_ttl_existing_initiatives_seconds,
    )
    return out
