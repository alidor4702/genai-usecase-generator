"""Step 0 — Upfront entity resolution.

Before any research happens, ask Mistral Small one question:
    "Given the user typed X, what canonical company name (if any) are
    they most likely referring to?"

This catches every entity-drift case at the top of the pipeline — Apple
vs Apple-fruit, Hermes vs Hermes-the-god, ZYX vs ZYX-Music, asdfqwerty
vs nothing — without any heuristics, regex patterns, or post-research
coherence checks downstream.

If the LLM resolves the input to a canonical name, we OVERRIDE the
user's input with that canonical name and continue with research. So
Apple → "Apple Inc.", Microsoft → "Microsoft Corporation", Carrefour
→ "Carrefour Group S.A.". Research downstream gets the unambiguous
name and converges on the right entity.

If the LLM cannot identify a real company (gibberish, empty, or genuinely
ambiguous), we refuse early with a useful message — saves the ~100s of
LLM time that the rest of the pipeline would burn on the wrong entity.

Cost: one Mistral Small call (~$0.001), ~1-2s. Cheap insurance against
the entire class of entity-drift bugs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import mistralai.workflows as workflows

from src._clients import mistral_client
from src._rate_limits import MISTRAL_API_RATE_LIMIT
from src._util import strip_fence
from src.config import settings
from src.trace import trace_step

logger = logging.getLogger(__name__)


@dataclass
class ResolvedEntity:
    """Outcome of the entity resolution call.

    `canonical_name` is the full company name to use downstream
    (e.g. "Apple Inc.", "Hermès International S.A.", "Carrefour Group").
    `confidence` reflects how sure the LLM was. `reason` explains the
    decision for logging / refusal UX.

    `resolved=False` means the input doesn't map to an identifiable
    company — gibberish, empty, or genuinely ambiguous. Caller should
    refuse instead of proceeding.
    """

    resolved: bool
    canonical_name: str | None
    confidence: Literal["high", "medium", "low"]
    reason: str


_RESOLUTION_SYSTEM = """\
You are an entity resolution helper for a B2B sales engineering tool.
The user typed some text into a "company name" field. Your job: figure
out what real, identifiable company they're most likely referring to.

If the input clearly maps to a real company → return its canonical
full name (e.g. legal name, registered name, or the name a sales
engineer would use in customer-facing materials).

If the input is gibberish, empty, a generic noun, a person's name, or
is too ambiguous to map to ONE specific company → return null.

Ambiguity handling: short company names are common and rarely truly
ambiguous in this context (the user is on a B2B sales-engineering tool
researching companies). When in doubt, pick the most likely COMMERCIAL
ENTITY interpretation. Don't penalize short inputs for being short.

Examples (definitive — match this pattern):
- "Apple" → "Apple Inc." (high confidence; the consumer technology
   company is what a sales engineer means)
- "Microsoft" → "Microsoft Corporation"
- "Tesla" → "Tesla, Inc."
- "Carrefour" → "Carrefour Group S.A."
- "HSBC" → "HSBC Holdings plc"
- "Spotify" → "Spotify Technology S.A."
- "BNP Paribas" → "BNP Paribas S.A."
- "L'Oréal" → "L'Oréal S.A."
- "Hermes" → "Hermès International S.A."
- "Sanofi" → "Sanofi S.A."
- "SAP" → "SAP SE"
- "Joe's Pizza Shop" → "Joe's Pizza" (real small NYC pizzeria; LOW
   confidence because it's tiny but still a real business)
- "OpenAI" → "OpenAI, OPCO LLC"
- "ByteDance" → "ByteDance Ltd."

Cases to REFUSE (return null + reason):
- "" or "   " → null, "Empty input"
- "asdfqwerty" → null, "Random keystrokes — no identifiable company"
- "ZYX Corporation" → null, "No single well-known company by this name
   — could be Zynex Medical, ZYX Music, or others"
- "John Smith" → null, "Person's name, not a company"
- "the company" → null, "Generic phrase, no specific entity"
- "apple fruit" → null, "Generic descriptor, not a company name"
- "show me X" / "list X" / "what is X" → null, "Looks like a command,
   not a company name" (the workflow has separate command routing for
   these)

