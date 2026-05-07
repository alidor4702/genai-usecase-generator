"""Workflow activity wrapping the quality-signals computation.

The diversity signal requires an embedding call (I/O), so the whole signal
assembly lives in an activity. Pure-compute helpers are in
`src/quality_signals.py` so they can also be used standalone (e.g. tests).
"""

from __future__ import annotations

import logging
from datetime import timedelta

import mistralai.workflows as workflows
from mistralai.client import Mistral

from src.config import settings
from src.models import (
    CompanyContext,
    EnrichedUseCase,
    FactCheckEntry,
    QualitySignals,
)
from src.quality_signals import assemble_quality_signals

logger = logging.getLogger(__name__)


@workflows.activity(start_to_close_timeout=timedelta(seconds=60))
async def compute_quality_signals_activity(
    uses: list[EnrichedUseCase],
    ctx: CompanyContext,
    fact_check: list[FactCheckEntry],
) -> QualitySignals:
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for quality-signals embedding")
    client = Mistral(api_key=settings.mistral_api_key)
    descriptions = [uc.description for uc in uses]
    if descriptions:
        resp = await client.embeddings.create_async(
            model=settings.mistral_embedding_model,
            inputs=descriptions,
        )
        embeds = [list(d.embedding) for d in resp.data]
    else:
        embeds = []

    signals = assemble_quality_signals(uses, ctx, embeds, fact_check)
    logger.info(
        "quality_signals: diversity=%.2f specificity=%s mistral_products=%d fact_pass=%.2f",
        signals.diversity,
        [round(s, 2) for s in signals.specificity_per_use_case],
        signals.mistral_product_diversity,
        signals.fact_check_pass_rate,
    )
    return signals
