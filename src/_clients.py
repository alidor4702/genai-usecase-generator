"""Per-process singleton clients.

Activities that talk to external services were instantiating fresh
clients on every call (`Mistral(api_key=...)`). The Mistral SDK keeps
internal state — connection pools, retry config — that benefits from
being reused. This module returns a single shared instance per client
type so the pool warms up once and stays alive.
"""

from __future__ import annotations

from functools import lru_cache

from mistralai.client import Mistral

from src.config import settings


@lru_cache(maxsize=1)
def mistral_client() -> Mistral:
    """Return the shared Mistral client.

    Cached for the lifetime of the process. Activities call this once
    per call instead of `Mistral(api_key=...)` — saves the constructor
    cost and shares the underlying httpx connection pool across all
    LLM + embedding calls.

    Raises RuntimeError if MISTRAL_API_KEY is missing — fail fast at
    the first call rather than letting downstream API requests 401.
    """
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY is required for Mistral activities")
    return Mistral(api_key=settings.mistral_api_key)
