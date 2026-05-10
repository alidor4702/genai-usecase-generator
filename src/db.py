"""SQLite layer — companies index, precedent corpus, cache.

All DB access flows through this module. Schema is created idempotently on
first connection. Async via aiosqlite to keep activities non-blocking, even
though SQLite operations are typically fast — we want one async story across
the codebase.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from src.config import settings

# Lazy-init guard for ensure_schema(). Runs once per process, per-db-path
# at the first connect(). Without this, `cache` table is missing on a
# fresh deploy (Render's container has no prior data/cache.db) and the
# pipeline crashes with `OperationalError: no such table: cache` on
# the first research-step cache lookup.
_schema_lock = asyncio.Lock()
_schema_initialised: set[str] = set()

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    wikidata_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    aliases TEXT,            -- JSON array
    industry TEXT,
    country TEXT,
    UNIQUE(name, country)
);
CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(name);
CREATE INDEX IF NOT EXISTS idx_companies_industry ON companies(industry);

CREATE TABLE IF NOT EXISTS precedents (
    id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    industry TEXT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    outcome TEXT,
    deep_content TEXT,
    source_url TEXT,
    source TEXT,             -- 'evidently' | 'google_cloud_1001' | 'google_cloud_1302' | 'google_cloud_blueprints'
    embedding TEXT           -- JSON array of floats; NULL until embedded
);
CREATE INDEX IF NOT EXISTS idx_precedents_industry ON precedents(industry);
CREATE INDEX IF NOT EXISTS idx_precedents_company ON precedents(company);
CREATE INDEX IF NOT EXISTS idx_precedents_source ON precedents(source);

CREATE TABLE IF NOT EXISTS cache (
    cache_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,   -- JSON
    fetched_at INTEGER NOT NULL,  -- unix epoch
    ttl_seconds INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_fetched_at ON cache(fetched_at);

-- Run history. Persists every completed pipeline run so the FE
-- /history page can browse past reports and the user can re-open any
-- one. Stored as full JSON so we don't have to migrate the schema
-- every time the Report model evolves.
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    status TEXT NOT NULL,          -- 'completed' | 'refused' | 'failed'
    started_at INTEGER NOT NULL,   -- unix epoch
    completed_at INTEGER NOT NULL,
    fact_check_pass_rate REAL,
    meta_eval_confidence REAL,
    sales_engineer_ready INTEGER,  -- 0 / 1
    report_json TEXT,              -- Report.model_dump_json() — null on refused/failed
    report_markdown TEXT,
    refusal_reason TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_company ON runs(company_name);
"""


