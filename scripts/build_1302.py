"""Parse the 1,302 GenAI use cases corpus from a manual paste, using an LLM extraction pass.

The file has loose paragraph structure with industry headers (`Automotive & Logistics`,
`Retail`, etc.), agent-type subheaders (`Customer Agents`, `Employee Agents`,
`Code Agents`, `Data Agents`, `Creative Agents`, `Security Agents`), and free-form
entry paragraphs. A regex parser catches only ~19% because of multi-paragraph
entries, varied verb cues, and irregular formatting.

Strategy: split the file into industry sections (cheap deterministic pass), then for
each section send the raw text to `mistral-medium-2604` with a strict JSON schema:
extract each company entry with company name, agent type, description, and the
"new this edition" asterisk flag. Cost: ~$0.25 for the full file.

Output: precedents with `source='google_cloud_1302'`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from mistralai.client import Mistral

from scripts._normalize import make_id, strip_vendor_terms
from src.config import settings
from src.db import ensure_schema, upsert_precedents

logger = logging.getLogger(__name__)


SOURCE_TXT = settings.data_raw_dir / "1,302real-worldgenAI.txt"
EXTRACTION_CACHE = settings.data_raw_dir / "1302_llm_extractions.json"


# Industry headers — exact strings that appear in the pasted file. Note that the
# user's paste sometimes concatenates the first header onto the end of an intro
# paragraph (no newline between), so we use substring search rather than
# line-based matching.
INDUSTRY_HEADERS = [
    "Automotive & Logistics",
    "Business & Professional Services",
    "Financial Services",
    "Healthcare & Life Sciences",
    "Hospitality & Travel",
    "Manufacturing, Industrial & Electronics",
    "Media, Marketing & Gaming",
    "Public Sector & Nonprofits",
    "Retail",
    "Technology",
    "Telecommunications",
]


EXTRACTION_SYSTEM_PROMPT = (
    "You are a strict, careful corpus parser. The user will give you a section of "
    "a Google Cloud blog page that lists real-world GenAI use cases at named "
    "companies. Your job is to extract every distinct entry as structured JSON.\n\n"
    "Rules:\n"
    "- Each entry corresponds to ONE company (or named organization) and ONE described "
    "use case. If the same company has multiple distinct use cases, emit one entry per "
    "use case.\n"
    "- The company name leads the entry. Strip a leading asterisk if present (it marks "
    "'new in this edition'). Track that as the `is_new_edition` flag.\n"
    "- Agent type subheaders look like 'Customer Agents', 'Employee Agents', 'Code Agents', "
    "'Data Agents', 'Creative Agents', 'Security Agents'. Use the most recent one before "
    "each entry as the entry's `agent_type`.\n"
    "- The `description` is the FULL sentence(s) describing the use case, verbatim from the "
    "source. Do NOT summarize, do NOT paraphrase, do NOT invent details. If the entry text "
    "is one sentence, the description is one sentence; if multiple, include all of them.\n"
    "- Skip introductory narrative (trends, intro paragraphs, calls-to-action) — only "
    "extract real company-named use case entries.\n"
    "- If unsure whether a paragraph is an entry, SKIP it. Quality over recall.\n\n"
    "Output strictly this JSON shape, no markdown, no commentary:\n"
    '{"entries": [{"company": str, "agent_type": str | null, "description": str, '
    '"is_new_edition": bool}, ...]}'
)


@dataclass
class IndustrySection:
    industry: str
    body: str


def _split_into_industry_sections(text: str) -> list[IndustrySection]:
    """Split the raw text by industry-header substring matches.

    Substring search (rather than per-line) catches the case where the user's
    paste accidentally concatenated the first header onto the end of an intro
    paragraph (no separating newline).

    The "Retail" header risks false positives because the substring appears
    inside other words (e.g. "retailer"); we anchor it with surrounding
    whitespace via a manual scan.
    """
    # Collect (start_index_in_text, header_label) for every match
    matches: list[tuple[int, str]] = []
    for header in INDUSTRY_HEADERS:
        # Scan for occurrences of the header at word boundaries; we accept the
        # match only when it sits at start-of-line OR end-of-line (most likely
        # to be a real section header, not prose use of the words).
        idx = 0
        while True:
            i = text.find(header, idx)
            if i == -1:
                break
            # boundary check: char before is newline-or-start AND char after is newline-or-end
            char_before = text[i - 1] if i > 0 else "\n"
            char_after_idx = i + len(header)
            char_after = text[char_after_idx] if char_after_idx < len(text) else "\n"
            on_own_line = char_before in "\n" and char_after in "\n:"
            # Also accept if the header is concatenated to the end of an
            # intro paragraph (preceding char is letter/punct, following is newline)
            concat_ok = char_before not in "\n " and char_after == "\n" and i > 0
            if on_own_line or concat_ok:
                matches.append((i, header))
            idx = i + len(header)

    # Sort by position in document
    matches.sort(key=lambda x: x[0])
    # Deduplicate same-position matches
    seen_positions: set[int] = set()
    unique: list[tuple[int, str]] = []
    for pos, label in matches:
        if pos in seen_positions:
            continue
        seen_positions.add(pos)
        unique.append((pos, label))

    if not unique:
        return []
    unique.append((len(text), "<<END>>"))

    sections: list[IndustrySection] = []
    for (start, label), (end, _) in zip(unique, unique[1:], strict=False):
        body_start = start + len(label)
        body = text[body_start:end].strip()
        if body:
            sections.append(IndustrySection(industry=label, body=body))
    return sections


def _load_cache() -> dict[str, list[dict[str, object]]]:
    if EXTRACTION_CACHE.exists():
        return json.loads(EXTRACTION_CACHE.read_text(encoding="utf-8"))
    return {}


def _save_cache(data: dict[str, list[dict[str, object]]]) -> None:
    EXTRACTION_CACHE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


AGENT_TYPE_HEADERS = (
    "Customer Agents",
    "Employee Agents",
    "Code Agents",
    "Data Agents",
    "Creative Agents",
    "Security Agents",
)


def _chunk_by_agent_type(body: str) -> list[tuple[str | None, str]]:
    """Return (agent_type, chunk_body) splits for a section's body.

    Agent-type subheaders may appear mid-paragraph (no surrounding blank line),
    so we substring-search for each header.
    """
    matches: list[tuple[int, str]] = []
    for h in AGENT_TYPE_HEADERS:
        idx = 0
        while True:
            i = body.find(h, idx)
            if i == -1:
                break
            char_before = body[i - 1] if i > 0 else "\n"
            after_idx = i + len(h)
            char_after = body[after_idx] if after_idx < len(body) else "\n"
            on_own_line = char_before == "\n" and char_after == "\n"
            concat_ok = char_before in ".\n" and char_after == "\n"
            if on_own_line or concat_ok:
                matches.append((i, h))
            idx = i + len(h)
    matches.sort(key=lambda x: x[0])
    if not matches:
        return [(None, body)]
    chunks: list[tuple[str | None, str]] = []
    # Pre-amble (any text before first agent header)
    if matches[0][0] > 0:
        pre = body[: matches[0][0]].strip()
        if pre:
            chunks.append((None, pre))
    for (start, label), nxt in zip(matches, matches[1:] + [(len(body), "<<END>>")], strict=False):
        chunk_body = body[start + len(label) : nxt[0]].strip()
        if chunk_body:
            chunks.append((label, chunk_body))
    return chunks


async def _llm_extract_chunk(
    client: Mistral,
    sem: asyncio.Semaphore,
    industry: str,
    agent_hint: str | None,
    body: str,
) -> list[dict[str, object]]:
    """Single LLM call to extract entries from one chunk."""
    user_message = (
        f"Industry: {industry}\n"
        + (f"Agent type for this chunk: {agent_hint}\n" if agent_hint else "")
        + f"\n=== SECTION CONTENT ===\n{body}\n=== END ==="
    )
    async with sem:
        try:
            r = await client.chat.complete_async(
                model="mistral-medium-2604",
                temperature=0.1,
                max_tokens=24000,
                timeout_ms=300_000,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
            )
            text = r.choices[0].message.content
            if isinstance(text, list):
                text = "".join(getattr(b, "text", "") for b in text)
            data = json.loads(_strip_fence(str(text or "")))
            entries = data.get("entries", [])
            if not isinstance(entries, list):
                return []
            return [e for e in entries if isinstance(e, dict) and e.get("company")]
        except Exception as e:
            logger.warning(
                "LLM chunk extract failed for %s/%s: %s",
                industry,
                agent_hint or "(no agent)",
                type(e).__name__,
            )
            return []


async def _llm_extract_section(
    client: Mistral, sem: asyncio.Semaphore, section: IndustrySection
) -> list[dict[str, object]]:
    # If the section is large, fall back to chunking by agent-type subheaders
    OVERSIZED = 30_000
    if len(section.body) > OVERSIZED:
        chunks = _chunk_by_agent_type(section.body)
        if len(chunks) > 1:
            logger.info(
                "1302 [%s]: oversized (%d chars), chunking into %d agent-type subsections",
                section.industry,
                len(section.body),
                len(chunks),
            )
            results = await asyncio.gather(
                *(
                    _llm_extract_chunk(client, sem, section.industry, ah, body)
                    for ah, body in chunks
                )
            )
            entries: list[dict[str, object]] = []
            for r in results:
                entries.extend(r)
            logger.info("1302 [%s]: %d entries extracted (chunked)", section.industry, len(entries))
            return entries

    # Default single-call path for normal-sized sections
    user_message = (
        f"Industry: {section.industry}\n\n=== SECTION CONTENT ===\n{section.body}\n=== END ==="
    )
    async with sem:
        try:
            r = await client.chat.complete_async(
                model="mistral-medium-2604",
                temperature=0.1,
                # Generous ceiling — large sections produce many entries; truncation = bad JSON
                max_tokens=24000,
                # The default httpx timeout is too short for big outputs.
                # 5 min covers worst-case Technology section.
                timeout_ms=300_000,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
            )
            text = r.choices[0].message.content
            if isinstance(text, list):
                text = "".join(getattr(b, "text", "") for b in text)
            data = json.loads(_strip_fence(str(text or "")))
            entries = data.get("entries", [])
            if not isinstance(entries, list):
                return []
            logger.info("1302 [%s]: %d entries extracted", section.industry, len(entries))
            return [e for e in entries if isinstance(e, dict) and e.get("company")]
        except Exception as e:
            logger.exception("LLM extract failed for %s: %s", section.industry, type(e).__name__)
            return []


def _to_precedent(industry: str, entry: dict[str, object]) -> dict[str, object] | None:
    company = str(entry.get("company") or "").strip()
    description_raw = str(entry.get("description") or "").strip()
    if not company or not description_raw or len(description_raw) < 30:
        return None
    description = strip_vendor_terms(description_raw)
    title = description.split(".")[0][:200].strip()
    return {
        "id": make_id("google_cloud_1302", company, title),
        "company": company,
        "industry": industry,
        "title": title,
        "description": description,
        "outcome": None,
        "deep_content": None,
        "source_url": None,
        "source": "google_cloud_1302",
        "embedding": None,
    }


async def run() -> dict[str, int]:
    if not SOURCE_TXT.exists():
        raise FileNotFoundError(f"Expected text at {SOURCE_TXT}")
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for LLM-based extraction")

    await ensure_schema()
    text = SOURCE_TXT.read_text(encoding="utf-8")
    sections = _split_into_industry_sections(text)
    logger.info("1302: split into %d industry sections", len(sections))

    cache = _load_cache()
    client = Mistral(api_key=settings.mistral_api_key)
    sem = asyncio.Semaphore(4)

    # Run any uncached sections through the LLM in parallel
    tasks = []
    todo: list[IndustrySection] = []
    for s in sections:
        if s.industry not in cache:
            todo.append(s)
            tasks.append(_llm_extract_section(client, sem, s))

    if tasks:
        results = await asyncio.gather(*tasks)
        for sec, res in zip(todo, results, strict=True):
            cache[sec.industry] = res
        _save_cache(cache)

    rows: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    for s in sections:
        entries = cache.get(s.industry, [])
        for e in entries:
            p = _to_precedent(s.industry, e)
            if p is None:
                continue
            if p["id"] in seen_ids:
                continue
            seen_ids.add(str(p["id"]))
            rows.append(p)

    written = await upsert_precedents(rows)
    logger.info("1302: wrote %d precedents across %d industries", written, len(sections))
    return {"sections": len(sections), "written": written}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(asyncio.run(run()))
