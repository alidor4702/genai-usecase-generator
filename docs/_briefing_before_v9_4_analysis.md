# Pre-analysis briefing — v9.4 heavy batch state

Written 2026-05-11 right before the deep analysis pass. The intent is
that post-compact me can re-read this file in one shot and pick up the
state.

## Project

Compastral / GenAI Use Case Generator — Mistral Proto Team Applied AI
Engineer take-home. Repo:
`/Users/alidor/Desktop/Programming Tings/MistralInterview1/Interview2`.
GitHub `alidor4702/genai-usecase-generator`. Deployed: Vercel
`compastral.vercel.app`, Render API `compastral-api.onrender.com`.

Branch: `main`, last commit `04b46cd` (audit fixes).

## Pipeline shape (the activity sequence)

  Step 1   research (parallel Wikipedia + Tavily news + jobs + existing
           initiatives) → CompanyContext + EvidenceLedger
  Step 1b  enrich_company_context — gap-fill missing fields via 4
           parallel Tavily probes + Mistral synthesis
  Confidence gate — refusal if research_confidence < 0.5 AND not
                    verified AND zero existing AI initiatives
  Step 2   retrieve_precedents — cosine over 2,150-entry corpus, top-8
  Step 3   generate_candidates — Mistral Medium @ T=0.7 + web_search
           tool (2-4 calls depending on tier). Produces N=8 candidates,
           ≥3 must be novel_direction.
  Step 4   score_candidates — Mistral Small × 2 parallel passes @
           T=0.2 / T=0.4, weighted by 5 criteria
  Step 5   verify_top_candidates — top-3 × Tavily + Mistral Small
           verdict (pass / partial_overlap / confirmed_existing)
  Step 6   select_and_enrich — Mistral Large 3 @ T=0.4 drafts customer-
           ready prose for top-3. Polish + attribution check on
           standard / max; skipped on fast.
  Step 7   meta_evaluate → web_verify rescue → source_judge → final_qualify
  Render   render_report_activity → markdown + UIComponent tree +
           mermaid canvases → ChatAssistantWorkflowOutput

Surfaces: Le Chat assistant (Rich UI Components + mermaid canvases),
standalone web app (Next.js + FastAPI on Render), CLI
(`scripts/run_example.py`).

## Heavy batch v9.4 — 28 runs

Script: `scripts/run_heavy_batch.py` (NOT yet committed — will commit
with analysis findings).

Outputs in `docs/benchmarks/v9_4/`:
  - `<slug>_<tier>.md` — full markdown report
  - `<slug>_<tier>_trace.md` — per-activity timing breakdown
  - `<slug>_<tier>_grounding.md` — evidence ledger (commented out in
    the script — needs to be added back; currently we don't write the
    grounding addendum)
  - `_summary.md` — wall-time + confidence + pass-rate per run

12 real companies × 2 tiers + 4 edge cases × fast tier = 28 runs.

### Verbatim summary table

```
Tesla              | fast      | 127.5s | 0.41 | 56%  | ✗ | ok
Tesla              | standard  | 123.1s | —    | —    | — | ERROR — "Report must contain exactly 3 use cases, got 1"
HSBC               | fast      | 119.4s | 0.80 | 95%  | ✗ | ok
HSBC               | standard  | 179.2s | 0.77 | 92%  | ✗ | ok
Nestle             | fast      | 148.5s | 0.64 | 79%  | ✗ | ok
Nestle             | standard  | 164.7s | 0.66 | 81%  | ✗ | ok
SAP                | fast      | 123.5s | 0.74 | 89%  | ✗ | ok
SAP                | standard  | 159.7s | 0.83 | 83%  | ✓ | ok     ← SE-ready
Sanofi             | fast      | 174.5s | 0.67 | 82%  | ✗ | ok
Sanofi             | standard  | 157.2s | 0.73 | 88%  | ✗ | ok
Schneider Electric | fast      | 137.7s | 0.62 | 67%  | ✗ | ok
Schneider Electric | standard  | 167.9s | 0.73 | 88%  | ✗ | ok
Decathlon          | fast      | 117.1s | 0.78 | 93%  | ✗ | ok
Decathlon          | standard  | 220.9s | 0.92 | 92%  | ✓ | ok     ← SE-ready
Spotify            | fast      | 108.0s | 0.85 | 100% | ✗ | ok     ← best confidence/pass-rate
Spotify            | standard  | 169.7s | 0.48 | 63%  | ✗ | ok     ← fast > standard
Ubisoft            | fast      | 108.6s | 0.56 | 71%  | ✗ | ok
Ubisoft            | standard  | 158.7s | 0.66 | 81%  | ✗ | ok
Air France-KLM    | fast      | 101.7s | 0.61 | 69%  | ✗ | ok
Air France-KLM    | standard  | 183.9s | 0.79 | 94%  | ✗ | ok
Bouygues           | fast      | 115.9s | 0.60 | 75%  | ✗ | ok
Bouygues           | standard  | 187.2s | 0.55 | 62%  | ✗ | ok     ← fast > standard
Hermes             | fast      | 105.6s | 0.78 | 93%  | ✗ | ok
Hermes             | standard  | 178.1s | 0.50 | 58%  | ✗ | ok     ← fast > standard

(empty)            | fast      | 102.5s | 0.61 | 76%  | ✗ | ok     ← edge case
asdfqwerty         | fast      | 105.5s | 0.40 | 55%  | ✗ | ok     ← edge case
Joe's Pizza Shop   | fast      | 113.2s | 0.39 | 54%  | ✗ | ok     ← edge case
ZYX Corporation    | fast      | 105.1s | 0.49 | 64%  | ✗ | ok     ← edge case
```