async def ensure_schema(path: Path | None = None) -> None:
    db_path = path or settings.sqlite_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        # Idempotent migrations — run after schema create
        async with db.execute("PRAGMA table_info(precedents)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        if "depth_score" not in cols:
            await db.execute("ALTER TABLE precedents ADD COLUMN depth_score REAL")
        if "depth_signals" not in cols:
            await db.execute("ALTER TABLE precedents ADD COLUMN depth_signals TEXT")
        await db.commit()


@asynccontextmanager
async def connect(path: Path | None = None) -> AsyncIterator[aiosqlite.Connection]:
    db_path = path or settings.sqlite_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Lazy-init the schema on first connect so a fresh deploy (no
    # data/cache.db) doesn't crash the pipeline with "no such table".
    key = str(db_path)
    if key not in _schema_initialised:
        async with _schema_lock:
            if key not in _schema_initialised:
                await ensure_schema(db_path)
                _schema_initialised.add(key)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        yield db


# ---- companies -------------------------------------------------------------


async def upsert_companies(rows: list[dict[str, str | list[str] | None]]) -> int:
    """Insert or replace company rows. Each row needs `wikidata_id`, `name`,
    optional `aliases` (list[str]), `industry`, `country`.
    Returns number of rows written.
    """
    if not rows:
        return 0
    async with connect() as db:
        await db.executemany(
            """INSERT OR REPLACE INTO companies (wikidata_id, name, aliases, industry, country)
            VALUES (?, ?, ?, ?, ?)""",
            [
                (
                    r.get("wikidata_id"),
                    r.get("name"),
                    json.dumps(r.get("aliases") or []),
                    r.get("industry"),
                    r.get("country"),
                )
                for r in rows
            ],
        )
        await db.commit()
    return len(rows)


async def count_companies() -> int:
    async with connect() as db:
        async with db.execute("SELECT COUNT(*) FROM companies") as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0


# ---- precedents ------------------------------------------------------------


async def upsert_precedents(rows: list[dict[str, object]]) -> int:
    if not rows:
        return 0
    async with connect() as db:
        await db.executemany(
            """INSERT OR REPLACE INTO precedents
            (id, company, industry, title, description, outcome, deep_content, source_url, source, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    r["id"],
                    r["company"],
                    r.get("industry"),
                    r["title"],
                    r["description"],
                    r.get("outcome"),
                    r.get("deep_content"),
                    r.get("source_url"),
                    r.get("source"),
                    json.dumps(r["embedding"]) if r.get("embedding") is not None else None,
                )
                for r in rows
            ],
        )
        await db.commit()
    return len(rows)


async def update_precedent_embedding(precedent_id: str, embedding: list[float]) -> None:
    async with connect() as db:
        await db.execute(
            "UPDATE precedents SET embedding = ? WHERE id = ?",
            (json.dumps(embedding), precedent_id),
        )
        await db.commit()


async def count_precedents() -> tuple[int, int]:
    """Return (total, with_embedding)."""
    async with connect() as db:
        async with db.execute("SELECT COUNT(*) FROM precedents") as cur:
            total_row = await cur.fetchone()
        async with db.execute("SELECT COUNT(*) FROM precedents WHERE embedding IS NOT NULL") as cur:
            embedded_row = await cur.fetchone()
    total = int(total_row[0]) if total_row else 0
    embedded = int(embedded_row[0]) if embedded_row else 0
    return total, embedded


async def fetch_precedents_missing_embeddings(
    limit: int = 1000,
) -> list[dict[str, object]]:
    async with connect() as db:
        async with db.execute(
            """SELECT id, company, industry, title, description, outcome, deep_content, source
            FROM precedents WHERE embedding IS NULL LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def fetch_precedents_missing_depth(
    limit: int = 5000,
) -> list[dict[str, object]]:
    async with connect() as db:
        async with db.execute(
            """SELECT id, company, industry, title, description, deep_content, source
            FROM precedents WHERE depth_score IS NULL LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def update_precedent_depth(
    precedent_id: str, depth_score: float, signals: dict[str, object]
) -> None:
    async with connect() as db:
        await db.execute(
            "UPDATE precedents SET depth_score = ?, depth_signals = ? WHERE id = ?",
            (depth_score, json.dumps(signals), precedent_id),
        )
        await db.commit()


async def update_precedent_depth_batch(
    items: list[tuple[str, float, dict[str, object]]],
) -> None:
    if not items:
        return
    async with connect() as db:
        await db.executemany(
            "UPDATE precedents SET depth_score = ?, depth_signals = ? WHERE id = ?",
            [(score, json.dumps(signals), pid) for pid, score, signals in items],
        )
        await db.commit()


async def load_all_precedents_for_index() -> list[dict[str, object]]:
    """Load every precedent that has an embedding — used to build the in-memory matrix."""
    async with connect() as db:
        async with db.execute(
            """SELECT id, company, industry, title, description, outcome, deep_content,
            source_url, source, depth_score, embedding
            FROM precedents WHERE embedding IS NOT NULL"""
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ---- runs (run history persistence) ----------------------------------------


async def persist_run(
    *,
    run_id: str,
    company_name: str,
    status: str,
    started_at: int,
    completed_at: int,
    fact_check_pass_rate: float | None,
    meta_eval_confidence: float | None,
    sales_engineer_ready: bool | None,
    report_json: str | None,
    report_markdown: str | None,
    refusal_reason: str | None,
    error: str | None,
) -> None:
    """Save a finished pipeline run to the runs table. Idempotent on
    run_id so re-runs (or retries) overwrite cleanly."""
    async with connect() as db:
        await db.execute(
            """INSERT OR REPLACE INTO runs
            (run_id, company_name, status, started_at, completed_at,
             fact_check_pass_rate, meta_eval_confidence, sales_engineer_ready,
             report_json, report_markdown, refusal_reason, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, company_name, status, started_at, completed_at,
                fact_check_pass_rate, meta_eval_confidence,
                int(sales_engineer_ready) if sales_engineer_ready is not None else None,
                report_json, report_markdown, refusal_reason, error,
            ),
        )
        await db.commit()


async def list_runs(limit: int = 50, offset: int = 0) -> list[dict[str, object]]:
    """Browse history — most-recent-first. Returns lightweight rows
    (no full report_json) for the FE list view."""
    async with connect() as db:
        async with db.execute(
            """SELECT run_id, company_name, status, started_at, completed_at,
            fact_check_pass_rate, meta_eval_confidence, sales_engineer_ready,
            refusal_reason, error
            FROM runs
            ORDER BY started_at DESC
            LIMIT ? OFFSET ?""",
            (limit, offset),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_run(run_id: str) -> dict[str, object] | None:
    """Fetch a full persisted run including report_json + markdown."""
    async with connect() as db:
        async with db.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def count_runs() -> int:
    async with connect() as db:
        async with db.execute("SELECT COUNT(*) FROM runs") as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0
