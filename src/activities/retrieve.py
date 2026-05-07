"""Retrieve activity — embed the company context, fetch top-k peer precedents.

Wraps `src.precedents.retrieve_top_k`. Deterministically converts the typed
`CompanyContext` into a single embedding query, then filters by industry,
depth, and target-company exclusion before cosine ranking.

The query text is composed once here so the rest of the pipeline doesn't have
to reconstruct it. Embedding via `mistral-embed`.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import mistralai.workflows as workflows
from mistralai.client import Mistral

from src.config import settings
from src.models import CompanyContext, RetrievedPrecedents
from src.precedents import retrieve_top_k

logger = logging.getLogger(__name__)


def _company_query_text(ctx: CompanyContext) -> str:
    parts: list[str] = [
        ctx.identity.name,
        f"Industry: {ctx.classification.industry}",
    ]
    if ctx.classification.sub_industries:
        parts.append("Sub-industries: " + ", ".join(ctx.classification.sub_industries))
    if ctx.business.business_model:
        parts.append(f"Business model: {ctx.business.business_model}")
    if ctx.data_and_tech.likely_data_assets:
        parts.append("Data assets: " + ", ".join(ctx.data_and_tech.likely_data_assets[:8]))
    if ctx.strategic_context.stated_priorities:
        parts.append("Stated priorities: " + "; ".join(ctx.strategic_context.stated_priorities[:6]))
    return "\n".join(parts)


@workflows.activity(start_to_close_timeout=timedelta(seconds=30))
async def retrieve_precedents_activity(
    ctx: CompanyContext, k: int = 8, min_depth: float = 0.4
) -> RetrievedPrecedents:
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for retrieval embedding")
    client = Mistral(api_key=settings.mistral_api_key)
    query_text = _company_query_text(ctx)
    resp = await client.embeddings.create_async(
        model=settings.mistral_embedding_model,
        inputs=[query_text],
    )
    query_embedding = list(resp.data[0].embedding)

    industries: list[str] = [ctx.classification.industry] + list(
        ctx.classification.sub_industries or []
    )
    out = await retrieve_top_k(
        query_embedding,
        k=k,
        target_company=ctx.identity.name,
        company_industries=industries,
        min_depth=min_depth,
        use_mmr=True,
    )
    logger.info(
        "retrieve: returned %d precedents (mmr=%s, top sim=%.3f)",
        len(out.items),
        out.used_mmr,
        max(out.similarity_scores) if out.similarity_scores else 0.0,
    )
    return out
