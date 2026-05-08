"""GenAIUseCaseWorkflow — the deterministic InteractiveWorkflow class.

Per Mistral Workflows rules (CLAUDE.md, architecture.md):
- This class MUST be deterministic. No `datetime.now()`, no `random.random()`,
  no direct I/O (HTTP / DB / file reads / LLM calls). All side effects live
  in activities, which the runtime invokes with retry + timeout + replay.
- Workflow code is pure orchestration: branching, sequencing, calling
  activities, optionally waiting for user input.

The workflow:
  Step 0 (optional, conversational) — clarification: focus area + criteria weights
  Step 1 — research (parallel sub-tasks + synthesis)
  Confidence gate — graceful refusal if confidence too low and unverified
  Step 2 — retrieve top-k peer precedents
  Step 3 — generate 12 candidates (with one diversity-regen if needed)
  Step 4 — score with self-consistency
  Step 5 — per-candidate verification of top-3
  Step 6 — selection + enrichment (top-3 final)
  Step 7 — meta-evaluation (with optional weakest-use-case regeneration)
  Render — Rich UI Components composition → ChatAssistantWorkflowOutput

Returns ChatAssistantWorkflowOutput so the workflow can publish as a Le Chat
assistant.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

import mistralai.workflows as workflows
import mistralai.workflows.plugins.mistralai as workflows_mistralai

from src.activities.compute_signals import compute_quality_signals_activity
from src.activities.generate import generate_candidates_activity
from src.activities.meta_evaluate import meta_evaluate_activity
from src.activities.research import (
    enrich_company_context_activity,
    research_company_activity,
)
from src.activities.retrieve import retrieve_precedents_activity
from src.activities.score import score_candidates_activity
from src.activities.select_enrich import select_and_enrich_activity
from src.activities.verify_per_candidate import verify_top_candidates_activity
from src.config import settings
from src.models import (
    CriteriaWeights,
    FocusArea,
    Report,
    WorkflowInput,
    WorkflowStatus,
)
from src.ui.render import render_report_to_components, render_report_to_markdown

logger = logging.getLogger(__name__)


def _parse_customization(text: str, params: WorkflowInput) -> WorkflowInput:
    """Parse a free-form customization string from the Step 0 ChatInput and
    return updated WorkflowInput. Recognized patterns:

      - One of {general, operations, customer, sustainability} mentioned
        anywhere → sets focus_area
      - Five space- or comma-separated floats anywhere (each in [0, 1]) →
        sets criteria weights in order: relevance, iconic, impact,
        feasibility, mistral

    Returns the original params unchanged if parsing finds nothing usable.
    Pure function — safe to call from workflow code.
    """
    text_lower = text.lower().strip()
    if not text_lower or text_lower in {"default", "go", "ok", "skip", "no"}:
        return params

    new_params = params.model_copy(deep=True)
    for fa in FocusArea:
        if fa.value in text_lower:
            new_params.focus_area = fa
            break

    floats = re.findall(r"\d*\.\d+|\d+", text_lower)
    if len(floats) >= 5:
        try:
            f = [float(x) for x in floats[:5]]
            if all(0.0 <= w <= 1.0 for w in f):
                new_params.weights = CriteriaWeights(
                    relevance=f[0],
                    iconic_potential=f[1],
                    estimated_impact=f[2],
                    feasibility=f[3],
                    mistral_suitability=f[4],
                )
        except (ValueError, TypeError):
            pass
    return new_params


def _build_refusal_output(
    company_name: str, signals_attempted: list[str]
) -> workflows_mistralai.ChatAssistantWorkflowOutput:
    msg = (
        f"I couldn't find enough information about **{company_name}** to confidently "
        f"generate GenAI use cases. To help me give you a useful report, please provide "
        f"more context:\n\n"
        f"- What industry / sub-industry does {company_name} operate in?\n"
        f"- Business model (B2B / B2C / B2G / mixed)?\n"
        f"- Where does the company operate primarily?\n"
        f"- Any stated strategic priorities you'd like the use cases to address?\n\n"
        f"Or try a different company name (the legal name or parent brand)."
    )
    return workflows_mistralai.ChatAssistantWorkflowOutput(
        content=[workflows_mistralai.TextOutput(text=msg)]
    )


@workflows.workflow.define(
    name="genai-usecase-generator",
    workflow_display_name="GenAI Use Case Generator",
    workflow_description=(
        "Generates 3 relevant, iconic, high-impact GenAI use cases for a specific "
        "company, scored against a five-criteria rubric and verified against the "
        "company's existing AI initiatives."
    ),
    execution_timeout=timedelta(minutes=15),
)
class GenAIUseCaseWorkflow(workflows.InteractiveWorkflow):
    """Deterministic orchestration only — no I/O, no datetime, no random."""

    def __init__(self) -> None:
        super().__init__()
        self.current_step: str = "initialized"
        self.company_name: str | None = None
        self.progress_percent: float = 0.0

    @workflows.workflow.query(name="get_status", description="Get current workflow progress")
    def get_status(self) -> WorkflowStatus:
        return WorkflowStatus(
            company=self.company_name,
            current_step=self.current_step,
            progress_percent=self.progress_percent,
        )

    @workflows.workflow.entrypoint
    async def run(self, params: WorkflowInput) -> workflows_mistralai.ChatAssistantWorkflowOutput:
        self.company_name = params.company_name

        # Step 0 — optional clarification checkpoint.
        # Two-stage interactive flow using Mistral Workflows' wait_for_input:
        #   (a) ConfirmationInput — does the user want defaults or to customize?
        #   (b) If 'custom', ChatInput collects free-form focus + weights.
        # Skipping the form is fine; the workflow proceeds with whatever
        # WorkflowInput was passed in.
        self.current_step = "clarification"
        self.progress_percent = 2.0
        try:
            confirm = await self.wait_for_input(
                workflows_mistralai.ConfirmationInput(
                    options=[
                        ("default", "Use defaults"),
                        ("custom", "Customize focus + criteria weights"),
                    ],
                    description=(
                        f"Generating GenAI use cases for **{params.company_name}**.\n\n"
                        f"Default config: focus = `{params.focus_area.value}`, "
                        f"all five criteria weighted equally (0.20 each).\n\n"
                        f"Click **Use defaults** to proceed, or **Customize** to "
                        f"adjust focus area and weights."
                    ),
                )
            )
            if getattr(confirm, "choice", "default") == "custom":
                chat_in = await self.wait_for_input(
                    workflows_mistralai.ChatInput(
                        prompt=(
                            "Type focus area + weights, e.g.: "
                            "'operations, weights 0.3 0.3 0.2 0.1 0.1'  "
                            "(focus options: general / operations / customer / "
                            "sustainability; weights are 5 floats 0-1 in order: "
                            "relevance, iconic, impact, feasibility, mistral)"
                        ),
                    )
                )
                params = _parse_customization(getattr(chat_in, "message", "") or "", params)
        except Exception as e:  # noqa: BLE001
            # If the runtime doesn't support interactive input (e.g. CLI
            # invocation, older worker), proceed silently with defaults.
            logger.info("workflow: Step 0 skipped (%s) — using passed params", type(e).__name__)

        self.current_step = "research"
        self.progress_percent = 5.0

        ctx, ledger = await research_company_activity(params.company_name, params.research_depth)

        self.current_step = "enrich_context"
        self.progress_percent = 12.0
        ctx, ledger = await enrich_company_context_activity(ctx, ledger)
        logger.info("workflow: evidence ledger seeded with %d entries", len(ledger.entries))

        # Confidence gate (after the context-completion pass)
        confidence_ok = (
            ctx.meta.research_confidence >= settings.research_confidence_threshold
            or ctx.meta.is_verified
            or len(ctx.existing_ai_initiatives) > 0
        )
        if not confidence_ok:
            self.current_step = "refused"
            return _build_refusal_output(params.company_name, ctx.meta.research_sources)

        self.current_step = "retrieve"
        self.progress_percent = 20.0
        retrieved = await retrieve_precedents_activity(ctx, settings.top_k_precedents)

        self.current_step = "generate"
        self.progress_percent = 35.0
        batch, ledger = await generate_candidates_activity(
            ctx, retrieved, params.focus_area.value, True, ledger=ledger
        )

        self.current_step = "score"
        self.progress_percent = 55.0
        scored = await score_candidates_activity(batch, ctx, params.weights)

        self.current_step = "verify"
        self.progress_percent = 70.0
        top_3_scored = scored.scored[:3]
        verified, ledger = await verify_top_candidates_activity(
            top_3_scored, ctx, params.company_name, ledger=ledger
        )

        self.current_step = "enrich"
        self.progress_percent = 80.0
        enriched_uses, rejected = await select_and_enrich_activity(
            scored, verified, ctx, retrieved=retrieved, ledger=ledger
        )

        self.current_step = "meta_evaluate"
        self.progress_percent = 88.0
        review, fact_claims = await meta_evaluate_activity(
            enriched_uses, rejected, ctx, retrieved=retrieved, ledger=ledger
        )

        self.current_step = "quality_signals"
        self.progress_percent = 93.0
        signals = await compute_quality_signals_activity(enriched_uses, ctx, fact_claims)

        # Note: targeted regeneration of the weakest use case on low confidence is
        # documented in architecture.md but deliberately scoped out of MVP — adding
        # one extra round of enrichment here would compound latency without
        # changing the architectural shape. Returns the report as-is, with the
        # meta-evaluator's confidence and weakest-use-case-id surfaced honestly
        # in the quality footer.

        self.current_step = "render"
        self.progress_percent = 98.0
        intro = (
            f"GenAI use cases for {ctx.identity.name}: three customer-ready proposals "
            f"scored against the five-criteria rubric and verified against {len(ctx.existing_ai_initiatives)} "
            f"existing AI initiatives discovered during research."
        )
        report = Report(
            company=ctx,
            weights_used=params.weights,
            focus_area=params.focus_area,
            research_depth=params.research_depth,
            top_use_cases=enriched_uses,
            rejected_appendix=rejected,
            quality=signals,
            meta_review=review,
            intro_text=intro,
        )

        # Try Rich UI Components first; fall back to Markdown if the plugin
        # path isn't available in this environment.
        components = render_report_to_components(report)
        if components is not None:
            content_blocks: list[object] = [
                workflows_mistralai.ResourceOutput(
                    resource=workflows_mistralai.UIComponentResource(component=components)
                )
            ]
        else:
            content_blocks = [
                workflows_mistralai.TextOutput(text=render_report_to_markdown(report))
            ]

        self.current_step = "complete"
        self.progress_percent = 100.0
        return workflows_mistralai.ChatAssistantWorkflowOutput(content=content_blocks)
