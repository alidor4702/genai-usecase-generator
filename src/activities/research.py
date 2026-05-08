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
from mistralai.client import Mistral

from src.config import settings
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
    ExistingInitiative,
    JobsSignal,
    NewsItem,
    ResearchBundle,
    ResearchDepth,
    VerifiedCompanyMatch,
    WikipediaFacts,
)
from src.research.existing_initiatives import fetch_existing_initiatives
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


async def _gather_research_bundle(company_name: str, depth: ResearchDepth) -> ResearchBundle:
    """Run the parallel sub-tasks per the configured depth toggle."""
    # Always-on: Wikipedia, verified-index, existing-initiatives
    wiki_t: asyncio.Task[WikipediaFacts] = asyncio.create_task(fetch_wikipedia_facts(company_name))
    verify_t: asyncio.Task[VerifiedCompanyMatch] = asyncio.create_task(verify_company(company_name))
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
    wiki_res, verify_res, existing_res = await asyncio.gather(
        wiki_t, verify_t, existing_t, return_exceptions=False
    )
    news_res: list[NewsItem] = await news_t if news_t else []
    jobs_res: JobsSignal | None = await jobs_t if jobs_t else None

    return ResearchBundle(
        wikipedia=wiki_res,
        news=news_res,
        jobs=jobs_res,
        existing_initiatives=existing_res,
        verified_match=verify_res,
        depth_used=depth,
    )


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


