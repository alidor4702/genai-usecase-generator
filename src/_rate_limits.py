"""Shared `RateLimit` budgets for Mistral Workflows activity decorators.

The Mistral Workflows SDK supports per-activity rate limiting via the
`rate_limit=RateLimit(time_window_in_sec=N, max_execution=M, key="...")`
argument on `@workflows.activity`. Activities that share a `key` share
a single budget pool across all workers and all pipeline runs.

We use two pools:

  ``mistral-api``  — every activity that calls the Mistral API
                     (LLM completions + embeddings). Cap: 30 calls per
                     60 seconds. A single pipeline run makes roughly 40
                     LLM calls spread across ~200 seconds, so within
                     budget. Two concurrent runs would burst past 30 in
                     the first window and the SDK would queue the rest
                     — exactly the protection we want against accidental
                     parallel storming.

  ``tavily-api``   — every activity that calls Tavily. Cap: 30 searches
                     per 60 seconds. Each run typically does 8-15 Tavily
                     calls; the budget covers 2 concurrent runs.

The decorator-level rate limit caps the number of times each ACTIVITY
INVOCATION fires per window. Inner-loop fan-out (e.g. judge_claim_sources
calling Mistral 17-25 times in a burst) is governed by the per-activity
asyncio.Semaphore — see each activity for its semaphore.
"""

from __future__ import annotations

from mistralai.workflows.core.rate_limiting.rate_limit import RateLimit

MISTRAL_API_RATE_LIMIT = RateLimit(
    time_window_in_sec=60,
    max_execution=30,
    key="mistral-api",
)

TAVILY_API_RATE_LIMIT = RateLimit(
    time_window_in_sec=60,
    max_execution=30,
    key="tavily-api",
)
