"""Step 7e — Final qualitative replacement.

The v7 verification chain (polish → meta-eval → web-verify → source-judge)
gives every specific number in the prose multiple chances to be anchored.
But by the time this step runs, some numbers will have failed every
chance — the source-judge confirmed there's no support. For those, the
report still currently shows the number verbatim, which a reviewer would
read as a fabricated claim.

This step does the surgical qualitative-replacement pass v6 used to do
INSIDE polish. Difference: we now know exactly which claims are
unsupported (post-judge) and only touch those, instead of pre-stripping
based on a regex over a heuristic ledger.

For each use case, gather the still-unsupported numeric claims, ask
Mistral Small to rewrite the prose so those specific assertions become
qualitative ("a meaningful reduction" / "a multi-petabyte data
platform"). One call per use case so the model has full context for
sentence-level rewriting.

Cost: 3 Mistral Small calls per run (~$0.0003), +~5s. Skipped silently
if no unsupported numeric claims remain.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import timedelta

import mistralai.workflows as workflows

from src._clients import mistral_client
from src._util import strip_fence
from src.config import settings
from src.models import EnrichedUseCase, FactCheckEntry
from src.trace import trace_step

logger = logging.getLogger(__name__)


_FINAL_QUALIFY_SYSTEM = """\
You rewrite specific factual claims into qualitative phrasing while leaving
the rest of the prose untouched. Given:
- A use case's customer-facing prose (description, why_this_company,
  time_to_value, top_implementation_risk)
- A list of specific claims that downstream verification could NOT support
  from any source

Rewrite the prose so those specific unsupported claims become qualitative.
Keep every other sentence verbatim — same words, same flow.

Replacement guide:
- Specific number → qualitative magnitude
    "reduces audit time by 30%" (unsupported) → "reduces audit time
    materially"
    "10 PB data platform" (unsupported) → "large-scale data platform"
    "14,000 stores" (unsupported) → "a global store network"
- Specific peer-deployment number → qualitative
    "Sephora's deployment reduced cart abandonment by 22%" → "Sephora has
    reported material engagement gains"
- Specific data-asset assertion that didn't verify → qualitative
    "L'Oréal has 110 years of historical sales data" (unsupported) →
    "L'Oréal has long-running sales data"
- Specific named-entity claim that didn't verify → drop or qualify
    "powered by Mistral Forge platform" (unsupported) → "powered by a
    Mistral platform"

DO NOT rewrite anything not in the unsupported list. If a claim is in
the unsupported list but already qualitative, leave it alone.

Output STRICT JSON:
{
  "description": "...",
  "why_this_company": "...",
  "time_to_value": "...",
  "top_implementation_risk": "..."
}
"""


_NUMERIC_HINT_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:%|percent|m|b|k|million|billion|thousand|pb|tb|gb|"
    r"weeks?|months?|years?|days?|stores?|countries|customers?|employees?)\b",
    re.IGNORECASE,
)


def _claim_has_numeric_or_specific_anchor(claim: str) -> bool:
    """Heuristic: only rewrite claims that have a SPECIFIC anchor worth
    qualifying. Generic-sounding unsupported claims ("the company has
    digital priorities") shouldn't trigger a rewrite — they're already
    qualitative and the rewrite would be a no-op.
    """
    if not claim:
        return False
    if _NUMERIC_HINT_RE.search(claim):
        return True
    # Capitalised multi-word phrase suggests a named entity — also worth
    # qualifying if unsupported.
    if re.search(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]+)+\b", claim):
        return True
    return False


async def _qualify_one_use_case(
    uc: EnrichedUseCase,
    unsupported_claims: list[FactCheckEntry],
) -> EnrichedUseCase:
    if not unsupported_claims:
        return uc
    relevant = [c for c in unsupported_claims if _claim_has_numeric_or_specific_anchor(c.claim)]
    if not relevant:
        return uc

    client = mistral_client()
    user = (
        f"Use case ID: {uc.id}\n\n"
        f"Description:\n{uc.description}\n\n"
        f"Why this company:\n{uc.why_this_company}\n\n"
        f"Time-to-value: {uc.time_to_value.estimate}\n\n"
        f"Top implementation risk:\n{uc.top_implementation_risk}\n\n"
        "## Unsupported claims (rewrite these specifically; leave everything else alone)\n"
        + "\n".join(f"- {c.claim}" for c in relevant)
        + "\n\nOutput the rewritten fields as STRICT JSON."
    )
    try:
        async with trace_step(
            "final_qualify",
            settings.mistral_scoring_model,
            "chat.complete",
            inputs_summary=f"use_case={uc.id} unsupported={len(relevant)}",
        ) as ev:
            r = await client.chat.complete_async(
                model=settings.mistral_scoring_model,
                temperature=0.1,
                max_tokens=4000,
                timeout_ms=60_000,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _FINAL_QUALIFY_SYSTEM},
                    {"role": "user", "content": user},
                ],
            )
            text = r.choices[0].message.content
            if isinstance(text, list):
                text = "".join(getattr(b, "text", "") for b in text)
            data = json.loads(strip_fence(str(text or "{}")))
            ev.outputs_summary = f"qualified {len(data)} fields"
    except Exception as e:
        logger.warning("final_qualify: failed for %s — %s; keeping original prose", uc.id, type(e).__name__)
        return uc

    new_desc = str(data.get("description") or uc.description)
    new_why = str(data.get("why_this_company") or uc.why_this_company)
    new_ttv = str(data.get("time_to_value") or uc.time_to_value.estimate)
    new_risk = str(data.get("top_implementation_risk") or uc.top_implementation_risk)

    updated = uc.model_copy(
        update={
            "description": new_desc.strip() or uc.description,
            "why_this_company": new_why.strip() or uc.why_this_company,
            "time_to_value": uc.time_to_value.model_copy(update={"estimate": new_ttv.strip() or uc.time_to_value.estimate}),
            "top_implementation_risk": new_risk.strip() or uc.top_implementation_risk,
        }
    )
    logger.info("final_qualify: %s — rewrote %d fields based on %d unsupported claims",
                uc.id, sum(1 for old, new in [(uc.description, new_desc), (uc.why_this_company, new_why),
                                              (uc.time_to_value.estimate, new_ttv),
                                              (uc.top_implementation_risk, new_risk)]
                          if old != new), len(relevant))
    return updated


@workflows.activity(start_to_close_timeout=timedelta(seconds=180))
async def final_qualitative_replacement_activity(
    enriched_uses: list[EnrichedUseCase],
    fact_claims: list[FactCheckEntry],
) -> list[EnrichedUseCase]:
    """Per use case, rewrite still-unsupported numeric/named claims into
    qualitative phrasing. No-op if there are no unsupported claims.
    """
    if not enriched_uses or not fact_claims:
        return enriched_uses
    by_uc: dict[str, list[FactCheckEntry]] = {}
    for c in fact_claims:
        if not c.passed:
            by_uc.setdefault(c.use_case_id, []).append(c)
    if not by_uc:
        return enriched_uses

    coros = [_qualify_one_use_case(uc, by_uc.get(uc.id, [])) for uc in enriched_uses]
    return list(await asyncio.gather(*coros))
