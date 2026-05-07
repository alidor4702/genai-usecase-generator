"""Import Evidently AI's 800+ ML/LLM use cases CSV into the precedents table.

Pattern: every CSV row becomes a shallow precedent. For text-media URLs (~88%
of rows: engineering blogs, articles, web pages), fetch the article body via
httpx + selectolax and store in `deep_content`. YouTube/podcasts/PDFs keep
metadata only.

CSV schema (from data/raw/evidently_800_use_cases.csv):
    Company, Industry, Short Description, Title, Technology, Tag, Year, Link
"""

from __future__ import annotations

import asyncio
import csv
import logging

import httpx

from scripts._fetch import extract_main_text, fetch_html, is_skippable_url
from scripts._normalize import make_id, strip_vendor_terms
from src.config import settings
from src.db import ensure_schema, upsert_precedents

logger = logging.getLogger(__name__)

CSV_PATH = settings.data_raw_dir / "evidently_800_use_cases.csv"

DEEP_FETCH_CONCURRENCY = 16
DEEP_FETCH_TIMEOUT = 12.0


def _read_csv() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            normalized = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            rows.append(normalized)
    return rows


def _row_to_precedent(row: dict[str, str]) -> dict[str, object]:
    company = row.get("Company") or "Unknown"
    industry = row.get("Industry") or None
    title = row.get("Title") or row.get("Short Description (< 5 words)") or "Untitled"
    short = row.get("Short Description (< 5 words)") or ""
    tech = row.get("Technology") or ""
    tag = row.get("Tag") or ""
    year = row.get("Year") or ""
    link = row.get("Link") or None

    description_parts = [short]
    if tech:
        description_parts.append(f"Technology: {tech}.")
    if tag:
        description_parts.append(f"Tags: {tag}.")
    if year:
        description_parts.append(f"Year: {year}.")
    description = strip_vendor_terms(" ".join(p for p in description_parts if p)).strip()
    if not description:
        description = strip_vendor_terms(title)

    return {
        "id": make_id("evidently", company, title),
        "company": company,
        "industry": industry,
        "title": strip_vendor_terms(title),
        "description": description,
        "outcome": None,
        "deep_content": None,  # filled in by deep-read pass
        "source_url": link,
        "source": "evidently",
        "embedding": None,
    }


async def _deep_read_one(client: httpx.AsyncClient, sem: asyncio.Semaphore, url: str) -> str | None:
    if not url or is_skippable_url(url):
        return None
    async with sem:
        html = await fetch_html(client, url, timeout_s=DEEP_FETCH_TIMEOUT)
        if html is None:
            return None
        text = extract_main_text(html)
        return strip_vendor_terms(text) if text else None


async def run(deep_read: bool = True) -> dict[str, int]:
    """Import the Evidently CSV; optionally deep-read text-media URLs."""

    if not CSV_PATH.exists():
        raise FileNotFoundError(f"Expected CSV at {CSV_PATH}")

    await ensure_schema()
    raw = _read_csv()
    precedents = [_row_to_precedent(r) for r in raw if r.get("Title")]
    logger.info("evidently: parsed %d rows", len(precedents))

    if deep_read:
        sem = asyncio.Semaphore(DEEP_FETCH_CONCURRENCY)
        progress = {"done": 0, "total": len(precedents)}

        async def _wrapped(client: httpx.AsyncClient, url: str) -> str | None:
            content = await _deep_read_one(client, sem, url)
            progress["done"] += 1
            if progress["done"] % 100 == 0:
                logger.info("evidently deep-read: %d/%d", progress["done"], progress["total"])
            return content

        async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}) as client:
            results = await asyncio.gather(
                *(_wrapped(client, p["source_url"] or "") for p in precedents),
                return_exceptions=False,
            )
        for p, content in zip(precedents, results, strict=True):
            if isinstance(content, str):
                p["deep_content"] = content

    written = await upsert_precedents(precedents)
    deep_count = sum(1 for p in precedents if p["deep_content"])
    logger.info("evidently: wrote %d precedents (%d with deep_content)", written, deep_count)
    return {"total": written, "with_deep_content": deep_count}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = asyncio.run(run())
    print(res)
