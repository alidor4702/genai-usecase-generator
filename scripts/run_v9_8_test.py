"""v9.8 — Validate upfront entity resolution.

Mix of:
  - Real companies that work today: Microsoft, Spotify, BNP Paribas, Carrefour
  - Ambiguous short names the resolution should rewrite: "Apple" → Apple Inc.,
    "Hermes" → Hermès International, "Target" → Target Corporation
  - Refusal cases entity resolution should reject in ~2s without burning the
    full pipeline: "asdfqwerty", "ZYX Corporation", "" (empty)
  - 1 borderline: "Joe's Pizza Shop" (real small business)
  - 1 max-tier run on Apple to confirm insane-mode still works on a real
    company after the resolution rewrite

Outputs to docs/benchmarks/v9_8/.
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


TARGETS: list[tuple[str, Tier, str]] = [
    # (input, tier, expected) — "expected" is informational, not enforced
    ("Apple",              Tier.STANDARD, "resolve to Apple Inc., then run"),
    ("Microsoft",          Tier.STANDARD, "resolve, then run"),
    ("Hermes",             Tier.STANDARD, "resolve to Hermès International, then run"),
    ("Carrefour",          Tier.STANDARD, "resolve, then run"),
    ("Joe's Pizza Shop",   Tier.STANDARD, "resolve to Joe's Pizza, then run (low confidence)"),
    ("asdfqwerty",         Tier.STANDARD, "REFUSE via entity resolution"),
    ("ZYX Corporation",    Tier.STANDARD, "REFUSE via entity resolution"),
    ("Apple",              Tier.MAX,      "max insane mode on real company"),
]


async def run_one(company: str, tier: Tier, run_idx: int, out_dir: Path) -> dict[str, object]:
    base_slug = (
        company.lower()
        .replace(" ", "_")
        .replace("'", "")
        .replace("-", "_")
        .replace(".", "")
    ) or "blank"
    slug = f"{base_slug}_{tier.value}_run{run_idx}"
    out_md = out_dir / f"{slug}.md"

    settings.tier = tier
    if not company:
        company = ""
    params_kwargs = {
        "company_name": company or "(empty)",
        "focus_area": FocusArea.GENERAL,
        "weights": CriteriaWeights(),
        "research_depth": ResearchDepth.MEDIUM,
    }
    try:
        params = WorkflowInput(**params_kwargs)
    except Exception as e:
        return {
            "company": company or "(empty)", "tier": tier.value, "run": run_idx,
            "wall_s": 0.0, "error": f"input validation: {type(e).__name__}: {e}",
            "refused": False, "confidence": None, "pass_rate": None, "sales_engineer_ready": None,
        }

    started_at = datetime.now(timezone.utc)
    trace = RunTrace(company_name=params.company_name, started_at=started_at)
    set_current_trace(trace)

    started = time.time()
    error: str | None = None
    refused: bool = False
    refusal_reason: str | None = None
    confidence: float | None = None
    pass_rate: float | None = None
    se_ready: bool | None = None
    resolved_name: str | None = None

    try:
        result = await execute_pipeline(params)
        if result.refused:
            refused = True
            refusal_reason = result.refusal_reason
            out_md.write_text(
                f"# {company or '(empty)'} ({tier.value}) — REFUSED\n\n"
                f"{result.refusal_reason or '(no reason)'}\n",
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
            (out_md.parent / f"{slug}_trace.md").write_text(trace_md, encoding="utf-8")
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
        logger.exception("run_one: %s @ %s run %d FAILED — %s", company, tier.value, run_idx, e)

    wall_s = time.time() - started
    return {
        "company": company or "(empty)",
        "tier": tier.value,
        "run": run_idx,
        "wall_s": round(wall_s, 1),
        "error": error,
        "refused": refused,
        "refusal_reason": refusal_reason,
        "confidence": confidence,
        "pass_rate": pass_rate,
        "sales_engineer_ready": se_ready,
        "resolved_name": resolved_name,
    }


def write_summary(results: list[dict[str, object]], out_dir: Path) -> None:
    lines = [
        "# v9.8 — Upfront entity resolution validation",
        "",
        "Tests that upfront entity resolution (one Mistral Small call at the top of",
        "the pipeline) correctly: (a) rewrites ambiguous short inputs to canonical",
        "names, (b) refuses gibberish/empty/unidentifiable inputs in ~2s, (c) doesn't",
        "regress real-company runs.",
        "",
        "| Input | Tier | Wall (s) | Resolved → | Conf | Pass | SE-ready | Status |",
        "|---|---|---:|---|---:|---:|:-:|---|",
    ]
    for r in results:
        company = str(r["company"])
        tier = str(r["tier"])
        if r.get("error"):
            status = f"ERROR — {r['error'][:60]}"
        elif r.get("refused"):
            status = f"REFUSED — {(r.get('refusal_reason') or '')[:80]}"
        else:
            status = "ok"
        conf = f"{r['confidence']:.2f}" if r.get("confidence") is not None else "—"
        pr = f"{r['pass_rate']:.0%}" if r.get("pass_rate") is not None else "—"
        ready = "✓" if r.get("sales_engineer_ready") else ("✗" if r.get("sales_engineer_ready") is False else "—")
        resolved = str(r.get("resolved_name") or "—")
        lines.append(
            f"| {company} | {tier} | {r['wall_s']} | {resolved} | {conf} | {pr} | {ready} | {status} |"
        )
    (out_dir / "_summary.md").write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    out_dir = Path(__file__).resolve().parent.parent / "docs" / "benchmarks" / "v9_8"
    out_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("mistralai").setLevel(logging.WARNING)

    results: list[dict[str, object]] = []
    for idx, (company, tier, expected) in enumerate(TARGETS, 1):
        label = company or "(empty)"
        print(f"\n=== [{idx}/{len(TARGETS)}] {label!r} ({tier.value}) — expected: {expected} ===", flush=True)
        r = await run_one(company, tier, idx, out_dir)
        results.append(r)
        write_summary(results, out_dir)
        if r["error"]:
            print(f"  → ERROR: {r['error']}", flush=True)
        elif r["refused"]:
            print(f"  → REFUSED in {r['wall_s']}s: {(r['refusal_reason'] or '')[:100]}", flush=True)
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
