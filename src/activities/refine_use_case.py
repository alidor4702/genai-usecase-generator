"""Activity: refine ONE use case based on user feedback from a Canvas edit.

Wired to the human-in-the-loop CanvasInput primitive in workflow.py:
after the report renders, the user can edit a feedback canvas + type a
follow-up note. The workflow extracts (a) which use case they want
changed (best-effort identification from chat message + edits) and
(b) what they want changed, then calls this activity to produce a
refined version of that one use case as a markdown chunk.

The result is shipped as a new markdown canvas in Le Chat. We don't
re-run the whole pipeline — this is a focused single-LLM-call refinement.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta

import mistralai.workflows as workflows

from src._clients import mistral_client
from src.config import settings
from src.trace import trace_step

logger = logging.getLogger(__name__)


_REFINE_SYSTEM = """\
You are refining ONE customer-facing GenAI use case based on direct user
feedback from a Mistral Workflows Canvas edit.

You will receive:
  - The original use case body markdown (what the user just read)
  - The user's edited version of a feedback canvas (their changes)
  - A short follow-up message from the user (optional)

Produce ONE refined version of the use case body as plain markdown.
Keep the same structure (title heading, description, why-this-company,
example input/output, blueprint, top risk, products, grounding) — just
revise the content per the user's feedback.

Hard rules:
- Stay grounded. Do not invent new facts or precedent IDs that weren't
  in the original use case. If the user asks for something we can't
  support from the original grounding, say so politely in the
  description and explain what evidence is missing.
- Keep customer-facing prose. No internal jargon, no system-prompt
  artifacts.
- Mirror the original's level of specificity. If the original cited a
  blueprint pattern, keep it. If it had an example_input/output, keep
  the same shape (just refine content).
- Output STRICTLY just the markdown — no JSON wrapper, no preamble,
  no "here is the refined version:" lead-in.
"""


def _identify_use_case_index(chat_msg: str, n_use_cases: int) -> int | None:
    """Best-effort: extract '1', '2', '3' or 'first', 'second', 'third'
    from the chat message. Returns None if we can't tell."""
    if not chat_msg:
        return None
    text = chat_msg.lower()
    # Numeric mentions
    for i in range(n_use_cases):
        # "use case 2", "uc 2", "second use case", etc.
        if re.search(rf"\b(use\s*case|uc)\s*{i + 1}\b", text):
            return i
        if re.search(rf"\bnumber\s*{i + 1}\b", text):
            return i
    # Ordinal mentions
    ordinals = ["first", "second", "third", "fourth", "fifth"]
    for i, word in enumerate(ordinals[:n_use_cases]):
        if re.search(rf"\b{word}\b", text):
            return i
    return None


@workflows.activity(start_to_close_timeout=timedelta(seconds=120))
async def refine_use_case_activity(
    use_case_body_md: str,
    edited_canvas_content: str,
    user_chat_message: str,
    company_name: str,
) -> str:
    """Run a single LLM call to produce a refined version of one use case.

    Returns the refined markdown body. On any LLM failure, returns a
    polite "couldn't refine" string so the workflow can ship something
    rather than crash.
    """
    if not settings.mistral_api_key:
        return (
            f"Got your feedback for **{company_name}**, but Mistral API key "
            f"isn't configured for refinement. The original use case stands."
        )

    client = mistral_client()
    user_msg = (
        f"# Target company\n{company_name}\n\n"
        f"# Original use case body\n{use_case_body_md}\n\n"
        f"# User's edited feedback canvas\n{edited_canvas_content or '(empty)'}\n\n"
        f"# User's chat message\n{user_chat_message or '(none)'}\n\n"
        f"# Your task\nProduce the refined use case body markdown per the system instructions."
    )

    async with trace_step(
        "refine_use_case",
        settings.mistral_enrichment_model,
        "chat.complete",
        inputs_summary=(
            f"company={company_name!r} feedback={user_chat_message[:60]!r} "
            f"edited_chars={len(edited_canvas_content)}"
        ),
    ) as ev:
        try:
            r = await client.chat.complete_async(
                model=settings.mistral_enrichment_model,
                temperature=0.3,
                max_tokens=4_000,
                timeout_ms=90_000,
                messages=[
                    {"role": "system", "content": _REFINE_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
            )
            text = r.choices[0].message.content
            if isinstance(text, list):
                text = "".join(getattr(b, "text", "") for b in text)
            refined = str(text or "").strip()
            ev.outputs_summary = f"refined {len(refined)} chars"
            return refined
        except Exception as e:  # noqa: BLE001
            logger.exception("refine_use_case: LLM call failed — %s", e)
            ev.outputs_summary = f"failed: {type(e).__name__}"
            return (
                f"_(couldn't run refinement: `{type(e).__name__}: {e}`. "
                f"The original use case stands.)_"
            )


def parse_use_case_index(chat_msg: str, edited: str, n: int) -> int | None:
    """Re-export for the workflow to call before invoking the activity."""
    return _identify_use_case_index(f"{chat_msg} {edited}", n)
