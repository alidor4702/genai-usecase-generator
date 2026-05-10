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

import ast
import json
import logging
import re
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


# Blueprint pattern → categorical color mapping for mermaid rendering.
# Surface-area-driven so a sales engineer can see at a glance which blueprint
# family each use case sits in. Hex values are accent colors; mermaid will use
# them as `classDef` fills.
_BLUEPRINT_COLORS: dict[str, dict[str, str]] = {
    "rag":                   {"fill": "#1e3a8a", "stroke": "#3b82f6", "color": "#dbeafe"},  # retrieval — blue
    "agent_with_tools":      {"fill": "#7c2d12", "stroke": "#fa552e", "color": "#fed7aa"},  # agentic — Mistral orange
    "document_ai_pipeline":  {"fill": "#064e3b", "stroke": "#10b981", "color": "#d1fae5"},  # document — green
    "fine_tuned_domain":     {"fill": "#581c87", "stroke": "#a855f7", "color": "#f3e8ff"},  # domain — purple
    "hybrid_retrieval":      {"fill": "#134e4a", "stroke": "#14b8a6", "color": "#ccfbf1"},  # hybrid — teal
}


def _decorate_mermaid(body: str, pattern: str) -> str:
    """Append a `classDef` + `class` line to apply the pattern's color to
    every node in the mermaid body. If the pattern isn't recognised, return
    the body unchanged.

    Mermaid syntax: nodes get a default class, and `classDef <name> fill:...`
    sets the style. We use one class for the whole graph so the entire flow
    reads as belonging to the same blueprint family.
    """
    palette = _BLUEPRINT_COLORS.get(pattern)
    if not palette or not body:
        return body
    cls = f"bp_{pattern}"
    style = (
        f"classDef {cls} fill:{palette['fill']},stroke:{palette['stroke']},"
        f"color:{palette['color']},stroke-width:1.5px"
    )
    # Match every node id at line start (mermaid graph nodes typically start
    # with a non-whitespace token followed by [ ( { or ->.
    node_ids: set[str] = set()
    for line in body.splitlines():
        m = re.match(r"\s*([A-Za-z_][\w]*)\s*[\[\(\{]", line)
        if m:
            node_ids.add(m.group(1))
    if not node_ids:
        return body
    return body + "\n" + style + "\n" + "class " + ",".join(sorted(node_ids)) + f" {cls}"


def _impact_badge_variant(uc: EnrichedUseCase) -> str:
    return {"high": "success", "medium": "default", "low": "secondary"}.get(
        uc.impact_tier.value, "default"
    )


def _cost_badge_variant(uc: EnrichedUseCase) -> str:
    return {"low": "success", "medium": "default", "high": "warning"}.get(
        uc.operating_cost_tier.value, "default"
    )


def _format_example_output_md(raw: str) -> str:
    """Render an example_output value as a fenced code block. The LLM
    typically emits a Python-dict-shaped literal; parse it via
    ast.literal_eval and pretty-print as JSON when possible, fall back
    to the raw string in a generic code block on parse failure.
    """
    stripped = raw.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = ast.literal_eval(stripped)
            pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
            return f"**Example output:**\n```json\n{pretty}\n```"
        except (ValueError, SyntaxError):
            pass
    return f"**Example output:**\n```\n{stripped}\n```"


def _format_use_case_md(
    uc: EnrichedUseCase,
    specificity: float | None,
    *,
    include_mermaid: bool = True,
) -> str:
    """Render one use case to markdown.

    `include_mermaid=False` skips the trailing fenced ```mermaid``` block —
    used when the caller plans to ship the diagram as its own resource
    (e.g. Le Chat per-use-case canvas pair).
    """
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
    # example_output is `str` in the model but the LLM commonly emits
    # a Python-dict-shaped literal (single quotes, no indentation).
    # Inline that as-is reads as one giant blob in any markdown
    # renderer. Try to parse it as a Python literal and re-emit as
    # pretty-printed JSON; on any parse failure fall back to the raw
    # string in a generic fenced code block so it at least gets fixed
    # spacing.
    parts.append(_format_example_output_md(uc.example_output))
    parts.append("")
    ttv_label = uc.time_to_value.estimate
    if uc.time_to_value.basis.value == "ballpark_assumption":
        ttv_label = f"~{uc.time_to_value.estimate} (estimated)"
    elif uc.time_to_value.basis.value == "precedent":
        ttv_label = f"{uc.time_to_value.estimate} (precedent-anchored)"
    parts.append(
        f"**Blueprint:** `{uc.blueprint_pattern.value}` (impact: {uc.impact_tier.value} · cost: {uc.operating_cost_tier.value} · complexity: {uc.complexity_tier.value} · TTV: {ttv_label})"
    )
    if uc.time_to_value.basis.value == "ballpark_assumption" and uc.time_to_value.rationale:
        parts.append(f"  _TTV rationale: {uc.time_to_value.rationale}_")
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
    if include_mermaid:
        cleaned_mermaid = _clean_mermaid(uc.blueprint_mermaid)
        if cleaned_mermaid:
            decorated = _decorate_mermaid(cleaned_mermaid, uc.blueprint_pattern.value)
            parts.append("\n**Architecture blueprint:**\n```mermaid\n" + decorated + "\n```")
    return "\n".join(parts)


