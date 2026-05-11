"""Generation activity — produce N candidate use cases for a target company
(N=`settings.candidates_to_generate`, default 8 in v9.3+).

Composes the full generation prompt by interpolating:
  - the five criteria (with positive AND negative examples) from `src.criteria`
  - the typed CompanyContext
  - the company's existing AI initiatives
  - the retrieved peer precedents (with industry + 200-char snippet for context)
  - the locked few-shot example outputs from `src.prompts.FEW_SHOT_EXAMPLES`

After parsing the model's JSON, runs a post-process gauntlet:
  1. Drop any `inspired_by` ID not in the actual retrieved set, log warning
  2. Drop any `grounded_in` field path that doesn't resolve in the company
     context schema, log warning
  3. Compute pairwise cosine similarity across the N candidate descriptions;
     if avg > diversity_threshold, run ONE regeneration with the regen-aware
     prompt slot enabled.
  4. Soft warning if novel-direction count < 3.

Per Mistral Workflows rules: this is a single activity that owns the LLM call
and post-process. No I/O lives in the workflow class itself.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from itertools import combinations
from typing import Any

import httpx
import mistralai.workflows as workflows
import numpy as np
from mistralai.client import Mistral

from scripts._fetch import extract_main_text, fetch_html
from src.config import settings
from src.criteria import render_criteria_for_prompt
from src._clients import mistral_client
from src.evidence import from_tavily_result
from src.trace import trace_step
from src.models import (
    Candidate,
    CandidateBatch,
    CompanyContext,
    EvidenceKind,
    EvidenceLedger,
    Novelty,
    ResearchBundle,
    RetrievedPrecedents,
)
from src.prompts import FEW_SHOT_EXAMPLES, GENERATION_SYSTEM

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt composition
# ---------------------------------------------------------------------------


def _format_precedents_block(retrieved: RetrievedPrecedents) -> str:
    """Produce the runtime list of valid precedent IDs with per-entry context."""
    if not retrieved.items:
        return "(none retrieved — propose only novel directions; inspired_by must be empty)"
    lines: list[str] = []
    for p in retrieved.items:
        snippet = (p.description or "")[:200].replace("\n", " ").strip()
        title = (p.title or "")[:120]
        company = p.company or "(unnamed)"
        industry = p.industry or "(unknown)"
        lines.append(f"{p.id}: {title} — {company}")
        lines.append(f"  Industry: {industry}. Snippet: {snippet}")
    return "\n".join(lines)


def _format_existing_initiatives(ctx: CompanyContext) -> str:
    if not ctx.existing_ai_initiatives:
        return "(none discovered — generation is unconstrained on duplicates)"
    lines: list[str] = []
    for ei in ctx.existing_ai_initiatives:
        lines.append(f"- ({ei.source.value}, conf={ei.confidence}) {ei.description[:600]}")
    return "\n".join(lines)


def _format_company_context(ctx: CompanyContext) -> str:
    return ctx.model_dump_json(indent=2)


def _format_few_shots(examples: list[dict[str, Any]]) -> str:
    parts: list[str] = [
        "Example outputs (for OTHER companies — match style and rigor, not content):\n"
    ]
    for i, ex in enumerate(examples, start=1):
        parts.append(f"--- Example {i} ---")
        parts.append(json.dumps(ex, indent=2, ensure_ascii=False))
        parts.append("")
    return "\n".join(parts)


def _format_raw_bundle(bundle: ResearchBundle | None) -> str:
    """Pass raw Wikipedia + news prose to generation so the model can find
    company-specific facts the synthesizer flattened. Helps when the structured
    CompanyContext is thin."""
    if bundle is None:
        return "(no raw bundle available — work from the structured CompanyContext only)"
    parts: list[str] = []
    if bundle.wikipedia.found and bundle.wikipedia.summary:
        parts.append(f"### Wikipedia summary\n{bundle.wikipedia.summary[:3500]}")
    if bundle.news:
        parts.append("### Recent news (deep-read bodies)")
        for n in bundle.news[:3]:
            body = n.deep_content or n.snippet or ""
            parts.append(f"- {n.title}\n  {body[:1500]}")
    if bundle.jobs and bundle.jobs.summary:
        parts.append(f"### Hiring signal\n{bundle.jobs.summary}")
    return "\n\n".join(parts) if parts else "(raw bundle present but empty)"


def _build_user_message(
    ctx: CompanyContext,
    retrieved: RetrievedPrecedents,
    focus_area: str,
    mistral_emphasis: bool,
    regeneration_attempt: int,
    prev_diversity_score: float | None,
    raw_bundle: ResearchBundle | None = None,
) -> str:
    sections: list[str] = []
    sections.append("# Five scoring criteria (with positive and negative anchors)\n")
    sections.append(render_criteria_for_prompt())
    sections.append("\n# Target company context (structured)\n")
    sections.append(_format_company_context(ctx))
    if ctx.free_text_notes:
        sections.append(f"\n## Synthesizer free-text notes\n{ctx.free_text_notes}\n")
    sections.append(
        "\n# Raw research signals (use these to find company-specific hooks the structured fields missed)\n"
    )
    sections.append(_format_raw_bundle(raw_bundle))
    sections.append("\n# Existing AI initiatives at this company (DO NOT duplicate)\n")
    sections.append(_format_existing_initiatives(ctx))
    sections.append("\n# Retrieved peer precedents (use as inspired_by source)\n")
    sections.append(_format_precedents_block(retrieved))
    sections.append("\n# Few-shot example outputs\n")
    sections.append(_format_few_shots(FEW_SHOT_EXAMPLES))
    sections.append("\n# User configuration\n")
    sections.append(f"- focus_area: {focus_area}")
    sections.append(f"- mistral_emphasis: {str(mistral_emphasis).lower()}")
    if regeneration_attempt > 1:
        sections.append(f"- regeneration_attempt: {regeneration_attempt}")
        sections.append(
            f"- prev_diversity_score: {prev_diversity_score:.3f}"
            if prev_diversity_score is not None
            else ""
        )
    sections.append("\n# Generation budget")
    sections.append(
        f"Generate EXACTLY {settings.candidates_to_generate} candidates. "
        f"At least 3 must be `novelty: novel_direction` per the hard rules."
    )
    sections.append("\n# Task")
    sections.append(
        f"Generate exactly {settings.candidates_to_generate} candidate use "
        f"cases for {ctx.identity.name} following ALL hard rules in the "
        f"system prompt. Return strict JSON matching the CandidateBatch "
        f"schema."
    )
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Post-process gauntlet
# ---------------------------------------------------------------------------


_PATH_RE = re.compile(r"^([\w_]+)(?:\.([\w_]+))?(?:\[(\d+)\])?$")
_NESTED_PATH_RE = re.compile(r"^([\w_]+)\.([\w_]+)(?:\[(\d+)\])?$")


def _path_resolves(path: str, ctx: CompanyContext) -> bool:
    """Best-effort check that a `grounded_in` path resolves in the actual context.

    Supports two-level paths: `top.sub` and `top.sub[index]`. Top-only paths
    without a subfield aren't valid for our schema (CompanyContext is always
    two levels), so we reject them.
    """
    m = _NESTED_PATH_RE.match(path.strip())
    if not m:
        return False
    top, sub, idx = m.group(1), m.group(2), m.group(3)
    top_obj = getattr(ctx, top, None)
    if top_obj is None:
        return False
    sub_obj = getattr(top_obj, sub, None)
    if sub_obj is None and not hasattr(top_obj, sub):
        return False
    # If an index was provided, the field must be a sequence and the index in range
    if idx is not None:
        if not isinstance(sub_obj, list):
            return False
        if int(idx) >= len(sub_obj):
            return False
    return True


def _drop_hallucinated_inspired_by(
    candidates: list[Candidate], retrieved: RetrievedPrecedents
) -> int:
    valid_ids = {p.id for p in retrieved.items}
    dropped_total = 0
    for cand in candidates:
        invalid = [i for i in cand.inspired_by if i not in valid_ids]
        if invalid:
            logger.warning(
                "generate: dropped %d hallucinated inspired_by IDs from %s: %s",
                len(invalid),
                cand.id,
                invalid,
            )
            cand.inspired_by = [i for i in cand.inspired_by if i in valid_ids]
            dropped_total += len(invalid)
    return dropped_total


def _drop_hallucinated_grounded_in(candidates: list[Candidate], ctx: CompanyContext) -> int:
    dropped_total = 0
    for cand in candidates:
        invalid = [p for p in cand.grounded_in if not _path_resolves(p, ctx)]
        if invalid:
            logger.warning(
                "generate: dropped %d hallucinated grounded_in paths from %s: %s",
                len(invalid),
                cand.id,
                invalid,
            )
            cand.grounded_in = [p for p in cand.grounded_in if _path_resolves(p, ctx)]
            dropped_total += len(invalid)
    return dropped_total


def _drop_hallucinated_evidence_ids(
    candidates: list[Candidate], ledger: EvidenceLedger
) -> int:
    """Strip any evidence_id the generator emitted that isn't actually in the
    ledger. Same intent as the inspired_by check: prevent fabricated citations
    from making it past generation."""
    valid = set(ledger.ids())
    dropped_total = 0
    for cand in candidates:
        invalid = [e for e in cand.evidence_ids if e not in valid]
        if invalid:
            logger.warning(
                "generate: dropped %d hallucinated evidence_ids from %s: %s",
                len(invalid),
                cand.id,
                invalid,
            )
            cand.evidence_ids = [e for e in cand.evidence_ids if e in valid]
            dropped_total += len(invalid)
    return dropped_total


def _check_novelty_quota(candidates: list[Candidate]) -> int:
    n_novel = sum(1 for c in candidates if c.novelty == Novelty.NOVEL_DIRECTION)
    if n_novel < 3:
        logger.warning(
            "generate: only %d/%d novel-direction candidates (target ≥3)",
            n_novel, len(candidates),
        )
    return n_novel


# ---------------------------------------------------------------------------
# Diversity scoring
# ---------------------------------------------------------------------------


async def _embed_descriptions(client: Mistral, texts: list[str]) -> np.ndarray:
    """Batch-embed candidate descriptions for the diversity check."""
    resp = await client.embeddings.create_async(
        model=settings.mistral_embedding_model,
        inputs=texts,
    )
    vecs = np.asarray([d.embedding for d in resp.data], dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def _avg_pairwise_cosine(matrix: np.ndarray) -> float:
    n = matrix.shape[0]
    if n < 2:
        return 0.0
    pairs = list(combinations(range(n), 2))
    sims = [float(matrix[i] @ matrix[j]) for i, j in pairs]
    return float(np.mean(sims))


# ---------------------------------------------------------------------------
# Single LLM call + JSON parse
# ---------------------------------------------------------------------------


from src._util import strip_fence as _strip_fence  # noqa: E402
from src._rate_limits import MISTRAL_API_RATE_LIMIT


# ---------------------------------------------------------------------------
# web_search tool — let the generator pull live web evidence on demand
# ---------------------------------------------------------------------------


WEB_SEARCH_TOOL_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for current information about a company, product, "
            "or topic. Returns up to 2 results, each with an evidence_id, URL, "
            "title, and deep-read article body. Cite the evidence_id in the "
            "candidate's evidence_ids field whenever you use the result. "
            "Use this to verify or extend the static context — recent "
            "announcements, named partnerships, specific brand names, scale "
            "numbers, regulatory contexts. Budget: 4 calls max per run."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "A specific 4-10 word search query. Be entity-specific "
                        "(named brand, year, document type) — generic queries "
                        "return aggregator pages."
                    ),
                },
            },
            "required": ["query"],
        },
    },
}

_TIER_TOOL_BUDGET: dict[str, int] = {"fast": 0, "standard": 2, "max": 4}


def _max_tool_calls() -> int:
    """Web-search budget per tier. fast=0 (no web search), standard=2, max=4."""
    return _TIER_TOOL_BUDGET.get(settings.tier.value, 4)


async def _web_search_tool_handler(
    query: str, ledger: EvidenceLedger
) -> dict[str, object]:
    """Execute one web_search tool call. Returns a JSON-serializable dict the
    LLM can read. Side effect: every fetched result is appended to the ledger
    so downstream steps can verify claims against the source content."""
    if not settings.tavily_api_key:
        return {"error": "tavily_unavailable", "results": []}

    from tavily import AsyncTavilyClient  # local import — keeps SDK deps lazy

    tavily = AsyncTavilyClient(api_key=settings.tavily_api_key)
    # Tavily caps queries at 400 chars; clamp to 399 to avoid the
    # "Maximum query length: 400" error class.
    query = query[:399]
    async with trace_step(
        "generate.web_search",
        "tavily",
        "search",
        inputs_summary=f"query={query!r}",
    ) as tavily_ev:
        try:
            resp = await tavily.search(query=query, search_depth="advanced", max_results=2)
        except Exception as e:
            logger.warning("web_search tool: Tavily failed: %s", type(e).__name__)
            tavily_ev.outputs_summary = f"FAILED: {type(e).__name__}"
            return {"error": type(e).__name__, "results": []}
        if not isinstance(resp, dict):
            tavily_ev.outputs_summary = "0 results (non-dict response)"
            return {"results": []}
        raw_results = resp.get("results", [])
        tavily_ev.outputs_summary = f"{len(raw_results)} raw results"

    out_results: list[dict[str, object]] = []

    async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}) as http:
        for r in raw_results[:2]:
            url = str(r.get("url") or "")
            title = str(r.get("title") or "")
            snippet = str(r.get("content") or "")[:1500]
            content = snippet
            if url:
                html = await fetch_html(http, url, timeout_s=10.0)
                if html:
                    body = extract_main_text(html, max_chars=6000)
                    if body:
                        content = body
            if not (url and title and content):
                continue
            item = from_tavily_result(
                url,
                title,
                content,
                kind=EvidenceKind.GENERATION_TOOL,
                fetched_at_step="generation_tool",
                confidence="medium",
            )
            ledger.add(item)
            out_results.append(
                {
                    "evidence_id": item.id,
                    "url": url,
                    "title": title,
                    "content": content[:2500],
                }
            )
    return {"results": out_results}


def _coerce_tool_arguments(arguments: object) -> dict[str, object]:
    """Mistral SDK may return arguments as a JSON string or a dict."""
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def _call_generator_with_tools(
    client: Mistral,
    user_message: str,
    ledger: EvidenceLedger,
) -> list[Candidate]:
    """Run the generation LLM call with the web_search tool available.

    Loops until the model returns a final non-tool-call response or we hit
    `_max_tool_calls()` total tool invocations. Every tool result is appended
    to the ledger by the handler.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": GENERATION_SYSTEM},
        {"role": "user", "content": user_message},
    ]
    tools = [WEB_SEARCH_TOOL_SPEC]
    tool_calls_used = 0

    for iteration in range(_max_tool_calls() + 2):
        # Once budget is exhausted, force final JSON without tools.
        active_tools = tools if tool_calls_used < _max_tool_calls() else None
        kwargs: dict[str, Any] = {
            "model": settings.mistral_generation_model,
            "temperature": 0.7,
            "max_tokens": 16_000,
            "timeout_ms": 240_000,
            "messages": messages,
        }
        if active_tools:
            kwargs["tools"] = active_tools
            kwargs["tool_choice"] = "auto"
        else:
            # Final pass — strict JSON, no tools.
            kwargs["response_format"] = {"type": "json_object"}

        async with trace_step(
            "generate",
            settings.mistral_generation_model,
            "chat.complete",
            inputs_summary=(
                f"iteration={iteration} tool_calls_used={tool_calls_used}/"
                f"{_max_tool_calls()} tools={'on' if active_tools else 'off'}"
            ),
        ) as gen_ev:
            r = await client.chat.complete_async(**kwargs)
            choice0 = r.choices[0]
            tcs = getattr(choice0.message, "tool_calls", None) or []
            content_preview = (choice0.message.content or "")
            if isinstance(content_preview, list):
                content_preview = "".join(getattr(b, "text", "") for b in content_preview)
            gen_ev.outputs_summary = (
                f"tool_calls={len(tcs)} | content_chars={len(str(content_preview or ''))}"
            )
        choice = r.choices[0]
        msg = choice.message

        tool_calls = getattr(msg, "tool_calls", None) or []
        content = msg.content
        if isinstance(content, list):
            content = "".join(getattr(b, "text", "") for b in content)
        content_str = str(content or "").strip()

        if tool_calls and tool_calls_used < _max_tool_calls():
            # Append the assistant turn (with tool_calls) and run each tool.
            assistant_turn: dict[str, Any] = {
                "role": "assistant",
                "content": content_str or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": (
                                tc.function.arguments
                                if isinstance(tc.function.arguments, str)
                                else json.dumps(tc.function.arguments)
                            ),
                        },
                    }
                    for tc in tool_calls
                ],
            }
            messages.append(assistant_turn)
            for tc in tool_calls:
                query_str: str = ""
                if tool_calls_used >= _max_tool_calls():
                    tool_result: dict[str, object] = {
                        "error": "budget_exhausted",
                        "results": [],
                    }
                else:
                    args = _coerce_tool_arguments(tc.function.arguments)
                    query_str = str(args.get("query", "")).strip()
                    if tc.function.name == "web_search":
                        tool_result = (
                            await _web_search_tool_handler(query_str, ledger)
                            if query_str
                            else {"error": "empty_query", "results": []}
                        )
                    else:
                        tool_result = {"error": "unknown_tool", "results": []}
                    tool_calls_used += 1
                results_count = (
                    len(tool_result.get("results") or [])
                    if isinstance(tool_result, dict)
                    else 0
                )
                logger.info(
                    "generate.web_search: query=%r results=%d (used %d/%d)",
                    query_str,
                    results_count,
                    tool_calls_used,
                    _max_tool_calls(),
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "name": tc.function.name,
                        "content": json.dumps(tool_result),
                    }
                )
            continue

        # No tool calls — this should be the final JSON.
        if not content_str:
            logger.warning("generate: empty response on iteration %d", iteration)
            continue
        try:
            data = json.loads(_strip_fence(content_str))
        except json.JSONDecodeError as e:
            logger.warning("generate: JSON decode failed (%s), retrying", e)
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "That response wasn't valid JSON. Re-emit ONLY the strict "
                        "CandidateBatch JSON object now, no commentary."
                    ),
                }
            )
            continue
        raw_candidates = data.get("candidates", [])
        if not isinstance(raw_candidates, list):
            raise ValueError("generator output missing 'candidates' list")
        candidates: list[Candidate] = []
        for c in raw_candidates:
            if not isinstance(c, dict):
                continue
            try:
                candidates.append(Candidate.model_validate(c))
            except Exception as e:
                logger.warning("generate: invalid candidate dropped: %s", type(e).__name__)
        return candidates

    raise RuntimeError("generation loop exceeded max iterations without a final response")


