"""Tests for the Pydantic model layer.

Focused on invariants that, if broken, would silently corrupt downstream
activity I/O — the Pydantic schema is the contract between activities.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.evidence import evidence_id_for
from src.models import (
    BlueprintPattern,
    Candidate,
    CompanyClassification,
    CompanyContext,
    CompanyIdentity,
    CompanyMeta,
    ComplexityTier,
    CostTier,
    CriteriaWeights,
    EnrichedUseCase,
    EvidenceItem,
    EvidenceKind,
    EvidenceLedger,
    FactCheckEntry,
    FocusArea,
    ImpactTier,
    Novelty,
    QualitySignals,
    RejectedCandidate,
    Report,
    ResearchDepth,
    TimeToValue,
    WorkflowInput,
)


# ----------------------------------------------------------------------------
# CriteriaWeights
# ----------------------------------------------------------------------------


def test_criteria_weights_default_sums_to_one() -> None:
    w = CriteriaWeights().normalized()
    total = w.relevance + w.iconic_potential + w.estimated_impact + w.feasibility + w.mistral_suitability
    assert total == pytest.approx(1.0)


def test_criteria_weights_normalized_with_uneven_input() -> None:
    w = CriteriaWeights(
        relevance=0.5,
        iconic_potential=0.5,
        estimated_impact=0.0,
        feasibility=0.0,
        mistral_suitability=0.0,
    ).normalized()
    assert w.relevance == pytest.approx(0.5)
    assert w.iconic_potential == pytest.approx(0.5)
    assert w.feasibility == 0.0


def test_criteria_weights_zero_total_returns_default() -> None:
    """All-zero input shouldn't produce NaN — fall back to equal split."""
    w = CriteriaWeights(
        relevance=0.0,
        iconic_potential=0.0,
        estimated_impact=0.0,
        feasibility=0.0,
        mistral_suitability=0.0,
    ).normalized()
    assert w.relevance == 0.2


def test_criteria_weights_clamped_to_unit_range() -> None:
    with pytest.raises(ValidationError):
        CriteriaWeights(relevance=2.0)  # type: ignore[arg-type]


# ----------------------------------------------------------------------------
# WorkflowInput
# ----------------------------------------------------------------------------


def test_workflow_input_defaults() -> None:
    wi = WorkflowInput(company_name="Veolia")
    assert wi.focus_area == FocusArea.GENERAL
    assert wi.research_depth == ResearchDepth.MEDIUM
    assert wi.weights.relevance == 0.2


def test_workflow_input_rejects_empty_company_name() -> None:
    with pytest.raises(ValidationError):
        WorkflowInput(company_name="")


# ----------------------------------------------------------------------------
# EvidenceLedger
# ----------------------------------------------------------------------------


def _ev(url: str, title: str, kind: EvidenceKind = EvidenceKind.WIKIPEDIA) -> EvidenceItem:
    return EvidenceItem(
        id=evidence_id_for(url, title),
        source_kind=kind,
        url=url,
        title=title,
        content="some content",
        fetched_at_step="research",
        confidence="high",
    )


def test_evidence_id_is_deterministic() -> None:
    a = evidence_id_for("https://x.com", "Title")
    b = evidence_id_for("https://x.com", "Title")
    assert a == b
    assert a.startswith("ev-")


def test_evidence_id_differs_on_different_inputs() -> None:
    a = evidence_id_for("https://x.com", "Title A")
    b = evidence_id_for("https://x.com", "Title B")
    assert a != b


def test_ledger_add_dedupes_by_id() -> None:
    ledger = EvidenceLedger()
    item = _ev("https://x.com", "T")
    assert ledger.add(item) is True
    assert ledger.add(item) is False  # second add is a no-op
    assert len(ledger.entries) == 1


def test_ledger_by_id_finds_entry() -> None:
    ledger = EvidenceLedger()
    item = _ev("https://x.com", "T")
    ledger.add(item)
    assert ledger.by_id(item.id) is item
    assert ledger.by_id("ev-nonexistent") is None


def test_ledger_of_kind_filters() -> None:
    ledger = EvidenceLedger()
    ledger.add(_ev("https://w.com", "wiki", EvidenceKind.WIKIPEDIA))
    ledger.add(_ev("https://n.com", "news", EvidenceKind.NEWS))
    ledger.add(_ev("https://t.com", "tav", EvidenceKind.TAVILY))
    wikis = ledger.of_kind(EvidenceKind.WIKIPEDIA)
    assert len(wikis) == 1
    assert wikis[0].title == "wiki"


