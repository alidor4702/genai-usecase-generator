"""Rich UI Components composition for the final report.

We render the report via the Mistral Workflows conversational UI components
(Card, Badge, PieChart, Markdown, Row, Column) when running in Le Chat /
the Workflows runtime. For the standalone web app and CLI, this same module
also exposes a Markdown fallback so the report is renderable without the
runtime.

Per docs/architecture.md, the structure is:
    Column [
        Markdown(intro)
        Row [Card(use_case_1), Card(use_case_2), Card(use_case_3)]
        Card("Cost distribution", PieChart(...))
        Card("Considered but not selected", Markdown(rejected))
        Markdown(quality footer)
    ]
"""

from __future__ import annotations

import logging
from typing import Any

from src.models import (
    CompanyContext,
    EnrichedUseCase,
    MetaEvalReview,
    QualitySignals,
    RejectedCandidate,
    Report,
)

logger = logging.getLogger(__name__)


def _clean_mermaid(s: str) -> str:
    """Strip leading ```mermaid / ``` and trailing ``` so the renderer can
    wrap the body without producing a double-fenced code block."""
    s = (s or "").strip()
    for prefix in ("```mermaid", "```Mermaid", "```"):
        if s.startswith(prefix):
            s = s[len(prefix) :].lstrip("\n").lstrip()
            break
    if s.endswith("```"):
        s = s[:-3].rstrip()
    return s.strip()


def _impact_badge_variant(uc: EnrichedUseCase) -> str:
    return {"high": "success", "medium": "default", "low": "secondary"}.get(
        uc.impact_tier.value, "default"
    )


def _cost_badge_variant(uc: EnrichedUseCase) -> str:
    return {"low": "success", "medium": "default", "high": "warning"}.get(
        uc.operating_cost_tier.value, "default"
    )


def _format_use_case_md(uc: EnrichedUseCase, specificity: float | None) -> str:
    parts: list[str] = []
    parts.append(f"### {uc.title}")
    if uc.builds_on_existing and uc.builds_on_note:
        parts.append(f"> _{uc.builds_on_note}_")
    parts.append(uc.description)
    parts.append("")
    parts.append(
        f"**Why {('this company' if not uc.builds_on_existing else 'this is a fit')}:** {uc.why_this_company}"
    )
    parts.append("")
    parts.append(f"**Example input:** `{uc.example_input}`")
    parts.append("")
    parts.append(f"**Example output:** {uc.example_output}")
    parts.append("")
    parts.append(
        f"**Blueprint:** `{uc.blueprint_pattern.value}` (impact: {uc.impact_tier.value} · cost: {uc.operating_cost_tier.value} · complexity: {uc.complexity_tier.value} · TTV: {uc.time_to_value.estimate})"
    )
    parts.append("")
    parts.append(f"**Top risk:** {uc.top_implementation_risk}")
    parts.append("")
    if uc.suggested_mistral_products:
        parts.append("**Mistral products:** " + ", ".join(uc.suggested_mistral_products))
        parts.append("")
    if uc.inspired_by:
        parts.append("**Inspired by precedents:** " + ", ".join(uc.inspired_by))
    if uc.grounded_in:
        parts.append("**Grounded in:** " + ", ".join(uc.grounded_in))
    if specificity is not None:
        parts.append(f"_Specificity score: {specificity:.2f}_")
    cleaned_mermaid = _clean_mermaid(uc.blueprint_mermaid)
    if cleaned_mermaid:
        parts.append("\n**Architecture blueprint:**\n```mermaid\n" + cleaned_mermaid + "\n```")
    return "\n".join(parts)


def _summarize_ttv_spread(spreads: list[str]) -> str:
    """Render TTV as a terse summary, not concatenated full prose."""
    import re as _re

    weeks: list[int] = []
    for s in spreads:
        for m in _re.finditer(r"(\d+)\s*[-–]\s*(\d+)\s*weeks", s.lower()):
            weeks.extend([int(m.group(1)), int(m.group(2))])
    if weeks:
        return f"{min(weeks)}–{max(weeks)} weeks (across {len(spreads)} use cases)"
    if any("unknown" in s.lower() for s in spreads):
        return f"mixed (some unknown across {len(spreads)} use cases)"
    return f"{len(spreads)} use cases, see per-card detail"


