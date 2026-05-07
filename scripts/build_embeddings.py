"""Embed every precedent in the corpus via mistral-embed.

Reads precedents missing embeddings, batches them through the embeddings API,
persists 1024-dim vectors as JSON arrays in the precedents table.

Concurrency: batched-by-N requests in parallel. Mistral's API rate-limits at
the org level so we keep concurrency moderate; if you blow through quota the
worker handles 429s with backoff.

Cost: ~$0.05–0.20 for the full ~1500–2000 precedent corpus at current pricing.
Re-run is a no-op for already-embedded rows (we filter by `embedding IS NULL`).
"""

from __future__ import annotations

import asyncio
import logging

from mistralai.client import Mistral

from src.config import settings
from src.db import (
    count_precedents,
    ensure_schema,
    fetch_precedents_missing_embeddings,
    update_precedent_embedding,
)

logger = logging.getLogger(__name__)


BATCH_SIZE = 32  # texts per API call
MAX_CONCURRENT_BATCHES = 4  # parallel batches in flight


def _text_for_embedding(p: dict[str, object]) -> str:
    """Compose the text we embed: title + description + (deep_content excerpt)."""
    parts: list[str] = []
    if p.get("title"):
        parts.append(str(p["title"]))
    if p.get("description"):
        parts.append(str(p["description"]))
    if p.get("deep_content"):
        # Cap excerpt to keep input size tractable
        parts.append(str(p["deep_content"])[:2000])
    return "\n\n".join(parts).strip() or "(empty)"


async def _embed_batch(
    client: Mistral,
    sem: asyncio.Semaphore,
    batch: list[dict[str, object]],
) -> list[tuple[str, list[float]]]:
    inputs = [_text_for_embedding(p) for p in batch]
    async with sem:
        try:
            resp = await client.embeddings.create_async(
                model=settings.mistral_embedding_model,
                inputs=inputs,
            )
        except Exception as e:
            logger.exception("embed batch failed: %s", type(e).__name__)
            return []
    out: list[tuple[str, list[float]]] = []
    for p, item in zip(batch, resp.data, strict=True):
        out.append((str(p["id"]), list(item.embedding)))
    return out


async def run() -> dict[str, int]:
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY is not set in .env. Required for the embedding pass.")
    await ensure_schema()
    total_before, embedded_before = await count_precedents()
    missing = total_before - embedded_before
    logger.info(
        "embed: %d total precedents, %d already embedded, %d to embed",
        total_before,
        embedded_before,
        missing,
    )

    if missing == 0:
        return {"total": total_before, "newly_embedded": 0}

    client = Mistral(api_key=settings.mistral_api_key)
    sem = asyncio.Semaphore(MAX_CONCURRENT_BATCHES)
    newly_embedded = 0

    while True:
        rows = await fetch_precedents_missing_embeddings(limit=1024)
        if not rows:
            break
        batches = [rows[i : i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]
        results = await asyncio.gather(*(_embed_batch(client, sem, b) for b in batches))
        for batch_result in results:
            for precedent_id, embedding in batch_result:
                await update_precedent_embedding(precedent_id, embedding)
                newly_embedded += 1
        logger.info("embed: %d / %d", newly_embedded, missing)

    total_after, embedded_after = await count_precedents()
    logger.info(
        "embed done: %d total, %d embedded (was %d before)",
        total_after,
        embedded_after,
        embedded_before,
    )
    return {"total": total_after, "newly_embedded": newly_embedded}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = asyncio.run(run())
    print(res)
