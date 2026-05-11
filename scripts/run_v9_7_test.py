"""v9.7 test batch — validate the new generate-prompt hardening + meta-eval
banner UX + max-tier insane mode.

3 differing companies × 3 standard runs each (averages variance):
  - L'Oréal      (beauty / consumer goods, French)
  - BNP Paribas  (banking, French)
  - Carrefour    (retail, French)

Plus 1 max-tier insane-mode run on Hermès — test the polish-on-Large +
critique-revise pass + budget-6 web_search + 16K deep-read combo.

Outputs to docs/benchmarks/v9_7/.

Run:
    uv run python -m scripts.run_v9_7_test
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


# 3 companies × 3 standard runs to average over variance, plus 1 max run.
TARGETS: list[tuple[str, Tier, int]] = [
    # company, tier, run_id (1..3 for 3-pack, 1 for max)
    ("L'Oréal",     Tier.STANDARD, 1),
    ("L'Oréal",     Tier.STANDARD, 2),
    ("L'Oréal",     Tier.STANDARD, 3),
    ("BNP Paribas", Tier.STANDARD, 1),
    ("BNP Paribas", Tier.STANDARD, 2),
    ("BNP Paribas", Tier.STANDARD, 3),
    ("Carrefour",   Tier.STANDARD, 1),
    ("Carrefour",   Tier.STANDARD, 2),
    ("Carrefour",   Tier.STANDARD, 3),
    ("Hermes",      Tier.MAX,      1),  # insane-mode test
]


async def run_one(company: str, tier: Tier, run_id: int, out_dir: Path) -> dict[str, object]:
    base_slug = (
        company.lower()
        .replace(" ", "_")
        .replace("'", "")
        .replace("-", "_")
        .replace(".", "")
    ) or "blank"
    slug = f"{base_slug}_{tier.value}_run{run_id}"
    out_md = out_dir / f"{slug}.md"

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
    sales_engineer_ready: bool | None = None

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
            (out_md.parent / f"{slug}_trace.md").write_text(
                trace_md, encoding="utf-8"
            )
            confidence = (
                result.report.meta_review.confidence
                if result.report.meta_review else None
            )
            pass_rate = result.report.quality.fact_check_pass_rate
            sales_engineer_ready = (
                result.report.meta_review.sales_engineer_ready
                if result.report.meta_review else None
            )
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
        logger.exception("run_one: %s @ %s run %d FAILED — %s", company, tier.value, run_id, e)

    wall_s = time.time() - started
    return {
        "company": company,
        "tier": tier.value,
        "run_id": run_id,
        "wall_s": round(wall_s, 1),
        "error": error,
        "refused": refused,
        "confidence": confidence,
        "pass_rate": pass_rate,
        "sales_engineer_ready": sales_engineer_ready,
    }


def write_summary(results: list[dict[str, object]], out_dir: Path) -> None:
    lines = [
        "# v9.7 — hardened generate prompt + meta-eval UX fix + max insane mode",
        "",
        "**Standard runs (3× per company)** — averages variance to compare model variants honestly.",
        "**Max run** — tests the insane-mode polish-on-Large + critique-revise pass.",
        "",
        "| Company | Tier | Run | Wall (s) | Conf | Pass | SE-ready | Status |",
        "|---|---|---:|---:|---:|---:|:---:|---|",
    ]
    for r in results:
        company = str(r["company"])
        tier = str(r["tier"])
        run_id = r["run_id"]
        if r.get("error"):
            status = f"ERROR — {r['error']}"
        elif r.get("refused"):
            status = "REFUSED"
        else:
            status = "ok"
        conf = f"{r['confidence']:.2f}" if r.get("confidence") is not None else "—"
        pr = f"{r['pass_rate']:.0%}" if r.get("pass_rate") is not None else "—"
        ready = "✓" if r.get("sales_engineer_ready") else ("✗" if r.get("sales_engineer_ready") is False else "—")
        lines.append(
            f"| {company} | {tier} | {run_id} | {r['wall_s']} | {conf} | {pr} | {ready} | {status} |"
        )
    # Aggregate stats per company
    by_company: dict[str, list[dict[str, object]]] = {}
    for r in results:
        if r.get("error") or r.get("refused"):
            continue
        if r.get("confidence") is None:
            continue
        key = f"{r['company']} / {r['tier']}"
        by_company.setdefault(key, []).append(r)
    if by_company:
        lines.append("")
        lines.append("## Per-company averages (excluding errors / refusals)")
        lines.append("")
        lines.append("| Group | n | Mean wall | Mean conf | Mean pass | SE-ready count |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for group, rows in by_company.items():
            n = len(rows)
            mean_wall = sum(float(r["wall_s"]) for r in rows) / n
            mean_conf = sum(float(r["confidence"]) for r in rows) / n
            mean_pass = sum(float(r["pass_rate"]) for r in rows if r.get("pass_rate") is not None) / max(1, sum(1 for r in rows if r.get("pass_rate") is not None))
            se_count = sum(1 for r in rows if r.get("sales_engineer_ready"))
            lines.append(
                f"| {group} | {n} | {mean_wall:.1f}s | {mean_conf:.2f} | {mean_pass:.0%} | {se_count}/{n} |"
            )
    (out_dir / "_summary.md").write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    out_dir = Path(__file__).resolve().parent.parent / "docs" / "benchmarks" / "v9_7"
    out_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("mistralai").setLevel(logging.WARNING)

    results: list[dict[str, object]] = []
    for company, tier, run_id in TARGETS:
        print(f"\n=== {company} ({tier.value}) run {run_id} ===", flush=True)
        r = await run_one(company, tier, run_id, out_dir)
        results.append(r)
        write_summary(results, out_dir)
        if r["error"]:
            print(f"  → ERROR: {r['error']}", flush=True)
        elif r["refused"]:
            print(f"  → REFUSED in {r['wall_s']}s", flush=True)
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
