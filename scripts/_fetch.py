"""Async HTTP helpers used by the corpus build scripts.

Plain `httpx` + `selectolax` for content extraction. Skips obviously non-text
domains (YouTube, podcasts) so we don't spend time fetching audio/video.
"""

from __future__ import annotations

import logging

import httpx
from selectolax.parser import HTMLParser

logger = logging.getLogger(__name__)


# Domains where there's no realistic chance of useful HTML body
NON_TEXT_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "spotify.com",
    "apple.com/podcasts",
    "podcasts.apple.com",
    "soundcloud.com",
    "vimeo.com",
)


def is_skippable_url(url: str) -> bool:
    u = url.lower()
    return any(d in u for d in NON_TEXT_DOMAINS) or u.endswith(".pdf")


async def fetch_html(
    client: httpx.AsyncClient, url: str, timeout_s: float = 10.0
) -> str | None:
    if is_skippable_url(url):
        return None
    try:
        r = await client.get(url, follow_redirects=True, timeout=timeout_s)
        r.raise_for_status()
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("fetch fail %s: %s", url, type(e).__name__)
        return None
    ctype = r.headers.get("content-type", "").lower()
    if "html" not in ctype and "text/plain" not in ctype:
        return None
    return r.text


def extract_main_text(html: str, max_chars: int = 20_000) -> str | None:
    """Strip boilerplate, keep main content. Returns None if too short."""
    if not html:
        return None
    try:
        tree = HTMLParser(html)
    except (ValueError, TypeError):
        return None
    for tag in ("script", "style", "nav", "footer", "header", "aside", "form"):
        for n in tree.css(tag):
            n.decompose()
    container = tree.css_first("article") or tree.css_first("main") or tree.body
    if container is None:
        return None
    text = container.text(separator=" ", strip=True)
    text = " ".join(text.split())
    if len(text) < 200:
        return None
    return text[:max_chars]
