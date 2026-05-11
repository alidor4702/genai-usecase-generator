"""Heavy multi-tier testing batch — 12 fresh companies + 4 edge cases on fast +
standard tiers. Outputs go to docs/benchmarks/v9_4/.

Usage:
    uv run python -m scripts.run_heavy_batch

Run sequentially to keep the SQLite cache + Mistral API budget happy.
Each run takes ~2-4 minutes; full batch is ~80-90 minutes of wall time.
Results saved as <slug>_<tier>.md (full report) + _trace.md (timings) +
_grounding.md (evidence) per run.
"""

from __future__ import annotations

import argparse
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


# 12 fresh companies — none tested before, varied industries / geographies / sizes.
REAL_COMPANIES: list[str] = [
    "Tesla",                    # auto + energy, US, large public
    "HSBC",                     # banking, UK, large
    "Nestle",                   # CPG, Swiss, very large
    "SAP",                      # B2B enterprise software, German
    "Sanofi",                   # pharma, French
    "Schneider Electric",       # industrial automation, French
    "Decathlon",                # retail / sports, French private
    "Spotify",                  # consumer media, Swedish
    "Ubisoft",                  # video games, French
    "Air France-KLM",           # airline, dual-listed
    "Bouygues",                 # construction + media + telecom conglomerate, French
    "Hermes",                   # luxury, French
]

# Edge cases — fast tier only (cheaper + their failure modes show up faster).
EDGE_CASES: list[tuple[str, str]] = [
    ("blank input",  ""),
    ("gibberish",    "asdfqwerty"),
    ("small_local",  "Joe's Pizza Shop"),
    ("fake_company", "ZYX Corporation"),
]


async def run_one(company: str, tier: Tier, out_dir: Path) -> dict[str, object]:
    """Run the pipeline once. Returns a result dict for the summary table."""
    slug = (
        company.lower()
        .replace(" ", "_")
        .replace("'", "")
        .replace("-", "_")
        .replace(".", "")
    ) or "blank"
    out_md = out_dir / f"{slug}_{tier.value}.md"

    # Reset settings.tier per run.
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
    refusal_reason: str | None = None
    confidence: float | None = None
    pass_rate: float | None = None
    sales_engineer_ready: bool | None = None

    try:
        result = await execute_pipeline(params)
        if result.refused:
            refused = True
            refusal_reason = result.refusal_reason
            out_md.write_text(
                f"# {company} ({tier.value}) — REFUSED\n\n{result.refusal_reason or '(no reason)'}\n",
                encoding="utf-8",
            )
        elif result.report is not None:
            md = render_report_to_markdown(result.report)
            out_md.parent.mkdir(parents=True, exist_ok=True)
            out_md.write_text(md, encoding="utf-8")
            # Trace next to it
            trace_md = (
                "# Trace\n\n"
                + result.trace.render_markdown()
                + "\n\n## Mermaid sequence\n\n```mermaid\n"
                + result.trace.render_mermaid()
                + "\n```\n"
            )
            (out_md.parent / f"{slug}_{tier.value}_trace.md").write_text(
                trace_md, encoding="utf-8"
            )
            confidence = (
                result.report.meta_review.confidence
                if result.report.meta_review
                else None
            )
            pass_rate = result.report.quality.fact_check_pass_rate
            sales_engineer_ready = (
                result.report.meta_review.sales_engineer_ready
                if result.report.meta_review
                else None
            )
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
        "refusal_reason": refusal_reason,
        "confidence": confidence,
        "pass_rate": pass_rate,
        "sales_engineer_ready": sales_engineer_ready,
        "out_file": str(out_md.relative_to(out_md.parent.parent.parent)),
    }


def write_summary(results: list[dict[str, object]], out_dir: Path) -> None:
    lines = [
        "# v9.4 heavy-batch summary",
        "",
        f"Generated {len(results)} runs across {len({r['company'] for r in results})} companies × {len({r['tier'] for r in results})} tiers.",
        "",
        "## Real companies (fast + standard)",
        "",
        "| Company | Tier | Wall (s) | Confidence | Pass-rate | SE-ready | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.get("error"):
            status = f"❌ ERROR — {r['error']}"
        elif r.get("refused"):
            status = f"⚠️ REFUSED — {r.get('refusal_reason', '')[:60]}"
        else:
            status = "✅ ok"
        conf = f"{r['confidence']:.2f}" if r.get("confidence") is not None else "—"
        pr = f"{r['pass_rate']:.0%}" if r.get("pass_rate") is not None else "—"
        ready = "✓" if r.get("sales_engineer_ready") else ("✗" if r.get("sales_engineer_ready") is False else "—")
        lines.append(
            f"| {r['company']} | {r['tier']} | {r['wall_s']} | {conf} | {pr} | {ready} | {status} |"
        )
    (out_dir / "_summary.md").write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "docs" / "benchmarks" / "v9_4",
    )
    parser.add_argument(
        "--skip-real",
        action="store_true",
        help="Skip the 12 real-company × 2 tier runs (24 total)",
    )
    parser.add_argument(
        "--skip-edge",
        action="store_true",
        help="Skip the 4 edge-case runs",
    )
    args = parser.parse_args()
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Quiet noisy ones
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("mistralai").setLevel(logging.WARNING)

    results: list[dict[str, object]] = []

    # Real companies × 2 tiers — interleave tiers so fast tier results
    # land sooner if you decide to inspect mid-batch.
    if not args.skip_real:
        for company in REAL_COMPANIES:
            for tier in [Tier.FAST, Tier.STANDARD]:
                print(f"\n=== {company} ({tier.value}) ===", flush=True)
                r = await run_one(company, tier, out_dir)
                results.append(r)
                # Write summary after every run so we can inspect partial progress.
                write_summary(results, out_dir)
                print(
                    f"  → wall {r['wall_s']}s, "
                    f"confidence={r.get('confidence')}, "
                    f"pass_rate={r.get('pass_rate')}, "
                    f"status={'ERROR' if r['error'] else ('REFUSED' if r['refused'] else 'ok')}",
                    flush=True,
                )

    # Edge cases — fast tier only.
    if not args.skip_edge:
        for label, company in EDGE_CASES:
            print(f"\n=== EDGE [{label}] {company!r} (fast) ===", flush=True)
            r = await run_one(company or "(empty)", Tier.FAST, out_dir)
            r["edge_label"] = label
            results.append(r)
            write_summary(results, out_dir)
            print(
                f"  → wall {r['wall_s']}s, "
                f"status={'ERROR' if r['error'] else ('REFUSED' if r['refused'] else 'ok')}",
                flush=True,
            )

    write_summary(results, out_dir)
    print(f"\n=== DONE — {len(results)} runs, summary at {out_dir / '_summary.md'} ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
