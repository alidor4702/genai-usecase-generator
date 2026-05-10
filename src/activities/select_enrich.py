"""Step 6 — Selection and enrichment.

Filters scored candidates by verification verdict (drops `confirmed_existing`,
promotes the next-highest near-miss) to land on a top-3 set, then makes ONE
LLM call to `mistral-large-2512` (T=0.4) to produce three customer-facing
`EnrichedUseCase` objects plus a `rejected_appendix`.

Large 3 is used here because this is the customer-facing prose — the highest
output-quality step in the pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import timedelta

import mistralai.workflows as workflows
from mistralai.client import Mistral

from src._clients import mistral_client
from src.config import settings
from src.models import (
    BlueprintPattern,
    CompanyContext,
    ComplexityTier,
    CostTier,
    EnrichedUseCase,
    EvidenceLedger,
    ImpactTier,
    RejectedCandidate,
    RetrievedPrecedents,
    ScoredBatch,
    ScoredCandidate,
    SupportingSnippet,
    TimeToValue,
    TimeToValueBasis,
    VerificationBatch,
    VerificationVerdict,
)
from src.prompts import ENRICHMENT_SYSTEM
from src.trace import trace_step

logger = logging.getLogger(__name__)


from src._util import strip_fence as _strip_fence  # noqa: E402


def _coerce_str_list(v: object) -> list[str]:
    """Robust coercion to list[str]. Handles the common LLM mistake of
    returning a single string for a field declared as list[str]."""
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, list):
        return [str(x) for x in v if x]
    return []


# Corpus-shaped ID regex — used to strip fabricated IDs from narrative prose.
_CORPUS_ID_RE = re.compile(
    r"\b(google_cloud_1302|google_cloud_blueprints|evidently)-[0-9a-f]{6,16}\b"
)


# ---------------------------------------------------------------------------
# Numeric-anchoring scrubber: every percentage, dollar amount, time span, or
# scale figure in enrichment prose must literally appear in a cited source.
# Anything that doesn't is replaced with [unanchored: ...] so the meta-eval
# can flag it and the customer-facing report doesn't ship fabricated figures.
# ---------------------------------------------------------------------------


_NUMERIC_CLAIM_PATTERNS = [
    # Percentages: "30%", "8.5%", "30 percent"
    r"\d{1,3}(?:[.,]\d{1,3})?\s*%",
    r"\d{1,3}(?:[.,]\d{1,3})?\s*percent\b",
    # Range percentages: "8-15%", "30 to 50%"
    r"\d{1,3}(?:\.\d+)?\s*[-–to]+\s*\d{1,3}(?:\.\d+)?\s*%",
    # Dollar amounts: "$2.4M", "$2.4 billion"
    r"\$\s*\d+(?:[.,]\d+)*\s*(?:M|B|million|billion|trillion)?",
    # Scale: "5M+", "2.4 million", "10K"
    r"\b\d+(?:\.\d+)?\s*(?:M|million|B|billion|K|thousand)\+?\b",
    # Time spans: "8 weeks", "3-5 days", "8-16 weeks"
    r"\b\d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?\s*(?:days?|weeks?|months?|years?)\b",
    r"\b\d+(?:\.\d+)?\s*(?:days?|weeks?|months?|years?)\b",
    # Multiplier: "5x", "10x throughput"
    r"\b\d+(?:\.\d+)?\s*x\b",
]

_COMBINED_NUMERIC_RE = re.compile(
    "|".join(f"(?:{p})" for p in _NUMERIC_CLAIM_PATTERNS), re.IGNORECASE
)


def _normalize_for_anchor_check(s: str) -> str:
    """Lowercase + collapse whitespace for substring matching against sources."""
    return re.sub(r"\s+", " ", s.strip().lower())


def _format_pool_excerpts(ledger: EvidenceLedger, max_entries: int = 40, excerpt_chars: int = 600) -> str:
    """Render the full ledger as compact excerpts for the polish prompt.

    Polish needs to verify quant claims against ALL retrieved evidence, not
    just what the model explicitly cited. Capped at 40 entries × 600 chars to
    stay within Mistral Small's token budget. Empty-content entries are
    skipped — they can't anchor numbers anyway.
    """
    parts: list[str] = []
    for item in ledger.entries[:max_entries]:
        body = (item.content or "").strip()
        if not body:
            continue
        excerpt = re.sub(r"\s+", " ", body)[:excerpt_chars]
        parts.append(
            f"  [{item.id}] title={item.title[:90]!r}\n"
            f"    url={item.url or '(none)'}\n"
            f"    excerpt: {excerpt}"
        )
    return "\n".join(parts) or "  (pool is empty)"


def _extract_digit_signature(claim: str) -> str:
    """Return only the digits + meaningful unit chars from a numeric claim
    so we can match across small whitespace/case differences. e.g.
    "8-15%" → "8-15%", "$2.4 billion" → "2.4billion"."""
    return re.sub(r"\s+", "", claim.lower())


def _build_anchored_corpus(
    inspired_by: list[str],
    evidence_ids: list[str],  # noqa: ARG001 — kept for signature stability; we now use the full ledger
    precedent_lookup: dict[str, str],
    ledger: EvidenceLedger,
) -> str:
    """Concatenate the deep_content of every cited precedent + EVERY ledger
    entry so we can substring-search numeric claims against the full pool.

    Earlier this function only consumed entries whose ids were in
    `evidence_ids` (claims the LLM explicitly cited). That under-anchored real
    facts: e.g. Carrefour's "14,000 stores" appears in the Wikipedia ledger
    entry but the use case rarely cites Wikipedia by id, so the regex
    scrubber wrapped the number as `[unanchored: 14,000]` and polish later
    stripped it. Meta-eval, downstream of polish, then never saw the number
    to verify.

    Widening the anchored corpus to the full ledger preserves any number
    that has support anywhere in the pipeline's evidence pool, while the
    citation step (later in polish) still attributes them to a specific
    source via the `cited_evidence_ids` map.
    """
    chunks: list[str] = []
    for pid in inspired_by:
        body = precedent_lookup.get(pid, "")
        if body:
            chunks.append(body)
    for item in ledger.entries:
        if item.content:
            chunks.append(item.content)
    return "\n".join(chunks)


def _scrub_unanchored_numerics(
    text: str,
    anchored_corpus: str,
    log_id: str,
) -> tuple[str, list[str]]:
    """Replace numeric claims that don't appear in the anchored corpus with
    `[unanchored: <claim>]`. Returns (cleaned_text, list_of_dropped_claims)."""
    if not text:
        return text, []
    anchored_norm = _normalize_for_anchor_check(anchored_corpus)
    anchored_no_space = re.sub(r"\s+", "", anchored_norm)
    dropped: list[str] = []

    def _replace(m: re.Match[str]) -> str:
        claim = m.group(0).strip()
        claim_norm = _normalize_for_anchor_check(claim)
        # Tier 1: exact (normalized) substring match
        if claim_norm in anchored_norm:
            return claim
        # Tier 2: digit-signature match — handles "8-15 %" vs "8-15%"
        sig = _extract_digit_signature(claim)
        if sig and sig in anchored_no_space:
            return claim
        dropped.append(claim)
        return f"[unanchored: {claim}]"

    cleaned = _COMBINED_NUMERIC_RE.sub(_replace, text)
    if dropped:
        logger.warning(
            "select_enrich: %d unanchored numeric claims in %s: %s",
            len(dropped),
            log_id,
            dropped[:6],
        )
    return cleaned, dropped


def _scrub_narrative_ids(text: str, valid_ids: set[str]) -> tuple[str, list[str]]:
    """Find corpus-shaped IDs in `text`. Drop any not in `valid_ids` and
    surrounding parenthetical scaffolding. Return (scrubbed_text, dropped_ids).

    Examples handled:
      "anchored on (precedent google_cloud_1302-abc123)" → drops the parenthetical
      "see Kroger's deployment, retail_2023-xx" → won't match (different prefix)
        — those are caught by a broader sweep below
    """
    dropped: list[str] = []
    # First pass: corpus-shaped IDs
    for m in list(_CORPUS_ID_RE.finditer(text)):
        if m.group(0) not in valid_ids:
            dropped.append(m.group(0))
    if dropped:
        # Strip parentheticals containing any dropped ID
        for bad in set(dropped):
            text = re.sub(r"\s*\([^)]*" + re.escape(bad) + r"[^)]*\)", "", text)
            text = text.replace(bad, "[unanchored]")
    # Second pass: tokens that LOOK like corpus IDs but use a wrong prefix
    # (the model invents `retail_analytics_2023-...`, `sustainability_ai_2023-...`)
    fake_pattern = re.compile(r"\b[a-z][a-z_]{4,40}_\d{4}-[0-9a-f]{6,16}\b")
    for m in list(fake_pattern.finditer(text)):
        token = m.group(0)
        if token not in valid_ids:
            dropped.append(token)
            text = re.sub(r"\s*\([^)]*" + re.escape(token) + r"[^)]*\)", "", text)
            text = text.replace(token, "[unanchored]")
    return text, dropped


def filter_and_promote(
    scored: ScoredBatch, verified: VerificationBatch, k: int = 3
) -> tuple[list[ScoredCandidate], list[ScoredCandidate], dict[str, VerificationVerdict]]:
    """Return (final_top_k, near_misses_for_appendix, verdict_lookup).

    - confirmed_existing → dropped, replaced by next near-miss (still subject to verification on the next pass — at this point the verification batch is fixed, so any promoted candidate is treated as `pass` by default)
    - partial_overlap → kept, flagged in EnrichedUseCase.builds_on_existing
    - pass → kept
    """
    verdict_by_id = {r.candidate_id: r.verdict for r in verified.results}
    confirmed_ids = {
        r.candidate_id
        for r in verified.results
        if r.verdict == VerificationVerdict.CONFIRMED_EXISTING
    }

    # Walk scored in aggregate-score order; skip confirmed_existing
    final: list[ScoredCandidate] = []
    appendix: list[ScoredCandidate] = []
    for sc in scored.scored:
        if sc.candidate.id in confirmed_ids:
            appendix.append(sc)
            continue
        if len(final) < k:
            final.append(sc)
        else:
            appendix.append(sc)
        if len(final) == k and len(appendix) >= 4:
            break

    # Near-dup swap: the generator self-marks `near_dup_of` when two
    # candidates address substantially the same workflow / data asset /
    # user persona / value chain stage. If the chosen top-k contains both
    # members of a near-dup pair, drop the lower-scored member and pull
    # the next non-linked candidate from the appendix. Avoids the
    # Carrefour-style "two sustainability variants in the top 3" outcome
    # without forcing arbitrary domain diversity.
    final_ids = {sc.candidate.id for sc in final}
    swapped = 0
    while True:
        # Find a near-dup pair within `final`
        dup_pair: tuple[int, int] | None = None
        for i, sc_i in enumerate(final):
            link = sc_i.candidate.near_dup_of
            if not link or link not in final_ids:
                continue
            j = next((idx for idx, sc_j in enumerate(final) if sc_j.candidate.id == link), None)
            if j is None or i == j:
                continue
            dup_pair = (i, j) if final[i].aggregate_score < final[j].aggregate_score else (j, i)
            break
        if dup_pair is None:
            break
        drop_idx, _keep_idx = dup_pair
        dropped = final[drop_idx]
        # Pick the next appendix candidate that isn't linked to anyone still in `final`.
        replacement_idx = None
        for ai, sc_app in enumerate(appendix):
            if sc_app.candidate.id in confirmed_ids:
                continue
            link = sc_app.candidate.near_dup_of
            if link and link in (final_ids - {dropped.candidate.id}):
                continue
            replacement_idx = ai
            break
        if replacement_idx is None:
            break  # nothing safe to swap in; leave the dup pair in place
        replacement = appendix.pop(replacement_idx)
        appendix.append(dropped)
        final[drop_idx] = replacement
        final_ids = {sc.candidate.id for sc in final}
        swapped += 1
        if swapped >= 3:  # belt-and-braces: avoid infinite loops on pathological linking
            break

    if swapped:
        logger.info("filter_and_promote: swapped %d near-dup candidate(s) out of top-3", swapped)
    return final, appendix, verdict_by_id


def _format_top3_input(
    final: list[ScoredCandidate],
    verdicts: dict[str, VerificationVerdict],
    ledger: EvidenceLedger | None = None,
    snippets_by_id: dict[str, list[SupportingSnippet]] | None = None,
) -> str:
    out: list[str] = []
    for sc in final:
        c = sc.candidate
        verdict = verdicts.get(c.id, VerificationVerdict.PASS)
        # Render the evidence titles inline so the enrichment model knows what
        # each cited evidence_id refers to (it'll use this to anchor claims).
        evidence_lines: list[str] = []
        if ledger is not None:
            for eid in c.evidence_ids:
                item = ledger.by_id(eid)
                if item:
                    evidence_lines.append(f"  - {eid}: {item.title} ({item.url or 'no url'})")
        evidence_block = (
            "\n".join(evidence_lines) if evidence_lines else "  (none — purely context-grounded)"
        )

        snip_block = ""
        snips = (snippets_by_id or {}).get(c.id, [])
        if snips:
            snip_lines = "\n".join(
                f'  - "{s.quote[:280]}" — {s.title or s.url} ({s.url})' for s in snips
            )
            snip_block = (
                "Verifier-extracted supporting snippets (use these as PRIMARY grounding "
                "when writing description / why_this_company — they are claim-relevant "
                "lines pulled live from per-candidate web searches):\n"
                f"{snip_lines}\n"
            )

        out.append(
            f"--- candidate_id: {c.id} ---\n"
            f"Title: {c.title}\n"
            f"Description: {c.description}\n"
            f"Why this company: {c.why_this_company}\n"
            f"Estimated impact (raw): {c.estimated_impact_summary}\n"
            f"Suggested Mistral products: {', '.join(c.suggested_mistral_products) or '(none)'}\n"
            f"Novelty: {c.novelty.value}\n"
            f"Verification verdict: {verdict.value}\n"
            f"Aggregate score: {sc.aggregate_score:.2f}\n"
            f"Per-criterion: relevance={sc.relevance.score} iconic={sc.iconic_potential.score} "
            f"impact={sc.estimated_impact.score} feasibility={sc.feasibility.score} "
            f"mistral_fit={sc.mistral_suitability.score}\n"
            f"Inspired_by: {', '.join(c.inspired_by) or '(empty — novel)'}\n"
            f"Grounded_in: {', '.join(c.grounded_in)}\n"
            f"Evidence_ids carried from generation:\n{evidence_block}\n"
            + snip_block
        )
    return "\n".join(out)


def _format_near_misses(appendix: list[ScoredCandidate]) -> str:
    if not appendix:
        return "(no near-misses — top-k candidates all passed)"
    return "\n".join(
        f"- {sc.candidate.id}: {sc.candidate.title} (aggregate {sc.aggregate_score:.2f})"
        for sc in appendix[:6]
    )


def _build_user_message(
    final: list[ScoredCandidate],
    appendix: list[ScoredCandidate],
    verdicts: dict[str, VerificationVerdict],
    ctx: CompanyContext,
    ledger: EvidenceLedger | None = None,
    snippets_by_id: dict[str, list[SupportingSnippet]] | None = None,
) -> str:
    return (
        "# Target company context\n"
        + ctx.model_dump_json(indent=2)
        + "\n\n# Verified top-3 candidates to enrich\n"
        + _format_top3_input(final, verdicts, ledger, snippets_by_id)
        + "\n\n# Near-misses (for the rejected_appendix)\n"
        + _format_near_misses(appendix)
        + "\n\nReturn STRICT JSON of shape:\n"
        '{"top_use_cases": [EnrichedUseCase, EnrichedUseCase, EnrichedUseCase],'
        ' "rejected_appendix": [{"title": str, "one_line_reason": str}, ...]}'
    )


def _coerce_enriched(
    raw: dict[str, object],
    scored: ScoredCandidate,
    verdict: VerificationVerdict,
    valid_corpus_ids: set[str] | None = None,
) -> EnrichedUseCase:
    blueprint_str = str(raw.get("blueprint_pattern") or "rag")
    try:
        bp = BlueprintPattern(blueprint_str)
    except ValueError:
        bp = BlueprintPattern.RAG

    cost_str = str(raw.get("operating_cost_tier") or "unknown")
    try:
        cost = CostTier(cost_str)
    except ValueError:
        cost = CostTier.UNKNOWN

    # Impact tier from aggregate score buckets
    if scored.estimated_impact.score >= 8:
        impact = ImpactTier.HIGH
    elif scored.estimated_impact.score >= 5:
        impact = ImpactTier.MEDIUM
    else:
        impact = ImpactTier.LOW

    # Complexity heuristic from feasibility (inverse-ish)
    if scored.feasibility.score >= 8:
        complexity = ComplexityTier.LOW
    elif scored.feasibility.score >= 5:
        complexity = ComplexityTier.MEDIUM
    else:
        complexity = ComplexityTier.HIGH

    ttv_raw = raw.get("time_to_value")

    builds_on = verdict == VerificationVerdict.PARTIAL_OVERLAP

    # Scrub fabricated corpus-shaped IDs from any free-text field.
    valid = valid_corpus_ids or set()
    description_raw = str(raw.get("description", scored.candidate.description))
    why_raw = str(raw.get("why_this_company", scored.candidate.why_this_company))
    risk_raw = str(raw.get("top_implementation_risk", ""))
    description, dropped_d = _scrub_narrative_ids(description_raw, valid)
    why_this, dropped_w = _scrub_narrative_ids(why_raw, valid)
    risk_text, dropped_r = _scrub_narrative_ids(risk_raw, valid)
    if isinstance(ttv_raw, dict):
        ttv_text, dropped_t = _scrub_narrative_ids(str(ttv_raw.get("estimate") or "unknown"), valid)
        ttv_anchored = [str(p) for p in (ttv_raw.get("anchored_to") or []) if isinstance(p, str)]
        basis_str = str(ttv_raw.get("basis") or "").strip().lower()
        ttv_rationale = ttv_raw.get("rationale")
        try:
            ttv_basis = TimeToValueBasis(basis_str) if basis_str else TimeToValueBasis.UNKNOWN
        except ValueError:
            ttv_basis = TimeToValueBasis.UNKNOWN
        # Infer basis when the model omitted it (back-compat).
        if ttv_basis == TimeToValueBasis.UNKNOWN and ttv_text.strip().lower() != "unknown":
            ttv_basis = TimeToValueBasis.PRECEDENT if ttv_anchored else TimeToValueBasis.BALLPARK_ASSUMPTION
        # Hard enforcement: a "precedent" estimate REQUIRES a non-empty
        # anchored_to. If the model claimed precedent without precedent IDs,
        # demote to ballpark_assumption (or unknown if estimate is "unknown").
        if ttv_basis == TimeToValueBasis.PRECEDENT and not ttv_anchored:
            if ttv_text.strip().lower() == "unknown":
                ttv_basis = TimeToValueBasis.UNKNOWN
            else:
                logger.warning(
                    "select_enrich: TTV claimed basis=precedent but anchored_to is empty for %s; "
                    "demoting to ballpark_assumption (estimate=%r)",
                    scored.candidate.id, ttv_text,
                )
                ttv_basis = TimeToValueBasis.BALLPARK_ASSUMPTION
        ttv = TimeToValue(
            estimate=ttv_text,
            anchored_to=ttv_anchored,
            basis=ttv_basis,
            rationale=(str(ttv_rationale).strip() if isinstance(ttv_rationale, str) and ttv_rationale.strip() else None),
        )
    else:
        ttv_text, dropped_t = _scrub_narrative_ids(str(ttv_raw or "unknown"), valid)
        ttv = TimeToValue(estimate=ttv_text, basis=TimeToValueBasis.UNKNOWN)
    total_dropped = dropped_d + dropped_w + dropped_r + dropped_t
    if total_dropped:
        logger.warning(
            "select_enrich: scrubbed %d fabricated IDs from %s narrative: %s",
            len(total_dropped),
            scored.candidate.id,
            total_dropped[:6],
        )

    return EnrichedUseCase(
        id=str(raw.get("id", scored.candidate.id)),
        title=str(raw.get("title", scored.candidate.title)),
        description=description,
        why_this_company=why_this,
        example_input=str(raw.get("example_input", "")),
        example_output=str(raw.get("example_output", "")),
        suggested_mistral_products=(
            _coerce_str_list(raw.get("suggested_mistral_products"))
            or scored.candidate.suggested_mistral_products
        ),
        blueprint_pattern=bp,
        blueprint_mermaid=str(raw.get("blueprint_mermaid", "")),
        time_to_value=ttv,
        operating_cost_tier=cost,
        impact_tier=impact,
        complexity_tier=complexity,
        top_implementation_risk=risk_text,
        inspired_by=_coerce_str_list(raw.get("inspired_by")) or scored.candidate.inspired_by,
        grounded_in=_coerce_str_list(raw.get("grounded_in")) or scored.candidate.grounded_in,
        evidence_ids=(
            _coerce_str_list(raw.get("evidence_ids")) or scored.candidate.evidence_ids
        ),
        builds_on_existing=builds_on,
        builds_on_note=(
            "Builds on an existing initiative at this company (partial overlap detected by verifier)."
            if builds_on
            else None
        ),
    )


_POLISH_SYSTEM = """\
You are polishing customer-facing AI use case prose for delivery to a Mistral
sales engineer. The text has been through automated checks and contains
intermediate markers and opaque IDs that you need to clean up.

