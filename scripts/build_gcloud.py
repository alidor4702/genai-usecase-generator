"""Scrape both Google Cloud GenAI use case pages into the precedents table.

Sources:
1. "1,001 real-world generative AI use cases" — broad coverage organized by
   industry × agent type (Customer, Employee, Creative, Code, Data, Security).
2. "101 real-world gen AI use cases with technical blueprints" — depth: each
   entry has business challenge + tech stack + blueprint flow.

Pattern: fetch HTML once per page, walk the document by heading boundaries,
extract company names + short descriptions where possible. Vendor-strip
descriptions (drop Vertex AI / BigQuery / etc.) so the generator stays
provider-agnostic.

The HTML structure on Google's content pages changes over time; this scraper
is intentionally tolerant — when we can identify a company-named entry we
take it, otherwise we skip rather than fabricate.
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx
from selectolax.parser import HTMLParser

from scripts._normalize import make_id, strip_vendor_terms
from src.config import settings
from src.db import ensure_schema, upsert_precedents

logger = logging.getLogger(__name__)

URL_1001 = "https://cloud.google.com/transform/101-real-world-generative-ai-use-cases-from-industry-leaders"
URL_BLUEPRINTS = (
    "https://cloud.google.com/blog/products/ai-machine-learning/"
    "real-world-gen-ai-use-cases-with-technical-blueprints"
)

# Heuristic: an entry's leading boldfaced span ("**Mercedes-Benz** is using ...")
# usually identifies the company. We capture that.
COMPANY_LEAD_RE = re.compile(
    r"^([A-Z][\w&'\.\-/ ]{1,60}?)\s+(is|has|deploys|uses|built|piloted|launched)\b"
)

# Industries we expect to see as headings on the 1001 page.
KNOWN_INDUSTRIES = {
    "automotive",
    "automotive & logistics",
    "business & professional services",
    "communications, media & entertainment",
    "consumer & retail",
    "energy",
    "financial services",
    "healthcare",
    "healthcare & life sciences",
    "manufacturing",
    "manufacturing & industrials",
    "public sector",
    "retail",
    "technology",
    "telecommunications",
    "travel & hospitality",
}


def _norm_industry(raw: str) -> str | None:
    if not raw:
        return None
    s = raw.strip().lower()
    return s.title() if s in KNOWN_INDUSTRIES else None


async def _fetch(url: str) -> str | None:
    async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}) as client:
        try:
            r = await client.get(url, follow_redirects=True, timeout=30.0)
            r.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("fetch failed %s: %s", url, e)
            return None
    return r.text


def _walk_paragraphs(tree: HTMLParser) -> list[tuple[str, str]]:
    """Yield (current-industry, paragraph-text) tuples in document order.

    Uses a single CSS selector spanning headings and content blocks; selectolax
    returns hits in document order so we can maintain heading state by scan.
    """
    container = tree.css_first("article") or tree.body
    if container is None:
        return []
    nodes = container.css("h1, h2, h3, h4, p, li")
    out: list[tuple[str, str]] = []
    current_industry = ""
    for node in nodes:
        if node.tag in ("h1", "h2", "h3", "h4"):
            t = node.text(separator=" ", strip=True)
            normalized = _norm_industry(t)
            if normalized:
                current_industry = normalized
        else:  # p or li
            txt = node.text(separator=" ", strip=True)
            if txt and len(txt) > 40:
                out.append((current_industry, txt))
    return out


def _extract_company(paragraph: str) -> str | None:
    m = COMPANY_LEAD_RE.match(paragraph.strip())
    if m:
        candidate = m.group(1).strip().rstrip(",.:;")
        if 2 <= len(candidate) <= 60 and not candidate.lower().startswith(("the ", "a ", "an ")):
            return candidate
    return None


def _make_precedent(
    *,
    company: str,
    industry: str | None,
    raw_text: str,
    source_url: str,
    source: str,
) -> dict[str, object]:
    desc = strip_vendor_terms(raw_text)
    title = strip_vendor_terms(_first_sentence(raw_text))
    return {
        "id": make_id(source, company, title),
        "company": company,
        "industry": industry,
        "title": title,
        "description": desc,
        "outcome": None,
        "deep_content": None,
        "source_url": source_url,
        "source": source,
        "embedding": None,
    }


def _first_sentence(text: str, max_chars: int = 200) -> str:
    text = text.strip()
    m = re.search(r"[.!?]", text)
    if m and m.start() < max_chars:
        return text[: m.start() + 1].strip()
    return text[:max_chars].rstrip() + ("…" if len(text) > max_chars else "")


async def scrape_1001() -> int:
    html = await _fetch(URL_1001)
    if html is None:
        return 0
    tree = HTMLParser(html)
    paragraphs = _walk_paragraphs(tree)
    logger.info("gcloud 1001: %d paragraphs to consider", len(paragraphs))

    precedents: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for industry, txt in paragraphs:
        company = _extract_company(txt)
        if company is None:
            continue
        p = _make_precedent(
            company=company,
            industry=industry or None,
            raw_text=txt,
            source_url=URL_1001,
            source="google_cloud_1001",
        )
        if p["id"] in seen_ids:
            continue
        seen_ids.add(str(p["id"]))
        precedents.append(p)

    written = await upsert_precedents(precedents)
    logger.info("gcloud 1001: wrote %d precedents", written)
    return written


async def scrape_blueprints() -> int:
    html = await _fetch(URL_BLUEPRINTS)
    if html is None:
        return 0
    tree = HTMLParser(html)
    paragraphs = _walk_paragraphs(tree)
    logger.info("gcloud blueprints: %d paragraphs to consider", len(paragraphs))

    precedents: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for industry, txt in paragraphs:
        company = _extract_company(txt)
        if company is None:
            continue
        p = _make_precedent(
            company=company,
            industry=industry or None,
            raw_text=txt,
            source_url=URL_BLUEPRINTS,
            source="google_cloud_blueprints",
        )
        # Blueprints page has architecture detail; deep_content gets the
        # surrounding text for richer context.
        p["deep_content"] = strip_vendor_terms(txt)
        if p["id"] in seen_ids:
            continue
        seen_ids.add(str(p["id"]))
        precedents.append(p)

    written = await upsert_precedents(precedents)
    logger.info("gcloud blueprints: wrote %d precedents", written)
    return written


async def run() -> dict[str, int]:
    await ensure_schema()
    n1 = await scrape_1001()
    n2 = await scrape_blueprints()
    return {"google_cloud_1001": n1, "google_cloud_blueprints": n2}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = asyncio.run(run())
    print(res)
