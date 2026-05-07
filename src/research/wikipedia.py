"""Wikipedia/Wikidata structured fetch for the research step.

Uses the public Wikipedia REST API + Wikidata SPARQL — no auth, no key.

Strategy:
    1. Search Wikipedia for the company name → best-matching page title.
    2. Fetch intro extract via the action=query API.
    3. Fetch the page's Wikidata Q-id and pull industry (P452) + country (P17)
       via the Wikidata entity endpoint.

Cached via `src.cache.cache` with the Wikipedia TTL from settings (30 days).
"""

from __future__ import annotations

import logging

import httpx

from src.cache import cache
from src.config import settings
from src.models import WikipediaFacts

logger = logging.getLogger(__name__)


WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"


async def _search_title(client: httpx.AsyncClient, name: str) -> str | None:
    r = await client.get(
        WIKI_API,
        params={
            "action": "query",
            "list": "search",
            "srsearch": name,
            "srlimit": 1,
            "format": "json",
        },
        timeout=10.0,
    )
    r.raise_for_status()
    hits = r.json().get("query", {}).get("search", [])
    if not hits:
        return None
    return hits[0].get("title")


async def _fetch_summary_and_qid(
    client: httpx.AsyncClient, title: str
) -> tuple[str | None, str | None]:
    r = await client.get(
        WIKI_API,
        params={
            "action": "query",
            "prop": "extracts|pageprops",
            "exintro": 1,
            "explaintext": 1,
            "titles": title,
            "format": "json",
            "redirects": 1,
        },
        timeout=15.0,
    )
    r.raise_for_status()
    pages = r.json().get("query", {}).get("pages", {})
    for p in pages.values():
        return p.get("extract"), p.get("pageprops", {}).get("wikibase_item")
    return None, None


async def _fetch_wikidata_facts(
    client: httpx.AsyncClient, qid: str
) -> tuple[str | None, str | None]:
    """Return (industry_label, country_label) for a Wikidata entity."""
    try:
        r = await client.get(WIKIDATA_ENTITY.format(qid=qid), timeout=15.0)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPError:
        return None, None
    entity = data.get("entities", {}).get(qid, {})
    claims = entity.get("claims", {})
    industry_qid = _first_claim_qid(claims.get("P452", []))
    country_qid = _first_claim_qid(claims.get("P17", []))
    industry_label = await _resolve_qid_label(client, industry_qid) if industry_qid else None
    country_label = await _resolve_qid_label(client, country_qid) if country_qid else None
    return industry_label, country_label


def _first_claim_qid(claims: list[dict[str, object]]) -> str | None:
    for c in claims:
        mainsnak = (c.get("mainsnak") or {}) if isinstance(c, dict) else {}
        if not isinstance(mainsnak, dict):
            continue
        datavalue = mainsnak.get("datavalue") or {}
        if isinstance(datavalue, dict):
            value = datavalue.get("value") or {}
            if isinstance(value, dict):
                qid = value.get("id")
                if isinstance(qid, str) and qid.startswith("Q"):
                    return qid
    return None


async def _resolve_qid_label(client: httpx.AsyncClient, qid: str) -> str | None:
    try:
        r = await client.get(WIKIDATA_ENTITY.format(qid=qid), timeout=10.0)
        r.raise_for_status()
        data = r.json()
    except httpx.HTTPError:
        return None
    labels = data.get("entities", {}).get(qid, {}).get("labels", {})
    en = labels.get("en", {})
    return en.get("value") if isinstance(en, dict) else None


async def fetch_wikipedia_facts(company_name: str) -> WikipediaFacts:
    cached = await cache.get(company_name, "wikipedia")
    if cached is not None:
        return WikipediaFacts.model_validate(cached)

    headers = {"User-Agent": settings.user_agent}
    async with httpx.AsyncClient(headers=headers) as client:
        try:
            title = await _search_title(client, company_name)
            if not title:
                facts = WikipediaFacts(found=False)
            else:
                summary, qid = await _fetch_summary_and_qid(client, title)
                industry, country = (None, None)
                if qid:
                    industry, country = await _fetch_wikidata_facts(client, qid)
                facts = WikipediaFacts(
                    found=True,
                    summary=summary,
                    industry=industry,
                    geography=country,
                    business_model=None,
                    founded_context=None,
                    wikidata_id=qid,
                )
        except httpx.HTTPError as e:
            logger.warning("wikipedia fetch failed for %s: %s", company_name, e)
            facts = WikipediaFacts(found=False)

    await cache.set(
        company_name,
        "wikipedia",
        facts.model_dump(),
        ttl_seconds=settings.cache_ttl_wikipedia_seconds,
    )
    return facts
