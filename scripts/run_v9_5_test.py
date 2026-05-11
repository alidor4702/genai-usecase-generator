"""v9.5 targeted test batch — validate the fixes from the v9.4 analysis.

Targets:
- Spotify (standard) — was 0.48 in v9.4; polish fix should lift confidence
- Hermès (standard)  — was 0.50 in v9.4; same expectation
- Bouygues (standard) — was 0.55 in v9.4; same
- SAP (standard)      — was 0.83 SE-ready; must NOT regress (control)
- ZYX Corporation (fast) — was 0.49 report; should REFUSE (entity-identity)
- "" / blank (fast) — was 0.61 report; should REFUSE (entity-identity)

Outputs to docs/benchmarks/v9_5/.

Run:
    uv run python -m scripts.run_v9_5_test
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
    ("Spotify",          Tier.STANDARD),  # regression case (v9.4: 0.48)
    ("Hermes",           Tier.STANDARD),  # regression case (v9.4: 0.50)
    ("Bouygues",         Tier.STANDARD),  # regression case (v9.4: 0.55)
    ("SAP",              Tier.STANDARD),  # SE-ready control (v9.4: 0.83)
    ("ZYX Corporation",  Tier.FAST),      # edge case → should refuse (entity drift)
    ("asdfqwerty",       Tier.FAST),      # edge case → should refuse (gibberish)
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
        "company": company or "(empty)",
        "tier": tier.value,
        "wall_s": round(wall_s, 1),
        "error": error,
        "refused": refused,
        "refusal_reason": refusal_reason,
        "confidence": confidence,
        "pass_rate": pass_rate,
        "sales_engineer_ready": sales_engineer_ready,
    }


def write_summary(results: list[dict[str, object]], out_dir: Path) -> None:
    lines = [
        "# v9.5 targeted test batch summary",
        "",
        "Validating fixes from v9.4 analysis. Comparison to v9.4 baseline:",
        "",
        "| Company | Tier | v9.4 | v9.5 wall | v9.5 conf | v9.5 pass | SE-ready | Status |",
        "|---|---|---|---:|---:|---:|---|---|",
    ]
    v9_4_baseline = {
        ("Spotify", "standard"): "0.48 / 63%",
        ("Hermes", "standard"): "0.50 / 58%",
        ("Bouygues", "standard"): "0.55 / 62%",
        ("SAP", "standard"): "0.83 / 83% SE-ready",
        ("ZYX Corporation", "fast"): "0.49 / 64% (no refuse)",
        ("(empty)", "fast"): "0.61 / 76% (no refuse)",
    }
    for r in results:
        company = str(r["company"])
        tier = str(r["tier"])
        baseline = v9_4_baseline.get((company, tier), "—")
        if r.get("error"):
            status = f"ERROR — {r['error']}"
        elif r.get("refused"):
            status = f"REFUSED — {(r.get('refusal_reason') or '')[:60]}"
        else:
            status = "ok"
        conf = f"{r['confidence']:.2f}" if r.get("confidence") is not None else "—"
        pr = f"{r['pass_rate']:.0%}" if r.get("pass_rate") is not None else "—"
        ready = "✓" if r.get("sales_engineer_ready") else ("✗" if r.get("sales_engineer_ready") is False else "—")
        lines.append(
            f"| {company} | {tier} | {baseline} | {r['wall_s']}s | {conf} | {pr} | {ready} | {status} |"
        )
    (out_dir / "_summary.md").write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    out_dir = Path(__file__).resolve().parent.parent / "docs" / "benchmarks" / "v9_5"
    out_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("mistralai").setLevel(logging.WARNING)

    results: list[dict[str, object]] = []
    for company, tier in TARGETS:
        label = company or "(empty)"
        print(f"\n=== {label} ({tier.value}) ===", flush=True)
        r = await run_one(company, tier, out_dir)
        results.append(r)
        write_summary(results, out_dir)
        if r["refused"]:
            print(f"  → REFUSED in {r['wall_s']}s: {r['refusal_reason']}", flush=True)
        elif r["error"]:
            print(f"  → ERROR: {r['error']}", flush=True)
        else:
            print(
                f"  → {r['wall_s']}s, conf={r['confidence']}, pass={r['pass_rate']}, SE-ready={r['sales_engineer_ready']}",
                flush=True,
            )

    write_summary(results, out_dir)
    print(f"\n=== DONE — {len(results)} runs, summary at {out_dir / '_summary.md'} ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
