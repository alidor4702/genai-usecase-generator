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
You are a source-claim coherence judge. Given:
- A factual CLAIM about a target company
- A SOURCE EXCERPT (a URL's title + body content)

YOUR CENTRAL QUESTION: would a reasonable reader, on their own,
conclude the claim is true given this snippet?

The snippet does NOT need to literally restate the claim. The way real
journalism fact-checking works: a number that satisfies a stated
relationship counts; rephrased equivalents count; specific instances
that imply a more general claim count. What does NOT count is mere
adjacency — entities or topics being mentioned without the assertion
itself being supportable from the text.

WHAT SUPPORTS THE CLAIM:
- Numerical sufficiency. Source contains a number that satisfies a
  relationship in the claim. "operates in 56 countries" supports
  "operates in 45+ countries". "215,000 employees" supports
  "approximately 200,000 employees". "10-petabyte platform" supports
  "multi-petabyte data platform".
- Synonyms / rephrasing. Source states the same fact in different
  words. "acquired ModiFace in 2018" supports "owns ModiFace".
  "EU-headquartered with sovereign deployment" supports "operates
  on EU-hosted infrastructure".
- Inference from instances. Source contains specific instances that
  imply a more general claim. "stores in France, Spain, Italy,
  Belgium" supports "operates across multiple EU markets".
- Direct match. Source literally restates the claim.

WHAT DOES NOT SUPPORT:
- Mere entity adjacency. Source mentions related entities but doesn't
  address the assertion. (A Mistral data-center article doesn't
  support a claim about L'Oréal–Mistral fit unless L'Oréal is named in
  context.)
- Topical adjacency. Source is in the same topic but not the same
  fact. (A job description mentioning AMI does not support a claim
  about Veolia's actual AMI read-success-rate data.)
- Token co-occurrence in unrelated context. (The company name and a
  number both appear, but in different paragraphs about different
  things.)

CONTRADICTION GUARD — these stay rejected even if they look close:
- The snippet asserts a different VALUE for the same fact. Claim
  "€3.279 trillion AUM" contradicted by snippet "€2.79 trillion AUM"
  → not supported (factual conflict, not approximation).
- The snippet asserts a different RANK or SUPERLATIVE. Claim "largest
  in Europe" contradicted by snippet "second largest in Europe"
  → not supported.
- The snippet asserts a different COUNT for an enumerated set. Claim
  "supports 12 European languages" contradicted by snippet "supports
  9 languages" → not supported (catches stale-training-data
  fabrications about specific feature counts).

Default for inconclusive evidence: NOT SUPPORTED.

Output STRICT JSON: {"supports": true|false, "reason": str}
The reason is one short sentence that points at what in the source
satisfied (or failed to satisfy) the central question.
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
        # reflects the post-judge state. Use in-scope claim ratios
        # (qualified_out claims excluded — defense-in-depth, since the
        # judge currently runs before final_qualify and qualified_out
        # would always be False here).
        all_active = [c for c in claims if not c.qualified_out]
        all_active_new = [c for c in new_claims if not c.qualified_out]
        old_pass = sum(1 for c in all_active if c.passed) / max(1, len(all_active))
        new_pass = sum(1 for c in all_active_new if c.passed) / max(1, len(all_active_new))
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
