"""Generation activity — produce 12 candidate use cases for a target company.

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
  3. Compute pairwise cosine similarity across the 12 candidate descriptions;
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

import mistralai.workflows as workflows
import numpy as np
from mistralai.client import Mistral

from src.config import settings
from src.criteria import render_criteria_for_prompt
from src.models import (
    Candidate,
    CandidateBatch,
    CompanyContext,
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
    sections.append("\n# Task")
    sections.append(
        f"Generate exactly 12 candidate use cases for {ctx.identity.name} "
        f"following ALL hard rules in the system prompt. Return strict JSON "
        f"matching the CandidateBatch schema."
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


def _check_novelty_quota(candidates: list[Candidate]) -> int:
    n_novel = sum(1 for c in candidates if c.novelty == Novelty.NOVEL_DIRECTION)
    if n_novel < 3:
        logger.warning("generate: only %d/12 novel-direction candidates (target ≥3)", n_novel)
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


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


async def _call_generator(
    client: Mistral,
    user_message: str,
) -> list[Candidate]:
    r = await client.chat.complete_async(
        model=settings.mistral_generation_model,
        temperature=0.7,  # creative variety across candidates
        max_tokens=12_000,
        timeout_ms=180_000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": GENERATION_SYSTEM},
            {"role": "user", "content": user_message},
        ],
    )
    text = r.choices[0].message.content
    if isinstance(text, list):
        text = "".join(getattr(b, "text", "") for b in text)
    data = json.loads(_strip_fence(str(text or "")))
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


# ---------------------------------------------------------------------------
# Workflow activity
# ---------------------------------------------------------------------------


@workflows.activity(start_to_close_timeout=timedelta(seconds=300))
async def generate_candidates_activity(
    ctx: CompanyContext,
    retrieved: RetrievedPrecedents,
    focus_area: str = "general",
    mistral_emphasis: bool = True,
    diversity_threshold: float | None = None,
    raw_bundle: ResearchBundle | None = None,
) -> CandidateBatch:
    """Generate 12 candidate use cases with post-process gauntlet + one regen.

    Determinism note: this is an ACTIVITY (not workflow code), so it owns all
    side effects: LLM call, embedding call, logging. The workflow merely
    awaits this activity's result.
    """
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for candidate generation")
    if diversity_threshold is None:
        diversity_threshold = settings.diversity_threshold

    client = Mistral(api_key=settings.mistral_api_key)

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
    candidates = await _call_generator(client, user_msg)
    logger.info("generate: first attempt produced %d candidates", len(candidates))

    if len(candidates) < 6:
        # Generator returned much less than expected — surface as error
        raise RuntimeError(
            f"generator returned only {len(candidates)} valid candidates (expected 12)"
        )

    # Post-process gauntlet (return values logged inside each helper)
    _drop_hallucinated_inspired_by(candidates, retrieved)
    _drop_hallucinated_grounded_in(candidates, ctx)
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
        candidates2 = await _call_generator(client, user_msg2)
        if len(candidates2) >= 6:
            _drop_hallucinated_inspired_by(candidates2, retrieved)
            _drop_hallucinated_grounded_in(candidates2, ctx)
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

    return CandidateBatch(
        candidates=candidates,
        diversity_score=diversity_score,
        regenerated_for_diversity=regenerated,
    )