Transformations to apply, IN ORDER:

1. UNANCHORED NUMBER MARKERS + ANY OTHER QUANTITATIVE COMPANY CLAIM —
   the regex-based numeric scrubber wraps obvious patterns ($, %, M/B,
   weeks, x-multipliers) as `[unanchored: X]`, but it doesn't catch
   every unit (PB, TB, store counts, customer counts, country counts,
   employee counts, dataset sizes in any unit). For ANY specific
   quantitative claim about THIS company's internals (regardless of
   whether the regex marked it), do this:

   STEP 1 — CHECK THE FULL SOURCE POOL.
   The user message includes a "## Full evidence pool" block listing every
   ledger entry the pipeline retrieved (Wikipedia, news, gap-fill,
   per-candidate verification, web_search). For each specific number in
   the prose, scan the pool excerpts for that figure attached to the same
   entity (or near-equivalent: "14,000 stores" matches "14000 stores",
   "operates in 40 countries" matches "present in 40 countries", "10 PB"
   matches "10-petabyte", etc.).

   STEP 2 — IF FOUND in the pool, KEEP the number AND attach a citation.
   Replace `[unanchored: X]` (if present) with `X [short anchor](url)`
   pulled from the pool entry where you found support. If the number is
   NOT inside an unanchored marker but you confirmed it from the pool,
   leave the number as-is and append a citation in the same sentence.
   ALSO add the ledger entry's `evidence_id` to the `cited_evidence_ids`
   field in your output (a flat list of ev-IDs you newly cited).

   STEP 3 — IF NOT FOUND in the pool, KEEP THE NUMBER AS-IS (drop the
   `[unanchored:]` wrapper but DO NOT replace with qualitative language).
   The number flows through to the downstream verification chain:
   meta-eval extracts it as a substantive claim, web-verify runs a Tavily
   search if the pool didn't anchor it, and a source-judge decides
   whether any retrieved source actually supports it. ONLY if that whole
   chain still finds no support will a separate final-render step rewrite
   the prose qualitatively.
     - "reduces audit time by [unanchored: 30%]" → "reduces audit time by
       30%" (keep the number, drop the bracket — verification chain decides)
     - "$[unanchored: 4M] in fines" → "$4M in fines"
     - "1.5B+ active devices" (already plain, no bracket) → leave as-is
   Polish's job is to render clean prose, NOT to pre-strip unverified
   numbers. v6 over-stripped real numbers (e.g. Carrefour's 14k stores,
   L'Oréal's 10 PB) because polish ran before the verification chain had
   a chance. v7 lets every number reach the verifier first.

   When a specific number IS already anchored in-sentence (precedent
   reference, markdown link to a ledger URL), KEEP IT and don't touch it.
   NEVER leave any `[unanchored: ...]` marker in the final output.

