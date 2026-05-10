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
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import mistralai.workflows as workflows

from src._clients import mistral_client
from src._util import strip_fence
from src.config import settings
from src.models import (
    EnrichedUseCase,
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

OUTPUT ONE OF THREE VERDICTS:

(1) `supported` — the snippet supports the claim:
- Numerical sufficiency. "56 countries" supports "45+ countries".
  "215,000 employees" supports "approximately 200,000 employees".
- Synonyms / rephrasing. "acquired ModiFace in 2018" supports
  "owns ModiFace".
- Inference from instances. "stores in France, Spain, Italy" supports
  "operates across multiple EU markets".
- Inference from established context. The snippet establishes facts
  (location, organisational structure, document origin, founding
  jurisdiction) from which the claim follows by basic geographic,
  regulatory, or organisational logic. e.g. "French AI company
  headquartered in Paris" supports "Paris HQ ensures alignment with
  EU regulatory frameworks".
- Direct match.

(2) `corrected` — the snippet CONTRADICTS the claim's specific value
but contains the correct value for the SAME fact. We use the source's
value to fix the prose inline. STRICT scope: this verdict applies ONLY
to numeric, rank/ordinal, and temporal facts. NEVER use it for entity
contradictions (a different company / product / person / program
name) — those stay `unsupported` because silent entity-substitution
would break downstream prose semantics.

  Examples that ARE corrections:
  - claim "€3.279T AUM" / snippet "€2.79T AUM"
    → corrected_value: "€2.79T AUM"
  - claim "supports 12 European languages" / snippet "across nine
    European languages"
    → corrected_value: "9 European languages"
  - claim "largest bank in Europe" / snippet "second largest bank in
    Europe by total assets"
    → corrected_value: "second largest bank in Europe"
  - claim "founded in 1859" / snippet "founded in 1854"
    → corrected_value: "founded in 1854"

  Examples that are NOT corrections (return `unsupported` instead):
  - claim "partnered with Sephora" / snippet "partnered with Estée
    Lauder" — different ENTITY, not a same-fact value mismatch.
  - claim "uses Mistral Forge" / snippet "uses Anthropic Claude" —
    different product entity.
  - claim "3,548 plants" / snippet "around 3,600 plants" — too vague
    to use as a clean replacement (no specific value to substitute).

  The `corrected_value` you output MUST be a complete drop-in PHRASE
  that can replace the original phrase in prose without leaving a
  grammar error. NOT just the bare number — include unit and noun.
  ("9 European languages", not just "9".)

(3) `unsupported` — the snippet does not support the claim and does
not contain a clean same-fact correction:
- Mere entity adjacency (mentions related entities, doesn't address
  the assertion).
- Topical adjacency (job description doesn't support claims about
  real-time data systems).
- Token co-occurrence in unrelated context.
- Contradicts the claim but no clean correction exists ("around 3,600
  plants" is too vague to correct "3,548 plants").
- Different ENTITY (use unsupported, not corrected).

Default for inconclusive evidence: `unsupported`.

OUTPUT FORMAT — STRICT JSON:
{
  "verdict": "supported" | "corrected" | "unsupported",
  "reason": "<one short sentence pointing at what's in (or missing
             from) the source that drove the verdict>",
  "corrected_value": "<full drop-in replacement phrase, ONLY if
                      verdict='corrected'>",
  "same_fact": true | false
}
`same_fact` MUST be present when verdict='corrected' and MUST be true.
If you cannot confirm the corrected value addresses the SAME entity +
metric as the claim, downgrade to `unsupported`.
"""


JudgeVerdict = Literal["supported", "corrected", "unsupported"]


@dataclass
class JudgeOutcome:
    verdict: JudgeVerdict
    reason: str | None
    corrected_value: str | None  # full drop-in replacement phrase, only when verdict=corrected
    source_url: str | None       # echoed back so the caller has the link to attach


async def _judge_one(
    claim: FactCheckEntry,
    source_excerpt: str,
    source_url: str | None,
) -> JudgeOutcome:
    """Run one judge call for a single (claim, source) pair.

    `source_url` may be None when the snippet came from meta-eval's
    supporting_signal rather than a resolved ledger entry — in that
    case the user message just labels the source generically.

    On any error, returns verdict=supported (fail-open) so a transient API
    blip doesn't drop a real claim. The judge is a quality gate, not a
    gatekeeper.
    """
    if not claim.claim or not source_excerpt:
        return JudgeOutcome("supported", None, None, source_url)
    client = mistral_client()
    url_label = source_url if source_url else "(meta-eval supporting_signal — no URL resolved)"
    user = (
        f"CLAIM: {claim.claim}\n\n"
        f"SOURCE: {url_label}\n"
        f"SOURCE EXCERPT (truncated to 1500 chars):\n"
        f"{source_excerpt[:1500]}\n\n"
        'Output STRICT JSON per the schema above.'
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
                max_tokens=400,
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
            ev.outputs_summary = f"verdict={data.get('verdict')}"
    except Exception as e:
        logger.warning("source_judge: call failed for claim %r — %s; defaulting to supported",
                       claim.claim[:60], type(e).__name__)
        return JudgeOutcome("supported", None, None, source_url)

    raw_verdict = data.get("verdict")
    # Backward compat: old schema returned {"supports": bool}; map to new.
    if raw_verdict not in ("supported", "corrected", "unsupported"):
        if isinstance(data.get("supports"), bool):
            raw_verdict = "supported" if data["supports"] else "unsupported"
        else:
            raw_verdict = "supported"  # fail-open
    verdict: JudgeVerdict = raw_verdict  # type: ignore[assignment]
    reason = data.get("reason")
    reason_str = str(reason).strip() if isinstance(reason, str) and reason.strip() else None
    corrected_value: str | None = None
    if verdict == "corrected":
        cv = data.get("corrected_value")
        same_fact = data.get("same_fact")
        if isinstance(cv, str) and cv.strip() and same_fact is True:
            corrected_value = cv.strip()
        else:
            # Judge marked corrected but didn't return a usable
            # corrected_value or a same_fact=true confirmation.
            # Demote to unsupported (the conservative choice — better to
            # flag than to silently substitute a value we can't verify).
            verdict = "unsupported"
            reason_str = (reason_str or "") + " (judge marked corrected but no valid same-fact replacement)"
    return JudgeOutcome(verdict, reason_str, corrected_value, source_url)


def _resolve_source_excerpt(
    claim: FactCheckEntry,
    ledger: EvidenceLedger,
) -> tuple[str, str | None] | None:
    """Pick the (source_excerpt, source_url_or_None) for a claim.

    Priority order:
    1. rescue_url (web-verify rescue layer) → ledger lookup, full body.
    2. source_url (meta-eval from "evidence:<ev-id>") → ledger lookup.
    3. **v9.1 fallback**: claim.rationale (the meta-eval supporting_signal
       text) → judge against that text directly. URL is None. Used for
       company_context.* and precedent:* source_kinds where there's no
       resolvable URL but meta-eval cited a quote — catches the L'Oréal
       Galderma slip-through where the supporting quote didn't actually
       mention the entities in the claim.
    4. None → judge skipped (no content of any kind to verify against).
    """
    url = claim.rescue_url or claim.source_url
    if url:
        for ent in ledger.entries:
            if ent.url == url and ent.content:
                return ent.content, url
    # No URL or URL didn't resolve to a ledger entry — fall back to the
    # supporting_signal text from meta-eval. The judge will be told the
    # source URL is None so its reason references "the supplied snippet"
    # without claiming a specific URL backed it.
    rationale = (claim.rationale or "").strip()
    if rationale:
        return rationale, None
    return None


def _apply_correction_to_prose(
    use_cases: list[EnrichedUseCase],
    use_case_id: str,
    original_value: str,
    corrected_value: str,
    source_url: str,
) -> bool:
    """Find the use case by id and substitute `original_value` with the
    corrected phrase + a markdown source citation. Returns True if any
    field was modified.

    Heuristic substitution: the original_value comes from the meta-eval
    claim text (e.g. "BNP Paribas operates in 64 countries"). The actual
    prose may contain the same numeric phrase ("operates in 64 countries")
    in description / why_this_company / time_to_value / risk. We search
    each field for any literal substring of the original_value that
    matches a numeric phrase, and replace with the corrected one.

    Conservative: if no substring matches, we don't touch the prose —
    the claim still flips to passed=True (the source confirms the same
    fact, just at a different magnitude) but the prose retains the old
    number. Step 7e (final_qualify) doesn't touch passed=True claims so
    the worst case is "claim flagged corrected in transparency, prose
    keeps original phrasing". An honest fallback.
    """
    import re as _re
    target = next((uc for uc in use_cases if uc.id == use_case_id), None)
    if target is None:
        return False

    # Pull "anchor" tokens from original_value — numbers and units.
    # If we can't find a clean numeric anchor, give up.
    anchors = _re.findall(
        r"(?:€|\$)?\d+(?:[.,]\d+)?\s*(?:%|T|B|M|K|trillion|billion|million|thousand|"
        r"countries|languages|stores|customers|years|weeks|months|"
        r"PB|TB|GB)?",
        original_value,
    )
    anchors = [a.strip() for a in anchors if a.strip() and any(ch.isdigit() for ch in a)]
    if not anchors:
        return False
    anchor = max(anchors, key=len)  # longest = most specific

    citation = f" ([source]({source_url}))" if source_url else ""
    replacement = f"{corrected_value}{citation}"

    def _patch(field_value: str) -> tuple[str, bool]:
        if anchor in field_value:
            return field_value.replace(anchor, replacement, 1), True
        return field_value, False

    new_desc, h1 = _patch(target.description)
    new_why, h2 = _patch(target.why_this_company)
    new_ttv, h3 = _patch(target.time_to_value.estimate)
    new_risk, h4 = _patch(target.top_implementation_risk)
    if not (h1 or h2 or h3 or h4):
        return False

    # Mutate the use case in place. (model_copy is awkward across nested
    # models; given EnrichedUseCase fields are simple strings here we can
    # reassign on the same instance — the activity owns the list.)
    target.description = new_desc
    target.why_this_company = new_why
    target.time_to_value.estimate = new_ttv
    target.top_implementation_risk = new_risk
    return True


@workflows.activity(start_to_close_timeout=timedelta(seconds=240))
async def judge_claim_sources_activity(
    review: MetaEvalReview,
    claims: list[FactCheckEntry],
    ledger: EvidenceLedger | None = None,
    enriched_uses: list[EnrichedUseCase] | None = None,
) -> tuple[MetaEvalReview, list[FactCheckEntry], list[EnrichedUseCase] | None]:
    """Judge whether each (claim, supporting URL) pair coheres.

    For claims passed=True with a resolvable supporting URL (rescue or
    ledger-cited), ask Mistral Small whether the source supports it.
    Three possible verdicts:

    - `supported`: claim stays passed=True (no change).
    - `corrected`: snippet contradicts but provides the correct same-fact
      value. Claim flips to passed=True with corrected/original_value
      set; prose is rewritten inline using the corrected value + source
      link. Renders with [corrected ↗ X→Y] chip.
    - `unsupported`: false positive. Claim flips to passed=False with
      judge_rejected=true.

    Re-anchors confidence on the post-judge claim set using the same
    bounded-qual_delta formula as web_verify.
    """
    if ledger is None or not claims:
        return review, claims, enriched_uses

    # Build the work list: ALL claims passed=True that have any content
    # to verify against (URL-resolved ledger entry, OR fallback to
    # meta-eval's supporting_signal text). v9.1 extension — was URL-only
    # before, missed the L'Oréal Galderma slip-through.
    work: list[tuple[int, FactCheckEntry, str, str | None]] = []
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
        return review, claims, enriched_uses

    # Concurrency 8 — bumped from 4 in v9. Long claim lists (Veolia / L'Oreal
    # ran 30+ pairs) were the choke; Mistral Small handles 8 concurrent
    # without rate-limiting. ~5–10s wall-clock saved per long run.
    sem = asyncio.Semaphore(8)

    async def _bound(idx: int, c: FactCheckEntry, body: str, url: str):
        async with sem:
            outcome = await _judge_one(c, body, url)
            return idx, outcome

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
    corrected_n = 0
    prose_patched = 0
    for idx, outcome in results:
        c = new_claims[idx]
        if outcome.verdict == "supported":
            continue
        if outcome.verdict == "corrected" and outcome.corrected_value:
            new_claims[idx] = c.model_copy(
                update={
                    "passed": True,
                    "corrected": True,
                    "original_value": c.claim,
                    "corrected_value": outcome.corrected_value,
                    "rescue_url": outcome.source_url or c.rescue_url,
                    "judge_reason": outcome.reason or "source-judge: corrected via same-fact value in source",
                }
            )
            corrected_n += 1
            if enriched_uses is not None and outcome.source_url:
                if _apply_correction_to_prose(
                    enriched_uses,
                    use_case_id=c.use_case_id,
                    original_value=c.claim,
                    corrected_value=outcome.corrected_value,
                    source_url=outcome.source_url,
                ):
                    prose_patched += 1
            continue
        # unsupported
        new_claims[idx] = c.model_copy(
            update={
                "passed": False,
                "judge_rejected": True,
                "judge_reason": outcome.reason or "source-judge: snippet does not support the claim",
            }
        )
        rejected += 1

    if rejected > 0 or corrected_n > 0:
        # Same confidence math as web_verify — clamp the qualitative
        # delta to [-0.15, +0.10] so meta-eval's penalty has bounded
        # influence on the post-rescue baseline.
        all_active = [c for c in claims if not c.qualified_out]
        all_active_new = [c for c in new_claims if not c.qualified_out]
        old_pass = sum(1 for c in all_active if c.passed) / max(1, len(all_active))
        new_pass = sum(1 for c in all_active_new if c.passed) / max(1, len(all_active_new))
        qual_delta_raw = review.confidence - old_pass
        qual_delta = max(-0.15, min(0.10, qual_delta_raw))
        new_confidence = max(0.0, min(1.0, new_pass + qual_delta))
        review = review.model_copy(update={"confidence": new_confidence})
        logger.info(
            "source_judge: rejected %d, corrected %d (prose-patched %d) / %d pairs; "
            "pass-rate %.2f → %.2f; confidence → %.2f",
            rejected, corrected_n, prose_patched, len(work),
            old_pass, new_pass, review.confidence,
        )
    else:
        logger.info("source_judge: all %d pairs cohered (no rejections, no corrections)", len(work))

    return review, new_claims, enriched_uses