CONFIDENCE LEVELS:
- high — major well-known public/private company (Fortune 1000, CAC 40,
   FTSE 100 scale, or famous mid-caps). Sales engineer would
   immediately recognize the canonical name.
- medium — real but less internationally famous; canonical name
   reasonable but possibly not the user's exact target.
- low — small / niche / tiny business; real but the canonical name
   might be debatable.

Output STRICT JSON, no markdown, no commentary:
{
  "resolved": true | false,
  "canonical_name": "<full company name>" | null,
  "confidence": "high" | "medium" | "low",
  "reason": "<one short sentence — for high-confidence resolutions,
              one phrase identifying the entity (e.g. 'consumer
              technology company'). For refusals, why you couldn't
              resolve it."
}
"""


@workflows.activity(start_to_close_timeout=timedelta(seconds=30), rate_limit=MISTRAL_API_RATE_LIMIT)
async def resolve_company_entity_activity(user_input: str) -> ResolvedEntity:
    """Resolve a user-typed company name to a canonical full name.

    Returns ResolvedEntity. Caller checks `resolved` — if False, refuse
    the run with `reason` as the user-facing explanation.

    Fails open: on any LLM error, returns `resolved=True` with the
    user's original input as the canonical name. The downstream pipeline
    will then run normally; if the input is genuinely problematic, the
    existing confidence/refusal gates downstream will catch it. Better
    than blocking a real run on a transient API blip.
    """
    user_input = (user_input or "").strip()
    if not user_input or len(user_input) < 2:
        return ResolvedEntity(
            resolved=False,
            canonical_name=None,
            confidence="high",
            reason="Empty or too-short input",
        )

    if not settings.mistral_api_key:
        # No API key — can't run resolution. Fall back to using the input
        # as-is and let the downstream gates handle drift.
        return ResolvedEntity(
            resolved=True,
            canonical_name=user_input,
            confidence="low",
            reason="MISTRAL_API_KEY missing — using input as-is",
        )

    client = mistral_client()
    try:
        async with trace_step(
            "resolve_entity",
            settings.mistral_scoring_model,
            "chat.complete",
            inputs_summary=f"user_input={user_input!r}",
        ) as ev:
            r = await client.chat.complete_async(
                model=settings.mistral_scoring_model,
                temperature=0.1,
                max_tokens=200,
                timeout_ms=15_000,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _RESOLUTION_SYSTEM},
                    {"role": "user", "content": f"User typed: {user_input!r}"},
                ],
            )
            text = r.choices[0].message.content
            if isinstance(text, list):
                text = "".join(getattr(b, "text", "") for b in text)
            data = json.loads(strip_fence(str(text or "{}")))
            ev.outputs_summary = (
                f"resolved={data.get('resolved')} → {data.get('canonical_name')!r}"
            )
    except Exception as e:
        logger.warning(
            "resolve_company_entity: failed (%s) — falling back to input as-is",
            type(e).__name__,
        )
        return ResolvedEntity(
            resolved=True,
            canonical_name=user_input,
            confidence="low",
            reason=f"Resolution failed ({type(e).__name__}); using input as-is",
        )

    resolved = bool(data.get("resolved"))
    canonical = data.get("canonical_name")
    confidence_raw = str(data.get("confidence", "medium")).lower()
    confidence: Literal["high", "medium", "low"] = (
        confidence_raw if confidence_raw in ("high", "medium", "low") else "medium"
    )
    reason = str(data.get("reason") or "")

    if not resolved or not isinstance(canonical, str) or not canonical.strip():
        return ResolvedEntity(
            resolved=False,
            canonical_name=None,
            confidence=confidence,
            reason=reason or "Could not identify a specific company",
        )

    return ResolvedEntity(
        resolved=True,
        canonical_name=canonical.strip(),
        confidence=confidence,
        reason=reason,
    )
