"""One-off helper to screenshot the standalone FE for the README image
placeholders. Not part of the runtime — only invoked manually.

Usage:
    # 1. Boot the standalone FE on port 3457 in another terminal
    cd standalone && PORT=3457 npm run dev
    # 2. Run this
    uv run python -m scripts.screenshot_fe

Writes:
    docs/img/landing.png         - landing page hero
    docs/img/generate.png        - /generate with the Mistral glyph
    docs/img/architecture.png    - /architecture (full)
    docs/img/pipeline-diagram.png - /architecture cropped to the pipeline diagram

For the use-case card we need a completed run, which requires the BE.
That image is sourced separately from a live run.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://localhost:3458"
OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "img"


async def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2,  # retina-quality screenshots
        )
        page = await ctx.new_page()

        # 1. Landing page — full hero with the new interactive compass
        await page.goto(f"{BASE}/", wait_until="networkidle")
        await page.wait_for_selector("svg[aria-label*='Pixel art compass']")
        # Let the typewriter animation finish so the screenshot has the
        # full "Compastral" wordmark instead of mid-typing.
        await page.wait_for_timeout(2000)
        await page.screenshot(
            path=str(OUT_DIR / "landing.png"),
            full_page=False,
            clip={"x": 0, "y": 0, "width": 1440, "height": 900},
        )
        print(f"wrote {OUT_DIR / 'landing.png'}")

        # 2. Generate page — type "Mistral AI" so the brand glyph
        # appears above the panel, then capture the form area.
        await page.goto(f"{BASE}/generate", wait_until="networkidle")
        await page.wait_for_selector("input[placeholder*='Type any public company']")
        await page.fill("input[placeholder*='Type any public company']", "Mistral AI")
        await page.wait_for_timeout(500)
        await page.screenshot(
            path=str(OUT_DIR / "generate.png"),
            full_page=False,
            clip={"x": 0, "y": 0, "width": 1440, "height": 900},
        )
        print(f"wrote {OUT_DIR / 'generate.png'}")

        # 3. Architecture page — full screenshot
        await page.goto(f"{BASE}/architecture", wait_until="networkidle")
        # Wait for mermaid render — the diagrams are async-rendered
        await page.wait_for_timeout(3000)
        await page.screenshot(
            path=str(OUT_DIR / "architecture.png"),
            full_page=True,
        )
        print(f"wrote {OUT_DIR / 'architecture.png'}")

        # 4. Pipeline diagram only — screenshot the "Full pipeline"
        # section so the README has a focused image of the new React
        # PipelineDiagram component.
        await page.goto(f"{BASE}/architecture", wait_until="networkidle")
        await page.wait_for_timeout(2000)
        section = page.locator("section:has(h2:has-text('Full pipeline'))").first
        await section.wait_for(state="visible", timeout=5000)
        await section.screenshot(path=str(OUT_DIR / "pipeline-diagram.png"))
        print(f"wrote {OUT_DIR / 'pipeline-diagram.png'}")

        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
