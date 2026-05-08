"""Derive a customer-facing industry label.

Wikidata's P452 (industry) returns statistical-classification codes — `"other
monetary intermediations"` for BNP Paribas, `"combined administrative office
services"` for Mistral AI — none of which a customer or sales engineer would
ever say out loud. The Wikipedia page intro almost always opens with an
editor-curated noun phrase that IS the right label: `"is a French multinational
universal bank"`, `"is a French personal care company"`.

Two-stage cleanup:
  1. Regex on the Wikipedia summary's first sentence — fast, free, works for
     the standard `"<Name> ... is a/an <industry phrase>"` opener.
  2. LLM polish (Mistral Small @ T=0.1) — fallback when regex misses or the
     extracted phrase is suspiciously short / long. Cheap (~$0.0001 / call).

The result REPLACES `WikipediaFacts.industry` so the synthesizer sees the
clean label downstream.
"""

from __future__ import annotations

import logging
import re

from mistralai.client import Mistral

from src.config import settings

logger = logging.getLogger(__name__)


# Match: "[anything up to is a/an] <captured industry phrase> [.|,|natural break]"
_INDUSTRY_OPENER_RE = re.compile(
    r"\bis\s+an?\s+([^.]+?)"
    r"(?:[.,;]|"
    r"\s+that\s|\s+which\s|\s+with\s|\s+whose\s|"
    r"\s+operating\s|\s+specializing\s|\s+focused\s|"
    r"\s+founded\s|\s+headquartered\s|\s+based\s|\s+listed\s|"
    r"\s+ranked\s|\s+known\s|\s+founded\s|\s+established\s)",
    re.IGNORECASE,
)


_LLM_POLISH_SYSTEM = """\
You are converting a raw industry/sector label from a structured database into
a 2-5 word customer-facing industry name, using a Wikipedia summary as the
authoritative signal.

Output a single line of plain text — no quotes, no markdown, no commentary.
Examples of good outputs:
  "French multinational bank"
  "global personal care company"
  "AI research startup"
  "French water and waste utility"

Bad outputs (do NOT produce these):
  "Other monetary intermediations" (statistical-classification garbage)
  "Combined administrative office services" (statistical-classification garbage)
  Multi-sentence descriptions

Use the Wikipedia summary as the source of truth. The raw label is a hint,
not a constraint — replace it if it's clearly miscoded.
"""


_TRAILING_CONNECTORS_RE = re.compile(
    r"\s+(?:and|or|the|a|an|in|with|for|of|at|on|by|to|from)$",
    re.IGNORECASE,
)


def _regex_extract(summary: str) -> str | None:
    """Try to extract industry from the Wikipedia summary's first sentence."""
    if not summary:
        return None
    head = summary[:600]
    m = _INDUSTRY_OPENER_RE.search(head)
    if not m:
        return None
    phrase = m.group(1).strip()
    # Collapse whitespace, strip punctuation, then strip trailing connector
    # words so e.g. "...registered in Paris and" becomes "...registered in Paris".
    phrase = re.sub(r"\s+", " ", phrase).strip(" ,;:")
    for _ in range(3):  # may have multiple trailing connectors stacked
        new_phrase = _TRAILING_CONNECTORS_RE.sub("", phrase).strip(" ,;:")
        if new_phrase == phrase:
            break
        phrase = new_phrase
    # Sanity bounds: 2-12 words, contains a noun-like token (heuristic)
    word_count = len(phrase.split())
    if word_count < 2 or word_count > 12:
        return None
    # Reject trivial copular continuations like "company" alone
    if word_count == 2 and phrase.lower() in {"a company", "a corporation", "a group"}:
        return None
    return phrase


async def _llm_polish(summary: str, raw_label: str | None) -> str | None:
    if not settings.mistral_api_key:
        return None
    if not summary and not raw_label:
        return None
    client = Mistral(api_key=settings.mistral_api_key)
    user = (
        f"Raw label from Wikidata P452: {raw_label or '(none)'}\n\n"
        f"Wikipedia summary:\n{summary[:1500] if summary else '(none)'}\n\n"
        "Output the 2-5 word customer-facing industry label only."
    )
    try:
        r = await client.chat.complete_async(
            model=settings.mistral_scoring_model,  # Mistral Small — cheap polish
            temperature=0.1,
            max_tokens=40,
            timeout_ms=20_000,
            messages=[
                {"role": "system", "content": _LLM_POLISH_SYSTEM},
                {"role": "user", "content": user},
            ],
        )
        text = r.choices[0].message.content
        if isinstance(text, list):
            text = "".join(getattr(b, "text", "") for b in text)
        polished = (text or "").strip().strip("\"'`").splitlines()[0].strip()
        if 2 <= len(polished.split()) <= 12:
            return polished
        return None
    except Exception as e:
        logger.warning("industry polish LLM failed: %s", type(e).__name__)
        return None


_GENERIC_INDUSTRY_TAILS = {
    "company",
    "corporation",
    "group",
    "firm",
    "business",
    "enterprise",
    "conglomerate",
    "holding",
}


def _looks_too_generic(phrase: str) -> bool:
    """True if the captured phrase is just demonyms + corporate-noun with no
    actual industry word. Examples that should return True: 'French
    transnational company', 'French multinational corporation'. Examples
    that should return False: 'French multinational universal bank',
    'French personal care corporation', 'French artificial intelligence
    (AI) company'.

    Heuristic: <=3 words total AND the last word is a generic corporate
    tail like 'company' / 'corporation' / 'group'. Three words is the
    threshold because 'French transnational company' = 3 generic words,
    while 'global personal care company' = 4 with 'personal care' adding
    actual industry meaning.
    """
    words = phrase.strip().split()
    if len(words) > 3:
        return False
    last_word = words[-1].lower().strip(".,;:")
    return last_word in _GENERIC_INDUSTRY_TAILS


async def derive_clean_industry_label(
    summary: str | None, raw_wikidata_label: str | None
) -> str | None:
    """Return a customer-facing industry label, or None if we genuinely have nothing.

    Priority order:
      1. Regex extraction from Wikipedia summary — but only if the result
         actually conveys industry, not just '<demonym> <corporate-tail>'
      2. LLM polish using both the summary and the raw Wikidata label
         (also runs when the regex result is too generic)
      3. Raw Wikidata label as last resort (still better than nothing)
    """
    summary_str = summary or ""
    regex_label = _regex_extract(summary_str)
    if regex_label and not _looks_too_generic(regex_label):
        logger.info("industry: regex-extracted '%s' from summary", regex_label)
        return regex_label
    if regex_label:
        logger.info(
            "industry: regex result '%s' looks too generic, falling through to LLM polish",
            regex_label,
        )
    polished = await _llm_polish(summary_str, raw_wikidata_label)
    if polished:
        logger.info("industry: LLM-polished '%s' (raw was '%s')", polished, raw_wikidata_label)
        return polished
    # If LLM polish failed but regex caught something (even generic), prefer
    # that over raw Wikidata garbage.
    if regex_label:
        logger.info("industry: LLM polish unavailable, falling back to generic regex label '%s'", regex_label)
        return regex_label
    if raw_wikidata_label:
        logger.info("industry: keeping raw Wikidata label '%s' (no better option)", raw_wikidata_label)
    return raw_wikidata_label