2. OPAQUE LEDGER IDS — every `(ev-XXXXXXXXXX)` reference is an internal ID
   that must become a markdown link. The mapping {evidence_id → {title, url}}
   is provided. REPLACE each `(ev-XXX)` with a markdown link of form
   `[short descriptive anchor](url)`. Use 2-6 words for anchor text that
   describes WHAT the source is. Examples:
     "supplier emissions platform (ev-62dd0bb89b)" →
       "supplier emissions platform ([Carrefour 2024 climate plan](https://...))"
     "(ev-7f9843cb4e)" →
       "([Concordis buying alliance announcement](https://...))"
   If the ev-ID has no provided mapping, drop it (just remove the
   parenthetical).

3. URLS — keep only URLs that match an entry in the provided source map.
   If you encounter any URL not in that map, strip it (rewrite the sentence
   to remove the link).

4. PRECEDENT IDS — any `google_cloud_*-...` or `evidently-...` corpus ID
   in prose should be removed (replaced with the named company if available).

PRESERVE:
- All numbers that are NOT inside [unanchored: X] markers — those have been
  verified.
- The structure of the prose (paragraphs, section flow).
- All factual claims and named entities.
- All existing markdown links that point at URLs in the source map.

Output STRICT JSON with the polished fields:
{
  "description": "...",
  "why_this_company": "...",
  "time_to_value": "...",
  "top_implementation_risk": "...",
  "cited_evidence_ids": ["ev-...", ...]   // pool entries you newly cited
}
"""


_ATTRIBUTION_CHECK_SYSTEM = """\
You correct misattributed precedent citations in text. Given a paragraph and
a mapping {precedent_id: actual_company}, find every place where the text
claims a peer-deployment at company X attached to precedent ID Y, but the
actual company for ID Y is Z (Z != X). Rewrite those claims so the company
matches the precedent ID. Leave everything else exactly as-is.