def _summarize_ttv_spread(spreads: list[str]) -> str:
    """Render TTV as a terse summary, not concatenated full prose."""
    weeks: list[int] = []
    for s in spreads:
        for m in re.finditer(r"(\d+)\s*[-–]\s*(\d+)\s*weeks", s.lower()):
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
    in_scope = [c for c in signals.fact_check if not c.qualified_out]
    qualified_n = len(signals.fact_check) - len(in_scope)
    qualified_note = (
        f" · {qualified_n} rewritten qualitatively (excluded from rate)"
        if qualified_n
        else ""
    )
    lines.append(
        f"- **Fact-check pass rate**: `{signals.fact_check_pass_rate:.0%}` "
        f"({sum(1 for c in in_scope if c.passed)}/{len(in_scope)} claims supported by research"
        f"{qualified_note})"
    )
    # Per-claim transparency block — without this the aggregate pass rate is
    # opaque (no way to tell whether meta-eval is being legitimately strict
    # or being a verbatim-match brat). List each claim's verdict so the
    # reader can judge for themselves.
    if signals.fact_check:
        in_scope = [c for c in signals.fact_check if not c.qualified_out]
        passed = [c for c in in_scope if c.passed]
        failed = [c for c in in_scope if not c.passed]
        rewritten = [c for c in signals.fact_check if c.qualified_out]
        lines.append("")
        lines.append("<details><summary>Fact-check detail (per claim)</summary>")
        lines.append("")
        if failed:
            lines.append(f"**Unsupported ({len(failed)}):**")
            for c in failed:
                rationale = c.rationale or "no source contained directly-supporting text"
                judge_chip = ""
                if c.judge_rejected:
                    judge_chip = " `[judge: rejected]`"
                    if c.judge_reason:
                        rationale = f"{c.judge_reason} (was: {rationale[:120]})"
                lines.append(
                    f"- [{c.use_case_id}] {c.claim}{judge_chip} — _{rationale[:240]}_"
                )
            lines.append("")
        if rewritten:
            lines.append(
                f"**Rewritten qualitatively ({len(rewritten)}):** _the original draft asserted these but the verification chain couldn't anchor them, so the rendered prose was rewritten into qualitative phrasing. Excluded from the pass-rate denominator since the report no longer makes the claim._"
            )
            for c in rewritten:
                lines.append(
                    f"- [{c.use_case_id}] {c.claim} `[rewritten qualitatively]`"
                )
            lines.append("")
        if passed:
            rescued = [c for c in passed if c.rescue_tier and not c.corrected]
            corrected = [c for c in passed if c.corrected]
            verified_n = sum(1 for c in rescued if c.rescue_tier == "verified")
            corroborated_n = sum(1 for c in rescued if c.rescue_tier == "corroborated")
            summary_parts: list[str] = []
            if rescued:
                summary_parts.append(
                    f"{len(rescued)} rescued via web search ({verified_n} verified, {corroborated_n} corroborated)"
                )
            if corrected:
                summary_parts.append(
                    f"{len(corrected)} self-corrected from source"
                )
            rescue_summary = f" — **{' · '.join(summary_parts)}**" if summary_parts else ""
            lines.append(f"**Supported ({len(passed)}):**{rescue_summary}")
            for c in passed:
                src = c.rationale[:140] + "…" if c.rationale and len(c.rationale) > 140 else (c.rationale or "")
                if c.corrected and c.corrected_value:
                    # Show the corrected value inline so the reviewer sees
                    # what the system fixed.
                    chip = (
                        f" [`corrected ↗ → {c.corrected_value[:60]}`]({c.rescue_url})"
                        if c.rescue_url
                        else f" `[corrected → {c.corrected_value[:60]}]`"
                    )
                    reason = c.judge_reason or src
                    lines.append(
                        f"- [{c.use_case_id}] {c.claim}{chip} — _{reason[:200]}_"
                    )
                    continue
                tier_badge = ""
                if c.rescue_tier == "verified" and c.rescue_url:
                    tier_badge = f" [`verified ↗`]({c.rescue_url})"
                elif c.rescue_tier == "verified":
                    tier_badge = " `[verified]`"
                elif c.rescue_tier == "corroborated" and c.rescue_url:
                    tier_badge = f" [`corroborated ↗`]({c.rescue_url})"
                elif c.rescue_tier == "corroborated":
                    tier_badge = " `[corroborated]`"
                lines.append(
                    f"- [{c.use_case_id}] {c.claim}{tier_badge}{(' — ' + src) if src else ''}"
                )
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


