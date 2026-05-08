"""Tests for the five-criteria definitions and prompt rendering.

The criteria live in code (not just prompts) so they can be inspected,
versioned, and reused across scorer + meta-evaluator + eval harness. These
tests guard the structural invariants — five criteria, all with positive
and negative anchors, unique keys, weights summing to 1.0 by default.
"""

from __future__ import annotations

from src.criteria import (
    CRITERIA,
    CRITERIA_BY_KEY,
    ESTIMATED_IMPACT,
    FEASIBILITY,
    ICONIC_POTENTIAL,
    MISTRAL_SUITABILITY,
    RELEVANCE,
    render_criteria_for_prompt,
)


def test_exactly_five_criteria() -> None:
    """The methodology spec is locked at five dimensions. Adding a sixth
    requires explicit conversation, not a code change. Guard accordingly."""
    assert len(CRITERIA) == 5


def test_criteria_keys_are_unique() -> None:
    keys = [c.key for c in CRITERIA]
    assert len(keys) == len(set(keys))


def test_criteria_keys_match_expected() -> None:
    keys = {c.key for c in CRITERIA}
    assert keys == {
        "relevance",
        "iconic_potential",
        "estimated_impact",
        "feasibility",
        "mistral_suitability",
    }


def test_criteria_by_key_lookup() -> None:
    assert CRITERIA_BY_KEY["relevance"] is RELEVANCE
    assert CRITERIA_BY_KEY["iconic_potential"] is ICONIC_POTENTIAL
    assert CRITERIA_BY_KEY["estimated_impact"] is ESTIMATED_IMPACT
    assert CRITERIA_BY_KEY["feasibility"] is FEASIBILITY
    assert CRITERIA_BY_KEY["mistral_suitability"] is MISTRAL_SUITABILITY


def test_each_criterion_has_positive_and_negative_anchors() -> None:
    """Negative examples are non-negotiable per methodology — they're a
    stronger signal to LLMs than positive anchors alone. If anyone ever
    deletes them, this test catches it."""
    for c in CRITERIA:
        assert c.positive_example.strip(), f"{c.key} missing positive example"
        assert c.negative_examples, f"{c.key} missing negative examples"
        for neg in c.negative_examples:
            assert neg.strip(), f"{c.key} has empty negative example"


def test_default_weights_sum_to_one() -> None:
    total = sum(c.default_weight for c in CRITERIA)
    assert total == 1.0


def test_render_criteria_for_prompt_contains_each_criterion() -> None:
    rendered = render_criteria_for_prompt()
    for c in CRITERIA:
        assert c.display_name in rendered, f"{c.display_name} missing from prompt render"


def test_render_includes_negative_examples() -> None:
    """The negative-example anchor is the key methodology point — make sure
    it actually shows up in the prompt the model sees."""
    rendered = render_criteria_for_prompt()
    assert "**Negative examples:**" in rendered


def test_iconic_potential_mentions_already_done_gate() -> None:
    """The hard-gate against duplicating existing initiatives is the most
    important methodological discipline. The prompt the scorer sees must
    reference it, otherwise the gate evaporates."""
    long_desc = ICONIC_POTENTIAL.long_description.lower()
    assert "already" in long_desc or "existing" in long_desc


def test_mistral_suitability_explains_why_mistral() -> None:
    """Mistral suitability captures 'why Mistral specifically over OpenAI/
    Anthropic/Google'. The prompt must surface a Mistral-distinctive driver
    or the criterion collapses to 'any LLM does this'."""
    long_desc = MISTRAL_SUITABILITY.long_description.lower()
    distinctive_drivers = ["sovereignty", "open-weight", "multilingual", "european", "mistral"]
    assert any(d in long_desc for d in distinctive_drivers)