def _quality_footer_md(signals: QualitySignals, meta: MetaEvalReview | None) -> str:
    lines: list[str] = ["---", "## Report quality signals", ""]
    lines.append(
        f"- **Topical diversity** (LLM-graded over titles + blueprint patterns): "
        f"`{signals.diversity:.2f}`"
    )
    lines.append(
        "- **Specificity** per use case: "
        + ", ".join(f"`{s:.2f}`" for s in signals.specificity_per_use_case)
    )
    lines.append(
        f"- **Mistral product diversity**: `{signals.mistral_product_diversity}` distinct products across the three use cases"
    )
    lines.append(
        f"- **Time-to-value spread**: {_summarize_ttv_spread(signals.time_to_value_spread)}"
    )
    cost_summary = ", ".join(t.value for t in signals.cost_tier_spread)
    lines.append(f"- **Cost-tier spread**: {cost_summary}")
    lines.append(
        f"- **Fact-check pass rate**: `{signals.fact_check_pass_rate:.0%}` ({sum(1 for c in signals.fact_check if c.passed)}/{len(signals.fact_check)} claims supported by research)"
    )
    # Per-claim transparency block — without this the aggregate pass rate is
    # opaque (no way to tell whether meta-eval is being legitimately strict
    # or being a verbatim-match brat). List each claim's verdict so the
    # reader can judge for themselves.
    if signals.fact_check:
        passed = [c for c in signals.fact_check if c.passed]
        failed = [c for c in signals.fact_check if not c.passed]
        lines.append("")
        lines.append("<details><summary>Fact-check detail (per claim)</summary>")
        lines.append("")
        if failed:
            lines.append(f"**Unsupported ({len(failed)}):**")
            for c in failed:
                rationale = c.rationale or "no source contained directly-supporting text"
                lines.append(
                    f"- [{c.use_case_id}] {c.claim} — _{rationale[:200]}_"
                )
            lines.append("")
        if passed:
            lines.append(f"**Supported ({len(passed)}):**")
            for c in passed:
                src = c.rationale[:140] + "…" if c.rationale and len(c.rationale) > 140 else (c.rationale or "")
                lines.append(f"- [{c.use_case_id}] {c.claim}{(' — ' + src) if src else ''}")
            lines.append("")
        lines.append("</details>")
    if meta is not None:
        lines.append("")
        lines.append(
            f"**Meta-evaluator confidence**: `{meta.confidence:.2f}` "
            f"({'sales-engineer-ready' if meta.sales_engineer_ready else 'NOT ready — needs revision'})"
        )
        if meta.cross_cutting_concern:
            lines.append(f"**Cross-cutting concern**: {meta.cross_cutting_concern}")
        if meta.duplicate_flag:
            lines.append(f"**Duplicate flag**: {meta.duplicate_flag}")
    return "\n".join(lines)


def _intro_md(ctx: CompanyContext, uses: list[EnrichedUseCase]) -> str:
    return (
        f"## GenAI Use Cases for {ctx.identity.name}\n\n"
        f"Three customer-ready use cases, scored against the Mistral Proto Team's "
        f"five-criteria rubric (relevance · iconic potential · estimated impact · "
        f"feasibility · Mistral suitability) and verified against {ctx.identity.name}'s "
        f"existing AI initiatives. Generated from a corpus of ~2,150 peer deployments "
        f"and {len(ctx.existing_ai_initiatives)} discovered existing initiatives at this "
        f"company.\n\n"
        f"_Industry: {ctx.classification.industry}. Research confidence: "
        f"{ctx.meta.research_confidence:.2f}. Verified: {ctx.meta.is_verified}._"
    )


def _rejected_md(rejected: list[RejectedCandidate]) -> str:
    if not rejected:
        return "_(no near-misses — top-3 candidates passed verification cleanly)_"
    return "\n".join(f"- **{r.title}** — {r.one_line_reason}" for r in rejected)


def render_report_to_markdown(report: Report) -> str:
    """Single-document Markdown rendering — for CLI / standalone web app."""
    parts: list[str] = []
    parts.append(_intro_md(report.company, report.top_use_cases))
    parts.append("")
    for i, uc in enumerate(report.top_use_cases):
        spec = (
            report.quality.specificity_per_use_case[i]
            if i < len(report.quality.specificity_per_use_case)
            else None
        )
        parts.append(_format_use_case_md(uc, spec))
        parts.append("")
    parts.append("## Considered but not selected")
    parts.append(_rejected_md(report.rejected_appendix))
    parts.append("")
    parts.append(_quality_footer_md(report.quality, report.meta_review))
    return "\n".join(parts)


def render_report_to_components(report: Report) -> Any:
    """Rich UI Components composition for Le Chat / Workflows runtime.

    Imports are inside the function so this module remains importable even if
    the conversational_ui_components plugin path differs across SDK minor
    versions. We construct a Column with sub-blocks; the calling site wraps
    this into a `ResourceOutput(UIComponentResource(...))`.
    """
    try:
        from mistralai.workflows.plugins.mistralai.conversational_ui_components import (
            Badge,
            Card,
            Column,
            Markdown,
            PieChart,
            Row,
        )
    except ImportError:  # pragma: no cover
        # Plugin not available in this environment — caller will fall back
        # to the markdown rendering.
        return None

    use_case_cards = []
    for i, uc in enumerate(report.top_use_cases):
        spec_score = (
            report.quality.specificity_per_use_case[i]
            if i < len(report.quality.specificity_per_use_case)
            else 0.0
        )
        badges = Row(
            children=[
                Badge(
                    variant=_impact_badge_variant(uc), children=f"Impact: {uc.impact_tier.value}"
                ),
                Badge(
                    variant=_cost_badge_variant(uc),
                    children=f"Cost: {uc.operating_cost_tier.value}",
                ),
                Badge(variant="default", children=f"Complexity: {uc.complexity_tier.value}"),
                Badge(variant="default", children=f"TTV: {uc.time_to_value.estimate}"),
            ]
        )
        body = Markdown(content=_format_use_case_md(uc, spec_score))
        use_case_cards.append(Card(title=uc.title, description=None, children=[badges, body]))

    cost_data = [
        {
            "label": uc.title[:40],
            "value": {"low": 1, "medium": 2, "high": 3, "unknown": 1}.get(
                uc.operating_cost_tier.value, 1
            ),
        }
        for uc in report.top_use_cases
    ]
    cost_card = Card(title="Estimated cost distribution", children=[PieChart(data=cost_data)])
    rejected_card = Card(
        title="Considered but not selected",
        children=[Markdown(content=_rejected_md(report.rejected_appendix))],
    )
    quality_md = Markdown(content=_quality_footer_md(report.quality, report.meta_review))

    return Column(
        children=[
            Markdown(content=_intro_md(report.company, report.top_use_cases)),
            Row(children=use_case_cards),
            cost_card,
            rejected_card,
            quality_md,
        ]
    )