# ---------------------------------------------------------------------------
# Workflow activity
# ---------------------------------------------------------------------------


@workflows.activity(start_to_close_timeout=timedelta(seconds=600), rate_limit=MISTRAL_API_RATE_LIMIT)
async def generate_candidates_activity(
    ctx: CompanyContext,
    retrieved: RetrievedPrecedents,
    focus_area: str = "general",
    mistral_emphasis: bool = True,
    diversity_threshold: float | None = None,
    raw_bundle: ResearchBundle | None = None,
    ledger: EvidenceLedger | None = None,
) -> tuple[CandidateBatch, EvidenceLedger]:
    """Generate N candidate use cases (N=`settings.candidates_to_generate`,
    default 8) with post-process gauntlet + one diversity regen.

    Generation has access to the `web_search` tool (Mistral function-calling),
    budget capped at 4 calls per run. Every fetched URL is appended to the
    EvidenceLedger so downstream steps can verify claims against the source
    content. The model is told to cite `evidence_id`s in each candidate when
    it uses tool results.

    Determinism note: this is an ACTIVITY (not workflow code), so it owns all
    side effects: LLM calls, web search, embedding calls, logging. The
    workflow merely awaits this activity's result.
    """
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for candidate generation")
    if diversity_threshold is None:
        diversity_threshold = settings.diversity_threshold
    if ledger is None:
        ledger = EvidenceLedger()

    client = mistral_client()

    # First attempt
    user_msg = _build_user_message(
        ctx=ctx,
        retrieved=retrieved,
        focus_area=focus_area,
        mistral_emphasis=mistral_emphasis,
        regeneration_attempt=1,
        prev_diversity_score=None,
        raw_bundle=raw_bundle,
    )
    candidates = await _call_generator_with_tools(client, user_msg, ledger)
    logger.info(
        "generate: first attempt produced %d candidates | ledger=%d",
        len(candidates),
        len(ledger.entries),
    )

    if len(candidates) < 6:
        # Generator returned much less than expected — surface as error
        raise RuntimeError(
            f"generator returned only {len(candidates)} valid candidates (expected 12)"
        )

    # Post-process gauntlet (return values logged inside each helper)
    _drop_hallucinated_inspired_by(candidates, retrieved)
    _drop_hallucinated_grounded_in(candidates, ctx)
    _drop_hallucinated_evidence_ids(candidates, ledger)
    _check_novelty_quota(candidates)

    # Diversity check
    descriptions = [c.description for c in candidates]
    desc_matrix = await _embed_descriptions(client, descriptions)
    diversity_score = _avg_pairwise_cosine(desc_matrix)
    logger.info(
        "generate: diversity avg pairwise cosine = %.3f (threshold = %.2f)",
        diversity_score,
        diversity_threshold,
    )

    regenerated = False
    if diversity_score > diversity_threshold:
        logger.info("generate: triggering one regeneration for diversity")
        user_msg2 = _build_user_message(
            ctx=ctx,
            retrieved=retrieved,
            focus_area=focus_area,
            mistral_emphasis=mistral_emphasis,
            regeneration_attempt=2,
            prev_diversity_score=diversity_score,
            raw_bundle=raw_bundle,
        )
        candidates2 = await _call_generator_with_tools(client, user_msg2, ledger)
        if len(candidates2) >= 6:
            _drop_hallucinated_inspired_by(candidates2, retrieved)
            _drop_hallucinated_grounded_in(candidates2, ctx)
            _drop_hallucinated_evidence_ids(candidates2, ledger)
            _check_novelty_quota(candidates2)
            desc_matrix2 = await _embed_descriptions(client, [c.description for c in candidates2])
            diversity_score2 = _avg_pairwise_cosine(desc_matrix2)
            logger.info(
                "generate: regen diversity = %.3f (was %.3f)",
                diversity_score2,
                diversity_score,
            )
            # Use regen if it actually improved diversity, else stick with original
            if diversity_score2 < diversity_score:
                candidates = candidates2
                diversity_score = diversity_score2
                regenerated = True

    batch = CandidateBatch(
        candidates=candidates,
        diversity_score=diversity_score,
        regenerated_for_diversity=regenerated,
    )
    return batch, ledger
