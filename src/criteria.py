"""The five scoring criteria, with positive and negative examples.

Encoding criteria in code (rather than only in prompts) makes them auditable,
testable, and reusable across the scorer prompt, the meta-evaluator prompt, and
the gold-example eval harness. See docs/methodology.md for the full design.

The negative example for each criterion ("here is what bad looks like") is
deliberately included — research consistently shows it's a stronger signal to
LLMs than positive anchoring alone.
"""

from __future__ import annotations

from pydantic import BaseModel


class Criterion(BaseModel):
    key: str
    display_name: str
    short_description: str
    long_description: str
    positive_example: str
    negative_examples: list[str]
    default_weight: float = 0.2


RELEVANCE = Criterion(
    key="relevance",
    display_name="Relevance",
    short_description="Touches a core business workflow at scale, with the data and stated priorities to make it work.",
    long_description=(
        "A use case is relevant if it touches a core business workflow that the company runs at scale, "
        "the company has the data assets needed to make it work, and it addresses a known strategic "
        "priority or pain point."
    ),
    positive_example=(
        "Veolia operates at scale across thousands of municipal water networks. AI-assisted leak "
        "detection across the smart-meter network is relevant — it touches core water-utility "
        "operations, exploits an existing data asset (smart meter telemetry), and aligns with stated "
        "priorities around resource efficiency."
    ),
    negative_examples=[
        "An AI-powered HR assistant for Veolia. Possible? Yes. But it doesn't touch the company's "
        "core business and could equally be recommended to any large employer. Low relevance.",
    ],
)


ICONIC_POTENTIAL = Criterion(
    key="iconic_potential",
    display_name="Iconic Potential",
    short_description=(
        "Visibly distinctive for this company AND not already done by them (hard gate on already-done-here)."
    ),
    long_description=(
        "A use case is iconic if it would be visibly associated with this company specifically "
        "— not a generic AI-could-help-any-business idea — AND the company is not currently doing it "
        "or anything substantially similar. It exploits something distinctive: their brand, their "
        "data, their unique market position. A customer or employee should react with 'this is so "
        "[Company]' if shown the use case. Substantial overlap with an existing initiative HARD-CAPS "
        "this score at 1-2 regardless of other merits."
    ),
    positive_example=(
        "For L'Oréal — an AI virtual try-on assistant trained on L'Oréal's multi-decade catalog of "
        "skin-tone and product data, embedded in their flagship retail experiences (assuming they "
        "haven't already deployed this)."
    ),
    negative_examples=[
        "For L'Oréal — a chatbot for customer service. Could be any consumer brand. Low iconic.",
        "For L'Oréal — an AI personalized skincare recommendation engine, when L'Oréal has publicly "
        "deployed exactly this. Hard disqualifier; iconic score capped at 1-2 regardless.",
    ],
)


ESTIMATED_IMPACT = Criterion(
    key="estimated_impact",
    display_name="Estimated Impact",
    short_description="Measurable financial or strategic value, anchored to peer deployments.",
    long_description=(
        "A use case is high-impact if it has measurable financial impact (cost saved, revenue "
        "unlocked, time saved at scale) or clear strategic value (defensible moat, regulatory "
        "advantage, brand differentiation), large enough to justify GenAI's complexity and operating "
        "cost. Time-to-value and cost-tier estimates within this dimension are anchored to the "
        "precedent corpus — 'unknown' is a valid output if no comparable precedent exists."
    ),
    positive_example=(
        "For BNP Paribas — an AI-powered KYC document review system that reduces onboarding time "
        "per corporate client from weeks to days across 5+ million clients. Quantifiable across a "
        "scale that justifies the build."
    ),
    negative_examples=[
        "A small efficiency improvement on an internal tool used by 20 employees. Real but too "
        "small to matter at the company's scale. Low impact.",
    ],
)


FEASIBILITY = Criterion(
    key="feasibility",
    display_name="Feasibility",
    short_description="Shippable with current GenAI tech in a customer engagement timeline.",
    long_description=(
        "A use case is feasible if it is shippable with current GenAI technology within a "
        "reasonable engagement timeline (weeks to months, not years), without requiring fundamental "
        "research breakthroughs. Considers data availability, technical maturity, regulatory "
        "clearability, and integration complexity."
    ),
    positive_example=(
        "A retrieval-augmented assistant over already-digitized policy documents — well-understood "
        "pattern, real precedents, ships in weeks."
    ),
    negative_examples=[
        "Real-time multi-agent autonomous decision-making across regulated financial transactions "
        "— possible in research, not realistic to ship in a customer engagement.",
    ],
)


MISTRAL_SUITABILITY = Criterion(
    key="mistral_suitability",
    display_name="Mistral Suitability",
    short_description="Leans into Mistral's distinctive strengths over other LLM providers.",
    long_description=(
        "A use case is Mistral-suitable if it leans into something Mistral does distinctively well "
        "— not just 'an LLM does this,' but specifically 'Mistral is the right LLM provider here.' "
        "Drivers: data sovereignty (EU-hosted, on-premise deployable), open-weight options "
        "(fine-tuning and self-hosting flexibility), multilingual capability (strong in European "
        "languages), competitive cost-quality tradeoffs, customer alignment (European companies, "
        "regulated sectors, companies skeptical of US hyperscaler lock-in). A use case high on the "
        "other four but neutral here is still a real use case, just not necessarily a Mistral one."
    ),
    positive_example=(
        "For BNP Paribas — an EU-hosted, on-premise-deployable financial-document assistant fine-"
        "tuned on French regulatory text. Mistral's open-weight Large model + EU sovereignty + "
        "multilingual French strength make this distinctly a Mistral story."
    ),
    negative_examples=[
        "A generic chatbot that any frontier provider could host equally well — neutral on Mistral "
        "suitability.",
    ],
)


CRITERIA: list[Criterion] = [
    RELEVANCE,
    ICONIC_POTENTIAL,
    ESTIMATED_IMPACT,
    FEASIBILITY,
    MISTRAL_SUITABILITY,
]


CRITERIA_BY_KEY: dict[str, Criterion] = {c.key: c for c in CRITERIA}


def render_criteria_for_prompt() -> str:
    """Render all five criteria as a markdown block suitable for embedding in LLM prompts.

    Includes positive and negative examples — both anchors are needed for
    consistent scoring.
    """

    lines: list[str] = []
    for i, c in enumerate(CRITERIA, start=1):
        lines.append(f"## {i}. {c.display_name} ({c.key})")
        lines.append(c.long_description)
        lines.append("")
        lines.append(f"**Positive example:** {c.positive_example}")
        lines.append("")
        lines.append("**Negative examples:**")
        for neg in c.negative_examples:
            lines.append(f"- {neg}")
        lines.append("")
    return "\n".join(lines).strip()