def _draft_banner_md(report: Report) -> str:
    """Top-of-report banner shown when meta-eval flagged the run as
    needing revision. Quotes the cross-cutting concern + confidence so
    the reviewer can audit immediately. NOT a suppression — every use
    case still renders below; this just surfaces the verdict honestly.
    """
    if report.meta_review is None:
        return ""
    conf = report.meta_review.confidence
    weakness = report.meta_review.weakness_reason or ""
    cross = report.meta_review.cross_cutting_concern or ""
    lines = [
        "> **Draft — needs revision before customer use.** "
        f"Meta-eval confidence `{conf:.2f}` (sales-engineer-ready threshold ≥ 0.70). "
        f"The report's three use cases render below for inspection, with each claim "
        f"tagged supported / unsupported / rewritten qualitatively in the fact-check block."
    ]
    if cross:
        lines.append(">")
        lines.append(f"> **Cross-cutting concern:** {cross}")
    if weakness:
        lines.append(">")
        lines.append(f"> **Weakest use case:** {weakness}")
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


def render_report_to_chunks(report: Report) -> dict[str, Any]:
    """Structured rendering — for surfaces that want each use case shipped as
    its own document (Le Chat per-use-case canvases). Returns a dict with:

      executive_summary: str  — draft banner (if any) + intro + considered-but-not-selected
      use_cases:        list[dict] — one per top-3 use case, each with:
                          title, body_md (no mermaid), diagram_mermaid, blueprint_pattern
      verification_md:  str  — quality signals + fact-check details + meta-eval verdict

    Plain dict / list / str values throughout so the result crosses the
    Mistral Workflows activity boundary cleanly without Pydantic
    isinstance issues.
    """
    exec_parts: list[str] = []
    if report.meta_review is not None and (
        not report.meta_review.sales_engineer_ready
        or report.meta_review.confidence < 0.70
    ):
        exec_parts.append(_draft_banner_md(report))
        exec_parts.append("")
    exec_parts.append(_intro_md(report.company, report.top_use_cases))
    exec_parts.append("")
    exec_parts.append("## Considered but not selected")
    exec_parts.append(_rejected_md(report.rejected_appendix))

    use_cases: list[dict[str, str]] = []
    for i, uc in enumerate(report.top_use_cases):
        spec = (
            report.quality.specificity_per_use_case[i]
            if i < len(report.quality.specificity_per_use_case)
            else None
        )
        cleaned = _clean_mermaid(uc.blueprint_mermaid)
        decorated = _decorate_mermaid(cleaned, uc.blueprint_pattern.value) if cleaned else ""
        use_cases.append(
            {
                "title": uc.title,
                "body_md": _format_use_case_md(uc, spec, include_mermaid=False),
                "diagram_mermaid": decorated,
                "blueprint_pattern": uc.blueprint_pattern.value,
            }
        )

    # Fact-check breakdown — counts for the Le Chat PieChart. Buckets:
    #   supported  — passed=True, no rescue (anchored in evidence pool directly)
    #   rescued    — passed=True, rescue_tier set (web-verify saved it)
    #   rewritten  — qualified_out=True (final-qualify rewrote to qualitative)
    #   unsupported— passed=False (judge rejected or no source found)
    fact_check_breakdown = {"supported": 0, "rescued": 0, "rewritten": 0, "unsupported": 0}
    for c in report.quality.fact_check:
        if c.qualified_out:
            fact_check_breakdown["rewritten"] += 1
        elif c.passed and c.rescue_tier:
            fact_check_breakdown["rescued"] += 1
        elif c.passed:
            fact_check_breakdown["supported"] += 1
        else:
            fact_check_breakdown["unsupported"] += 1

    return {
        "executive_summary": "\n".join(exec_parts),
        "use_cases": use_cases,
        "verification_md": _quality_footer_md(report.quality, report.meta_review),
        "fact_check_breakdown": fact_check_breakdown,
        "confidence": (
            report.meta_review.confidence if report.meta_review is not None else None
        ),
        "sales_engineer_ready": (
            report.meta_review.sales_engineer_ready
            if report.meta_review is not None
            else False
        ),
        "cross_cutting_concern": (
            report.meta_review.cross_cutting_concern
            if report.meta_review is not None
            else None
        ),
        "pass_rate": report.quality.fact_check_pass_rate,
    }


def render_report_to_markdown(report: Report) -> str:
    """Single-document Markdown rendering — for CLI / standalone web app."""
    parts: list[str] = []
    # Draft banner — when meta-eval flagged the report not-ready or
    # confidence is low, surface that AT THE TOP rather than burying it
    # in the quality footer. Honest signal beats hidden caveats; the
    # reviewer still sees every use case + the rejection chain below.
    if report.meta_review is not None and (
        not report.meta_review.sales_engineer_ready
        or report.meta_review.confidence < 0.70
    ):
        parts.append(_draft_banner_md(report))
        parts.append("")
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
                Badge(
                    variant=("warning" if uc.time_to_value.basis.value == "ballpark_assumption" else "default"),
                    children=(
                        f"TTV: ~{uc.time_to_value.estimate} (estimated)"
                        if uc.time_to_value.basis.value == "ballpark_assumption"
                        else f"TTV: {uc.time_to_value.estimate}"
                    ),
                ),
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
