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
- Use ONLY the provided signals. If a field is not supported, leave it empty
  or "unknown" — do not fabricate.
- Do NOT extract financial details (revenue, employee count, stock price,
  founding year, executive names) — they don't drive any downstream decision.
- `existing_ai_initiatives` MUST enumerate every distinct already-deployed
  initiative discovered. The downstream pipeline uses these as a hard gate
  against recommending what the company already does.
- `meta.research_confidence` is a float in [0, 1] reflecting how coherently
  the signals converge. Sparse / contradictory signals → low confidence.
- The verified-companies index is a confidence boost, never a gate.
- `scale.size_tier` is one of: "startup", "scaleup", "enterprise", "unknown"
- `scale.public_or_private` is one of: "public", "private", "unknown"
- `business.primary_customers` is one of: "B2B", "B2C", "B2G", "mixed", "unknown"
- `data_and_tech.known_tech_maturity` is one of: "high", "medium", "low", "unknown"

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


def _coerce_company_context(
    name: str, bundle: ResearchBundle, data: dict[str, object]
) -> CompanyContext:
    """Map LLM JSON to CompanyContext, filling required fields with sensible fallbacks."""
    identity_raw = data.get("identity", {})
    identity = CompanyIdentity(
        name=str(identity_raw.get("name", name) if isinstance(identity_raw, dict) else name),
        legal_name=(identity_raw.get("legal_name") if isinstance(identity_raw, dict) else None),
    )
    classification_raw = (
        data.get("classification", {}) if isinstance(data.get("classification"), dict) else {}
    )
    classification = CompanyClassification(
        industry=str(classification_raw.get("industry", "Unknown")),
        sub_industries=list(classification_raw.get("sub_industries") or []),
        geography=classification_raw.get("geography"),
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
    meta = CompanyMeta(
        research_confidence=max(0.0, min(1.0, confidence)),
        research_sources=sources,
        is_verified=bundle.verified_match.matched,
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