# ----------------------------------------------------------------------------
# Candidate / EnrichedUseCase
# ----------------------------------------------------------------------------


def test_candidate_defaults_have_empty_lists() -> None:
    c = Candidate(
        id="x",
        title="t",
        description="d",
        why_this_company="w",
        estimated_impact_summary="i",
    )
    assert c.inspired_by == []
    assert c.grounded_in == []
    assert c.evidence_ids == []
    assert c.novelty == Novelty.ADAPTED_FROM_PRECEDENT


def test_enriched_use_case_carries_evidence_ids() -> None:
    uc = EnrichedUseCase(
        id="x",
        title="t",
        description="d",
        why_this_company="w",
        example_input="i",
        example_output="o",
        suggested_mistral_products=["Mistral Large 3"],
        blueprint_pattern=BlueprintPattern.RAG,
        blueprint_mermaid="",
        time_to_value=TimeToValue(estimate="8-16 weeks"),
        operating_cost_tier=CostTier.MEDIUM,
        impact_tier=ImpactTier.HIGH,
        complexity_tier=ComplexityTier.LOW,
        top_implementation_risk="risk",
        evidence_ids=["ev-abc", "ev-def"],
    )
    assert uc.evidence_ids == ["ev-abc", "ev-def"]


# ----------------------------------------------------------------------------
# Report — must contain exactly 3 use cases
# ----------------------------------------------------------------------------


def _stub_company_context() -> CompanyContext:
    return CompanyContext(
        identity=CompanyIdentity(name="X"),
        classification=CompanyClassification(industry="retail"),
        meta=CompanyMeta(research_confidence=0.7),
    )


def _stub_use_case(uid: str) -> EnrichedUseCase:
    return EnrichedUseCase(
        id=uid,
        title=f"Use case {uid}",
        description="d",
        why_this_company="w",
        example_input="i",
        example_output="o",
        suggested_mistral_products=[],
        blueprint_pattern=BlueprintPattern.RAG,
        blueprint_mermaid="",
        time_to_value=TimeToValue(estimate="8-16 weeks"),
        operating_cost_tier=CostTier.MEDIUM,
        impact_tier=ImpactTier.HIGH,
        complexity_tier=ComplexityTier.LOW,
        top_implementation_risk="r",
    )


def test_report_requires_exactly_three_use_cases() -> None:
    quality = QualitySignals(diversity=0.5)
    with pytest.raises(ValidationError):
        Report(
            company=_stub_company_context(),
            weights_used=CriteriaWeights(),
            focus_area=FocusArea.GENERAL,
            research_depth=ResearchDepth.MEDIUM,
            top_use_cases=[_stub_use_case("a"), _stub_use_case("b")],
            quality=quality,
            intro_text="intro",
        )


def test_report_accepts_three_use_cases() -> None:
    quality = QualitySignals(diversity=0.5)
    report = Report(
        company=_stub_company_context(),
        weights_used=CriteriaWeights(),
        focus_area=FocusArea.GENERAL,
        research_depth=ResearchDepth.MEDIUM,
        top_use_cases=[_stub_use_case("a"), _stub_use_case("b"), _stub_use_case("c")],
        quality=quality,
        intro_text="intro",
    )
    assert len(report.top_use_cases) == 3


# ----------------------------------------------------------------------------
# QualitySignals
# ----------------------------------------------------------------------------


def test_fact_check_pass_rate_empty_returns_one() -> None:
    """No claims = nothing to fact-check; treat as passing."""
    q = QualitySignals(diversity=0.5)
    assert q.fact_check_pass_rate == 1.0


def test_fact_check_pass_rate_mixed() -> None:
    q = QualitySignals(
        diversity=0.5,
        fact_check=[
            FactCheckEntry(claim="a", use_case_id="x", passed=True),
            FactCheckEntry(claim="b", use_case_id="x", passed=False),
            FactCheckEntry(claim="c", use_case_id="x", passed=True),
        ],
    )
    assert q.fact_check_pass_rate == pytest.approx(2 / 3)


def test_quality_signals_diversity_clamped() -> None:
    with pytest.raises(ValidationError):
        QualitySignals(diversity=1.5)
    with pytest.raises(ValidationError):
        QualitySignals(diversity=-0.1)


# ----------------------------------------------------------------------------
# RejectedCandidate
# ----------------------------------------------------------------------------


def test_rejected_candidate_basic() -> None:
    r = RejectedCandidate(title="t", one_line_reason="reason")
    assert r.title == "t"
    assert r.one_line_reason == "reason"
