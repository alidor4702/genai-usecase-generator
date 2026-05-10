"""Research activity — orchestrate the 5 parallel sub-tasks and the synthesis LLM call.

Implements the orchestrator-worker pattern (Anthropic 2025): parallel breadth-
first signal gathering, then a single LLM synthesis call that produces a typed
`CompanyContext` and a `research_confidence` score.

Depth toggle scales how many parallel sub-tasks run:
    low    : Wikipedia + verified-companies + existing-initiatives
    medium : low + recent news
    high   : medium + jobs

Per CLAUDE.md, this activity sets `start_to_close_timeout=120s` for itself and
relies on internal sub-task timeouts to bound work. All side effects (HTTP, DB,
LLM) live here, never in workflow.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta

import httpx
import mistralai.workflows as workflows

from src._clients import mistral_client

from src.config import settings
from src.evidence import (
    from_existing_initiative,
    from_news_item,
    from_tavily_result,
    from_wikipedia,
)
from src.trace import trace_step
from src.models import (
    CompanyBusiness,
    CompanyClassification,
    CompanyConstraints,
    CompanyContext,
    CompanyDataAndTech,
    CompanyIdentity,
    CompanyMeta,
    CompanyScale,
    CompanyStrategicContext,
    EvidenceKind,
    EvidenceLedger,
    ExistingInitiative,
    JobsSignal,
    NewsItem,
    ResearchBundle,
    ResearchDepth,
    VerifiedCompanyMatch,
    WikipediaFacts,
)
from src.research.existing_initiatives import fetch_existing_initiatives
from src.research.industry_label import derive_clean_industry_label
from src.research.jobs import fetch_jobs_signal_safe
from src.research.news import fetch_recent_news
from src.research.verification import verify_company
from src.research.wikipedia import fetch_wikipedia_facts

logger = logging.getLogger(__name__)


SYNTHESIS_SYSTEM = """\
You are a research synthesis agent for the Mistral Proto Team. Given multiple
parallel signals about a target company (Wikipedia/Wikidata facts, recent news
with deep-read article bodies, AI/ML hiring direction, the company's existing AI
initiatives, and a verified-companies-index match), produce ONE structured
`CompanyContext` JSON object.

Hard rules:
- Use the provided signals as the basis for every field. A signal can be a
  structured Wikidata field (like `industry`) OR free text inside the Wikipedia
  summary, news bodies, or job postings. READ the prose carefully and extract
  facts from it; do not just defer to structured fields.
- NEVER output "Unknown" / "" / [] for a field if the information IS available
  in the prose. Copying clearly-stated facts is NOT fabrication.
- Be DENSE. Empty lists are usually a synthesis failure, not a data limit.
  - `data_and_tech.likely_data_assets`: aim for 4-8 entries. Infer from the
    business model — a hypermarket chain has loyalty data, transaction data,
    in-store imagery, supply chain telemetry, supplier catalogs. State them.
  - `strategic_context.stated_priorities`: aim for 3-6 entries. Pull every
    public commitment, transformation theme, sustainability target, or
    multi-year plan mentioned in news/Wikipedia. Carbon-neutral goals,
    digital transformation themes, retail-media expansion, regional growth —
    all qualify. Read the news bodies, don't just headline-skim.
  - `business.key_products_or_services`: aim for 3-6 entries. Format names,
    private-label brands, financial-services arms, retail-media networks,
    digital platforms — all qualify. Be specific (Carrefour Bio, Carrefour
    Express, Carrefour Banque, Carrefour Media — not just "groceries").
  - `classification.sub_industries`: aim for 2-4 entries.
  - `business.business_model`: 2-3 sentence description, not a single label.
- `free_text_notes`: any company-specific detail that doesn't cleanly fit a
  structured field but matters for downstream generation goes here. 2-5
  sentences. This is your escape valve — use it.
- IF SIGNALS CONTRADICT (Wikipedia says X, news suggests Y), include both in
  the relevant fields and lower confidence accordingly.
- Do NOT extract financial details (revenue, employee count, stock price,
  founding year, executive names) — they don't drive downstream decisions.
- `existing_ai_initiatives` MUST enumerate every distinct already-deployed
  initiative discovered.
- `meta.research_confidence` reflects how coherently the signals converge.
  Calibration: a famous public company with rich Wikipedia + multiple news
  articles + verified-index hit should be 0.80-0.95. Niche / sparse
  signals → 0.40-0.65.
