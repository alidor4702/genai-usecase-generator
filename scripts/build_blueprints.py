"""Parse the 101 GenAI Use Cases with Technical Blueprints corpus from a manual paste.

The user pasted the page contents into `data/raw/101genAIusecaseswithtechnicalblueprints.txt`.
Image URLs are inline (Google Cloud's gweb-cloudblog-publish CDN), so we can:

1. Walk the file line-by-line, tracking the current industry section.
2. For each numbered blueprint heading (`N. Title`), parse `Business challenge`, `Tech stack`,
   `Blueprint:` flow, and the immediately following architecture image URL.
3. Async-download every architecture image to `data/raw/blueprint_images/`.
4. Run Mistral vision (mistral-medium-2604) on each image to get a structured architecture
   description, cached to `data/raw/blueprint_image_extractions.json` for re-runs.
5. Merge each parsed blueprint with its image extraction into a rich precedent and upsert
   to the `precedents` table with `source='google_cloud_blueprints'`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from mistralai.client import Mistral

from scripts._normalize import make_id, strip_vendor_terms
from src.config import settings
from src.db import ensure_schema, upsert_precedents

logger = logging.getLogger(__name__)


SOURCE_TXT = settings.data_raw_dir / "101genAIusecaseswithtechnicalblueprints.txt"
IMAGES_DIR = settings.data_raw_dir / "blueprint_images"
EXTRACTIONS_CACHE = settings.data_raw_dir / "blueprint_image_extractions.json"

INDUSTRY_HEADER_RE = re.compile(
    r"images/101GenAI_Blog_Header_\d+-([\w\-]+)\.max-",
)
NUMBERED_HEADING_RE = re.compile(r"^(\d+)\.\s+(.+?)\s*$")
BLUEPRINT_IMAGE_RE = re.compile(
    r"https://storage\.googleapis\.com/gweb-cloudblog-publish/images/(?!101GenAI_Blog_Header)([^\s]+\.png)"
)
INDUSTRY_HEADER_IMAGE_RE = re.compile(
    r"^https://storage\.googleapis\.com/gweb-cloudblog-publish/images/101GenAI_Blog_Header_\d+-"
)

# Map slug fragment -> nice industry label
INDUSTRY_LABELS: dict[str, str] = {
    "Retail": "Retail",
    "Media-Marketing-Gam": "Media, Marketing & Gaming",
    "Automotive--Logisti": "Automotive & Logistics",
    "Financial-Services": "Financial Services",
    "Healthcare--Life-Sc": "Healthcare & Life Sciences",
    "Telecommunication": "Telecommunications",
    "Hospitality--Travel": "Hospitality & Travel",
    "Manufacturing-Indus": "Manufacturing & Industrials",
    "Public-Sector--Nonp": "Public Sector & Nonprofit",
    "Technology": "Technology",
}


VISION_PROMPT = (
    "This is a Google Cloud architecture diagram for a GenAI use case blueprint. "
    "Extract every visible component (boxes, services, layers), its role label "
    "(if shown), and the data flows / arrows between components. Be factual and "
    "literal. Do NOT invent any component, layer, or arrow that is not visible "
    "in the image. If text is unreadable, say so explicitly. Format your answer "
    "as a single dense paragraph (no markdown), starting with the diagram's title "
    "if visible, then components and flows."
)


@dataclass
class Blueprint:
    number: int
    title: str
    industry: str | None
    business_challenge: str = ""
    tech_stack: str = ""
    blueprint_flow: str = ""
    image_url: str | None = None
    image_filename: str | None = None
    architecture_extraction: str | None = None  # filled after vision pass
    raw_text: str = ""  # whole section as-is for fallback
    extra_lines: list[str] = field(default_factory=list)


# ---- Parser --------------------------------------------------------------


def parse_file(path: Path) -> list[Blueprint]:
    if not path.exists():
        raise FileNotFoundError(f"Expected blueprints text at {path}")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    blueprints: list[Blueprint] = []
    current_industry: str | None = None
    current_blueprint: Blueprint | None = None

    def _flush() -> None:
        nonlocal current_blueprint
        if current_blueprint is not None:
            current_blueprint.raw_text = current_blueprint.raw_text.strip()
            blueprints.append(current_blueprint)
            current_blueprint = None

    for raw in lines:
        line = raw.rstrip()

        # Industry section header (a header image like Header_005-Healthcare--Life-Sc)
        m_industry = INDUSTRY_HEADER_RE.search(line)
        if INDUSTRY_HEADER_IMAGE_RE.match(line) and m_industry:
            slug = m_industry.group(1)
            current_industry = INDUSTRY_LABELS.get(slug, slug.replace("-", " "))
            continue

        # Blueprint architecture image (NOT an industry header image)
        if line.startswith("https://storage.googleapis.com") and not INDUSTRY_HEADER_IMAGE_RE.match(
            line
        ):
            m_img = BLUEPRINT_IMAGE_RE.search(line)
            if current_blueprint is not None and m_img and current_blueprint.image_url is None:
                current_blueprint.image_url = line.strip()
                current_blueprint.image_filename = m_img.group(1)
            continue

        # Numbered blueprint heading
        m_num = NUMBERED_HEADING_RE.match(line)
        if m_num:
            _flush()
            number = int(m_num.group(1))
            title = m_num.group(2).strip()
            # Sanity guard: blueprint numbers are 1-101; otherwise it's incidental
            if 1 <= number <= 101:
                current_blueprint = Blueprint(
                    number=number,
                    title=title,
                    industry=current_industry,
                )
                current_blueprint.raw_text = line + "\n"
                continue

        # Field lines within a blueprint
        if current_blueprint is None:
            continue

        current_blueprint.raw_text += line + "\n"
        stripped = line.strip()

        if stripped.lower().startswith("business challenge:"):
            current_blueprint.business_challenge = stripped.split(":", 1)[1].strip()
        elif stripped.lower().startswith("tech stack:"):
            current_blueprint.tech_stack = stripped.split(":", 1)[1].strip()
        elif stripped.lower().startswith("blueprint:"):
            current_blueprint.blueprint_flow = stripped.split(":", 1)[1].strip()
        elif stripped:
            # Accumulate continuation lines (multi-line blueprint flows are common)
            if current_blueprint.blueprint_flow:
                current_blueprint.blueprint_flow += " " + stripped
            elif current_blueprint.business_challenge and not current_blueprint.tech_stack:
                current_blueprint.business_challenge += " " + stripped
            else:
                current_blueprint.extra_lines.append(stripped)

    _flush()
    logger.info("blueprints: parsed %d entries", len(blueprints))
    return blueprints


# ---- Image download ------------------------------------------------------


async def download_image(client: httpx.AsyncClient, url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        return True
    try:
        r = await client.get(url, follow_redirects=True, timeout=30.0)
        r.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("download failed %s: %s", url, type(e).__name__)
        return False
    dest.write_bytes(r.content)
    return True


async def download_all_images(blueprints: list[Blueprint]) -> int:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    targets: list[tuple[Blueprint, Path]] = []
    for bp in blueprints:
        if not bp.image_filename:
            continue
        dest = IMAGES_DIR / bp.image_filename
        targets.append((bp, dest))
    if not targets:
        return 0
    sem = asyncio.Semaphore(8)

    async def _one(bp: Blueprint, dest: Path, client: httpx.AsyncClient) -> bool:
        async with sem:
            assert bp.image_url is not None
            return await download_image(client, bp.image_url, dest)

    async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}) as client:
        results = await asyncio.gather(
            *(_one(bp, d, client) for bp, d in targets),
            return_exceptions=True,
        )
    ok = sum(1 for r in results if r is True)
    logger.info("blueprints: downloaded %d / %d images", ok, len(targets))
    return ok


# ---- Mistral vision extraction --------------------------------------------


def _load_extraction_cache() -> dict[str, str]:
    if EXTRACTIONS_CACHE.exists():
        return json.loads(EXTRACTIONS_CACHE.read_text(encoding="utf-8"))
    return {}


def _save_extraction_cache(data: dict[str, str]) -> None:
    EXTRACTIONS_CACHE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


async def _vision_one(
    client: Mistral, sem: asyncio.Semaphore, image_path: Path
) -> tuple[str, str | None]:
    async with sem:
        try:
            with image_path.open("rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            r = await client.chat.complete_async(
                model="mistral-medium-2604",
                temperature=0.1,
                max_tokens=900,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": VISION_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": f"data:image/png;base64,{b64}",
                            },
                        ],
                    }
                ],
            )
            text = r.choices[0].message.content
            if isinstance(text, list):
                text = "".join(getattr(b, "text", "") for b in text)
            return image_path.name, str(text).strip() if text else None
        except Exception as e:
            logger.warning("vision failed for %s: %s", image_path.name, type(e).__name__)
            return image_path.name, None


async def vision_extract_all(blueprints: list[Blueprint]) -> dict[str, str]:
    cache = _load_extraction_cache()
    if not settings.mistral_api_key:
        raise RuntimeError("MISTRAL_API_KEY required for vision extraction")
    client = Mistral(api_key=settings.mistral_api_key)
    sem = asyncio.Semaphore(4)

    paths: list[Path] = []
    for bp in blueprints:
        if not bp.image_filename:
            continue
        p = IMAGES_DIR / bp.image_filename
        if not p.exists():
            continue
        if bp.image_filename in cache and cache[bp.image_filename]:
            continue  # already extracted previously
        paths.append(p)

    if not paths:
        logger.info("vision: nothing to extract (cache hit on all)")
        return cache

    logger.info("vision: extracting %d images", len(paths))
    results = await asyncio.gather(*(_vision_one(client, sem, p) for p in paths))

    for name, text in results:
        if text:
            cache[name] = text
    _save_extraction_cache(cache)
    logger.info("vision: cache now contains %d extractions", len(cache))
    return cache


# ---- Build precedents -----------------------------------------------------


def _to_precedent(bp: Blueprint, vision_cache: dict[str, str]) -> dict[str, object]:
    parts: list[str] = []
    if bp.business_challenge:
        parts.append(f"Business challenge: {bp.business_challenge}")
    if bp.tech_stack:
        parts.append(f"Tech stack: {bp.tech_stack}")
    if bp.blueprint_flow:
        parts.append(f"Blueprint: {bp.blueprint_flow}")
    body = strip_vendor_terms("\n\n".join(parts))

    arch = vision_cache.get(bp.image_filename or "")
    deep = body
    if arch:
        deep = body + "\n\nArchitecture diagram:\n" + strip_vendor_terms(arch)

    description = strip_vendor_terms(bp.business_challenge or bp.title)

    return {
        "id": make_id("google_cloud_blueprints", "GoogleCloud", f"{bp.number:03d}-{bp.title}"),
        # Customer is rarely named in blueprints — these are pattern templates, not customer stories
        "company": "Google Cloud Blueprint",
        "industry": bp.industry,
        "title": strip_vendor_terms(bp.title),
        "description": description,
        "outcome": None,
        "deep_content": deep,
        "source_url": bp.image_url,
        "source": "google_cloud_blueprints",
        "embedding": None,
    }


async def run() -> dict[str, int]:
    await ensure_schema()
    blueprints = parse_file(SOURCE_TXT)
    logger.info(
        "blueprints: parsed=%d, with_image=%d, industries=%d",
        len(blueprints),
        sum(1 for b in blueprints if b.image_url),
        len({b.industry for b in blueprints if b.industry}),
    )
    await download_all_images(blueprints)
    vision_cache = await vision_extract_all(blueprints)
    rows = [_to_precedent(b, vision_cache) for b in blueprints]
    written = await upsert_precedents(rows)
    logger.info(
        "blueprints: wrote %d precedents (%d with arch extraction)",
        written,
        sum(1 for r in rows if "Architecture diagram:" in (r.get("deep_content") or "")),
    )
    return {"parsed": len(blueprints), "written": written, "vision_extractions": len(vision_cache)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(asyncio.run(run()))