### High-level patterns visible from the summary

- **1 hard ERROR**: Tesla standard — `ValidationError: 1 validation error for Report`, `Report must contain exactly 3 use cases, got 1`. Worth investigating — the enrich call returned 1 use case instead of 3. Likely Mistral Large 3 either truncated or returned a partial JSON.
- **Only 2 SE-ready** (≥0.70 confidence AND sales_engineer_ready=True): SAP standard 0.83, Decathlon standard 0.92.
- **Fast > standard quality on 3 of 12** companies: Spotify (0.85 vs 0.48), Hermès (0.78 vs 0.50), Bouygues (0.60 vs 0.55), Decathlon (0.78 fast 93% — but standard was higher at 0.92). High variance.
- **Edge cases all completed gracefully** — no crashes. Empty input even produced a 0.61-confidence report (Mistral hallucinated a company). Worth flagging.
- **Wall time stable**: fast 100-175s (median ~118), standard 123-221s (median ~165). Confirms Phase 3 baselines.

### What to investigate during deep analysis

1. **Tesla standard ERROR** — read tesla_standard_trace.md to find which step crashed
2. **Fast > standard cases** (Spotify, Hermès, Bouygues) — is this real signal or LLM noise?
3. **Edge case quality** — the 0.61 confidence on empty input suggests the refusal path didn't fire — why?
4. **Cross-tier confidence drift** — Decathlon goes 0.78 → 0.92 (gain), Spotify goes 0.85 → 0.48 (loss). What changes?
5. **Pass rate vs confidence correlation** — Spotify fast has 100% pass-rate but only 0.85 conf. Are these signalling different things?
6. **Common failure modes in low-confidence reports** — empty fields? Duplicate use cases? Cited but unsupported claims?
7. **Per-activity timing** — does enrich dominate as in Phase 3?
8. **Did the refresh-corruption issue surface again** (empty top_implementation_risk / time_to_value / example_output)?
9. **Mermaid renders** — any sanitizer failures in the actual diagrams?

## Three pending fixes — held until user approves after seeing analysis

### A. Hyperlinks fix

Problem: LLM produces inline references like `[Aptori precedent]` or
`(GitGuardian documentation)` in `why_this_company` prose. These look
like markdown links but are bracket-text only — render as plain text,
look broken.

Fix plan:
  1. Add hard rule to `src/prompts.py ENRICHMENT_SYSTEM`: "Do NOT
     write attributive phrases in [brackets] or (parens) unless they
     are either (a) `(ev-XXX)` evidence references the polish step
     will hyperlink, or (b) literal markdown links `[text](url)` you
     control. Bare `[X]` and `(X)` render as plain text and look
     broken."
  2. Post-process strip: in `_coerce_enriched` (NOT polish — fast
     tier skips polish so post-process must be in coerce), regex
     out bare `[X]` patterns that aren't proper markdown links and
     aren't `(ev-...)` refs. Replace with italicised text or strip
     brackets entirely.

Effort: ~15 min. Time impact: <100ms. Applies to all tiers if
post-process lives in coerce (NOT polish).

### B. Query-length clamp on generate's web_search tool

Problem: When LLM uses the `web_search` tool inside generate, it can
write verbose queries > Tavily's 400-char cap. Tavily returns a 400
error with body "Maximum query length: 400 characters". Our code logs
"Tavily failed: <err>" and returns empty results — visible in the
batch logs.

Fix: 1-line clamp in `src/activities/generate.py:_web_search_tool_handler`:
  `query = query[:350]` before `await tavily.search(query=query, ...)`