async def _synthesize_company_context(name: str, bundle: ResearchBundle) -> CompanyContext:
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for research synthesis")
    client = Mistral(api_key=settings.mistral_api_key)
    user = _build_synthesis_prompt(name, bundle)
    r = await client.chat.complete_async(
        model=settings.mistral_research_model,
        temperature=0.2,  # consistent factual aggregation
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
    ctx = _coerce_company_context(name, bundle, data)
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


@workflows.activity(start_to_close_timeout=timedelta(seconds=120))
async def research_company_activity(
    company_name: str, depth: ResearchDepth = ResearchDepth.MEDIUM
) -> CompanyContext:
    """Workflow activity: gather the research bundle + synthesize CompanyContext."""
    logger.info("research start: company=%s depth=%s", company_name, depth.value)
    bundle = await _gather_research_bundle(company_name, depth)
    ctx = await _synthesize_company_context(company_name, bundle)
    logger.info(
        "research done: company=%s confidence=%.2f verified=%s sources=%s",
        company_name,
        ctx.meta.research_confidence,
        ctx.meta.is_verified,
        ctx.meta.research_sources,
    )
    return ctx


async def gather_bundle_for_company(
    company_name: str, depth: ResearchDepth = ResearchDepth.MEDIUM
) -> ResearchBundle:
    """Public helper so the workflow / CLI can pass the raw bundle to the
    generation step (which uses it to find specifics the synthesizer may have
    flattened). Cache hits make this near-free on the second call."""
    return await _gather_research_bundle(company_name, depth)


# ---------------------------------------------------------------------------
# Context-completion agent — runs after initial research, identifies fields
# that came back empty/unknown despite mattering for downstream generation,
# runs targeted Tavily searches, then re-synthesizes with augmented signals.
# ---------------------------------------------------------------------------


def _identify_gaps(ctx: CompanyContext) -> list[tuple[str, str]]:
    """Return list of (field_label, search_query) for missing fields that matter."""
    gaps: list[tuple[str, str]] = []
    name = ctx.identity.name

    if _is_unknown(ctx.classification.industry):
        gaps.append(("industry", f"{name} company industry sector business"))
    if not ctx.classification.geography or _is_unknown(ctx.classification.geography):
        gaps.append(("geography", f"{name} company headquarters country where based"))
    if not ctx.business.business_model or _is_unknown(ctx.business.business_model):
        gaps.append(("business_model", f"{name} business model how it makes money"))
    if not ctx.business.key_products_or_services:
        gaps.append(("products", f"{name} main products and services offered"))
    if not ctx.data_and_tech.likely_data_assets:
        gaps.append(("data_assets", f"{name} data assets technology stack platforms"))
    if not ctx.strategic_context.stated_priorities:
        gaps.append(("priorities", f"{name} strategic priorities goals announcements 2024 2025"))
    return gaps


async def _run_targeted_search(
    tavily_client: object, http_client: httpx.AsyncClient, query: str
) -> str:
    """Run one Tavily search, deep-read top 1 result, return aggregated text."""
    from tavily import AsyncTavilyClient as _TavilyType  # noqa: F401  (type assist only)

    try:
        resp = await tavily_client.search(  # type: ignore[attr-defined]
            query=query, search_depth="advanced", max_results=2
        )
    except Exception as e:
        logger.warning("completion search failed: %s", type(e).__name__)
        return ""
    if not isinstance(resp, dict):
        return ""
    parts: list[str] = []
    results = resp.get("results", [])
    for i, r in enumerate(results[:2]):
        title = str(r.get("title") or "")
        snippet = str(r.get("content") or "")[:1500]
        parts.append(f"- {title}: {snippet}")
        # Deep-read only the top result to keep cost down
        if i == 0:
            url = str(r.get("url") or "")
            if url:
                from scripts._fetch import extract_main_text, fetch_html

                html = await fetch_html(http_client, url, timeout_s=10.0)
                if html:
                    body = extract_main_text(html, max_chars=3000)
                    if body:
                        parts.append(f"  DEEP-READ {url}:\n  {body}")
    return "\n".join(parts)


async def _resynthesize_with_extra_signals(
    name: str, bundle: ResearchBundle, extra_signal_block: str
) -> CompanyContext:
    """Re-run the synthesis call with the extra signal block appended."""
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for re-synthesis")
    client = Mistral(api_key=settings.mistral_api_key)
    user = (
        _build_synthesis_prompt(name, bundle)
        + "\n\n## Additional targeted research (gap-filling pass)\n"
        + extra_signal_block
        + "\n\nNow produce the final CompanyContext using ALL signals above, "
        "including the gap-filling block."
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


@workflows.activity(start_to_close_timeout=timedelta(seconds=180))
async def enrich_company_context_activity(
    ctx: CompanyContext, bundle: ResearchBundle | None = None
) -> CompanyContext:
    """Adaptive research pass: identify Unknown/empty fields and fill them.

    For each gap, run a targeted Tavily search and add the results to the
    synthesis context. Then re-synthesize the CompanyContext.

    Skips silently if there are no gaps OR if Tavily isn't configured.
    """
    gaps = _identify_gaps(ctx)
    if not gaps:
        logger.info("enrich_context: no gaps detected, skipping")
        return ctx
    if not settings.tavily_api_key:
        logger.info("enrich_context: TAVILY_API_KEY missing, skipping (gaps=%d)", len(gaps))
        return ctx

    logger.info("enrich_context: %d gaps to fill: %s", len(gaps), [g[0] for g in gaps])

    # Run a fresh research bundle to pass to re-synthesis (cached, so cheap)
    if bundle is None:
        bundle = await _gather_research_bundle(ctx.identity.name, ResearchDepth.LOW)

    from tavily import AsyncTavilyClient

    tavily = AsyncTavilyClient(api_key=settings.tavily_api_key)
    sem = asyncio.Semaphore(3)

    async def _one(label: str, query: str) -> tuple[str, str]:
        async with sem:
            async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}) as http:
                txt = await _run_targeted_search(tavily, http, query)
                return label, txt

    results = await asyncio.gather(*(_one(label, q) for label, q in gaps))
    extra_block_lines: list[str] = []
    for label, txt in results:
        if not txt:
            continue
        extra_block_lines.append(f"### Gap fill: {label}\n{txt}")
    if not extra_block_lines:
        logger.info("enrich_context: targeted searches returned nothing, returning original ctx")
        return ctx

    extra_signal_block = "\n\n".join(extra_block_lines)
    enriched = await _resynthesize_with_extra_signals(ctx.identity.name, bundle, extra_signal_block)
    enriched.meta.research_sources = list(
        set(enriched.meta.research_sources + ["completion_agent"])
    )
    logger.info(
        "enrich_context: re-synthesized | confidence=%.2f | industry=%s | filled=%s",
        enriched.meta.research_confidence,
        enriched.classification.industry,
        [g[0] for g in gaps],
    )
    return enriched
