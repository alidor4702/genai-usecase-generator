"""Pull company entities from Wikidata SPARQL into the verified-companies index.

Filters to companies (Q783794) that have an English Wikipedia article — this
keeps the index practical (~100k–500k entities) and trims long-tail Q-IDs
that nobody types as a company name. Pages via LIMIT/OFFSET; Wikidata's
SPARQL endpoint enforces a 60s per-query cap.

The verified-companies index is purely a confidence-boost fast path during
research; it never gates access to unknown companies. See
docs/architecture.md (cold-start handling).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from src.config import settings
from src.db import ensure_schema, upsert_companies

logger = logging.getLogger(__name__)


WDQS_URL = "https://query.wikidata.org/sparql"

PAGE_SIZE = 5_000
MAX_RETRIES_PER_PAGE = 3
INTER_PAGE_DELAY_SECONDS = 1.0


def _sparql_query(limit: int, offset: int, root_class: str) -> str:
    """Direct instance-of query (no transitive subclass) — keeps the query
    cheap enough for Wikidata's SPARQL endpoint to serve without 502s.

    The transitive `wdt:P31/wdt:P279*` variant catches more entities but blows
    past the endpoint's compute limits on large root classes like Q4830453.
    Trade-off accepted: smaller-but-served index over a broader-but-failing
    query. The runtime Wikipedia/Tavily fallbacks (Fix 4 + Fix 5) cover the
    long tail of companies not in this index.
    """
    return f"""\
SELECT DISTINCT ?company ?companyLabel ?industryLabel ?countryLabel WHERE {{
  ?company wdt:P31 wd:{root_class} .
  ?wp schema:about ?company ;
      schema:isPartOf <https://en.wikipedia.org/> .
  OPTIONAL {{ ?company wdt:P452 ?industry . }}
  OPTIONAL {{ ?company wdt:P17 ?country . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
}}
ORDER BY ?company
LIMIT {limit} OFFSET {offset}
"""


# Direct-instance-of root classes. Q4830453 = business, Q783794 = company,
# Q161726 = multinational corporation, Q1525627 = corporation. UPSERT collapses
# overlap on Q-id PRIMARY KEY so multi-pass writes don't double count.
ROOT_CLASSES = ("Q783794", "Q4830453", "Q161726", "Q1525627")


def _parse_bindings(json_payload: dict[str, Any]) -> list[dict[str, str | None]]:
    bindings = json_payload.get("results", {}).get("bindings", [])
    rows: list[dict[str, str | None]] = []
    for b in bindings:
        company_uri = b.get("company", {}).get("value", "")
        if not company_uri:
            continue
        wikidata_id = company_uri.rsplit("/", 1)[-1]  # e.g. "Q312"
        name = b.get("companyLabel", {}).get("value")
        if not name or name.startswith("Q"):  # label fallback returned the Q-id
            continue
        rows.append(
            {
                "wikidata_id": wikidata_id,
                "name": name,
                "industry": b.get("industryLabel", {}).get("value"),
                "country": b.get("countryLabel", {}).get("value"),
            }
        )
    return rows


async def _fetch_page(
    client: httpx.AsyncClient, limit: int, offset: int, root_class: str = "Q783794"
) -> list[dict[str, str | None]]:
    query = _sparql_query(limit, offset, root_class)
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES_PER_PAGE + 1):
        try:
            r = await client.get(
                WDQS_URL,
                params={"query": query, "format": "json"},
                timeout=70.0,
                headers={
                    "Accept": "application/sparql-results+json",
                    "User-Agent": settings.user_agent,
                },
            )
            r.raise_for_status()
            return _parse_bindings(r.json())
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            last_err = e
            backoff = 2.0 * attempt
            logger.warning(
                "wikidata page offset=%d attempt %d/%d failed: %s; backoff %.1fs",
                offset,
                attempt,
                MAX_RETRIES_PER_PAGE,
                type(e).__name__,
                backoff,
            )
            await asyncio.sleep(backoff)
    logger.error(
        "wikidata page offset=%d giving up after %d attempts", offset, MAX_RETRIES_PER_PAGE
    )
    if last_err:
        raise last_err
    return []


async def run(limit: int = 100_000) -> int:
    """Pull up to `limit` entities per root class. Walks each root class
    (Q4830453 business, Q783794 company) with transitive subclass paths.
    Returns the cumulative number of rows written across both passes.

    UPSERT semantics: same Wikidata Q-id collapses on PRIMARY KEY, so
    overlap between root classes doesn't double-count rows.
    """
    await ensure_schema()
    written_total = 0
    async with httpx.AsyncClient() as client:
        for root_class in ROOT_CLASSES:
            offset = 0
            class_written = 0
            while offset < limit:
                page_size = min(PAGE_SIZE, limit - offset)
                rows = await _fetch_page(client, page_size, offset, root_class)
                if not rows:
                    logger.info("wikidata: %s empty at offset=%d, moving on", root_class, offset)
                    break
                written = await upsert_companies(rows)
                class_written += written
                written_total += written
                logger.info(
                    "wikidata: root=%s offset=%d page=%d (class=%d, total=%d)",
                    root_class,
                    offset,
                    len(rows),
                    class_written,
                    written_total,
                )
                offset += page_size
                await asyncio.sleep(INTER_PAGE_DELAY_SECONDS)
    logger.info("wikidata: done, wrote %d row-upserts across all root classes", written_total)
    return written_total


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    n = asyncio.run(run(limit=limit))
    print(f"wrote {n} companies")
