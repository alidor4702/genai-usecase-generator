"""Activities for the non-GENERATE actions exposed via WorkflowInput.action.

`history` and `architecture` are lightweight read-only paths the user can pick
from the Le Chat entry form so the assistant feels less single-purpose. Both
return a markdown blob the workflow class can ship directly via a single
ChatAssistantWorkflowOutput.

Implemented as activities (not workflow code) because:
  - `history` does SQLite I/O via `src.db.list_runs`
  - `architecture` doesn't strictly need an activity but living next to
    history keeps the alt-action surface uniform

Both have short timeouts — they're not long-running operations.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import mistralai.workflows as workflows

from src.db import list_runs

logger = logging.getLogger(__name__)


def _format_runs_md(rows: list[dict[str, object]]) -> str:
    if not rows:
        return (
            "No past runs found in the local SQLite history yet. "
            "Pick **Generate use cases** and run a company to start populating history."
        )
    lines: list[str] = [
        "## Recent runs",
        "",
        "The 10 most recent pipeline runs from the local SQLite history. "
        "Each row shows the company, status, wall time, and the meta-evaluator's "
        "confidence + fact-check pass rate.",
        "",
        "| When | Company | Status | Wall | Confidence | Pass-rate | SE-ready |",
        "|---|---|---|---:|---:|---:|:---:|",
    ]
    for row in rows[:10]:
        company = str(row.get("company_name") or "(unknown)")
        status = str(row.get("status") or "?")
        started_at = row.get("started_at")
        completed_at = row.get("completed_at")
        when = "—"
        wall = "—"
        if isinstance(started_at, (int, float)):
            dt = datetime.fromtimestamp(int(started_at), tz=timezone.utc)
            when = dt.strftime("%Y-%m-%d %H:%M")
            if isinstance(completed_at, (int, float)):
                wall = f"{int(completed_at) - int(started_at)}s"
        conf = row.get("meta_eval_confidence")
        conf_str = f"{float(conf):.2f}" if isinstance(conf, (int, float)) else "—"
        pr = row.get("fact_check_pass_rate")
        pr_str = f"{float(pr):.0%}" if isinstance(pr, (int, float)) else "—"
        se = row.get("sales_engineer_ready")
        se_str = "✓" if se == 1 else ("✗" if se == 0 else "—")
        if status == "refused":
            wall = "(refused)"
            conf_str = pr_str = se_str = "—"
        elif status == "failed":
            wall = "(failed)"
            conf_str = pr_str = se_str = "—"
        lines.append(
            f"| {when} | **{company}** | {status} | {wall} | {conf_str} | {pr_str} | {se_str} |"
        )
    lines.append("")
    lines.append(
        "_Tip: open the standalone web app's `/history` page for full reports + "
        "the ability to re-open any past run._"
    )
    return "\n".join(lines)


_ARCHITECTURE_SUMMARY_MD = """\
## How this works — 14-step pipeline

Five phases process the company name → 3 customer-ready use cases. Every
side-effect lives in a Mistral Workflows activity with an explicit
timeout; the orchestrator class is pure routing.

**Phase 1 · Research & retrieve.** Parallel Wikipedia + Tavily news +
careers + existing-AI-initiatives + verified-companies-index fetch.
Synthesized into a typed `CompanyContext` by Mistral Medium 3.5 at T=0.2.
Gap-fill agent runs targeted Tavily queries for any empty list fields.
Cosine top-k over the 2,150-deployment precedent corpus retrieves peer
examples.

**Phase 2 · Candidate generation.** Mistral Medium 3.5 at T=0.7 with a
`web_search` tool (Tavily function-calling, budget by tier) drafts 8
candidate use cases. Mistral Small × 2 parallel passes (T=0.2 + T=0.4)
scores each on the five criteria for self-consistency. Top-3 verified
against the live web with Tavily + Mistral Small at T=0.1.

**Phase 3 · Enrich top-3.** Mistral Large 3 at T=0.4 drafts customer-
ready prose for the three winners. Polish (Mistral Small at T=0.1)
converts opaque ev-IDs to markdown links with a strict citation
discipline. Meta-evaluator (Mistral Medium 3.5) splits the prose into
atomic claims and matches each against the full evidence pool.

**Phase 4 · Verification chain.** Web-verify rescue runs targeted
Tavily searches for unsupported claims through a two-tier credibility
gate. Source-judge (Mistral Small at T=0.1) reads every cited source
against its claim and rejects false positives. Final-qualify rewrites
remaining unanchored numbers + named entities qualitatively so the
prose doesn't ship unverified specifics.

**Phase 5 · Output.** Quality signals (diversity, specificity,
fact-check pass rate) computed. Report persisted to the SQLite runs
table for history. Rich UI Components composed for Le Chat; markdown
fallback for the standalone web app + CLI.

---

**Want the interactive version?** Open `/architecture` in the
standalone web app — every step in the diagram is clickable, with
model + temperature + timeout + reads/writes/why-activity for each.
Or read `docs/architecture.md` in the repo.
"""


@workflows.activity(start_to_close_timeout=timedelta(seconds=15))
async def list_recent_runs_activity() -> str:
    """List the last 10 persisted runs as a markdown table."""
    try:
        rows = await list_runs(limit=10, offset=0)
    except Exception as e:  # noqa: BLE001
        logger.warning("list_recent_runs: failed: %s", type(e).__name__)
        return (
            "Couldn't read the runs table — the SQLite database might not exist "
            "yet (no runs have been completed) or there's a transient I/O error."
        )
    return _format_runs_md(rows)


@workflows.activity(start_to_close_timeout=timedelta(seconds=5))
async def architecture_summary_activity() -> str:
    """Return the markdown architecture summary for the ARCHITECTURE action."""
    return _ARCHITECTURE_SUMMARY_MD
