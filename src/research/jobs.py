"""AI / ML / data role hiring signal.

Approach:
  1. Tavily-search for "{company} careers AI ML engineer" to find a small set of
     career-page URLs.
  2. Fetch them with httpx; if content is too thin (likely JS-rendered), fall
     back to Playwright + Lightpanda CDP backend per CLAUDE.md.
  3. Synthesize a `JobsSignal` summarizing what AI/ML roles the company is
     hiring for, with notable postings.

Lightpanda fallback is only triggered when httpx returns no usable content. We
don't run the headless browser by default — too heavy.
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx

try:
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

from scripts._fetch import extract_main_text, fetch_html
from src.cache import cache
from src.config import settings
from src.models import JobPostingSignal, JobsSignal

logger = logging.getLogger(__name__)


AI_ROLE_RE = re.compile(
    r"\b(ai|machine learning|ml engineer|data scientist|nlp|llm|generative ai|"
    r"applied scientist|prompt engineer|vision engineer|research engineer)\b",
    re.IGNORECASE,
)


async def _tavily_career_urls(company_name: str) -> list[str]:
    if not settings.tavily_api_key:
        return []
    from tavily import AsyncTavilyClient

    client = AsyncTavilyClient(api_key=settings.tavily_api_key)
    try:
        resp = await client.search(
            query=f"{company_name} careers AI machine learning engineer",
            max_results=4,
        )
    except Exception as e:
        logger.warning("jobs: Tavily search failed: %s", type(e).__name__)
        return []
    if not isinstance(resp, dict):
        return []
    return [str(r.get("url")) for r in resp.get("results", []) if r.get("url")]


async def _httpx_fetch_and_extract(client: httpx.AsyncClient, url: str) -> str | None:
    html = await fetch_html(client, url, timeout_s=12.0)
    if html is None:
        return None
    return extract_main_text(html, max_chars=10_000)


async def _playwright_fetch(url: str) -> str | None:
    if not PLAYWRIGHT_AVAILABLE:
        return None
    try:
        async with async_playwright() as pw:
            if settings.lightpanda_cdp_url:
                # Lightpanda runs as a CDP server we connect to
                browser = await pw.chromium.connect_over_cdp(settings.lightpanda_cdp_url)
            else:
                browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, timeout=20_000, wait_until="networkidle")
            html = await page.content()
            await browser.close()
        return extract_main_text(html, max_chars=10_000)
    except Exception as e:
        logger.warning("jobs: Playwright fetch failed for %s: %s", url, type(e).__name__)
        return None


def _summarize_postings(text: str) -> list[JobPostingSignal]:
    """Pull out lines mentioning AI/ML roles."""
    out: list[JobPostingSignal] = []
    for line in text.splitlines():
        s = line.strip()
        if 20 <= len(s) <= 300 and AI_ROLE_RE.search(s):
            out.append(JobPostingSignal(role_title=s[:200], description=s))
        if len(out) >= 8:
            break
    return out


async def fetch_jobs_signal(company_name: str) -> JobsSignal | None:
    cached = await cache.get(company_name, "jobs")
    if cached is not None:
        return JobsSignal.model_validate(cached)

    urls = await _tavily_career_urls(company_name)
    if not urls:
        return None

    used_fallback = False
    aggregated_text = ""
    notable: list[JobPostingSignal] = []
    async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}) as client:
        for url in urls:
            text = await _httpx_fetch_and_extract(client, url)
            if not text or len(text) < 500:
                # Fall back to a real browser for likely-JS pages
                text_pw = await _playwright_fetch(url)
                if text_pw:
                    used_fallback = True
                    text = text_pw
            if text:
                aggregated_text += "\n\n" + text
                notable.extend(_summarize_postings(text))

    if not aggregated_text.strip():
        return None

    summary_lines: list[str] = []
    if notable:
        unique_roles = list({p.role_title for p in notable})[:8]
        summary_lines.append(
            f"{len(notable)} relevant openings observed; sample titles: " + "; ".join(unique_roles)
        )
    else:
        summary_lines.append(
            "Career pages reviewed; no AI/ML-specific titles surfaced in the visible content."
        )

    out = JobsSignal(
        summary=" ".join(summary_lines),
        notable_postings=notable[:5],
        used_fallback=used_fallback,
    )
    await cache.set(
        company_name,
        "jobs",
        out.model_dump(),
        ttl_seconds=settings.cache_ttl_jobs_seconds,
    )
    return out


async def fetch_jobs_signal_safe(company_name: str) -> JobsSignal | None:
    """Wraps fetch_jobs_signal so a slow career-page fetch can't kill the whole research step."""
    try:
        return await asyncio.wait_for(fetch_jobs_signal(company_name), timeout=45.0)
    except (TimeoutError, Exception) as e:
        logger.warning("jobs: failed for %s: %s", company_name, type(e).__name__)
        return None
