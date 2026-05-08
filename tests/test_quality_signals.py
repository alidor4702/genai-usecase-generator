"""Tests for pure-compute quality signal helpers.

The diversity/specificity/etc. helpers are computed deterministically over
already-fetched data, so they're easy to test without LLM calls or network.
The LLM-graded versions (in src/activities/compute_signals.py) override the
pure-compute specificity + diversity at runtime; these tests cover the
fallback behavior the LLM grader replaces.
"""

from __future__ import annotations

from src.models import (
    BlueprintPattern,
    CompanyBusiness,
    CompanyClassification,
    CompanyContext,
    CompanyDataAndTech,
    CompanyIdentity,
    CompanyMeta,
    CompanyStrategicContext,
    ComplexityTier,
    CostTier,
    EnrichedUseCase,
    ImpactTier,
    TimeToValue,
)
from src.quality_signals import (
    cost_tier_spread,
    diversity_score,
    mistral_product_diversity,
    risks_per_use_case,
    source_coverage_per_use_case,
    specificity_per_use_case,
    time_to_value_spread,
)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _ctx(
    industry: str = "Retail",
    products: list[str] | None = None,
    priorities: list[str] | None = None,
    data_assets: list[str] | None = None,
) -> CompanyContext:
    return CompanyContext(
        identity=CompanyIdentity(name="Carrefour"),
        classification=CompanyClassification(industry=industry),
        business=CompanyBusiness(key_products_or_services=products or []),
        data_and_tech=CompanyDataAndTech(likely_data_assets=data_assets or []),
        strategic_context=CompanyStrategicContext(stated_priorities=priorities or []),
        meta=CompanyMeta(research_confidence=0.7),
    )


_DEFAULT = object()  # sentinel so callers can pass an explicit empty list


def _uc(
    uid: str,
    products: list[str] | None | object = _DEFAULT,
    risk: str = "default risk",
    ttv: str = "8-16 weeks",
    cost: CostTier = CostTier.MEDIUM,
    description: str = "default description",
    grounded_in: list[str] | None = None,
    inspired_by: list[str] | None = None,
) -> EnrichedUseCase:
    products_arg: list[str] = (
        ["Mistral Large 3"] if products is _DEFAULT else (products or [])  # type: ignore[arg-type]
    )
    return EnrichedUseCase(
        id=uid,
        title=f"Use case {uid}",
        description=description,
        why_this_company="why",
        example_input="i",
        example_output="o",
        suggested_mistral_products=products_arg,
        blueprint_pattern=BlueprintPattern.RAG,
        blueprint_mermaid="",
        time_to_value=TimeToValue(estimate=ttv),
        operating_cost_tier=cost,
        impact_tier=ImpactTier.HIGH,
        complexity_tier=ComplexityTier.LOW,
        top_implementation_risk=risk,
        grounded_in=grounded_in or [],
        inspired_by=inspired_by or [],
    )


# ----------------------------------------------------------------------------
# diversity_score
# ----------------------------------------------------------------------------


def test_diversity_score_single_item_returns_one() -> None:
    """One item is trivially fully-diverse (no comparison possible)."""
    assert diversity_score([[1.0, 0.0, 0.0]]) == 1.0


def test_diversity_score_empty_returns_one() -> None:
    assert diversity_score([]) == 1.0


def test_diversity_score_identical_embeddings_is_zero() -> None:
    """Three copies of the same vector → cosine similarity 1 → distance 0."""
    v = [1.0, 0.0, 0.0]
    score = diversity_score([v, v, v])
    assert score == 0.0


