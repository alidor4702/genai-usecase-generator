"""Recent-news search via Tavily, with deep-read of the top 2-3 results.

Tavily provides AI-friendly snippet extraction. For the top results we fetch
the full article body via httpx + selectolax — the snippet alone is too thin
for the synthesis call to detect strategic signals.

Cached via `src.cache.cache` with the news TTL from settings (24h by default).
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from tavily import AsyncTavilyClient

from scripts._fetch import extract_main_text, fetch_html
from src.cache import cache
from src.config import settings
from src.models import NewsItem

logger = logging.getLogger(__name__)


async def _deep_read(client: httpx.AsyncClient, url: str) -> str | None:
    html = await fetch_html(client, url, timeout_s=12.0)
    if html is None:
        return None
    return extract_main_text(html, max_chars=8000)


async def fetch_recent_news(company_name: str) -> list[NewsItem]:
    cached = await cache.get(company_name, "news")
    if cached is not None:
        return [NewsItem.model_validate(c) for c in cached]

    if not settings.tavily_api_key:
        logger.warning("news: TAVILY_API_KEY not set, skipping news search")
        return []

    client = AsyncTavilyClient(api_key=settings.tavily_api_key)
    query = f"{company_name} AI announcement OR product launch OR partnership 2024 2025 2026"
    try:
        resp = await client.search(
            query=query,
            search_depth="advanced",
            max_results=settings.tavily_max_results,
            topic="news",
        )
    except Exception as e:
        logger.warning("news: Tavily search failed: %s", type(e).__name__)
        return []

    raw_results: list[dict[str, object]] = resp.get("results", []) if isinstance(resp, dict) else []
    items: list[NewsItem] = [
        NewsItem(
            title=str(r.get("title") or "")[:300],
            url=str(r.get("url") or ""),
            snippet=str(r.get("content") or "")[:1500] or None,
            published_at=r.get("published_date") or None,
        )
        for r in raw_results
        if r.get("url")
    ]

    # Deep-read the top N
    top_n = min(settings.tavily_deep_read_top_n, len(items))
    if top_n > 0:
        async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}) as http:
            deep_texts = await asyncio.gather(
                *(_deep_read(http, items[i].url) for i in range(top_n)),
                return_exceptions=True,
            )
        for i, dt in enumerate(deep_texts):
            if isinstance(dt, str):
                items[i].deep_content = dt

    await cache.set(
        company_name,
        "news",
        [it.model_dump() for it in items],
        ttl_seconds=settings.cache_ttl_news_seconds,
    )
    return items
