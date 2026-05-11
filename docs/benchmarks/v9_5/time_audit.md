## Time-optimization audit (post-v9.5)

Median standard-tier wall time from v9.4: **167s**. v9.5 (Medium polish) pushed
it to ~210s. v9.6 (Small + strict polish, in flight) should claw back ~30-50s.

Per-step median time across the 11 successful v9.4 standard runs (from
findings.md):

```
research      ~8s
gap_fill      ~5s
generate     ~27s  ← bottleneck #2
score        ~25s  (already parallel — both passes)
verify       ~20s  (already parallel — 3 candidates)
enrich       ~58s  ← bottleneck #1
meta_eval    ~12s
web_verify    ~2s
source_judge ~14s
final_qualify ~2s
quality      ~4s
```

The two real bottlenecks are **enrich (58s)** and **generate (27s)**. Everything
else is small or already parallel.

### Opportunities — ranked by saving × inverse-of-risk

#### 1. Parallel web_search tool calls inside generate (LOW RISK, ~4-8s save)

Generate uses Mistral function-calling: LLM emits tool_calls, we run them, feed
results back, LLM emits another turn. Today the loop runs each tool_call
sequentially via a `for tc in tool_calls` block (`src/activities/generate.py`
lines 511-543). The Mistral model CAN emit multiple tool_calls in one turn —
if it does, we currently serialise them.

Fix: replace the sequential for-loop with `asyncio.gather` across the
tool_call list. Each Tavily search is ~4s; running two in parallel saves ~4s on
standard, ~8s on max.

Risk: none — tool calls are independent reads. The LLM gets the same set of
tool_result messages in the same order.

Implementation: ~15 lines. Test plan: re-run a 4-company batch and compare
generate-phase wall time.

#### 2. enable_parallel_enrich = True (MEDIUM RISK, ~30-40s save)

The single biggest lever. Config knob already exists
(`Settings.enable_parallel_enrich`, default False). When True, the
`select_and_enrich_activity` fires THREE parallel Mistral Large 3 calls — one
per top-3 candidate — instead of one big call drafting all three.

Saves the difference between max(t1, t2, t3) and t1+t2+t3 — roughly
40s out of the ~58s enrich phase.

Risk: each call lacks cross-use-case awareness. The diversity / cross-cutting
concern that the single-call draft can spot (e.g. "all three use cases lean on
the same data asset") gets lost. Phase 3c benchmarked this but didn't ship.

Test plan: enable the flag, re-run Spotify, Hermès, Bouygues, SAP, Decathlon
on standard, READ the actual reports for cross-use-case diversity issues vs
v9.4/v9.6 baseline. NOT just confidence scores — actual content quality.

#### 3. Parallel polish + attribution_check inside select_enrich (LOW RISK, ~5-10s save)

Today these run sequentially in `select_and_enrich_activity`:
```
polish (3 parallel LLM calls) → attribution_check (3 parallel LLM calls)
```

Each is internally parallel across the 3 use cases but the two phases are
sequential. Could run as `asyncio.gather(polish_for_all_3, attribution_for_all_3)`.

Risk: both modify `use_case` in-place (description, why_this_company, etc.).
A simultaneous polish + attribution write to the same field can race. BUT:
- polish removes corpus IDs and adds citations (replaces text)
- attribution rewrites company names attached to corpus IDs (replaces text)
- If polish removes corpus IDs first, attribution has nothing to do — current
  order may be redundant

Alternative: skip attribution_check entirely on tiers where polish already
strips corpus IDs. Cleaner and saves 5-10s.

Test plan: A/B with attribution_check disabled. Diff the reports to see what
attribution was catching that polish doesn't.

#### 4. Parallel final_qualify + quality_signals (NO RISK, ~2-4s save)

In `src/pipeline.py` these run sequentially after source_judge. They're
independent: final_qualify rewrites prose for unsupported claims; quality_signals
computes diversity / specificity scores. No data dependency.

Fix: `asyncio.gather(final_qualify, quality_signals)` in pipeline.py.
Same in workflow.py.

Risk: none — independent reads/writes.

Test plan: just refactor and re-run. Trace timing should show overlap.

#### 5. Source-judge concurrency 8 → 16 (LOW RISK, ~2-3s save)

`src/activities/source_judge.py:407` has `Semaphore(8)`. Was 4 in v8, bumped to
8. Could try 16 — Mistral Small's throughput easily supports it.

Saves ~2-3s on runs with 30+ claims (most standard runs).

Risk: low. Watch for rate-limit errors in the trace.

Test plan: edit, re-run, check trace for failures.

#### 6. Batched source-judge (MEDIUM RISK, ~5-10s save)

Instead of 30-50 separate LLM calls at ~600ms each, batch ALL (claim, source)
pairs into ONE Mistral Small call with a multi-claim prompt. Returns one
JSON with verdicts per claim.

Saves the LLM call overhead (auth + setup + network) across ~30-50 calls.

Risk: prompt context grows large (~30 claims × 1500-char snippet ≈ 50K chars),
JSON output schema more complex, harder to debug per-claim failures. Also
loses concurrency benefit.

Probably not worth it given (5) is simpler. Skip unless we see a clear win.

#### 7. Web-verify rescue Tavily concurrency 3 → 6 (LOW RISK, ~1-3s save)

`_TAVILY_CONCURRENCY = 3` in `src/activities/web_verify.py:73`. Could go to 6.
Saves ~1-3s on rescue-heavy runs (e.g. Hermès v9.4 spent 14.9s on rescue).

Risk: Tavily rate-limit. Generally OK at 6.

#### 8. Meta-eval max_tokens 10K → 8K (NO SAVE, just defensive)

`src/activities/meta_evaluate.py:276` has `max_tokens=10_000`. Most runs use
~3-5K. The cap doesn't slow us, but tightening to 8K is defensive against
runaway generation. Not a real time saver.

### Net potential

If items 1, 3 (skip attribution), 4, 5, 7 all ship — the SAFE set —
estimated total saving: **8-15s** on standard. New median: ~150s.

If item 2 (parallel_enrich) also ships — **+30-40s saving on top, total
~38-55s** save. New median: ~120-130s. With quality risk on cross-use-case
awareness.

If we also keep v9.6's Small polish (which v9.6 batch in flight is testing) —
that adds back ~20-30s of headroom.

### Test plan summary

I'd suggest staging:

**Phase A (no-risk):** ship items 4, 5, 7. Re-run the 4 standard companies.
Expect ~5-10s save with no quality change.

**Phase B (low-risk):** ship item 1 (parallel tool calls in generate). Re-run.
Expect another ~4-8s save.

**Phase C (medium-risk, needs quality A/B):** enable item 2 (parallel_enrich).
Re-run the 4 companies AND the SE-ready controls. READ the actual reports for
cross-use-case diversity quality. If reports look fine, ship.

**Phase D (architectural):** consider whether attribution_check is still
needed after stricter polish. If polish removes corpus IDs reliably,
attribution can be retired entirely — saves 5-10s and reduces code.
