"""Cache layer for research sub-tasks.

Tiered TTLs from `src.config.settings` (Wikipedia 30 days, news 24h, jobs 48h,
existing initiatives 7 days, per-candidate verification 7 days). Backed by the
`cache` table in SQLite.

The interface is intentionally small — `get` / `set` of JSON-serializable values
keyed by `(company_name, data_type)`. Activities call this at the top of each
research sub-task; on hit they skip the network round-trip entirely. This keeps
repeat runs on the same company cheap.

Production-migration path: swap `_SQLiteCache` for `_RedisCache` (interface
identical). Mentioned only — not implemented in the prototype.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import aiosqlite

from src.db import connect

logger = logging.getLogger(__name__)


def _make_key(company_name: str, data_type: str) -> str:
    h = hashlib.sha1(f"{company_name.lower().strip()}|{data_type}".encode()).hexdigest()
    return f"{data_type}:{h[:16]}"


class Cache:
    """Async cache with TTL semantics. Backed by SQLite for the prototype."""

    async def get(self, company_name: str, data_type: str) -> Any | None:
        key = _make_key(company_name, data_type)
        now = int(time.time())
        async with connect() as db:
            async with db.execute(
                "SELECT payload, fetched_at, ttl_seconds FROM cache WHERE cache_key = ?",
                (key,),
            ) as cur:
                row: aiosqlite.Row | None = await cur.fetchone()
        if row is None:
            return None
        if (now - int(row["fetched_at"])) > int(row["ttl_seconds"]):
            return None  # expired — leave the row, it'll be overwritten by next set
        try:
            return json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            logger.warning("cache: bad payload for key=%s, ignoring", key)
            return None

    async def set(
        self,
        company_name: str,
        data_type: str,
        payload: Any,
        ttl_seconds: int,
    ) -> None:
        key = _make_key(company_name, data_type)
        now = int(time.time())
        try:
            payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as e:
            logger.warning("cache: payload not JSON-serializable for key=%s: %s", key, e)
            return
        async with connect() as db:
            await db.execute(
                """INSERT OR REPLACE INTO cache (cache_key, payload, fetched_at, ttl_seconds)
                VALUES (?, ?, ?, ?)""",
                (key, payload_json, now, ttl_seconds),
            )
            await db.commit()


# Module-level singleton — activities call `cache.get(...)` directly
cache = Cache()
