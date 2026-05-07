"""Top-level data build entry point.

Subcommands:
    init      -- create SQLite schema (idempotent)
    evidently -- import the Evidently CSV + deep-read text-media URLs
    gcloud    -- scrape both Google Cloud GenAI use case pages
    wikidata  -- pull verified-companies index from Wikidata SPARQL (paged)
    embed     -- generate mistral-embed vectors for every precedent
    status    -- print row counts
    all       -- evidently + gcloud + wikidata + embed (in that order)

Usage:
    uv run python scripts/build_data.py init
    uv run python scripts/build_data.py evidently
    uv run python scripts/build_data.py gcloud
    uv run python scripts/build_data.py wikidata --limit 100000
    uv run python scripts/build_data.py embed
    uv run python scripts/build_data.py all --wikidata-limit 100000
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from scripts import build_embeddings, build_evidently, build_gcloud, build_wikidata
from src.db import count_companies, count_precedents, ensure_schema


async def cmd_status() -> None:
    n_companies = await count_companies()
    total, embedded = await count_precedents()
    print(f"companies              : {n_companies:>10,}")
    print(f"precedents (total)     : {total:>10,}")
    print(f"precedents (embedded)  : {embedded:>10,}")


async def cmd_init() -> None:
    await ensure_schema()
    print("schema ensured")


async def cmd_all(wikidata_limit: int) -> None:
    await ensure_schema()
    print("→ evidently")
    print(await build_evidently.run())
    print("→ gcloud")
    print(await build_gcloud.run())
    print("→ wikidata")
    print(await build_wikidata.run(limit=wikidata_limit))
    print("→ embed")
    print(await build_embeddings.run())
    print("→ status")
    await cmd_status()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the local data layer")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    sub.add_parser("evidently")
    sub.add_parser("gcloud")
    p_wd = sub.add_parser("wikidata")
    p_wd.add_argument("--limit", type=int, default=100_000)
    sub.add_parser("embed")
    p_all = sub.add_parser("all")
    p_all.add_argument("--wikidata-limit", type=int, default=100_000)

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    match args.cmd:
        case "init":
            asyncio.run(cmd_init())
        case "status":
            asyncio.run(cmd_status())
        case "evidently":
            print(asyncio.run(build_evidently.run()))
        case "gcloud":
            print(asyncio.run(build_gcloud.run()))
        case "wikidata":
            print(asyncio.run(build_wikidata.run(limit=args.limit)))
        case "embed":
            print(asyncio.run(build_embeddings.run()))
        case "all":
            asyncio.run(cmd_all(wikidata_limit=args.wikidata_limit))


if __name__ == "__main__":
    main()
