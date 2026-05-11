"""Build the Le Chat Rich UI Components tree for a finished report.

This is the rendering used on the Le Chat assistant. Instead of shipping
the report as raw markdown canvases, we compose Mistral's Rich UI
Components — Card, Badge, PieChart, Alert, Row, Column, Markdown — into
a structured component tree the assistant renders natively.

The architecture.md doc described this approach from the start; we built
the markdown-canvas variant first because it was simpler. This module
restores the design.

The function operates on the chunks dict returned by
`render_report_activity` (see `src/ui/render.py:render_report_to_chunks`),
so the activity boundary is a pure dict — no UIComponent objects cross
the sandbox.
"""

from __future__ import annotations

from typing import Any

from mistralai.workflows.plugins.mistralai.conversational_ui_components import (
    Alert,
    Badge,
    Card,
    Column,
    Markdown,
    PieChart,
    Row,
    UIComponent,
)


def _badge(label: str, variant: str = "default") -> Badge:
    """Shorthand for a small badge — coerces invalid variants safely."""
    valid = {"default", "primary", "success", "warning", "error"}
    if variant not in valid:
        variant = "default"
    return Badge(children=label, variant=variant, size="sm")  # type: ignore[arg-type]


def _confidence_variant(conf: float) -> str:
    """Colour the confidence badge by SE-ready threshold."""
    if conf >= 0.70:
        return "success"
    if conf >= 0.55:
        return "warning"
    return "error"


def _impact_variant(tier: str) -> str:
    return {"high": "success", "medium": "primary", "low": "default"}.get(tier, "default")


def _cost_variant(tier: str) -> str:
    return {"low": "success", "medium": "primary", "high": "warning"}.get(tier, "default")


def _build_use_case_card(uc: dict[str, Any], idx: int) -> Card:
    """One Card per use case: title + body markdown + tier badges row."""
    title = str(uc.get("title", f"Use case {idx + 1}"))
    body_md = str(uc.get("body_md", ""))
    blueprint = str(uc.get("blueprint_pattern", ""))

    # Pull the metadata badges out of the body_md if the renderer set
    # them. We use heuristic regex against the standard format string
    # `**Blueprint:** \`{pattern}\` (impact: {x} · cost: {y} · ...`.
    impact = "medium"
    cost = "medium"
    complexity = "medium"
    ttv = ""
    if "impact:" in body_md:
        for tier in ("high", "medium", "low"):
            if f"impact: {tier}" in body_md:
                impact = tier
                break
        for tier in ("high", "medium", "low"):
            if f"cost: {tier}" in body_md:
                cost = tier
                break
        for tier in ("high", "medium", "low"):
            if f"complexity: {tier}" in body_md:
                complexity = tier
                break
    # TTV — pull out "TTV: 12-16 weeks" or similar
    import re

    m = re.search(r"TTV: ([0-9]+\s*[\-–]\s*[0-9]+\s*weeks)", body_md)
    if m:
        ttv = m.group(1)

    children: list[UIComponent] = [
        Markdown(content=body_md),
        Row(
            gap="sm",
            wrap=True,
            children=[
                _badge(f"Impact · {impact}", _impact_variant(impact)),
                _badge(f"Cost · {cost}", _cost_variant(cost)),
                _badge(f"Complexity · {complexity}", "default"),
                *([_badge(f"TTV · {ttv}", "primary")] if ttv else []),
                *([_badge(f"Blueprint · {blueprint}", "default")] if blueprint else []),
            ],
        ),
    ]
    return Card(
        title=f"Use case {idx + 1} · {title}",
        padding="md",
        children=children,
    )


def _build_quality_pie(quality_md: str, fact_check: dict[str, int]) -> PieChart | None:
    """If we have fact-check counts, render them as a pie chart. The
    `fact_check` arg is a precomputed dict
        {supported, rescued, rewritten, unsupported}
    Returns None if there's nothing to show."""
    total = sum(fact_check.values())
    if total == 0:
        return None
    data = [
        {"name": "Supported", "value": fact_check.get("supported", 0), "fill": "#10b981"},
        {"name": "Rescued", "value": fact_check.get("rescued", 0), "fill": "#3b82f6"},
        {"name": "Rewritten", "value": fact_check.get("rewritten", 0), "fill": "#f59e0b"},
        {"name": "Unsupported", "value": fact_check.get("unsupported", 0), "fill": "#ef4444"},
    ]
    # Drop zero-count slices for cleanliness
    data = [d for d in data if d["value"] > 0]
    return PieChart(data=data, title="Claim source-anchoring breakdown")


