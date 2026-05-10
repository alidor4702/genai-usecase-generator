"""GenAIUseCaseWorkflow — the deterministic InteractiveWorkflow class.

Per Mistral Workflows rules (CLAUDE.md, architecture.md):
- This class MUST be deterministic. No `datetime.now()`, no `random.random()`,
  no direct I/O (HTTP / DB / file reads / LLM calls). All side effects live
  in activities, which the runtime invokes with retry + timeout + replay.
- Workflow code is pure orchestration: branching, sequencing, calling
  activities, optionally waiting for user input.

The workflow:
  Le Chat input form — auto-rendered from `WorkflowInput` (company + focus area)
  Step 1 — research (parallel sub-tasks + synthesis)
  Confidence gate — graceful refusal if confidence too low and unverified
  Step 2 — retrieve top-k peer precedents
  Step 3 — generate N candidates (default N=8, with one diversity-regen if needed)
  Step 4 — score with self-consistency
  Step 5 — per-candidate verification of top-3
  Step 6 — selection + enrichment (top-3 final)
  Step 7 — meta-evaluation + web-verify rescue + source-judge + final-qualify
  Render — markdown + mermaid canvases → ChatAssistantWorkflowOutput

Returns ChatAssistantWorkflowOutput so the workflow can publish as a Le Chat
assistant.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta

import mistralai.workflows as workflows
import mistralai.workflows.plugins.mistralai as workflows_mistralai
import temporalio.workflow as _temporal_workflow

# Mistral Workflows uses Temporal under the hood. Temporal's workflow sandbox
# loads workflow.py inside a restricted namespace at definition time and
# blocks any module touching non-deterministic stuff (urllib.request, socket,
# datetime.now, etc.). Our activity modules transitively import httpx →
# urllib.request, which triggers the block.
#
# Standard fix: wrap activity-module imports in
# `temporalio.workflow.unsafe.imports_passed_through()` so the sandbox
# treats them as trusted. The activity functions are still safe to reference
# from the workflow class — they only execute side effects when the runtime
# invokes them on the activity executor (outside the sandbox), not when the
# workflow class is loaded.
with _temporal_workflow.unsafe.imports_passed_through():
    from src.activities.compute_signals import compute_quality_signals_activity
    from src.activities.final_qualify import final_qualitative_replacement_activity
    from src.activities.generate import generate_candidates_activity
    from src.activities.meta_evaluate import meta_evaluate_activity
    from src.activities.render_report import render_report_activity
    from src.activities.research import (
        enrich_company_context_activity,
        research_company_activity,
    )
    from src.activities.retrieve import retrieve_precedents_activity
    from src.activities.score import score_candidates_activity
    from src.activities.select_enrich import select_and_enrich_activity
    from src.activities.source_judge import judge_claim_sources_activity
    from src.activities.verify_per_candidate import verify_top_candidates_activity
    from src.activities.web_verify import web_verify_unsupported_claims_activity
    from src.config import settings
    from src.ui.le_chat_components import build_report_component_tree

# Pure typed-models module — no I/O, safe at definition time
from src.models import (
    CriteriaWeights,
    FocusArea,
    WorkflowInput,
    WorkflowStatus,
)

logger = logging.getLogger(__name__)


def _md_canvas(
    title: str, content: str
) -> workflows_mistralai.ResourceOutput:
    """Build a text/markdown ResourceOutput chunk for Le Chat."""
    return workflows_mistralai.ResourceOutput(
        resource=workflows_mistralai.CanvasResource(
            canvas=workflows_mistralai.CanvasPayload(
                type="text/markdown",
                title=title,
                content=content,
            ),
        ),
    )


_MERMAID_STYLING_LINE_RX = re.compile(r"^\s*(classDef|class)\s+.*$", re.MULTILINE)


def _compact_mermaid_for_chat(diagram: str) -> str:
    """Strip `classDef` and `class` styling lines from a mermaid diagram.

    The web app's `MermaidDiagram` component honours those lines for
    blueprint-pattern colour coding, but Le Chat's mermaid canvas
    renders the diagram at a fixed size — extra lines push more nodes
    onto the canvas and force scrolling. Stripping the decorative
    styling removes ~3 lines per diagram and lets the structural
    flowchart render compactly without scroll. The full styled
    diagram still flows to CLI / web via the canonical markdown.
    """
    stripped = _MERMAID_STYLING_LINE_RX.sub("", diagram)
    return "\n".join(line for line in stripped.splitlines() if line.strip())


def _mermaid_canvas(
    title: str, content: str
) -> workflows_mistralai.ResourceOutput:
    """Build a mermaid ResourceOutput chunk so Le Chat renders an actual
    diagram instead of raw flowchart syntax. Strips decorative
    styling for compactness."""
    return workflows_mistralai.ResourceOutput(
        resource=workflows_mistralai.CanvasResource(
            canvas=workflows_mistralai.CanvasPayload(
                type="mermaid",
                title=title,
                content=_compact_mermaid_for_chat(content),
            ),
        ),
    )




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

        # Apply the per-run tier override. Activities downstream read
        # `settings.tier` so we mutate the singleton at the start of
        # the run. Concurrent runs with different tiers will race on
        # this global; that's acceptable for the take-home's
        # single-user usage and would migrate to a context-var in
        # production.
        if params.tier != settings.tier:
            logger.info("workflow: tier override %s → %s", settings.tier.value, params.tier.value)
            settings.tier = params.tier

        # No second clarification prompt — the entry form already
        # collects everything we need. Earlier versions added an
        # explicit `ConfirmationInput("Use defaults / Customize")`
        # here, but it duplicated the form the user just filled and
        # made the chat read like a multi-step bureaucracy.

        # Le Chat progress pattern — `workflows.task_from` with
        # `ChatAssistantWorkingTask`. Each phase emits ONE task block
        # that renders inline in the conversation as a "thinking"
        # indicator. The earlier TodoList experiment rendered as a
        # checklist in a sidebar/composer surface — wrong place;
        # users want the steps inside the chat thread.

        # ── Research ──────────────────────────────────────────────────
        self.current_step = "research"
        self.progress_percent = 5.0
        async with workflows.task_from(
            state=workflows_mistralai.ChatAssistantWorkingTask(
                title="Researching the company",
                content="Reading Wikipedia + recent news + existing AI initiatives",
            )
        ) as task:
            ctx, ledger, bundle = await research_company_activity(
                params.company_name, params.research_depth
            )
            ctx, ledger = await enrich_company_context_activity(ctx, ledger, bundle)
            logger.info(
                "workflow: evidence ledger seeded with %d entries", len(ledger.entries)
            )
            await task.update_state(updates={
                "title": "Research complete",
                "content": (
                    f"{len(ledger.entries)} ledger entries · "
                    f"confidence {ctx.meta.research_confidence:.2f}"
                ),
            })

        # Confidence gate after the context-completion pass.
        confidence_ok = (
            ctx.meta.research_confidence >= settings.research_confidence_threshold
            or ctx.meta.is_verified
            or len(ctx.existing_ai_initiatives) > 0
        )
        if not confidence_ok:
            self.current_step = "refused"
            return _build_refusal_output(
                params.company_name, ctx.meta.research_sources
            )

        # ── Retrieve ──────────────────────────────────────────────────
        self.current_step = "retrieve"
        self.progress_percent = 20.0
        async with workflows.task_from(
            state=workflows_mistralai.ChatAssistantWorkingTask(
                title="Retrieving peer precedents",
                content="Searching the 2,150-deployment corpus for industry-similar examples",
            )
        ) as task:
            retrieved = await retrieve_precedents_activity(
                ctx, settings.top_k_precedents
            )
            await task.update_state(updates={
                "title": "Retrieved peer precedents",
                "content": f"{len(retrieved.items)} precedents from the corpus",
            })

        # ── Generate ──────────────────────────────────────────────────
        self.current_step = "generate"
        self.progress_percent = 35.0
        async with workflows.task_from(
            state=workflows_mistralai.ChatAssistantWorkingTask(
                title="Generating candidate use cases",
                content="Mistral Medium drafting use cases grounded in the company's data + priorities",
            )
        ) as task:
            batch, ledger = await generate_candidates_activity(
                ctx, retrieved, params.focus_area.value, True, ledger=ledger
            )
            await task.update_state(updates={
                "title": "Candidates generated",
                "content": f"{len(batch.candidates)} candidates",
            })

        # ── Score ─────────────────────────────────────────────────────
        self.current_step = "score"
        self.progress_percent = 55.0
        async with workflows.task_from(
            state=workflows_mistralai.ChatAssistantWorkingTask(
                title="Scoring against 5 criteria",
                content="Self-consistency: relevance · iconic potential · impact · feasibility · Mistral fit",
            )
        ) as task:
            scored = await score_candidates_activity(batch, ctx, params.weights)
            await task.update_state(updates={
                "title": "Candidates scored",
                "content": f"top aggregate score: {scored.scored[0].aggregate_score:.2f}",
            })

        # ── Verify ────────────────────────────────────────────────────
        self.current_step = "verify"
        self.progress_percent = 70.0
        async with workflows.task_from(
            state=workflows_mistralai.ChatAssistantWorkingTask(
                title="Verifying top candidates against the live web",
                content="Targeted Tavily searches to check what the company already does",
            )
        ) as task:
            top_3_scored = scored.scored[:3]
            verified, ledger = await verify_top_candidates_activity(
                top_3_scored, ctx, params.company_name, ledger=ledger
            )
            await task.update_state(updates={
                "title": "Verification complete",
                "content": f"{len(verified.results)} candidates verified",
            })

        # ── Enrich ────────────────────────────────────────────────────
        self.current_step = "enrich"
        self.progress_percent = 80.0
        async with workflows.task_from(
            state=workflows_mistralai.ChatAssistantWorkingTask(
                title="Writing customer-ready prose",
                content="Mistral Large 3 drafting the top-3 with descriptions, blueprints, risks",
            )
        ) as task:
            enriched_uses, rejected = await select_and_enrich_activity(
                scored, verified, ctx, retrieved=retrieved, ledger=ledger
            )
            await task.update_state(updates={
                "title": "Top-3 enriched",
                "content": f"{len(enriched_uses)} customer-ready use cases",
            })

        # ── Review (bundles meta-eval + web-verify + source-judge + final-qualify + quality-signals) ──
        async with workflows.task_from(
            state=workflows_mistralai.ChatAssistantWorkingTask(
                title="Senior-reviewer fact-check",
                content="Per-claim verification, web-verify rescue, source-judge, qualitative rewrite",
            )
        ) as task:
            self.current_step = "meta_evaluate"
            self.progress_percent = 88.0
            review, fact_claims = await meta_evaluate_activity(
                enriched_uses, rejected, ctx, retrieved=retrieved, ledger=ledger
            )

            self.current_step = "web_verify"
            self.progress_percent = 90.0
            review, fact_claims, ledger = await web_verify_unsupported_claims_activity(
                review, fact_claims, ctx.identity.name, ledger
            )

            self.current_step = "source_judge"
            self.progress_percent = 92.0
            review, fact_claims, enriched_uses_post = await judge_claim_sources_activity(
                review, fact_claims, ledger, enriched_uses
            )
            if enriched_uses_post is not None:
                enriched_uses = enriched_uses_post

            self.current_step = "final_qualify"
            self.progress_percent = 94.0
            enriched_uses, fact_claims = await final_qualitative_replacement_activity(
                enriched_uses, fact_claims
            )

            self.current_step = "quality_signals"
            self.progress_percent = 96.0
            signals = await compute_quality_signals_activity(
                enriched_uses, ctx, fact_claims
            )

            passed = sum(1 for c in fact_claims if c.passed and not c.qualified_out)
            in_scope = sum(1 for c in fact_claims if not c.qualified_out)
            pass_rate = passed / max(1, in_scope)
            await task.update_state(updates={
                "title": "Review complete",
                "content": (
                    f"fact-check pass rate {pass_rate:.0%} ({passed}/{in_scope}) · "
                    f"confidence {review.confidence:.2f} · "
                    f"{'SE-ready' if review.sales_engineer_ready else 'draft'}"
                ),
            })

        # ── Final render via an activity ──────────────────────────────
        # Renders typed Report → markdown + structured chunks. Lives in
        # an activity because the workflow sandbox re-imports src.models
        # in its own namespace; a CompanyContext returned from a prior
        # activity has a different class identity than the one this
        # workflow class imports, so Pydantic v2 strict isinstance fails
        # on Report(...) construction here. The activity has no sandbox
        # isolation so the types match. Activity returns a plain dict
        # (not a typed model) so the result crosses the boundary cleanly.
        self.current_step = "render"
        self.progress_percent = 98.0
        company_name = ctx.identity.name
        intro = (
            f"GenAI use cases for **{company_name}** — three customer-ready proposals "
            f"scored against the five-criteria rubric and verified against "
            f"{len(ctx.existing_ai_initiatives)} existing AI initiative(s) discovered during research."
        )
        full_md = ""
        chunks: dict[str, object] | None = None
        try:
            render_result = await render_report_activity(
                ctx,
                params.weights,
                params.focus_area,
                params.research_depth,
                enriched_uses,
                rejected,
                signals,
                review,
                intro,
            )
            full_md = str(render_result.get("full_markdown", ""))
            raw_chunks = render_result.get("chunks")
            if isinstance(raw_chunks, dict):
                chunks = raw_chunks
            logger.info(
                "workflow: render_report_activity returned %d bytes · chunks=%s",
                len(full_md),
                bool(chunks),
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("workflow: render_report_activity FAILED — %s", e)
            full_md = f"{intro}\n\n_(render activity failed: `{type(e).__name__}: {e}`)_"

        self.current_step = "complete"
        self.progress_percent = 100.0

        # Le Chat output — Rich UI Components tree as the primary
        # rendering, plus separate mermaid canvases for the per-use-case
        # architecture diagrams (the UI Component library has no mermaid
        # renderer, so diagrams ship alongside).
        #
        # Tree: Column [
        #   Alert (only if confidence < 0.70 or not SE-ready)
        #   Card "Executive summary" with Confidence/PassRate/Tier badges
        #   Card per use case with body + Impact/Cost/Complexity/TTV badges
        #   Card "Quality signals" with PieChart of fact-check breakdown
        # ]
        #
        # If chunks is None (render_report_activity failed), fall back to
        # a single text/markdown canvas with the bare error message.
        chat_intro = f"Report ready for **{company_name}**."
        output_chunks: list[
            workflows_mistralai.TextOutput | workflows_mistralai.ResourceOutput
        ] = [workflows_mistralai.TextOutput(text=chat_intro)]

        if chunks is not None:
            confidence = chunks.get("confidence")
            pass_rate = chunks.get("pass_rate")
            fact_check_breakdown = chunks.get("fact_check_breakdown")
            sales_engineer_ready = bool(chunks.get("sales_engineer_ready", False))
            cross_cutting = chunks.get("cross_cutting_concern")
            try:
                tree = build_report_component_tree(
                    company_name=company_name,
                    chunks=chunks,
                    confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
                    pass_rate=float(pass_rate) if isinstance(pass_rate, (int, float)) else None,
                    fact_check_breakdown=(
                        fact_check_breakdown if isinstance(fact_check_breakdown, dict) else None
                    ),
                    sales_engineer_ready=sales_engineer_ready,
                    cross_cutting_concern=str(cross_cutting) if cross_cutting else None,
                    tier=settings.tier.value,
                )
                output_chunks.append(
                    workflows_mistralai.ResourceOutput(
                        resource=workflows_mistralai.UIComponentResource(component=tree)
                    )
                )
                logger.info("workflow: built Rich UI Components tree for report")
            except Exception as e:  # noqa: BLE001
                logger.exception("workflow: UI Component tree FAILED — fallback to markdown: %s", e)
                output_chunks.append(
                    _md_canvas(f"GenAI use cases — {company_name}", full_md)
                )

            # Per-use-case mermaid diagrams as separate canvas chunks —
            # the UI Component library doesn't have a mermaid renderer,
            # so each architecture diagram ships as a focused canvas
            # next to the structured component tree.
            use_case_chunks = chunks.get("use_cases") or []
            if isinstance(use_case_chunks, list):
                for i, uc in enumerate(use_case_chunks):
                    if not isinstance(uc, dict):
                        continue
                    diagram = str(uc.get("diagram_mermaid", ""))
                    if diagram:
                        output_chunks.append(
                            _mermaid_canvas(
                                f"Use case {i + 1} — architecture", diagram
                            )
                        )
        else:
            output_chunks.append(
                _md_canvas(f"GenAI use cases — {company_name}", full_md)
            )

        try:
            await workflows_mistralai.send_assistant_message(output_chunks)
            logger.info(
                "workflow: send_assistant_message OK — pushed %d chunks", len(output_chunks)
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("workflow: send_assistant_message FAILED — %s", e)

        # ── Canvas editing for human-in-the-loop refinement ─────────
        # After the report renders, ship a small editable canvas
        # that invites the user to flag changes. The CanvasInput
        # primitive lets the user edit the canvas directly in Le Chat
        # and submit; we receive the edited content back. Wait up to
        # 90 seconds — if the user ignores the affordance, the
        # workflow finishes without the feedback loop.
        feedback_uri = f"file://canvas/feedback-{company_name.lower().replace(' ', '-').replace(',', '')}"
        feedback_canvas = workflows_mistralai.CanvasResource(
            uri=feedback_uri,
            canvas=workflows_mistralai.CanvasPayload(
                type="text/markdown",
                title=f"Refine the report — {company_name}",
                content=(
                    f"# Want to refine any of the use cases?\n\n"
                    f"Edit this canvas with what you'd like changed, then submit. "
                    f"Examples:\n\n"
                    f"- _Replace use case 2 with one focused on retail-specific data privacy._\n"
                    f"- _Tighten the example output of use case 1 — too much illustrative data._\n"
                    f"- _The Concordis claim in use case 3 wasn't supported — drop the use case._\n\n"
                    f"Submit when ready, or wait 90 seconds to skip and finish.\n\n"
                    f"---\n\n"
                    f"_({company_name} report ready · {len(output_chunks)} chunks shipped above)_"
                ),
            ),
        )
        try:
            await workflows_mistralai.send_assistant_message(
                "Want to refine any of the use cases? Edit the canvas below.",
                canvas=feedback_canvas,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("workflow: feedback canvas push FAILED — %s", e)

        feedback_text: str | None = None
        try:
            feedback = await self.wait_for_input(
                workflows_mistralai.CanvasInput(
                    canvas_uri=feedback_uri,
                    prompt="What would you like to refine? (or leave blank and submit to skip)",
                ),
                timeout=90.0,
            )
            edited_canvas = getattr(feedback, "canvas", None)
            edited_content = (
                str(getattr(edited_canvas, "content", "")) if edited_canvas else ""
            )
            chat_input = getattr(feedback, "chatInput", None)
            chat_msg = str(getattr(chat_input, "message", "")) if chat_input else ""
            feedback_text = (
                f"chat: {chat_msg!r}, canvas-edited: {len(edited_content)} chars"
            )
            logger.info("workflow: canvas feedback received — %s", feedback_text)

            # Acknowledge the feedback. A v2 of this would parse the
            # edits, identify which use case was touched, and trigger
            # a targeted regen via select_and_enrich_activity. For
            # now we surface the human-in-the-loop primitive working
            # end-to-end — the parsing + regen wiring is documented
            # as a planned enhancement in architecture.md.
            ack_lines = [
                f"Got your feedback for **{company_name}** — thanks.",
            ]
            if chat_msg:
                ack_lines.append(f"\n**Your note:** {chat_msg}")
            if edited_content:
                ack_lines.append(
                    f"\n_Canvas edit captured ({len(edited_content)} chars). "
                    f"Targeted regen wiring is documented as a planned enhancement; "
                    f"this run demonstrates the CanvasInput primitive end-to-end._"
                )
            await workflows_mistralai.send_assistant_message("\n".join(ack_lines))
        except asyncio.TimeoutError:
            logger.info("workflow: canvas feedback timeout (90s) — no edits provided, finishing")
        except Exception as e:  # noqa: BLE001
            # CanvasInput unsupported on this runtime / surface — log
            # and continue. The report still renders; only the
            # human-in-the-loop affordance is missing.
            logger.info(
                "workflow: canvas feedback skipped (%s) — finishing without refinement loop",
                type(e).__name__,
            )

        return workflows_mistralai.ChatAssistantWorkflowOutput(content=output_chunks)
