"""v9.9 — spot check: 5 fresh companies (none tested before) across tiers.

Validates the v9.8 final cleanup:
  - 0.80 SE-ready bar in action
  - Inline link density rule (more markdown links in prose)
  - Entity resolution rewriting unfamiliar inputs

Targets — all NEW (not in v9.4 / v9.5 / v9.6 / v9.7 / v9.8):
  - Adidas       (German sportswear)        — standard
  - TotalEnergies (French integrated energy) — standard
  - ASML         (Dutch semiconductor eq.)   — fast
  - IKEA         (Swedish furniture retail)  — fast
  - Roche        (Swiss pharma)              — max
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src.config import Tier, settings
from src.models import CriteriaWeights, FocusArea, ResearchDepth, WorkflowInput
from src.pipeline import execute_pipeline
from src.trace import RunTrace, set_current_trace
from src.ui.render import render_report_to_markdown

logger = logging.getLogger(__name__)

TARGETS: list[tuple[str, Tier]] = [
    ("Adidas",        Tier.STANDARD),
    ("TotalEnergies", Tier.STANDARD),
    ("ASML",          Tier.FAST),
    ("IKEA",          Tier.FAST),
    ("Roche",         Tier.MAX),
]


async def run_one(company: str, tier: Tier, out_dir: Path) -> dict[str, object]:
    slug = (
        company.lower()
        .replace(" ", "_")
        .replace("'", "")
        .replace("-", "_")
        .replace(".", "")
    ) or "blank"
    out_md = out_dir / f"{slug}_{tier.value}.md"
    settings.tier = tier

    params = WorkflowInput(
        company_name=company,
        focus_area=FocusArea.GENERAL,
        weights=CriteriaWeights(),
        research_depth=ResearchDepth.MEDIUM,
    )

    started_at = datetime.now(timezone.utc)
    trace = RunTrace(company_name=company, started_at=started_at)
    set_current_trace(trace)

    started = time.time()
    error: str | None = None
    refused: bool = False
    confidence: float | None = None
    pass_rate: float | None = None
    se_ready: bool | None = None
    resolved_name: str | None = None

    try:
        result = await execute_pipeline(params)
        if result.refused:
            refused = True
            out_md.write_text(
                f"# {company} ({tier.value}) — REFUSED\n\n{result.refusal_reason or '(no reason)'}\n",
                encoding="utf-8",
            )
        elif result.report is not None:
            md = render_report_to_markdown(result.report)
            out_md.parent.mkdir(parents=True, exist_ok=True)
            out_md.write_text(md, encoding="utf-8")
            trace_md = (
                "# Trace\n\n"
                + result.trace.render_markdown()
                + "\n\n## Mermaid sequence\n\n```mermaid\n"
                + result.trace.render_mermaid()
                + "\n```\n"
            )
            (out_md.parent / f"{slug}_{tier.value}_trace.md").write_text(trace_md, encoding="utf-8")
            confidence = (
                result.report.meta_review.confidence if result.report.meta_review else None
            )
            pass_rate = result.report.quality.fact_check_pass_rate
            se_ready = (
                result.report.meta_review.sales_engineer_ready
                if result.report.meta_review else None
            )
            resolved_name = result.report.company.identity.name
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
        logger.exception("run_one: %s @ %s FAILED — %s", company, tier.value, e)

    wall_s = time.time() - started
    return {
        "company": company,
        "tier": tier.value,
        "wall_s": round(wall_s, 1),
        "error": error,
        "refused": refused,
        "confidence": confidence,
        "pass_rate": pass_rate,
        "sales_engineer_ready": se_ready,
        "resolved_name": resolved_name,
    }


def write_summary(results: list[dict[str, object]], out_dir: Path) -> None:
    lines = [
        "# v9.9 — five fresh companies on the v9.8 system",
        "",
        "Companies: Adidas, TotalEnergies, ASML, IKEA, Roche — all new to the",
        "test corpus. Validates: 0.80 SE-ready bar, INLINE LINK DENSITY rule,",
        "max insane mode on a real pharma giant, fast tier on two more cases.",
        "",
        "| Company | Tier | Wall | Resolved → | Conf | Pass | SE-ready | Status |",
        "|---|---|---:|---|---:|---:|:-:|---|",
    ]
    for r in results:
        if r.get("error"):
            status = f"ERROR — {r['error'][:60]}"
        elif r.get("refused"):
            status = "REFUSED"
        else:
            status = "ok"
        conf = f"{r['confidence']:.2f}" if r.get("confidence") is not None else "—"
        pr = f"{r['pass_rate']:.0%}" if r.get("pass_rate") is not None else "—"
        ready = "✓" if r.get("sales_engineer_ready") else ("✗" if r.get("sales_engineer_ready") is False else "—")
        resolved = str(r.get("resolved_name") or "—")
        lines.append(
            f"| {r['company']} | {r['tier']} | {r['wall_s']}s | {resolved} | {conf} | {pr} | {ready} | {status} |"
        )
    (out_dir / "_summary.md").write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    out_dir = Path(__file__).resolve().parent.parent / "docs" / "benchmarks" / "v9_9"
    out_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("mistralai").setLevel(logging.WARNING)

    results: list[dict[str, object]] = []
    for company, tier in TARGETS:
        print(f"\n=== {company} ({tier.value}) ===", flush=True)
        r = await run_one(company, tier, out_dir)
        results.append(r)
        write_summary(results, out_dir)
        if r["error"]:
            print(f"  → ERROR: {r['error']}", flush=True)
        elif r["refused"]:
            print(f"  → REFUSED in {r['wall_s']}s", flush=True)
        else:
            print(
                f"  → {r['wall_s']}s, resolved={r.get('resolved_name')}, "
                f"conf={r['confidence']}, pass={r['pass_rate']}, SE={r['sales_engineer_ready']}",
                flush=True,
            )

    write_summary(results, out_dir)
    print(f"\n=== DONE — {len(results)} runs, summary at {out_dir / '_summary.md'} ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