If a sentence cites a precedent ID without naming any specific peer company,
leave it alone — that's not a misattribution. If the text already attributes
correctly, return it unchanged.

Output STRICT JSON: {"description": "...", "why_this_company": "..."}
"""


async def _attribution_check_use_case(
    use_case: EnrichedUseCase,
    precedent_company_lookup: dict[str, str],
    target_company: str,
    client: Mistral,
) -> int:
    """Detect and correct misattributed precedent citations.

    For each corpus ID cited in prose, verify the surrounding sentence's
    named peer company matches the precedent's actual company. Covers
    description, why_this_company, time_to_value.estimate, and
    top_implementation_risk — anywhere a citation can land. Uses Mistral
    Small @ T=0.1 to rewrite mismatches, since the rewrite is contextual
    enough that a regex would be too brittle.
    """
    fields_to_check: dict[str, str] = {
        "description": use_case.description,
        "why_this_company": use_case.why_this_company,
        "time_to_value": use_case.time_to_value.estimate,
        "top_implementation_risk": use_case.top_implementation_risk,
    }
    found_ids: set[str] = set()
    for text in fields_to_check.values():
        for m in _CORPUS_ID_RE.finditer(text or ""):
            full_id = m.group(0)
            if full_id in precedent_company_lookup:
                found_ids.add(full_id)
    if not found_ids:
        return 0

    id_company_lines = "\n".join(
        f"  {pid}: {precedent_company_lookup[pid]}" for pid in sorted(found_ids)
    )
    user = (
        f"Target company (we are recommending TO this company, do NOT change "
        f"its mentions): {target_company}\n\n"
        f"Precedent IDs cited in the text → actual peer companies:\n"
        f"{id_company_lines}\n\n"
        f"Original description:\n{use_case.description}\n\n"
        f"Original why_this_company:\n{use_case.why_this_company}\n\n"
        f"Original time_to_value:\n{use_case.time_to_value.estimate}\n\n"
        f"Original top_implementation_risk:\n{use_case.top_implementation_risk}\n\n"
        'Output STRICT JSON: {"description": "...", "why_this_company": "...", '
        '"time_to_value": "...", "top_implementation_risk": "..."}'
    )
    try:
        async with trace_step(
            "attribution_check",
            settings.mistral_scoring_model,
            "chat.complete",
            inputs_summary=f"use_case={use_case.id} cited_ids={sorted(found_ids)}",
        ) as ev:
            r = await client.chat.complete_async(
                model=settings.mistral_scoring_model,
                temperature=0.1,
                max_tokens=4000,
                timeout_ms=60_000,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _ATTRIBUTION_CHECK_SYSTEM},
                    {"role": "user", "content": user},
                ],
            )
            text = r.choices[0].message.content
            if isinstance(text, list):
                text = "".join(getattr(b, "text", "") for b in text)
            data = json.loads(_strip_fence(str(text or "{}")))
            ev.outputs_summary = f"received {len(data)} fields"
    except Exception as e:
        logger.warning("attribution check failed for %s: %s", use_case.id, type(e).__name__)
        return 0

    new_desc = str(data.get("description") or use_case.description)
    new_why = str(data.get("why_this_company") or use_case.why_this_company)
    new_ttv = str(data.get("time_to_value") or use_case.time_to_value.estimate)
    new_risk = str(
        data.get("top_implementation_risk") or use_case.top_implementation_risk
    )
    corrections = 0
    if new_desc.strip() and new_desc != use_case.description:
        use_case.description = new_desc
        corrections += 1
    if new_why.strip() and new_why != use_case.why_this_company:
        use_case.why_this_company = new_why
        corrections += 1
    if new_ttv.strip() and new_ttv != use_case.time_to_value.estimate:
        use_case.time_to_value.estimate = new_ttv
        corrections += 1
    if new_risk.strip() and new_risk != use_case.top_implementation_risk:
        use_case.top_implementation_risk = new_risk
        corrections += 1
    if corrections:
        logger.info(
            "attribution: corrected %d field(s) on %s (cited IDs: %s)",
            corrections,
            use_case.id,
            sorted(found_ids),
        )
    return corrections


_URL_RE = re.compile(r"https?://[^\s\)\]]+")
# Detect empty-link markdown like `[Source name]()` left over when the polish
# pass tried to insert a citation but the ledger entry had no URL.
_EMPTY_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(\s*\)")


def _strip_fabricated_urls(text: str, valid_urls: set[str]) -> tuple[str, list[str]]:
    """Remove any URL not in `valid_urls` (from the ledger), then collapse any
    empty-link markdown `[text]()` to just `text`. Returns
    (cleaned_text, list_of_dropped_urls)."""
    if not text:
        return text, []
    dropped: list[str] = []

    def _check(m: re.Match[str]) -> str:
        url = m.group(0).rstrip(".,;:")
        if url in valid_urls:
            return m.group(0)
        dropped.append(url)
        return ""

    cleaned = _URL_RE.sub(_check, text)
    # Collapse any `[text]()` (left after the URL was stripped, or emitted by
    # the polish LLM when no URL was available) to bare `text` — broken
    # markdown is worse than no link.
    cleaned = _EMPTY_MD_LINK_RE.sub(lambda m: m.group(1), cleaned)
    return cleaned, dropped


async def _polish_use_case(
    use_case: EnrichedUseCase,
    ledger: EvidenceLedger,
    client: Mistral,
) -> int:
    """Run the polish LLM call: convert [unanchored: X] markers to qualitative
    language, opaque (ev-XXX) IDs to markdown links, and drop any URL not in
    the ledger. Returns the number of fields that were modified.

    This is the customer-facing cleanup step — without it, the report shows
    [unanchored: 30%] markers and (ev-a1b2c3) IDs that mean nothing to a
    sales engineer reading the output.
    """
    # Build the source map the polish prompt needs
    source_map: dict[str, dict[str, str]] = {}
    for eid in use_case.evidence_ids:
        item = ledger.by_id(eid)
        if item is None:
            continue
        source_map[eid] = {"title": item.title, "url": item.url or ""}

    text_blob = (
        (use_case.description or "")
        + (use_case.why_this_company or "")
        + (use_case.time_to_value.estimate or "")
        + (use_case.top_implementation_risk or "")
    )
    has_unanchored = "[unanchored:" in text_blob
    has_opaque_ev = bool(re.search(r"\(ev-[0-9a-f]{6,12}\)", text_blob))
    if not has_unanchored and not has_opaque_ev:
        # Nothing for the LLM to clean up. Still run URL validation below.
        valid_urls = {item.url for item in ledger.entries if item.url}
        modified = 0
        new_desc, dropped_d = _strip_fabricated_urls(use_case.description, valid_urls)
        if dropped_d:
            use_case.description = new_desc
            modified += 1
        new_why, dropped_w = _strip_fabricated_urls(use_case.why_this_company, valid_urls)
        if dropped_w:
            use_case.why_this_company = new_why
            modified += 1
        if dropped_d or dropped_w:
            logger.warning(
                "polish: stripped %d fabricated URLs from %s: %s",
                len(dropped_d) + len(dropped_w),
                use_case.id,
                (dropped_d + dropped_w)[:6],
            )
        return modified

    source_map_block = (
        "\n".join(
            f"  {eid}: title={info['title'][:80]!r}, url={info['url'] or '(none)'}"
            for eid, info in source_map.items()
        )
        or "  (none — opaque (ev-XXX) IDs without mapping should be dropped)"
    )
    pool_excerpts = _format_pool_excerpts(ledger)
    user = (
        f"Source map for opaque (ev-XXX) replacement:\n{source_map_block}\n\n"
        f"## Full evidence pool (use these excerpts to verify quantitative claims "
        f"before stripping; cite via cited_evidence_ids when you find support)\n"
        f"{pool_excerpts}\n\n"
        f"Original description:\n{use_case.description}\n\n"
        f"Original why_this_company:\n{use_case.why_this_company}\n\n"
        f"Original time_to_value:\n{use_case.time_to_value.estimate}\n\n"
        f"Original top_implementation_risk:\n{use_case.top_implementation_risk}\n\n"
        'Output STRICT JSON: {"description": "...", "why_this_company": "...", '
        '"time_to_value": "...", "top_implementation_risk": "...", '
        '"cited_evidence_ids": ["ev-...", ...]}'
    )
    try:
        async with trace_step(
            "polish",
            settings.mistral_scoring_model,
            "chat.complete",
            inputs_summary=(
                f"use_case={use_case.id} unanchored={has_unanchored} "
                f"opaque_ev={has_opaque_ev}"
            ),
        ) as ev:
            r = await client.chat.complete_async(
                model=settings.mistral_scoring_model,
                temperature=0.1,
                max_tokens=5000,
                timeout_ms=90_000,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _POLISH_SYSTEM},
                    {"role": "user", "content": user},
                ],
            )
            text = r.choices[0].message.content
            if isinstance(text, list):
                text = "".join(getattr(b, "text", "") for b in text)
            data = json.loads(_strip_fence(str(text or "{}")))
            ev.outputs_summary = f"polished {len(data)} fields"
    except Exception as e:
        logger.warning("polish failed for %s: %s", use_case.id, type(e).__name__)
        return 0

    new_desc = str(data.get("description") or use_case.description)
    new_why = str(data.get("why_this_company") or use_case.why_this_company)
    new_ttv = str(data.get("time_to_value") or use_case.time_to_value.estimate)
    new_risk = str(
        data.get("top_implementation_risk") or use_case.top_implementation_risk
    )

    # Pick up any new ledger entries the polish step cited from the full pool
    # so downstream meta-eval / fact-check sees them as supporting evidence.
    newly_cited_raw = data.get("cited_evidence_ids") or []
    if isinstance(newly_cited_raw, list):
        valid_ids = {item.id for item in ledger.entries}
        newly_cited = [
            str(eid) for eid in newly_cited_raw
            if isinstance(eid, str) and eid in valid_ids and eid not in use_case.evidence_ids
        ]
        if newly_cited:
            use_case.evidence_ids = list(use_case.evidence_ids) + newly_cited
            logger.info(
                "polish: %s newly cited %d pool entries from full evidence pool: %s",
                use_case.id, len(newly_cited), newly_cited[:6],
            )

    # Final URL validation pass — strip any URL the LLM emitted that isn't in
    # the ledger (defense in depth against fabricated URLs).
    valid_urls = {item.url for item in ledger.entries if item.url}
    new_desc, dropped_d = _strip_fabricated_urls(new_desc, valid_urls)
    new_why, dropped_w = _strip_fabricated_urls(new_why, valid_urls)
    new_ttv, dropped_t = _strip_fabricated_urls(new_ttv, valid_urls)
    new_risk, dropped_r = _strip_fabricated_urls(new_risk, valid_urls)
    total_dropped_urls = len(dropped_d) + len(dropped_w) + len(dropped_t) + len(dropped_r)
    if total_dropped_urls:
        logger.warning(
            "polish: stripped %d fabricated URLs from %s: %s",
            total_dropped_urls,
            use_case.id,
            (dropped_d + dropped_w + dropped_t + dropped_r)[:6],
        )

    modified = 0
    if new_desc.strip() and new_desc != use_case.description:
        use_case.description = new_desc
        modified += 1
    if new_why.strip() and new_why != use_case.why_this_company:
        use_case.why_this_company = new_why
        modified += 1
    if new_ttv.strip() and new_ttv != use_case.time_to_value.estimate:
        use_case.time_to_value.estimate = new_ttv
        modified += 1
    if new_risk.strip() and new_risk != use_case.top_implementation_risk:
        use_case.top_implementation_risk = new_risk
        modified += 1
    if modified:
        logger.info(
            "polish: cleaned %d field(s) on %s (unanchored=%s opaque_ev=%s)",
            modified,
            use_case.id,
            has_unanchored,
            has_opaque_ev,
        )
    return modified


def _apply_numeric_anchoring(
    use_case: EnrichedUseCase,
    precedent_lookup: dict[str, str],
    ledger: EvidenceLedger,
) -> int:
    """Run the numeric-anchoring scrubber over the customer-facing fields.

    Returns count of dropped claims (logged for observability)."""
    anchored_corpus = _build_anchored_corpus(
        use_case.inspired_by, use_case.evidence_ids, precedent_lookup, ledger
    )
    if not anchored_corpus.strip():
        # No cited sources at all — can't anchor; leave numbers in place but
        # the meta-eval will catch them via fact-check.
        return 0
    total_dropped = 0
    use_case.description, dropped = _scrub_unanchored_numerics(
        use_case.description, anchored_corpus, f"{use_case.id}.description"
    )
    total_dropped += len(dropped)
    use_case.why_this_company, dropped = _scrub_unanchored_numerics(
        use_case.why_this_company, anchored_corpus, f"{use_case.id}.why_this_company"
    )
    total_dropped += len(dropped)
    return total_dropped




@workflows.activity(start_to_close_timeout=timedelta(seconds=300))
async def select_and_enrich_activity(
    scored: ScoredBatch,
    verified: VerificationBatch,
    ctx: CompanyContext,
    retrieved: RetrievedPrecedents | None = None,
    ledger: EvidenceLedger | None = None,
) -> tuple[list[EnrichedUseCase], list[RejectedCandidate]]:
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for enrichment")
    if ledger is None:
        ledger = EvidenceLedger()

    final, appendix, verdicts = filter_and_promote(scored, verified, k=3)
    if len(final) < 3:
        # Pad from appendix if needed (shouldn't usually happen)
        while len(final) < 3 and appendix:
            final.append(appendix.pop(0))

    # Build the valid corpus IDs set from all candidates' validated inspired_by
    # plus the few-shot example IDs that the model also sees in the prompt.
    valid_corpus_ids: set[str] = set()
    for sc in scored.scored:
        valid_corpus_ids.update(sc.candidate.inspired_by)
    # Few-shot example IDs (kept in sync with src/prompts.py FEW_SHOT_EXAMPLES)
    valid_corpus_ids.update(
        {
            "google_cloud_1302-9177e5acd1",
            "google_cloud_blueprints-e59370be9e",
            "google_cloud_1302-d90664fc2c",
        }
    )

    # Build precedent lookup (id → deep_content) for numeric anchoring.
    precedent_lookup: dict[str, str] = {}
    if retrieved is not None:
        for p in retrieved.items:
            precedent_lookup[p.id] = (p.deep_content or p.description or "")

    client = mistral_client()
    snippets_by_id: dict[str, list[SupportingSnippet]] = {
        r.candidate_id: list(r.supporting_snippets) for r in verified.results
    }
    user_msg = _build_user_message(final, appendix, verdicts, ctx, ledger, snippets_by_id)
    # Tier dispatch: fast uses mistral-medium for enrichment (faster, slight
    # quality drop on prose polish but full guardrails preserved); standard
    # and max use mistral-large per the locked stack.
    enrich_model = (
        settings.mistral_research_model
        if settings.tier.value == "fast"
        else settings.mistral_enrichment_model
    )
    async with trace_step(
        "enrich",
        enrich_model,
        "chat.complete",
        inputs_summary=f"tier={settings.tier.value} top_3={[sc.candidate.id for sc in final]}",
    ) as ev:
        r = await client.chat.complete_async(
            model=enrich_model,
            temperature=0.4,
            max_tokens=12_000,
            timeout_ms=240_000,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": ENRICHMENT_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
        )
        text = r.choices[0].message.content
        if isinstance(text, list):
            text = "".join(getattr(b, "text", "") for b in text)
        data = json.loads(_strip_fence(str(text or "")))
        ev.outputs_summary = f"enriched {len(data.get('top_use_cases', []))} use cases"

    raw_uses = data.get("top_use_cases", [])
    enriched: list[EnrichedUseCase] = []
    if isinstance(raw_uses, list):
        for raw, sc in zip(raw_uses, final, strict=False):
            if not isinstance(raw, dict):
                continue
            enriched.append(
                _coerce_enriched(
                    raw,
                    sc,
                    verdicts.get(sc.candidate.id, VerificationVerdict.PASS),
                    valid_corpus_ids,
                )
            )

    # Pad if the model returned fewer than 3 enriched outputs
    while len(enriched) < len(final):
        sc = final[len(enriched)]
        enriched.append(
            _coerce_enriched(
                {"id": sc.candidate.id},
                sc,
                verdicts.get(sc.candidate.id, VerificationVerdict.PASS),
                valid_corpus_ids,
            )
        )

    # Numeric anchoring scrub — mark percentages / scale figures / dollar
    # amounts that don't literally appear in a cited precedent or ledger entry.
    # The polish pass below converts the [unanchored: X] markers into
    # qualitative language for customer-facing prose.
    total_unanchored = 0
    for uc in enriched:
        total_unanchored += _apply_numeric_anchoring(uc, precedent_lookup, ledger)

    # Polish pass — converts intermediate markers and opaque IDs into the
    # customer-facing form. Replaces every [unanchored: X] with qualitative
    # language; replaces (ev-XXX) opaque IDs with [title](url) markdown links;
    # strips any URL not in the ledger (fabricated-URL defense).
    # Run all 3 use cases concurrently — independent calls, no shared state.
    # Tier dispatch: fast skips polish to save ~5s (numeric scrubber still
    # marks unanchored claims; just no qualitative rewrite).
    total_polished = 0
    if settings.tier.value != "fast":
        polish_results = await asyncio.gather(
            *(_polish_use_case(uc, ledger, client) for uc in enriched)
        )
        total_polished = sum(polish_results)

    # Attribution check — misattributed precedent citations (e.g. "Walmart's
    # deployment (precedent X)" when X is actually Schwarz Group) get rewritten.
    # Also parallelizable across the 3 use cases. Skipped on fast tier.
    precedent_company_lookup: dict[str, str] = (
        {p.id: p.company for p in retrieved.items} if retrieved is not None else {}
    )
    total_attribution_fixes = 0
    if precedent_company_lookup and settings.tier.value != "fast":
        attribution_results = await asyncio.gather(
            *(
                _attribution_check_use_case(
                    uc, precedent_company_lookup, ctx.identity.name, client
                )
                for uc in enriched
            )
        )
        total_attribution_fixes = sum(attribution_results)

    raw_rej = data.get("rejected_appendix") or []
    rejected: list[RejectedCandidate] = []
    # Build a set of title-like tokens for the enriched top-3 so we can
    # de-dup the rejected_appendix. Veolia v8 had "Circular economy
    # marketplace agent" appearing in BOTH the top-3 AND the appendix —
    # the LLM included a near-miss version of an already-promoted use
    # case in its JSON output. Drop any rejected entry whose title
    # substantially overlaps with a top-3 title.
    enriched_title_tokens: list[set[str]] = []
    for uc in enriched:
        toks = {t.lower() for t in re.findall(r"\b[a-zA-Z]{4,}\b", uc.title or "")}
        # Strip generic words so e.g. "circular economy marketplace agent"
        # tokens overlap cleanly with the title's distinctive words.
        toks -= {"agent", "system", "platform", "tool", "use", "case", "ai", "for"}
        enriched_title_tokens.append(toks)

    def _is_dup_of_top3(rej_title: str) -> bool:
        rej_toks = {t.lower() for t in re.findall(r"\b[a-zA-Z]{4,}\b", rej_title or "")}
        rej_toks -= {"agent", "system", "platform", "tool", "use", "case", "ai", "for"}
        if not rej_toks:
            return False
        for top_toks in enriched_title_tokens:
            if not top_toks:
                continue
            overlap = rej_toks & top_toks
            # If ≥ 2 distinctive words match (or all tokens match for short
            # titles), treat as duplicate.
            if len(overlap) >= 2 or (len(rej_toks) <= 2 and overlap == rej_toks):
                return True
        return False

    if isinstance(raw_rej, list):
        for r_item in raw_rej:
            if not isinstance(r_item, dict):
                continue
            title = str(r_item.get("title", ""))
            if _is_dup_of_top3(title):
                logger.info(
                    "select_enrich: dropping rejected_appendix entry duplicating a top-3 title: %r",
                    title,
                )
                continue
            rejected.append(
                RejectedCandidate(
                    title=title,
                    one_line_reason=str(r_item.get("one_line_reason", "")),
                )
            )

    logger.info(
        "select_enrich: enriched %d use cases | %d rejected appendix entries | "
        "%d unanchored numeric claims marked | %d polish field-rewrites | "
        "%d attribution fixes",
        len(enriched),
        len(rejected),
        total_unanchored,
        total_polished,
        total_attribution_fixes,
    )
    return enriched[:3], rejected
