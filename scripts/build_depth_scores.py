"""Score every precedent on architectural depth via Mistral.

For each precedent we ask `mistral-medium-2604`:

    1. depth: int 1-10 (1 = high-level use case label only;
                         10 = full architecture with named components, data flows,
                         tech stack, and concrete metrics)
    2. has_architecture: bool — does the entry describe HOW the system works?
    3. has_metrics: bool      — are there quantitative outcomes?
    4. has_named_tech: bool   — are specific technologies named?

Used at retrieval time: precedents below a depth floor are filtered out so the
generator gets architecturally-meaningful peer examples, not "X uses AI" labels.

Batched (10 precedents per LLM call) and cached. Re-runnable; only scores the
precedents whose `depth_score` column is NULL.
"""

from __future__ import annotations

import asyncio
import json
import logging

from mistralai.client import Mistral

from src.config import settings
from src.db import (
    ensure_schema,
    fetch_precedents_missing_depth,
    update_precedent_depth_batch,
)

logger = logging.getLogger(__name__)


CACHE_PATH = settings.data_raw_dir / "precedents_depth_scores.json"

BATCH_SIZE = 10
MAX_CONCURRENCY = 4


SYSTEM_PROMPT = """\
You are scoring real-world GenAI use case entries on architectural depth.

For each entry the user gives you, return:
- depth (int 1-10)
    1-3: just labels what the AI is for, no HOW (e.g. "X uses AI for fraud detection")
    4-6: names a technology / pattern (e.g. "uses RAG over policy docs")
    7-9: describes architecture, data flows, or specific components
    10:  full multi-component blueprint with stack + flow + metrics
- has_architecture (bool): does it describe HOW the system works?
- has_metrics (bool): does it cite quantitative outcomes (%, latency, throughput, etc.)?
- has_named_tech (bool): are specific named technologies/products mentioned?

Be honest and calibrated. Most entries are 3-5; only true blueprints earn 8+.

Output STRICT JSON, no markdown:
{"scores": [
  {"id": "<entry id>", "depth": int, "has_architecture": bool,
   "has_metrics": bool, "has_named_tech": bool}, ...
]}
The number of items in `scores` must equal the number of entries provided.
"""


def _entry_text(p: dict[str, object]) -> str:
    parts: list[str] = []
    if p.get("title"):
        parts.append(f"Title: {p['title']}")
    if p.get("description"):
        parts.append(f"Description: {p['description']}")
    if p.get("deep_content"):
        parts.append(f"Deep content (first 1500 chars): {str(p['deep_content'])[:1500]}")
    return "\n".join(parts)


async def _score_batch(
    client: Mistral, sem: asyncio.Semaphore, batch: list[dict[str, object]]
) -> list[tuple[str, float, dict[str, object]]]:
    user_msg_lines = [f"Score the following {len(batch)} entries:\n"]
    for p in batch:
        user_msg_lines.append(f"--- id: {p['id']} ---")
        user_msg_lines.append(f"Source: {p.get('source')}")
        user_msg_lines.append(_entry_text(p))
        user_msg_lines.append("")
    user_msg = "\n".join(user_msg_lines)

    async with sem:
        try:
            r = await client.chat.complete_async(
                model="mistral-medium-2604",
                temperature=0.1,
                max_tokens=4000,
                timeout_ms=120_000,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            )
            text = r.choices[0].message.content
            if isinstance(text, list):
                text = "".join(getattr(b, "text", "") for b in text)
            data = json.loads(str(text or ""))
            scores = data.get("scores", [])
            if not isinstance(scores, list):
                return []
        except Exception as e:
            logger.warning("depth batch failed: %s", type(e).__name__)
            return []

    out: list[tuple[str, float, dict[str, object]]] = []
    for s in scores:
        if not isinstance(s, dict) or "id" not in s:
            continue
        depth_int = int(s.get("depth", 0))
        depth_norm = max(0.0, min(1.0, depth_int / 10.0))
        signals = {
            "depth_int": depth_int,
            "has_architecture": bool(s.get("has_architecture", False)),
            "has_metrics": bool(s.get("has_metrics", False)),
            "has_named_tech": bool(s.get("has_named_tech", False)),
        }
        out.append((str(s["id"]), depth_norm, signals))
    return out


def _load_cache() -> dict[str, dict[str, object]]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_cache(data: dict[str, dict[str, object]]) -> None:
    CACHE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


async def run() -> dict[str, int]:
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for depth scoring")
    await ensure_schema()
    todo = await fetch_precedents_missing_depth(limit=10_000)
    logger.info("depth: %d precedents to score", len(todo))
    if not todo:
        return {"scored": 0}

    cache = _load_cache()
    client = Mistral(api_key=settings.mistral_api_key)
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    batches = [todo[i : i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
    logger.info("depth: %d batches of %d", len(batches), BATCH_SIZE)

    results = await asyncio.gather(*(_score_batch(client, sem, b) for b in batches))

    flat: list[tuple[str, float, dict[str, object]]] = []
    for batch in results:
        flat.extend(batch)

    # Persist to JSON cache for reproducibility
    for pid, score, signals in flat:
        cache[pid] = {"depth_score": score, **signals}
    _save_cache(cache)

    # Persist to DB
    await update_precedent_depth_batch(flat)
    logger.info("depth: scored %d precedents", len(flat))
    return {"scored": len(flat)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(asyncio.run(run()))
