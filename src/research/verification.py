"""Verified-companies lookup — fast-path fuzzy match, then live-search fallthrough.

Two-stage verification:
  1. Fast path: rapidfuzz over the local Wikidata-derived `companies` SQLite
     table (sub-ms, no network). Hits give us rich data — wikidata_id, industry,
     country — that downstream synthesis benefits from.
  2. Fallthrough: when the fuzzy match misses (e.g. famous companies that
     weren't in the narrow Wikidata index build), live Tavily search of
     `"{name} official website"` + `"{name} company about"`. If credible-domain
     results return with the company name in the title, we treat that as a real-
     world verification and return matched=True with score=85.

This way unknown-to-our-index companies still get `is_verified=True` when they
have a public web presence — the metric reflects "is this a real company" not
"is this in our snapshot."

Live verification also surfaces the read URLs so the caller (research activity)
can append them to the EvidenceLedger.
"""

from __future__ import annotations

import logging
import unicodedata
from urllib.parse import urlparse

import httpx
from rapidfuzz import fuzz, process
from tavily import AsyncTavilyClient

from src.config import settings
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


async def _verify_fuzzy(company_name: str, threshold: int) -> VerifiedCompanyMatch:
    rows = await _load_index()
    if not rows:
        return VerifiedCompanyMatch(matched=False)

    names = [r[1] for r in rows]
    best = process.extractOne(company_name, names, scorer=fuzz.WRatio, score_cutoff=threshold)
    if best is None:
        return VerifiedCompanyMatch(matched=False)
    name, score, idx = best
    _, _, industry, country, wikidata_id = rows[idx]
    return VerifiedCompanyMatch(
        matched=True,
        name=name,
        industry=industry,
        country=country,
        wikidata_id=wikidata_id,
        score=float(score),
    )


_CREDIBLE_TLDS = {".com", ".fr", ".eu", ".io", ".ai", ".net", ".org", ".co", ".de", ".uk", ".gov"}


def _domain_is_credible(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return any(host.endswith(tld) for tld in _CREDIBLE_TLDS)


def _strip_accents(s: str) -> str:
    """Normalize unicode and drop diacritics so 'L'Oréal' matches 'L'Oreal'."""
    nfd = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in nfd if not unicodedata.combining(ch))


def _name_in_title(company_name: str, title: str) -> bool:
    """True if any 4+ char token from the company name appears in the title.

    Compares accent-stripped lowercased forms so 'L'Oreal' matches 'L'Oréal'.
    """
    name_norm = _strip_accents(company_name.lower())
    title_norm = _strip_accents(title.lower())
    if name_norm in title_norm:
        return True
    tokens = [
        t for t in name_norm.split() if len(t) >= 4 and t not in {"group", "inc"}
    ]
    return any(t in title_norm for t in tokens)


async def _verify_live(
    company_name: str,
) -> tuple[VerifiedCompanyMatch, list[tuple[str, str, str]]]:
    """Live Tavily verification. Returns (match, [(url, title, snippet), ...]).

    Match rule: ≥1 credible-domain hit whose title contains the (accent-
    stripped) company name. The credible-domain + name-in-title check is
    already a tight gate, so a single hit is real signal. The source list
    is for the EvidenceLedger so the URL content can be cited later.
    """
    if not settings.tavily_api_key:
        return VerifiedCompanyMatch(matched=False), []

    tavily = AsyncTavilyClient(api_key=settings.tavily_api_key)
    queries = [
        f"{company_name} official website",
        f"{company_name} company about",
    ]

    sources: list[tuple[str, str, str]] = []
    credible_hits = 0
    seen_urls: set[str] = set()
    for q in queries:
        try:
            resp = await tavily.search(query=q, search_depth="basic", max_results=3)
        except (httpx.HTTPError, ValueError, RuntimeError) as e:
            logger.warning("verify_live: search failed (%s): %s", q, type(e).__name__)
            continue
        if not isinstance(resp, dict):
            continue
        for r in resp.get("results", []):
            url = str(r.get("url") or "")
            title = str(r.get("title") or "")
            content = str(r.get("content") or "")[:1500]
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            if _domain_is_credible(url) and _name_in_title(company_name, title):
                credible_hits += 1
                sources.append((url, title, content))

    if credible_hits >= 1:
        logger.info(
            "verify_live: %s confirmed by %d credible-domain results",
            company_name,
            credible_hits,
        )
        return (
            VerifiedCompanyMatch(matched=True, name=company_name, score=85.0),
            sources,
        )
    logger.info(
        "verify_live: %s NOT confirmed (credible_hits=0)", company_name
    )
    return VerifiedCompanyMatch(matched=False), sources


async def verify_company(
    company_name: str, *, threshold: int = 88
) -> tuple[VerifiedCompanyMatch, list[tuple[str, str, str]]]:
    """Two-stage company verification. Returns (match, ledger_sources).

    Tries fuzzy first; if it misses, falls through to live Tavily search.
    `ledger_sources` are the URLs read during live verification (empty when
    fuzzy already matched). The caller appends them to the EvidenceLedger.
    """
    fuzzy = await _verify_fuzzy(company_name, threshold)
    if fuzzy.matched:
        return fuzzy, []
    live_match, sources = await _verify_live(company_name)
    return live_match, sources