Effort: ~3 min. Time impact: 0s. Applies to standard (budget=2) and
max (budget=4); fast doesn't use the tool (budget=0).

### C. Deep-read content bumps

Currently the AI sees only 3-8K chars per source. Mistral context
window can handle much more. Bumping:
  - `src/activities/generate.py:390` max_chars=3000 → 6000
  - `src/activities/verify_per_candidate.py` max_chars=6000 → 10000
  - `src/activities/web_verify.py` max_chars=8000 → 12000

Effort: ~5 min. Time impact: +1-5s per run depending on tier (more
chars to parse + more LLM input tokens). Cost: +$0.01-0.03 per run.
Benefit: 2× content per source → likely improves grounding density
(fewer unsupported claims).

Tier translation:
  - Fast: verify deep-read applies → +1-1.5s
  - Standard: all three apply → +1.5-3s
  - Max: all three at full force → +2-5s

## Recent commits worth knowing

- `04b46cd` — fix: 4 audit bugs + 12 stale-comment / dead-code cleanups
  (red bugs: Any-import, req.tier dropped, self_consistency_passes,
  redundant bundle re-fetch)
- `c03bfbf` — revert streaming enrich (raw JSON was ugly in chat)
- `667e6a6` — feat: stream the enrich phase + rate_limit on every
  external-API activity (streaming reverted in c03bfbf, rate_limit
  kept)
- `0c40817` — fix(le-chat + render): six small but visible quality fixes
- `1b00352` — canvas editing for human-in-the-loop refinement
- `4aa9ca4` — Rich UI Components for the report
- `acaac84` — Phase 3 benchmark suite + experimental flags

## Open queued items (NOT in this turn's scope)

- Empty-fields validator — re-LLM critical fields when blank. ~45min.
- Eval harness — `tests/eval/gold_examples.jsonl` + `run_eval.py` +
  LLM-as-judge. ~2h. Closes methodology.md gap.
- Architecture.md output-rendering rewrite — describe Rich UI tree we
  actually ship. ~30min.

## User preferences (from MEMORY.md + observation)

- Plain commit messages, NEVER `Co-Authored-By` or other Claude trailers
- Don't touch `Interview1/` or parent `.git/`
- Locked architecture per CLAUDE.md (Mistral Workflows, locked models,
  Pydantic v2, FastAPI, Next.js 14 App Router)
- Hard donts: no datetime.now in workflow.py, no LangChain / LangGraph /
  CrewAI, no microservices, no Redux, no Playwright/Lightpanda except
  the documented Lightpanda CDP fallback for jobs research
- Wants honest framing, ships visible work fast, dislikes vague summaries
- The user's name is Ali (ali4702@gmail.com)

## What I'll do after compact

1. **Read every report in `docs/benchmarks/v9_4/`** systematically
   (28 markdown files + 28 trace files). Use targeted `grep` + Read
   with offset/limit to be token-efficient.
2. **Build a deep findings doc** at `docs/benchmarks/v9_4/findings.md`
   covering:
     - Per-tier timing distribution + outliers (with median, range,
       per-step breakdowns)
     - Quality patterns (which companies hit SE-ready, which floored,
       what predicts success)
     - Failure modes (Tesla error root-cause, low-confidence patterns,
       empty-fields rate)
     - Edge case behaviour (refusal path, hallucinated reports for
       gibberish input)
     - Concrete recommendations: what to fix, what's working,
       priority order
3. **Commit + push** the heavy-batch script, the 28 output files, and
   the findings doc together in one commit
4. **Then ask** the user what to do next — ship A/B/C, fix the Tesla
   error specifically, build the empty-fields validator, etc.

## Files I'll most need to read post-compact

- `docs/benchmarks/v9_4/_summary.md` (already in this briefing)
- `docs/benchmarks/v9_4/tesla_standard_trace.md` (for the ERROR
  investigation)
- A representative sample of report files for quality analysis:
  - `docs/benchmarks/v9_4/spotify_fast.md` (best run)
  - `docs/benchmarks/v9_4/sap_standard.md` (SE-ready run)
  - `docs/benchmarks/v9_4/decathlon_standard.md` (other SE-ready)
  - `docs/benchmarks/v9_4/joes_pizza_shop_fast.md` (edge case)
  - `docs/benchmarks/v9_4/blank_fast.md` (empty input edge case)
  - `docs/benchmarks/v9_4/hermes_standard.md` (fast > standard case)
- Selected trace files for timing audit:
  - All 12 standard traces (compute per-step medians)
  - All 12 fast traces (compute per-step medians)
- `src/activities/select_enrich.py` (Tesla ERROR root cause — the
  Report constructor validates exactly-3-use-cases somewhere)
