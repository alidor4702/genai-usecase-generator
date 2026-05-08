"""Derive a customer-facing industry label from the Wikipedia summary.

Earlier versions of this module did a regex pass over the Wikipedia first
sentence ("X is a/an <industry phrase>"), with LLM polish only as a
fallback. That worked for clean openers but over-narrowed when the company
operates in multiple business areas — the regex stopped at "with" and
produced "French transnational company" for Veolia (water + waste +
energy), and follow-up LLM polish then over-narrowed to "French water
utility" because the rest of the summary was water-heavy.

Current design: ALWAYS run a single LLM call (Mistral Small @ T=0.1) over
the full Wikipedia summary. Costs ~$0.0001 per company; produces clean
breadth-preserving labels every time. Wikidata P452 has been retired
because its statistical-classification codes ("other monetary
intermediations") are unfit for customer-facing output.

Country is still pulled from Wikidata P17 (clean Q-id labels) — that path
lives in src/research/wikipedia.py.
"""

from __future__ import annotations

import logging

from mistralai.client import Mistral

from src.config import settings

logger = logging.getLogger(__name__)


_LLM_INDUSTRY_SYSTEM = """\
You produce a single concise customer-facing industry label for a company,
given the company's Wikipedia summary.

Rules:
- Output 2-8 words. Plain text, no quotes, no markdown, no commentary.
- Cover ALL major business areas the summary describes. Do NOT narrow
  to one area when the company operates in several.
  Examples that PRESERVE BREADTH (good):
    "French water, waste and energy services"
    "diversified consumer-products and beauty"
    "global financial services and asset management"
  Examples that NARROW (bad — do not produce):
    "French water utility" (when the company also does waste and energy)
    "personal care company" (when the company also does dermatology and active cosmetics)
- Prefer descriptive nouns over corporate-tail words alone.
  Bad: "French multinational company" (no industry signal)
  Good: "French multinational retail and wholesaling corporation"
- Include the geographic descriptor when distinctive (e.g. "French",
  "global", "European") — it adds context for sales-engineer use.
- If the summary genuinely doesn't identify an industry (rare; the
  company doesn't have a Wikipedia page or the page is stub-only),
  output "Unknown".

Examples of clean output:
  "French multinational universal bank and financial services"
  "French water, waste, and energy services"
  "French multinational retail and wholesaling corporation"
  "French personal care, beauty, and cosmetics multinational"
  "French artificial intelligence company"
"""


async def derive_clean_industry_label(
    summary: str | None, raw_wikidata_label: str | None = None
) -> str | None:
    """Single-call industry label from the Wikipedia summary.

    `raw_wikidata_label` is accepted for backward compatibility with old
    callers (P452 has been retired in src/research/wikipedia.py and is now
    always None) — passed to the LLM as a tiebreaker hint when present.

    Returns None if no summary AND no Wikidata hint exist (i.e. a company
    with no Wikipedia presence at all). The synthesizer's structural
    fallback then handles it.
    """
    if not summary and not raw_wikidata_label:
        return None
    if not settings.mistral_api_key:
        logger.warning("industry: MISTRAL_API_KEY missing, returning raw_label as-is")
        return raw_wikidata_label

    client = Mistral(api_key=settings.mistral_api_key)
    user_parts = [f"Wikipedia summary:\n{(summary or '(none)')[:2500]}"]
    if raw_wikidata_label:
        user_parts.append(f"\n(Wikidata hint, may be junk: {raw_wikidata_label})")
    user_parts.append("\nOutput the 2-8 word industry label only.")
    user = "".join(user_parts)

    try:
        r = await client.chat.complete_async(
            model=settings.mistral_scoring_model,  # cheap, fast — Mistral Small
            temperature=0.1,
            max_tokens=60,
            timeout_ms=20_000,
            messages=[
                {"role": "system", "content": _LLM_INDUSTRY_SYSTEM},
                {"role": "user", "content": user},
            ],
        )
        text = r.choices[0].message.content
        if isinstance(text, list):
            text = "".join(getattr(b, "text", "") for b in text)
        polished = (text or "").strip().strip("\"'`").splitlines()[0].strip()
        word_count = len(polished.split())
        if 2 <= word_count <= 12:
            logger.info("industry: '%s' (%d words)", polished, word_count)
            return polished
        logger.warning(
            "industry: LLM returned out-of-range output (%d words): %r",
            word_count,
            polished,
        )
        return raw_wikidata_label
    except Exception as e:
        logger.warning("industry LLM call failed: %s", type(e).__name__)
        return raw_wikidata_label