- The verified-companies index is a confidence boost, never a gate.
- Enum constraints: `scale.size_tier` ∈ {startup, scaleup, enterprise, unknown};
  `scale.public_or_private` ∈ {public, private, unknown}; `business.primary_customers`
  ∈ {B2B, B2C, B2G, mixed, unknown}; `data_and_tech.known_tech_maturity` ∈
  {high, medium, low, unknown}.

Output STRICT JSON matching the CompanyContext schema; no markdown, no commentary.
"""


def _build_synthesis_prompt(name: str, bundle: ResearchBundle) -> str:
    sections: list[str] = [f"# Target company: {name}\n"]

    if bundle.verified_match.matched:
        sections.append(
            "## Verified-companies index match (boost confidence)\n"
            f"- name: {bundle.verified_match.name}\n"
            f"- industry: {bundle.verified_match.industry or 'unknown'}\n"
            f"- country: {bundle.verified_match.country or 'unknown'}\n"
            f"- wikidata_id: {bundle.verified_match.wikidata_id or 'unknown'}\n"
            f"- match score: {bundle.verified_match.score:.1f}\n"
        )
    else:
        sections.append("## Verified-companies index match\nNo strong match.\n")

    if bundle.wikipedia.found:
        sections.append(
            "## Wikipedia / Wikidata\n"
            f"- summary: {(bundle.wikipedia.summary or '')[:3000]}\n"
            f"- industry: {bundle.wikipedia.industry or 'unknown'}\n"
            f"- geography: {bundle.wikipedia.geography or 'unknown'}\n"
        )
    else:
        sections.append("## Wikipedia / Wikidata\nNo page found.\n")

    if bundle.news:
        sections.append("## Recent news (with deep-read bodies)")
        for n in bundle.news:
            body = n.deep_content or n.snippet or ""
            sections.append(f"- {n.title}\n  url: {n.url}\n  body: {body[:2000]}\n")
    else:
        sections.append("## Recent news\n(none collected)\n")

    if bundle.jobs:
        sections.append(
            "## AI/ML hiring signal\n"
            f"- summary: {bundle.jobs.summary}\n"
            "- notable postings: "
            + "; ".join(p.role_title for p in bundle.jobs.notable_postings)
            + "\n"
        )

    if bundle.existing_initiatives:
        sections.append("## Existing AI initiatives at this company")
        for ei in bundle.existing_initiatives:
            sections.append(f"- ({ei.source.value}, conf={ei.confidence}) {ei.description[:600]}")
    else:
        sections.append("## Existing AI initiatives\n(none discovered)\n")

    return "\n".join(sections)


async def _gather_research_bundle(
    company_name: str, depth: ResearchDepth
) -> tuple[ResearchBundle, list[tuple[str, str, str]]]:
    """Run the parallel sub-tasks per the configured depth toggle.

    Returns (bundle, live_verification_sources). The second tuple is empty
    when fuzzy verification already matched; otherwise it contains the URLs
    Tavily fetched during live verification, ready to be added to the ledger.
    """
    # Always-on: Wikipedia, verified-index, existing-initiatives
    wiki_t: asyncio.Task[WikipediaFacts] = asyncio.create_task(fetch_wikipedia_facts(company_name))
    verify_t: asyncio.Task[tuple[VerifiedCompanyMatch, list[tuple[str, str, str]]]] = (
        asyncio.create_task(verify_company(company_name))
    )
    existing_t: asyncio.Task[list[ExistingInitiative]] = asyncio.create_task(
        fetch_existing_initiatives(company_name)
    )

    news_t: asyncio.Task[list[NewsItem]] | None = None
    jobs_t: asyncio.Task[JobsSignal | None] | None = None

    if depth in (ResearchDepth.MEDIUM, ResearchDepth.HIGH):
        news_t = asyncio.create_task(fetch_recent_news(company_name))
    if depth == ResearchDepth.HIGH:
        jobs_t = asyncio.create_task(fetch_jobs_signal_safe(company_name))

    # Await all
    wiki_res, verify_pair, existing_res = await asyncio.gather(
        wiki_t, verify_t, existing_t, return_exceptions=False
    )
    verify_res, live_sources = verify_pair
    news_res: list[NewsItem] = await news_t if news_t else []
    jobs_res: JobsSignal | None = await jobs_t if jobs_t else None

    bundle = ResearchBundle(
        wikipedia=wiki_res,
        news=news_res,
        jobs=jobs_res,
        existing_initiatives=existing_res,
        verified_match=verify_res,
        depth_used=depth,
    )

    # Replace raw Wikidata P452 industry label with a customer-facing one
    # before any caller sees the bundle. Done here (not just in
    # research_company_activity) so the gap-fill re-synthesis path also
    # gets the cleaned label — otherwise re-synthesis re-derives industry
    # from the raw P452 garbage and overrides the clean version.
    cleaned_industry = await derive_clean_industry_label(
        bundle.wikipedia.summary, bundle.wikipedia.industry
    )
    if cleaned_industry and cleaned_industry != bundle.wikipedia.industry:
        bundle.wikipedia.industry = cleaned_industry

    return bundle, live_sources


from src._util import strip_fence as _strip_fence  # backward-compatible alias
from src._rate_limits import MISTRAL_API_RATE_LIMIT


async def _synthesize_company_context(name: str, bundle: ResearchBundle) -> CompanyContext:
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for research synthesis")
    client = mistral_client()
    user = _build_synthesis_prompt(name, bundle)
    async with trace_step(
        "research",
        settings.mistral_research_model,
        "chat.complete",
        inputs_summary=f"synthesize CompanyContext for {name} | depth={bundle.depth_used.value}",
    ) as ev:
        r = await client.chat.complete_async(
            model=settings.mistral_research_model,
            temperature=0.2,  # consistent factual aggregation
            max_tokens=6000,
            timeout_ms=120_000,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYNTHESIS_SYSTEM},
                {"role": "user", "content": user},
            ],
        )
        text = r.choices[0].message.content
        if isinstance(text, list):
            text = "".join(getattr(b, "text", "") for b in text)
        data = json.loads(_strip_fence(str(text or "")))
        ctx = _coerce_company_context(name, bundle, data)
        ev.outputs_summary = (
            f"industry={ctx.classification.industry!r} "
            f"verified={ctx.meta.is_verified} "
            f"conf={ctx.meta.research_confidence:.2f}"
        )
    return ctx


_UNKNOWN_TOKENS = {"", "unknown", "n/a", "none", "null"}


def _is_unknown(v: object) -> bool:
    return isinstance(v, str) and v.strip().lower() in _UNKNOWN_TOKENS


def _coerce_company_context(
    name: str, bundle: ResearchBundle, data: dict[str, object]
) -> CompanyContext:
    """Map LLM JSON to CompanyContext, filling required fields with sensible fallbacks.

    Structural fallback: if the LLM returned empty / "unknown" for a field that
    the structured signal bundle has populated (e.g. wikipedia.industry from a
    direct Wikidata fetch, or verified_match.country), override with the
    structured value. This is the "trust the data over the LLM's interpretation"
    rule — it preserves no-fabrication discipline because we're using actual
    signals, but prevents the model from collapsing real data into "Unknown".
    """
    identity_raw = data.get("identity", {})
    identity = CompanyIdentity(
        name=str(identity_raw.get("name", name) if isinstance(identity_raw, dict) else name),
        legal_name=(identity_raw.get("legal_name") if isinstance(identity_raw, dict) else None),
    )
    classification_raw = (
        data.get("classification", {}) if isinstance(data.get("classification"), dict) else {}
    )

    # Structural fallback: use Wikipedia/verified-match data if the LLM's value is empty/unknown
    industry_llm = classification_raw.get("industry")
    industry_fallback = bundle.wikipedia.industry or bundle.verified_match.industry
    industry = (
        str(industry_fallback)
        if (_is_unknown(industry_llm) and industry_fallback)
        else str(industry_llm or industry_fallback or "Unknown")
    )

    geography_llm = classification_raw.get("geography")
    geography_fallback = bundle.wikipedia.geography or bundle.verified_match.country
    geography = (
        str(geography_fallback)
        if (_is_unknown(geography_llm) and geography_fallback)
        else (str(geography_llm) if geography_llm else geography_fallback)
    )

    classification = CompanyClassification(
        industry=industry,
        sub_industries=list(classification_raw.get("sub_industries") or []),
        geography=geography,
        operating_regions=list(classification_raw.get("operating_regions") or []),
    )
    scale_raw = data.get("scale", {}) if isinstance(data.get("scale"), dict) else {}
    scale = CompanyScale(
        size_tier=str(scale_raw.get("size_tier", "unknown")),  # type: ignore[arg-type]
        public_or_private=str(scale_raw.get("public_or_private", "unknown")),  # type: ignore[arg-type]
    )
    business_raw = data.get("business", {}) if isinstance(data.get("business"), dict) else {}
    business = CompanyBusiness(
        business_model=business_raw.get("business_model"),
        primary_customers=str(business_raw.get("primary_customers", "unknown")),  # type: ignore[arg-type]
        key_products_or_services=list(business_raw.get("key_products_or_services") or []),
    )
    dt_raw = data.get("data_and_tech", {}) if isinstance(data.get("data_and_tech"), dict) else {}
    data_and_tech = CompanyDataAndTech(
        likely_data_assets=list(dt_raw.get("likely_data_assets") or []),
        known_tech_maturity=str(dt_raw.get("known_tech_maturity", "unknown")),  # type: ignore[arg-type]
    )
    sc_raw = (
        data.get("strategic_context", {}) if isinstance(data.get("strategic_context"), dict) else {}
    )
    strategic_context = CompanyStrategicContext(
        stated_priorities=list(sc_raw.get("stated_priorities") or []),
        recent_strategic_moves=list(sc_raw.get("recent_strategic_moves") or []),
    )
    cons_raw = data.get("constraints", {}) if isinstance(data.get("constraints"), dict) else {}
    constraints = CompanyConstraints(
        regulatory_context=list(cons_raw.get("regulatory_context") or []),
        data_sovereignty_concerns=bool(cons_raw.get("data_sovereignty_concerns", False)),
    )
    existing = bundle.existing_initiatives + [
        ExistingInitiative.model_validate(e)
        for e in (data.get("existing_ai_initiatives") or [])
        if isinstance(e, dict) and e.get("description")
    ]
    # Deduplicate
    seen: set[str] = set()
    deduped: list[ExistingInitiative] = []
    for ei in existing:
        key = ei.description[:120].lower().strip()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ei)

    meta_raw = data.get("meta", {}) if isinstance(data.get("meta"), dict) else {}
    confidence = float(meta_raw.get("research_confidence", 0.5))
    if bundle.verified_match.matched:
        confidence = max(confidence, 0.6)
    sources = list(meta_raw.get("research_sources") or [])
    if bundle.wikipedia.found and "wikipedia" not in sources:
        sources.append("wikipedia")
    if bundle.news and "news" not in sources:
        sources.append("news")
    if bundle.jobs and "jobs" not in sources:
        sources.append("jobs")
    if bundle.verified_match.matched and "verified_index" not in sources:
        sources.append("verified_index")
    # Confidence floor — when several signals corroborate, the model often
    # underrates its own confidence. Floor at 0.7 when verified + Wikipedia
    # found + ≥1 news article + ≥1 existing-initiative.
    if (
        bundle.verified_match.matched
        and bundle.wikipedia.found
        and len(bundle.news) >= 1
        and len(deduped) >= 1
    ):
        confidence = max(confidence, 0.75)

    meta = CompanyMeta(
        research_confidence=max(0.0, min(1.0, confidence)),
        research_sources=sources,
        is_verified=bundle.verified_match.matched,
    )

    free_text_notes = data.get("free_text_notes")
    free_text_str = (
        str(free_text_notes).strip()
        if isinstance(free_text_notes, str) and free_text_notes.strip()
        else None
    )

    return CompanyContext(
        identity=identity,
        classification=classification,
        scale=scale,
        business=business,
        data_and_tech=data_and_tech,
        strategic_context=strategic_context,
        existing_ai_initiatives=deduped,
        constraints=constraints,
        meta=meta,
        free_text_notes=free_text_str,
    )


def _seed_ledger_from_bundle(
    bundle: ResearchBundle,
    company_name: str,
    live_verification_sources: list[tuple[str, str, str]] | None = None,
) -> EvidenceLedger:
    """Build the initial ledger from the research bundle. Wikipedia + each news
    item + each existing-initiative-with-source-url + any live-verification
    URLs becomes one entry."""
    ledger = EvidenceLedger()
    wiki_item = from_wikipedia(bundle.wikipedia, company_name)
    if wiki_item is not None:
        ledger.add(wiki_item)
    for n in bundle.news:
        ledger.add(from_news_item(n))
    for ei in bundle.existing_initiatives:
        item = from_existing_initiative(ei)
        if item is not None:
            ledger.add(item)
    for url, title, content in live_verification_sources or []:
        ledger.add(
            from_tavily_result(
                url,
                title,
                content,
                kind=EvidenceKind.COMPANY_VERIFICATION,
                fetched_at_step="research",
                confidence="high",
            )
        )
    return ledger


@workflows.activity(start_to_close_timeout=timedelta(seconds=120), rate_limit=MISTRAL_API_RATE_LIMIT)
async def research_company_activity(
    company_name: str, depth: ResearchDepth = ResearchDepth.MEDIUM
) -> tuple[CompanyContext, EvidenceLedger, ResearchBundle]:
    """Workflow activity: gather the research bundle + synthesize CompanyContext.

    Returns the synthesized CompanyContext, an EvidenceLedger seeded with every
    external source the research step read (Wikipedia summary, each news
    article's deep-read body, each existing-initiative source URL, and any
    live-verification Tavily hits), AND the raw ResearchBundle so the gap-fill
    step can re-use it instead of re-fetching everything from cache.

    The ledger threads through the pipeline so downstream steps can pin claims
    to specific source content. The bundle is the unprocessed signal slice that
    the synthesizer flattened — keeping it lets gap-fill re-synthesize with the
    same depth (medium/high) the original call used, instead of dropping to
    `low` like the previous re-fetch path did.
    """
    logger.info("research start: company=%s depth=%s", company_name, depth.value)
    bundle, live_sources = await _gather_research_bundle(company_name, depth)
    # `bundle.wikipedia.industry` was already cleaned inside _gather_research_bundle
    # so the synthesis prompt sees the customer-facing label, not raw P452.
    ctx = await _synthesize_company_context(company_name, bundle)
    ledger = _seed_ledger_from_bundle(bundle, company_name, live_sources)
    logger.info(
        "research done: company=%s confidence=%.2f verified=%s sources=%s | ledger=%d entries",
        company_name,
        ctx.meta.research_confidence,
        ctx.meta.is_verified,
        ctx.meta.research_sources,
        len(ledger.entries),
    )
    return ctx, ledger, bundle


async def gather_bundle_for_company(
    company_name: str, depth: ResearchDepth = ResearchDepth.MEDIUM
) -> ResearchBundle:
    """Public helper so the workflow / CLI can pass the raw bundle to the
    generation step (which uses it to find specifics the synthesizer may have
    flattened). Cache hits make this near-free on the second call. Live-
    verification sources are dropped here — the research activity already
    seeded the ledger with them."""
    bundle, _ = await _gather_research_bundle(company_name, depth)
    return bundle


# ---------------------------------------------------------------------------
# Context-completion agent — runs after initial research, identifies fields
# that came back empty/unknown despite mattering for downstream generation,
# runs targeted Tavily searches, then re-synthesizes with augmented signals.
# ---------------------------------------------------------------------------


def _identify_missing_fields(ctx: CompanyContext) -> list[str]:
    """Return labels of fields that are unset and matter for downstream generation."""
    out: list[str] = []
    if _is_unknown(ctx.classification.industry):
        out.append("industry")
    if not ctx.classification.geography or _is_unknown(ctx.classification.geography):
        out.append("geography")
    if not ctx.business.business_model or _is_unknown(ctx.business.business_model):
        out.append("business_model")
    if not ctx.business.key_products_or_services:
        out.append("products")
    if not ctx.data_and_tech.likely_data_assets:
        out.append("data_assets")
    if not ctx.strategic_context.stated_priorities:
        out.append("priorities")
    return out


def _fallback_query_for(name: str, field: str) -> str:
    """Hardcoded fallback queries — used when LLM query generation fails."""
    fallbacks = {
        "industry": f"{name} company industry sector business",
        "geography": f"{name} headquarters country location",
        "business_model": f"{name} business model revenue streams",
        "products": f"{name} products services offerings list",
        "data_assets": f"{name} data assets technology stack platforms",
        "priorities": f"{name} strategic priorities 2024 2025 announcements",
    }
    return fallbacks.get(field, f"{name} {field}")


_GAP_QUERY_GEN_SYSTEM = """\
You generate targeted web-search queries to fill specific gaps in a company profile.