def test_diversity_score_orthogonal_embeddings_is_one() -> None:
    """Three orthogonal unit vectors → cosine similarity 0 → distance 1."""
    score = diversity_score([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    assert score == 1.0


def test_diversity_score_zero_vector_does_not_crash() -> None:
    """A zero vector has norm 0; the L2-normalize step must not divide-by-zero."""
    score = diversity_score([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    assert 0.0 <= score <= 1.0


# ----------------------------------------------------------------------------
# specificity_per_use_case (regex-based fallback; LLM grader overrides at runtime)
# ----------------------------------------------------------------------------


def test_specificity_zero_when_no_named_entities() -> None:
    """If a use case has no named entities (everything lowercase), regex
    finds nothing — specificity is undefined; helper returns 0."""
    ctx = _ctx(products=["Carrefour Bio"])
    uc = _uc("a", description="a small lower-case description without nouns")
    scores = specificity_per_use_case([uc], ctx)
    assert scores == [0.0]


def test_specificity_returns_one_entry_per_use_case() -> None:
    """Sanity check: helper returns a list of the same length as input use
    cases, with each entry in [0, 1]. The regex-based helper is the
    fallback path (LLM grader overrides at runtime, see
    src/activities/compute_signals.py); recall on multi-word entities is
    known-poor due to greedy matching, hence we don't assert a specific
    value here."""
    ctx = _ctx(products=["Carrefour Bio", "Carrefour Express"])
    uses = [_uc("a"), _uc("b"), _uc("c")]
    scores = specificity_per_use_case(uses, ctx)
    assert len(scores) == 3
    for s in scores:
        assert 0.0 <= s <= 1.0


# ----------------------------------------------------------------------------
# mistral_product_diversity
# ----------------------------------------------------------------------------


def test_mistral_product_diversity_distinct_count() -> None:
    uses = [
        _uc("a", products=["Mistral Large 3", "Mistral Embed"]),
        _uc("b", products=["Mistral Large 3", "Pixtral"]),
        _uc("c", products=["Mistral Document AI"]),
    ]
    # Mistral Large 3 (shared) + Mistral Embed + Pixtral + Mistral Document AI = 4
    assert mistral_product_diversity(uses) == 4


def test_mistral_product_diversity_strips_parenthetical() -> None:
    """The helper normalizes 'Pixtral (vision-language)' and 'Pixtral'
    to the same product — same base, parenthetical clarification ignored."""
    uses = [
        _uc("a", products=["Pixtral (vision-language)"]),
        _uc("b", products=["Pixtral"]),
    ]
    assert mistral_product_diversity(uses) == 1


def test_mistral_product_diversity_empty() -> None:
    uses = [_uc("a", products=[])]
    assert mistral_product_diversity(uses) == 0


# ----------------------------------------------------------------------------
# time_to_value_spread / cost_tier_spread / risks_per_use_case
# ----------------------------------------------------------------------------


def test_time_to_value_spread() -> None:
    uses = [_uc("a", ttv="6 weeks"), _uc("b", ttv="8-16 weeks"), _uc("c", ttv="unknown")]
    assert time_to_value_spread(uses) == ["6 weeks", "8-16 weeks", "unknown"]


def test_cost_tier_spread() -> None:
    uses = [
        _uc("a", cost=CostTier.LOW),
        _uc("b", cost=CostTier.MEDIUM),
        _uc("c", cost=CostTier.HIGH),
    ]
    assert cost_tier_spread(uses) == ["low", "medium", "high"]


def test_risks_per_use_case() -> None:
    uses = [_uc("a", risk="risk A"), _uc("b", risk="risk B")]
    assert risks_per_use_case(uses) == ["risk A", "risk B"]


# ----------------------------------------------------------------------------
# source_coverage_per_use_case
# ----------------------------------------------------------------------------


def test_source_coverage_maps_grounded_in_paths_to_sources() -> None:
    """Each grounded_in field path should map to the research source that
    populates it. e.g. 'business.business_model' → wikipedia."""
    ctx = _ctx()
    use = _uc(
        "a",
        grounded_in=["business.business_model", "strategic_context.stated_priorities[0]"],
        inspired_by=["google_cloud_1302-abc"],
    )
    coverage = source_coverage_per_use_case([use], ctx)
    assert len(coverage) == 1
    assert "wikipedia" in coverage[0]  # business.* maps to wikipedia
    assert "news" in coverage[0]  # strategic_context.* maps to news
    assert "precedent_corpus" in coverage[0]  # has inspired_by


def test_source_coverage_no_inspired_by_omits_corpus() -> None:
    ctx = _ctx()
    use = _uc("a", grounded_in=["business.business_model"], inspired_by=[])
    coverage = source_coverage_per_use_case([use], ctx)
    assert "precedent_corpus" not in coverage[0]
