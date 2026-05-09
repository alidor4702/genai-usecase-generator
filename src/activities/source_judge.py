"""Step 7d — Final-render-gate source judge.

Runs after web-verify. For every claim marked `passed=True` whose support
comes from a URL (rescue-layer URL OR original ledger entry cited via
`source_kind: evidence:<ev-id>`), ask Mistral Small whether that URL's
content actually supports the claim — vs. just containing the relevant
entities or numbers in unrelated context.

Why this exists: both the v6 web-verify rescue (deterministic credibility
gate) and the upstream generation web_search step can promote URLs whose
text mentions the company and a number but doesn't actually assert the
claim being made. Concrete failures from the v6 batch:

  - L'Oréal: aiautomationglobal.com listed as corroboration for
    "Mistral GDPR alignment for L'Oréal" — the source doesn't mention
    L'Oréal at all.
  - L'Oréal: a Chinese skincare-OEM LinkedIn page cited for
    "Creed/Balenciaga regulatory pathways" — mentions neither brand.
  - BNP: intuitionlabs.ai/pdfs/mistral-large-3... cited for a
    BNP-Mistral partnership claim — the PDF is Mistral Large 3
    architecture documentation, no BNP partnership content.

Same failure class in all three: "URL appeared in a search result that
mentioned the relevant entities" treated as sufficient evidence. The
deterministic rescue layer can't tell that apart from real support;
neither can meta-eval if the URL is in its evidence pool. An LLM judge
reading the actual snippet vs. the actual claim catches it cheaply.

Cost: one Mistral Small call per (claim, URL) pair across the 3 use
cases. Typical run sees 30-50 such pairs, ~$0.005, +~15s. Pairs without
a URL (company_context / precedent-id sources, or no source at all) are
skipped — those are trusted by virtue of being internal pipeline state.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta

import mistralai.workflows as workflows

from src._clients import mistral_client
from src._util import strip_fence
from src.config import settings
from src.models import (
    EvidenceLedger,
    FactCheckEntry,
    MetaEvalReview,
)
from src.trace import trace_step

logger = logging.getLogger(__name__)


_JUDGE_SYSTEM = """\
You are a strict source-claim coherence judge. Given:
- A factual CLAIM about a target company
- A SOURCE EXCERPT (a URL's title + body content)

Decide whether the source ACTUALLY supports the claim, or just contains
related entities / numbers / keywords in unrelated context.

Strict rule: a source supports the claim only if a reader of the source
would, on their own, be able to conclude the claim from what's written
there. Mere mention of the company name or a similar number elsewhere on
the page does NOT count.

Examples of NOT supporting:
- Claim: "BNP partnered with Mistral on LLMs" / Source: a PDF about
  Mistral Large 3 architecture that doesn't mention BNP. → not supported.
- Claim: "L'Oréal achieved GDPR alignment via Mistral" / Source: a
  generic "AI automation for retail" blog with no L'Oréal mention.
  → not supported.
- Claim: "Carrefour reduced waste by 22%" / Source: a Carrefour press
  release saying they have a sustainability program but no 22% figure.
  → not supported.

Examples of supporting:
- Claim: "Carrefour partnered with Centric Software" / Source: Centric
  Software press release announcing the Carrefour PLM deal. → supported.
- Claim: "L'Oréal operates a 10 PB data platform" / Source: L'Oréal
  annual report literally containing "our 10-petabyte data platform".
  → supported.
- Claim: "Veolia operates the GreenUp program" / Source: Veolia 2024
  strategic plan announcing GreenUp. → supported.

Be strict. The default for inconclusive evidence is NOT SUPPORTED.

Output STRICT JSON: {"supports": true|false, "reason": str}
The reason should be one short sentence pointing at what's in (or
missing from) the source.
"""


async def _judge_one(
    claim: FactCheckEntry,
    source_excerpt: str,
    source_url: str,
) -> tuple[bool, str | None]:
    """Run one judge call for a single (claim, source) pair.

    Returns (supports, reason). On error, returns (True, None) — fail-open
    so a transient API error doesn't drop a real claim. The judge is a
    quality gate, not a gatekeeper.
    """
    if not claim.claim or not source_excerpt:
        return True, None
    client = mistral_client()
    user = (
        f"CLAIM: {claim.claim}\n\n"
        f"SOURCE URL: {source_url}\n"
        f"SOURCE EXCERPT (truncated to 1500 chars):\n"
        f"{source_excerpt[:1500]}\n\n"
        'Output STRICT JSON: {"supports": true|false, "reason": "..."}'
    )
    try:
        async with trace_step(
            "source_judge",
            settings.mistral_scoring_model,
            "chat.complete",
            inputs_summary=f"claim={claim.claim[:60]!r}",
        ) as ev:
            r = await client.chat.complete_async(
                model=settings.mistral_scoring_model,
                temperature=0.1,
                max_tokens=300,
                timeout_ms=20_000,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _JUDGE_SYSTEM},
                    {"role": "user", "content": user},
                ],
            )
            text = r.choices[0].message.content
            if isinstance(text, list):
                text = "".join(getattr(b, "text", "") for b in text)
            data = json.loads(strip_fence(str(text or "{}")))
            ev.outputs_summary = f"supports={data.get('supports')}"
    except Exception as e:
        logger.warning("source_judge: call failed for claim %r — %s; defaulting to supports=true",
                       claim.claim[:60], type(e).__name__)
        return True, None

    supports_raw = data.get("supports")
    reason = data.get("reason")
    supports = bool(supports_raw) if isinstance(supports_raw, bool) else True
    return supports, (str(reason).strip() if isinstance(reason, str) and reason.strip() else None)


def _resolve_source_excerpt(
    claim: FactCheckEntry,
    ledger: EvidenceLedger,
) -> tuple[str, str] | None:
    """Pick the source excerpt + url for a claim.

    Priority order:
    1. rescue_url (web-verify rescue layer) — find the matching ledger
       entry by URL match; use its content.
    2. source_url (set by meta-eval from "evidence:<ev-id>" lookup) —
       same path.
    3. None — judge skipped, claim trusted as-is.
    """
    url = claim.rescue_url or claim.source_url
    if not url:
        return None
    # Find the ledger entry whose url matches.
    for ent in ledger.entries:
        if ent.url == url and ent.content:
            return ent.content, url
    return None


@workflows.activity(start_to_close_timeout=timedelta(seconds=240))
async def judge_claim_sources_activity(
    review: MetaEvalReview,
    claims: list[FactCheckEntry],
    ledger: EvidenceLedger | None = None,
) -> tuple[MetaEvalReview, list[FactCheckEntry]]:
    """Judge whether each (claim, supporting URL) pair coheres.

    For claims passed=True with a resolvable supporting URL (rescue or
    ledger-cited), ask Mistral Small whether the source actually supports
    the claim. Flip false positives back to passed=False with
    judge_rejected=true, judge_reason set. Re-anchor confidence on the
    post-judge claim set using the same formula as web_verify.
    """
    if ledger is None or not claims:
        return review, claims

    # Build the work list: only claims passed=True with a resolvable URL.
    work: list[tuple[int, FactCheckEntry, str, str]] = []
    for idx, c in enumerate(claims):
        if not c.passed:
            continue
        excerpt = _resolve_source_excerpt(c, ledger)
        if excerpt is None:
            continue
        body, url = excerpt
        work.append((idx, c, body, url))

    if not work:
        logger.info("source_judge: no (claim, URL) pairs to judge")
        return review, claims

    sem = asyncio.Semaphore(4)

    async def _bound(idx: int, c: FactCheckEntry, body: str, url: str):
        async with sem:
            supports, reason = await _judge_one(c, body, url)
            return idx, supports, reason

    async with trace_step(
        "source_judge",
        settings.mistral_scoring_model,
        "judge_claim_sources",
        inputs_summary=f"pairs={len(work)}",
    ) as ev:
        results = await asyncio.gather(*(_bound(i, c, b, u) for i, c, b, u in work))
        ev.outputs_summary = f"judged {len(results)} pairs"

    new_claims = list(claims)
    rejected = 0
    for idx, supports, reason in results:
        if supports:
            continue
        c = new_claims[idx]
        new_claims[idx] = c.model_copy(
            update={
                "passed": False,
                "judge_rejected": True,
                "judge_reason": reason or "source-judge: snippet does not support the claim",
            }
        )
        rejected += 1

    if rejected > 0:
        # Re-run the same confidence math as web_verify so the headline
        # reflects the post-judge state. Use the pre-judge claim ratio as
        # the "old" anchor.
        old_pass = sum(1 for c in claims if c.passed) / max(1, len(claims))
        new_pass = sum(1 for c in new_claims if c.passed) / max(1, len(new_claims))
        qual_delta = review.confidence - old_pass
        new_confidence = max(0.0, min(1.0, new_pass + qual_delta))
        review = review.model_copy(update={"confidence": new_confidence})
        logger.info(
            "source_judge: rejected %d/%d pairs; pass-rate %.2f → %.2f; confidence → %.2f",
            rejected, len(work), old_pass, new_pass, review.confidence,
        )
    else:
        logger.info("source_judge: all %d pairs cohered (no rejections)", len(work))

    return review, new_claims