Given:
- The company name
- A short Wikipedia excerpt (may be empty)
- A list of missing fields that need filling

For EACH missing field, produce ONE search query that:
- Is specific (named entities, year if temporal, document type if relevant)
- Targets primary sources (the company's own announcements, strategy decks,
  engineering blog, regulatory filings, peer-reviewed analyses) rather than
  generic aggregator pages
- Is 4-10 words

Examples of good queries:
  Field: priorities — "Carrefour 2027 strategy retail media plan"
  Field: products — "Carrefour private label brands Bio Express"
  Field: data_assets — "Carrefour loyalty program transaction scale stores"

Examples of bad queries (do NOT produce):
  "<name> company info" (too generic, will return Wikipedia)
  "<name> industry sector" (too generic)
  "<name> overview" (too generic)

Output STRICT JSON: {"queries": [{"field": "priorities", "query": "..."}, ...]}
"""


async def _generate_gap_queries(
    company_name: str, wiki_summary: str, missing_fields: list[str]
) -> list[tuple[str, str]]:
    """Use Mistral Small to produce entity-specific search queries per gap.

    Falls back to hardcoded templates if the model call fails or returns junk.
    """
    if not missing_fields:
        return []
    if not settings.mistral_api_key:
        return [(f, _fallback_query_for(company_name, f)) for f in missing_fields]

    client = mistral_client()
    user = (
        f"Company: {company_name}\n\n"
        f"Wikipedia excerpt:\n{(wiki_summary or '(none)')[:600]}\n\n"
        "Missing fields:\n" + "\n".join(f"- {f}" for f in missing_fields)
    )
    try:
        async with trace_step(
            "gap_fill",
            settings.mistral_scoring_model,
            "chat.complete",
            inputs_summary=f"generate gap queries | fields={missing_fields}",
        ) as ev:
            r = await client.chat.complete_async(
                model=settings.mistral_scoring_model,
                temperature=0.2,
                max_tokens=600,
                timeout_ms=30_000,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _GAP_QUERY_GEN_SYSTEM},
                    {"role": "user", "content": user},
                ],
            )
            text = r.choices[0].message.content
            if isinstance(text, list):
                text = "".join(getattr(b, "text", "") for b in text)
            data = json.loads(_strip_fence(str(text or "{}")))
            ev.outputs_summary = f"queries={len(data.get('queries', []))}"
    except Exception as e:
        logger.warning("gap query gen failed: %s — falling back to templates", type(e).__name__)
        return [(f, _fallback_query_for(company_name, f)) for f in missing_fields]

    raw_queries = data.get("queries", [])
    out: list[tuple[str, str]] = []
    if isinstance(raw_queries, list):
        for q in raw_queries:
            if not isinstance(q, dict):
                continue
            field = str(q.get("field", "")).strip()
            query = str(q.get("query", "")).strip()
            if field in missing_fields and 4 <= len(query.split()) <= 12:
                out.append((field, query))
    # Add hardcoded fallbacks for any field the LLM didn't cover
    covered = {f for f, _ in out}
    for f in missing_fields:
        if f not in covered:
            out.append((f, _fallback_query_for(company_name, f)))
    return out


_LAYER2_EXTRACT_SYSTEM = """\
You extract specific noun phrases from text. Be concrete and verbatim — quote
exact phrasing from the source where possible. No paraphrasing into generic
categories. Output STRICT JSON only.
"""


async def _layer2_extract_field(
    field_name: str, gap_block: str, company_name: str
) -> list[str]:
    """One-shot fallback extraction when the synthesis LLM left a list field empty
    despite the gap-fill block containing usable content. Targets exactly one
    field at a time so the model can't hedge by writing nothing.
    """
    if not settings.mistral_api_key or not gap_block.strip():
        return []
    field_descriptions = {
        "priorities": "specific strategic priorities, transformation themes, or multi-year plans (e.g. 'carbon neutrality by 2050', 'retail-media expansion', 'digital-first transformation')",
        "data_assets": "specific datasets the company plausibly owns at scale (e.g. 'loyalty card transactions across 13,000 stores', 'in-store CCTV imagery', 'supplier catalog metadata')",
        "products": "specific named products, brands, sub-brands, or services (e.g. 'Carrefour Bio', 'Carrefour Banque', 'Carrefour Media')",
    }
    desc = field_descriptions.get(field_name, field_name)
    user = (
        f"From the following text about {company_name}, extract 3-6 {desc}. "
        f"Each item must be a specific noun phrase, not a generic category. "
        f"If the text genuinely lacks information about this field, return an empty list.\n\n"
        f"Text:\n{gap_block[:6000]}\n\n"
        'Output STRICT JSON: {"items": ["...", "..."]}'
    )
    client = mistral_client()
    try:
        async with trace_step(
            "gap_fill",
            settings.mistral_scoring_model,
            "chat.complete",
            inputs_summary=f"layer-2 extract field={field_name}",
        ) as ev:
            r = await client.chat.complete_async(
                model=settings.mistral_scoring_model,
                temperature=0.2,
                max_tokens=400,
                timeout_ms=30_000,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _LAYER2_EXTRACT_SYSTEM},
                    {"role": "user", "content": user},
                ],
            )
            text = r.choices[0].message.content
            if isinstance(text, list):
                text = "".join(getattr(b, "text", "") for b in text)
            data = json.loads(_strip_fence(str(text or "{}")))
            ev.outputs_summary = f"items={len(data.get('items', []))}"
    except Exception as e:
        logger.warning("layer2 extract failed for %s: %s", field_name, type(e).__name__)
        return []
    items = data.get("items", [])
    if not isinstance(items, list):
        return []
    return [str(x).strip() for x in items if str(x).strip()]


async def _run_targeted_search(
    tavily_client: object, http_client: httpx.AsyncClient, query: str
) -> tuple[str, list[tuple[str, str, str]]]:
    """Run one Tavily search, deep-read top 1 result.

    Returns (rendered_block, evidence_tuples) where evidence_tuples is a
    list of (url, title, content) for every distinct source we read — the
    caller adds these to the EvidenceLedger so downstream steps can verify
    claims against the actual fetched content.
    """
    from tavily import AsyncTavilyClient as _TavilyType  # noqa: F401  (type assist only)

    try:
        resp = await tavily_client.search(  # type: ignore[attr-defined]
            query=query, search_depth="advanced", max_results=2
        )
    except Exception as e:
        logger.warning("completion search failed: %s", type(e).__name__)
        return "", []
    if not isinstance(resp, dict):
        return "", []
    parts: list[str] = []
    sources: list[tuple[str, str, str]] = []
    results = resp.get("results", [])
    for i, r in enumerate(results[:2]):
        title = str(r.get("title") or "")
        url = str(r.get("url") or "")
        snippet = str(r.get("content") or "")[:1500]
        parts.append(f"- {title} [{url}]: {snippet}")
        if snippet and url:
            sources.append((url, title, snippet))
        # Deep-read only the top result to keep cost down
        if i == 0 and url:
            from scripts._fetch import extract_main_text, fetch_html

            html = await fetch_html(http_client, url, timeout_s=10.0)
            if html:
                body = extract_main_text(html, max_chars=3000)
                if body:
                    parts.append(f"  DEEP-READ {url}:\n  {body}")
                    sources.append((url, title, body))
    return "\n".join(parts), sources


async def _resynthesize_with_extra_signals(
    name: str, bundle: ResearchBundle, extra_signal_block: str
) -> CompanyContext:
    """Re-run the synthesis call with the extra signal block appended."""
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for re-synthesis")
    client = mistral_client()
    user = (
        _build_synthesis_prompt(name, bundle)
        + "\n\n## Additional targeted research — TRUSTED SIGNALS (gap-filling pass)\n"
        "Every bullet below was retrieved live from a credible web source for "
        "this company. You may quote them VERBATIM — these are TRUSTED. Extract "
        "specific noun phrases as `stated_priorities`, `likely_data_assets`, "
        "and `key_products_or_services` items. Copy concrete company facts into "
        "the relevant structured fields. Empty lists are WRONG if the bullets "
        "below contain real content.\n\n"
        + extra_signal_block
        + "\n\nNow produce the final CompanyContext using ALL signals above, "
        "INCLUDING the gap-filling block. Lists are mandatory if the gap-fill "
        "block has any usable content."
    )
    r = await client.chat.complete_async(
        model=settings.mistral_research_model,
        temperature=0.2,
        max_tokens=4000,
        timeout_ms=120_000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYNTHESIS_SYSTEM},
            {"role": "user", "content": user},
        ],
    )
    text = r.choices[0].message.content
    if isinstance(text, list):
        text = "".join(getattr(b, "text", "") for b in text)
    data = json.loads(_strip_fence(str(text or "")))
    return _coerce_company_context(name, bundle, data)


@workflows.activity(start_to_close_timeout=timedelta(seconds=180), rate_limit=MISTRAL_API_RATE_LIMIT)
async def enrich_company_context_activity(
    ctx: CompanyContext,
    ledger: EvidenceLedger | None = None,
    bundle: ResearchBundle | None = None,
) -> tuple[CompanyContext, EvidenceLedger]:
    """Adaptive research pass: identify Unknown/empty fields and fill them.

    For each gap, run a targeted Tavily search and add the results to the
    synthesis context. Then re-synthesize the CompanyContext. Every fetched
    source is added to the EvidenceLedger so downstream claims can pin to it.

    Skips silently if there are no gaps OR if Tavily isn't configured.
    """
    if ledger is None:
        ledger = EvidenceLedger()
    missing = _identify_missing_fields(ctx)
    if not missing:
        logger.info("enrich_context: no gaps detected, skipping")
        return ctx, ledger
    if not settings.tavily_api_key:
        logger.info("enrich_context: TAVILY_API_KEY missing, skipping (gaps=%d)", len(missing))
        return ctx, ledger

    logger.info("enrich_context: %d gaps to fill: %s", len(missing), missing)

    # Run a fresh research bundle to pass to re-synthesis (cached, so cheap).
    # Live-verification sources from this re-fetch are ignored — the original
    # research call already seeded the ledger with them.
    if bundle is None:
        bundle, _ = await _gather_research_bundle(ctx.identity.name, ResearchDepth.LOW)

    # AI-generated entity-specific queries (one per gap), with hardcoded fallbacks.
    gap_queries = await _generate_gap_queries(
        ctx.identity.name, bundle.wikipedia.summary or "", missing
    )
    logger.info(
        "enrich_context: generated queries: %s",
        [(label, q[:60]) for label, q in gap_queries],
    )

    from tavily import AsyncTavilyClient

    tavily = AsyncTavilyClient(api_key=settings.tavily_api_key)
    sem = asyncio.Semaphore(3)

    async def _one(label: str, query: str) -> tuple[str, str, list[tuple[str, str, str]]]:
        async with sem:
            async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}) as http:
                txt, sources = await _run_targeted_search(tavily, http, query)
                return label, txt, sources

    results = await asyncio.gather(*(_one(label, q) for label, q in gap_queries))
    extra_block_lines: list[str] = []
    fetched_sources: list[tuple[str, str, str]] = []
    gap_block_for_layer2: dict[str, str] = {}
    for label, txt, sources in results:
        if not txt:
            continue
        extra_block_lines.append(f"### Gap fill: {label}\n{txt}")
        fetched_sources.extend(sources)
        gap_block_for_layer2[label] = txt

    # Append every fetched gap-fill source to the ledger
    for url, title, content in fetched_sources:
        ledger.add(
            from_tavily_result(
                url,
                title,
                content,
                kind=EvidenceKind.GAP_FILL,
                fetched_at_step="gap_fill",
                confidence="medium",
            )
        )

    if not extra_block_lines:
        logger.info("enrich_context: targeted searches returned nothing, returning original ctx")
        return ctx, ledger

    # Layer-1 re-synthesis is decorative: across all 5 example companies it
    # consistently returned empty list fields (priorities=0, data_assets=0,
    # products=0) even with the gap-fill block attached, leaving layer-2 to
    # do all the actual list population. We skip the ~11s mistral-medium
    # re-synthesis call and run layer-2 directly against the gap-fill blocks
    # for the missing list fields. Industry/geography are already handled
    # by the structural fallback in _coerce_company_context.
    enriched = ctx.model_copy(deep=True)
    enriched.meta.research_sources = list(
        set(enriched.meta.research_sources + ["completion_agent"])
    )

    # Layer-2 per-field extraction — runs concurrently for the missing list
    # fields that have gap-fill content. Each call is ~1-2s on Mistral Small.
    field_specs = [
        (
            "priorities",
            enriched.strategic_context.stated_priorities,
            gap_block_for_layer2.get("priorities", ""),
        ),
        (
            "data_assets",
            enriched.data_and_tech.likely_data_assets,
            gap_block_for_layer2.get("data_assets", ""),
        ),
        (
            "products",
            enriched.business.key_products_or_services,
            gap_block_for_layer2.get("products", ""),
        ),
    ]
    targets: list[tuple[str, str]] = [
        (name, block)
        for name, current, block in field_specs
        if not current and len(block) > 200
    ]
    layer2_results: list[list[str]] = []
    if targets:
        layer2_results = list(
            await asyncio.gather(
                *(
                    _layer2_extract_field(name, block, ctx.identity.name)
                    for name, block in targets
                )
            )
        )
    layer2_runs: list[str] = []
    for (name, _block), items in zip(targets, layer2_results, strict=True):
        if not items:
            continue
        if name == "priorities":
            enriched.strategic_context.stated_priorities = items
        elif name == "data_assets":
            enriched.data_and_tech.likely_data_assets = items
        elif name == "products":
            enriched.business.key_products_or_services = items
        layer2_runs.append(f"{name}({len(items)})")

    # Confidence bump for a successful gap-fill — the metric was undersold before
    # because we added signal but never re-rated confidence to reflect it.
    enriched.meta.research_confidence = min(1.0, enriched.meta.research_confidence + 0.10)
    logger.info(
        "enrich_context: re-synthesized | confidence=%.2f | industry=%s | filled=%s | "
        "layer2=%s | priorities=%d | data_assets=%d | products=%d | ledger=%d",
        enriched.meta.research_confidence,
        enriched.classification.industry,
        missing,
        layer2_runs or "(none)",
        len(enriched.strategic_context.stated_priorities),
        len(enriched.data_and_tech.likely_data_assets),
        len(enriched.business.key_products_or_services),
        len(ledger.entries),
    )
    return enriched, ledger
