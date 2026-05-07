"""Verified-companies fuzzy lookup against the local Wikidata-derived index.

Pure local: just rapidfuzz over the `companies` SQLite table. Fast (sub-ms at
this scale), no network call, no LLM. A match boosts research confidence and
sets `is_verified=True` on the CompanyContext.

This is a fast path, NOT a gate — unknown companies still flow through the rest
of the research pipeline.
"""

from __future__ import annotations

import logging

from rapidfuzz import fuzz, process

from src.db import connect
from src.models import VerifiedCompanyMatch

logger = logging.getLogger(__name__)


_INDEX_CACHE: list[tuple[int, str, str | None, str | None, str]] | None = None


async def _load_index() -> list[tuple[int, str, str | None, str | None, str]]:
    """Return list of (rowid, name, industry, country, wikidata_id)."""
    global _INDEX_CACHE
    if _INDEX_CACHE is not None:
        return _INDEX_CACHE
    rows: list[tuple[int, str, str | None, str | None, str]] = []
    async with connect() as db:
        async with db.execute(
            "SELECT rowid, name, industry, country, wikidata_id FROM companies"
        ) as cur:
            async for r in cur:
                rows.append(
                    (
                        int(r["rowid"]),
                        str(r["name"]),
                        r["industry"],
                        r["country"],
                        str(r["wikidata_id"]),
                    )
                )
    _INDEX_CACHE = rows
    logger.info("verification: loaded %d companies into in-memory index", len(rows))
    return rows


async def verify_company(company_name: str, *, threshold: int = 88) -> VerifiedCompanyMatch:
    rows = await _load_index()
    if not rows:
        return VerifiedCompanyMatch(matched=False)

    names = [r[1] for r in rows]
    best = process.extractOne(company_name, names, scorer=fuzz.WRatio, score_cutoff=threshold)
    if best is None:
        return VerifiedCompanyMatch(matched=False)
    name, score, idx = best
    rowid, _, industry, country, wikidata_id = rows[idx]
    return VerifiedCompanyMatch(
        matched=True,
        name=name,
        industry=industry,
        country=country,
        wikidata_id=wikidata_id,
        score=float(score),
    )
