"""Pure-compute quality signals on the final report.

These signals are surfaced in the metadata footer so a downstream user
(reviewer, customer, sales engineer) can evaluate output trustworthiness
at a glance. Per the methodology, signals are deliberately picked to be
the system's own honest assessment of its output — including the negatives
(low specificity, sparse source coverage).

The diversity signal needs an embedding call (I/O), so this module exposes
a pure-compute helper plus an async function that combines compute with
embedding; the embedding-aware function is wrapped in a workflow activity
in `src/activities/compute_signals.py`.
"""

from __future__ import annotations

import re

import numpy as np

from src.models import (
    CompanyContext,
    EnrichedUseCase,
    FactCheckEntry,
    QualitySignals,
)


def _l2_normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def diversity_score(description_embeddings: list[list[float]]) -> float:
    """Average pairwise cosine distance between use-case description embeddings.

    Higher = more diverse. Range [0, 1]. With three items there are 3 pairs.
    """
    if len(description_embeddings) < 2:
        return 1.0
    matrix = _l2_normalize(np.asarray(description_embeddings, dtype=np.float32))
    n = matrix.shape[0]
    sims: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            sims.append(float(matrix[i] @ matrix[j]))
    avg_sim = float(np.mean(sims)) if sims else 0.0
    return max(0.0, 1.0 - avg_sim)  # convert similarity → distance


_NAMED_ENTITY_RE = re.compile(r"\b[A-Z][\w&'\-/]{2,}(?:\s+[A-Z][\w&'\-/]{1,}){0,3}\b")


def _entity_set(text: str) -> set[str]:
    return {m.group(0).strip().lower() for m in _NAMED_ENTITY_RE.finditer(text)}


def _context_entities(ctx: CompanyContext) -> set[str]:
    parts: list[str] = []
    if ctx.business.business_model:
        parts.append(ctx.business.business_model)
    parts.extend(ctx.business.key_products_or_services)
    parts.extend(ctx.data_and_tech.likely_data_assets)
    parts.extend(ctx.strategic_context.stated_priorities)
    parts.extend(ctx.strategic_context.recent_strategic_moves)
    parts.extend(ctx.classification.sub_industries)
    parts.extend(ctx.classification.operating_regions)
    parts.append(ctx.identity.name)
    return _entity_set(" ".join(parts))


def specificity_per_use_case(uses: list[EnrichedUseCase], ctx: CompanyContext) -> list[float]:
    """Fraction of named-entity tokens in each use case that overlap with the
    company context's named-entity set. Higher = more specifically grounded."""
    context_entities = _context_entities(ctx)
    out: list[float] = []
    for uc in uses:
        text = " ".join(
            [
                uc.title,
                uc.description,
                uc.why_this_company,
                uc.example_input,
                uc.example_output,
            ]
        )
        ents = _entity_set(text)
        if not ents:
            out.append(0.0)
            continue
        overlap = ents & context_entities
        out.append(len(overlap) / len(ents))
    return out


def mistral_product_diversity(uses: list[EnrichedUseCase]) -> int:
    products: set[str] = set()
    for uc in uses:
        for p in uc.suggested_mistral_products:
            # Normalize to a base product name (strip parenthetical clarifications)
            base = re.sub(r"\s*\(.*?\)\s*", "", p).strip().lower()
            if base:
                products.add(base)
    return len(products)


def time_to_value_spread(uses: list[EnrichedUseCase]) -> list[str]:
    return [uc.time_to_value.estimate for uc in uses]


def cost_tier_spread(uses: list[EnrichedUseCase]) -> list[str]:
    return [uc.operating_cost_tier.value for uc in uses]


def source_coverage_per_use_case(
    uses: list[EnrichedUseCase], ctx: CompanyContext
) -> list[list[str]]:
    """For each use case, list which research sources contributed evidence.

    Heuristic: the candidate's `grounded_in` paths point at company-context
    fields; we map each top-level field path back to which research source
    typically populates it.
    """
    field_to_source = {
        "identity": "wikipedia",
        "classification": "wikipedia",
        "scale": "wikipedia",
        "business": "wikipedia",
        "data_and_tech": "synthesis",
        "strategic_context": "news",
        "existing_ai_initiatives": "existing_initiatives",
        "constraints": "wikipedia+news",
    }
    out: list[list[str]] = []
    for uc in uses:
        sources: set[str] = set()
        for path in uc.grounded_in:
            top = path.split(".", 1)[0]
            sources.add(field_to_source.get(top, "synthesis"))
        if uc.inspired_by:
            sources.add("precedent_corpus")
        out.append(sorted(sources))
    return out


def risks_per_use_case(uses: list[EnrichedUseCase]) -> list[str]:
    return [uc.top_implementation_risk for uc in uses]


def assemble_quality_signals(
    uses: list[EnrichedUseCase],
    ctx: CompanyContext,
    description_embeddings: list[list[float]],
    fact_check: list[FactCheckEntry],
) -> QualitySignals:
    return QualitySignals(
        diversity=diversity_score(description_embeddings),
        specificity_per_use_case=specificity_per_use_case(uses, ctx),
        mistral_product_diversity=mistral_product_diversity(uses),
        time_to_value_spread=time_to_value_spread(uses),
        cost_tier_spread=[uc.operating_cost_tier for uc in uses],
        source_coverage_per_use_case=source_coverage_per_use_case(uses, ctx),
        risks_per_use_case=risks_per_use_case(uses),
        fact_check=fact_check,
    )