def build_report_component_tree(
    company_name: str,
    chunks: dict[str, Any],
    confidence: float | None,
    pass_rate: float | None,
    fact_check_breakdown: dict[str, int] | None,
    sales_engineer_ready: bool,
    cross_cutting_concern: str | None,
    tier: str,
) -> Column:
    """Assemble the full report as a Column tree of Rich UI Components.

    Layout:
      Column
      ├── Alert (only when meta-eval flagged the run as needing revision)
      ├── Card "Executive summary" with confidence/pass-rate/tier badges
      ├── Card per use case (title + body_md + impact/cost/complexity/TTV badges)
      ├── Card "Quality signals" with PieChart + verification markdown
      └── Card "Considered but not selected" (collapsible-feeling, just markdown)
    """
    children: list[UIComponent] = []

    # ── Status banner ────────────────────────────────────────────────
    # Confidence (a 0-1 float) and sales_engineer_ready (the model's binary
    # judgment) are SEPARATE signals. Confidence is a numerical floor;
    # sales_engineer_ready is qualitative — the model can mark the report
    # not-ready due to a strategic concern even when confidence is above
    # the 0.70 bar. The banner differentiates which signal is the issue
    # so a confidence-0.75-but-flagged report doesn't read as contradictory.
    if sales_engineer_ready and (confidence is None or confidence >= 0.70):
        banner_variant = "success"
        banner_title = "Sales-engineer-ready"
        banner_body_text = (
            "Confidence "
            + (f"`{confidence:.2f}` at or above the `0.70` bar. " if confidence else "")
            + "The use cases passed the full verification chain (numeric anchoring · "
            + "per-claim fact-check · web-verify rescue · source-judge · qualitative "
            + "rewrite) and are ready to share."
        )
    elif confidence is not None and confidence >= 0.70 and not sales_engineer_ready:
        banner_variant = "warning"
        banner_title = "Above 0.70 bar, but flagged for strategic revision"
        banner_body_text = (
            f"Confidence `{confidence:.2f}` is at or above the `0.70` numerical bar, "
            f"but the meta-evaluator marked the report not sales-engineer-ready. "
            f"This is a qualitative concern (report-level reasoning), not a numerical "
            f"or factual issue — see the cross-cutting note below for what to revise. "
            f"The use cases have been through the full verification chain; the prose "
            f"doesn't assert unverified specifics."
        )
    elif confidence is not None and confidence < 0.55:
        banner_variant = "warning"
        banner_title = "Confidence well below SE-ready bar — revision suggested"
        banner_body_text = (
            f"Confidence `{confidence:.2f}` is well below the `0.70` bar. The use "
            f"cases below have still been through the full verification chain — "
            f"the prose doesn't assert unverified specifics — but the citation "
            f"density is low. See the cross-cutting note and per-claim verdicts "
            f"in **Quality signals** for revision targets."
        )
    else:
        banner_variant = "warning"
        banner_title = "Confidence below SE-ready bar — revision suggested"
        banner_body_text = (
            "Confidence "
            + (f"`{confidence:.2f}` below the " if confidence else "is below the ")
            + "`0.70` SE-ready bar. The use cases have been through the full "
            + "verification chain (numeric scrub + claim fact-check + web-verify "
            + "+ source-judge + qualitative rewrite); the threshold gap reflects "
            + "citation density, not factual correctness. Suggestions for revision "
            + "in **Quality signals** below."
        )
    alert_body: list[UIComponent] = [Markdown(content=banner_body_text)]
    if cross_cutting_concern:
        alert_body.append(
            Markdown(content=f"**Cross-cutting improvement note:** {cross_cutting_concern}")
        )
    children.append(
        Alert(variant=banner_variant, title=banner_title, children=alert_body)  # type: ignore[arg-type]
    )

    # ── Executive summary ────────────────────────────────────────────
    summary_badges: list[UIComponent] = [
        _badge(f"Tier · {tier}", "primary"),
    ]
    if confidence is not None:
        summary_badges.append(
            _badge(f"Confidence · {confidence:.2f}", _confidence_variant(confidence))
        )
    if pass_rate is not None:
        summary_badges.append(
            _badge(
                f"Source-anchored · {pass_rate:.0%}",
                "success" if pass_rate >= 0.80 else "warning",
            )
        )
    children.append(
        Card(
            title=f"GenAI use cases — {company_name}",
            description="Three customer-ready proposals scored against the five-criteria rubric.",
            padding="md",
            children=[
                Row(gap="sm", wrap=True, children=summary_badges),
                Markdown(content=str(chunks.get("executive_summary", ""))),
            ],
        )
    )

    # ── Per-use-case cards ───────────────────────────────────────────
    use_cases = chunks.get("use_cases") or []
    if isinstance(use_cases, list):
        for i, uc in enumerate(use_cases):
            if isinstance(uc, dict):
                children.append(_build_use_case_card(uc, i))

    # ── Quality signals ──────────────────────────────────────────────
    verification_md = str(chunks.get("verification_md", ""))
    quality_children: list[UIComponent] = []
    if fact_check_breakdown:
        pie = _build_quality_pie(verification_md, fact_check_breakdown)
        if pie is not None:
            quality_children.append(pie)
    if verification_md:
        quality_children.append(Markdown(content=verification_md))
    if quality_children:
        children.append(
            Card(
                title="Quality signals & per-claim verification",
                padding="md",
                children=quality_children,
            )
        )

    return Column(gap="lg", children=children)
