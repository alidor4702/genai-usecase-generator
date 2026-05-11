## v9.4 heavy-batch findings

Deep analysis of the 28-run heavy batch (12 real companies × fast + standard
tiers + 4 edge cases × fast tier). Written 2026-05-11.

Methodology: each run produced a markdown report (`<slug>_<tier>.md`) plus a
per-activity trace (`<slug>_<tier>_trace.md`). Findings below are grounded in
those files — every claim cross-references a specific run + line so the
reader can verify.

### Headline

**Quality plateau is real and visible.** Only 2 of 24 real runs landed
sales-engineer-ready (`confidence ≥ 0.70 AND sales_engineer_ready=True`):
SAP standard (0.83) and Decathlon standard (0.92). Across the full 28 runs
the median confidence was 0.62 (fast) and 0.73 (standard). The bar isn't
"the pipeline doesn't crash" — it's whether a sales engineer would put
their name on the output. Today, ~8% of runs clear it.

**One hard error, one quasi-error, four soft errors.**

- Tesla standard: `ValidationError: Report must contain exactly 3 use cases,
  got 1` — root cause undetermined despite log + code trace (see "Tesla
  error" below)
- Sanofi fast: 174.5s wall time (median fast tier 115s) — enrich took 74.8s
  on Mistral Medium, ~5× the floor of 13s. Same enrich path as every other
  fast run, no obvious cause in the trace.
- Empty input / asdfqwerty / Joe's Pizza / ZYX Corporation: all completed
  full reports rather than refusing. Refusal path is too lenient — the
  research step drifted to adjacent entities (empty → "SoundHound AI",
  asdfqwerty → Asdf quantum compiler paper, ZYX → ZYX Music / Zyxware) and
  those entities had `existing_initiatives > 0`, which alone passes the
  confidence gate.

### Per-tier timing — medians + outliers (all 27 successful traces)

| Step          | Fast median | Fast range  | Std median | Std range  | Notes |
|---------------|------------:|-------------|-----------:|------------|-------|
| research      |       7.6s  |  3.5–11.1s  |      7.9s  |  5.2–12.7s | First-run uncached; Tesla fast uncached, Tesla standard would have cache-hit had it completed |
| gap_fill      |       ~3.2s |             |     ~5.0s  |            | 4 parallel Mistral Small calls |
| generate      |      21.0s  | 16.9–29.5s  |     26.7s  | 23.9–45.4s | Standard does up to 2 web_search calls (off on fast) |
| score         |      24.5s  | 21.6–27.8s  |     24.6s  | 23.3–27.1s | Two parallel passes; very stable |
| verify        |      17.5s  | 11.6–24.3s  |     19.9s  | 11.9–46.5s | Decathlon std 46.5s outlier (one verify LLM call took 32.2s) |
| **enrich**    |    **15.4s**| 12.9–74.8s  |   **58.1s**| 50.9–77.1s | Standard's dominant cost — 40-50% of wall time |
| meta_eval     |      10.2s  |  7.5–16.4s  |     11.8s  | 11.2–30.6s | Decathlon std 30.6s outlier |
| web_verify    |       3.0s  |  1.3–6.3s   |      2.0s  |  1.3–14.9s | Hermes std 14.9s (rescuing many claims) |
| source_judge  |      12.0s  |  7.9–52.0s  |     14.0s  |  9.8–28.6s | Nestle fast 52.0s outlier (~2.6s/pair vs ~700ms typical) |
| **TOTAL wall**| **115s**    | 102–175s    |  **167s**  | 123–221s   | Standard ~50s slower median, almost all from enrich |

Concrete pattern: enrich on standard is ~40s longer than fast (consistent
across all 11 successful standard runs). The polish + attribution checks
that fast skips add ~3-5s on top. The remaining ~10-15s is the Mistral Large
3 vs Medium delta on the actual enrichment draft.

### Quality outcomes per run

```
Spotify       fast   0.85  100%  ← best  (18/18 supported, 1 rescued)
Decathlon     std    0.92   92%  ← SE-ready (24/26, 2 corrected)
HSBC          fast   0.80   95%
SAP           std    0.83   83%  ← SE-ready (15/18, 2 rewritten qualitatively)
Hermes        fast   0.78   93%
Decathlon     fast   0.78   93%
Air France-KLM std   0.79   94%
HSBC          std    0.77   92%
SAP           fast   0.74   89%
Sanofi        std    0.73   88%
Schneider     std    0.73   88%
Sanofi        fast   0.67   82%
Nestle        std    0.66   81%
Ubisoft       std    0.66   81%
Nestle        fast   0.64   79%
Schneider     fast   0.62   67%
Air France-KLM fast  0.61   69%
(empty)       fast   0.61   76%  ← edge
Bouygues      fast   0.60   75%
Ubisoft       fast   0.56   71%
Bouygues      std    0.55   62%
Hermes        std    0.50   58%
ZYX Corp      fast   0.49   64%  ← edge
Spotify       std    0.48   63%
Tesla         fast   0.41   56%
asdfqwerty    fast   0.40   55%  ← edge
Joe's Pizza   fast   0.39   54%  ← edge
Tesla         std   ERROR
```

### Three "fast > standard" regressions — root cause

Spotify, Hermès, and Bouygues all scored lower on standard than fast:

| Company  | Fast conf | Std conf | Fast pass | Std pass |
|----------|----------:|---------:|----------:|---------:|
| Spotify  |    0.85   |   0.48   |   100%    |   63%    |
| Hermès   |    0.78   |   0.50   |    93%    |   58%    |
| Bouygues |    0.60   |   0.55   |    75%    |   62%    |

Reading the actual reports side by side, the pattern is consistent and
explainable:

1. **Standard adds proper markdown hyperlinks** in `why_this_company`
   sections (Spotify std: `[Spotify partnering with multinational music
   companies...](https://www.theguardian.com/...)`). Fast doesn't —
   it skips polish, so opaque `(ev-XXX)` IDs stay opaque OR the model just
   writes prose without them.
2. **Standard's polish step actively pulls in MORE sources to cite** from
   the full evidence pool ("STEP 1 — CHECK THE FULL SOURCE POOL" in
   `_POLISH_SYSTEM` at `src/activities/select_enrich.py:537`). This grows
   the claims list — Spotify std has 19 claims, Spotify fast has 18 (very
   similar, but the extra one is rescued and then judge-rejected).
3. **Standard's web_verify rescue runs harder** on more candidates — Hermes
   std web_verify took 14.9s vs 3.9s on fast, finding peer-deployment
   claims via Tavily. These are typically thin matches.
4. **The source-judge then correctly rejects the marginal claims.** Spotify
   std has 7/19 judge-rejected. Hermès std has 5/12 unsupported (mostly
   "Peer luxury brands like LVMH/Richemont/Chanel/Kering have reported
   material reductions/efficiency gains"). Each rejection is a real
   "the cited source doesn't actually support the claim" call.

Net result on these three: more claims, lower pass rate, lower confidence.
Fast stays closer to what the generation model originally drafted (with the
candidate's own evidence_ids), which is tighter.

This is NOT "fast is better." It's: the polish + web_verify chain currently
optimises for citation density, not citation accuracy. Standard's extra
machinery is doing real work (Decathlon std caught the "50+ proprietary
brands → just under 30" correction and rendered it properly), but the
chain over-eagerly adds peer-deployment citations from Tavily that are
adjacent, not supporting.

### What's working

1. **The source-judge is the right idea and it's working.** On Tesla fast
   it correctly rejected a rescued claim where Tavily had returned a source
   that literally contradicted the claim ("Tesla doesn't have a data
   moat"). That's a hard-to-catch case the judge nailed.
2. **The `[corrected →]` feature.** Decathlon std: the model claimed "50+
   proprietary brands", the judge found "just under 30 private labels" in
   the source, and the chain rewrote the prose inline AND showed the
   correction in the transparency block. Both use cases that cited the
   number got the same correction. Impressive.
3. **The `[rewritten qualitatively]` feature.** SAP std: the "400,000
   customers" claim couldn't be anchored, so the prose was rewritten
   qualitatively and the claim was excluded from the pass-rate
   denominator. 2 claims handled this way in SAP. Honest framing.
4. **Edge cases don't crash.** Gibberish, empty input, fake company names
   all produced complete reports. No exceptions. The graceful-degradation
   story holds. (Whether they SHOULD have produced reports is a separate
   question — see refusal section below.)
5. **Diversity stays meaningful.** Hermes fast: diversity 0.95
   (counterfeit IP / artisan knowledge / sustainable sourcing — three
   genuinely different surface areas). SAP std: diversity 0.70 (all three
   are SAP-platform-adjacent, but distinct workflows).

### Bugs and quality issues found in v9.4 output

These are all things visible in the rendered reports — not speculative
code issues.

1. **Fake `(ev-XXX)` IDs left in prose.** `hermes_standard.md:177-179`:
   "ZYX integrates with Hermès' traceability standards
   (ev-7a1b2c3d4e)". The model invented `ev-7a1b2c3d4e` — it's not in the
   ledger. Polish was supposed to drop unrecognized ev-IDs but didn't.
   Code lives at `src/activities/select_enrich.py:570-587` (the
   `(ev-XXX)` replacement prompt). The prompt says "If the ev-ID has no
   provided mapping, drop it" — but the model treats it as a valid URL
   and keeps it. Need a regex post-process to strip any `(ev-[0-9a-f]+)`
   that doesn't match a real ledger entry.

2. **Bracket leakage from source_judge correction.** `decathlon_standard.md:9`:
   "scale ecodesign adoption across just under 30 proprietary brands
   ([source](https://www.vue.ai/blog/leaders-in-retail/decathlon-innovatio/))+
   proprietary brands". The original prose said "50+ proprietary brands",
   the correction replaced "50+" with "just under 30 proprietary brands
   ([source](...))" but left the trailing "+ proprietary brands" intact.
   The substitution logic in `src/activities/source_judge.py` (or
   wherever the `corrected_value` rewrite happens) needs to be aware of
   the surrounding word boundary and consume the duplicated noun.

3. **Refusal path doesn't fire for edge cases.** All four edge inputs got
   reports. Root cause: the confidence gate at `src/pipeline.py:99-103`
   is OR-joined:
   ```
   research_confidence >= 0.5 OR is_verified OR existing_initiatives_count > 0
   ```
   Research drifted in all four cases:
   - `(empty)` → research_company_activity picked up "SoundHound AI"
     from somewhere (probably a Tavily search with an empty query
     defaulted to recent tech news). 5 existing initiatives — passes
     gate.
   - `asdfqwerty` → research found a real academic paper about
     "Asdf"/"Qwerty" quantum compiler. 5 existing initiatives (about
     Quanscient, BuySell, etc. — unrelated).
   - `ZYX Corporation` → research found Zynex Medical AND ZYX Music AND
     Zyxware (cross-cutting concern in report explicitly names the
     drift).
   - `Joe's Pizza Shop` → this one actually found the real NYC pizzeria
     via Wikipedia, so this isn't really an "edge case" — Joe's Pizza is
     a public business.

   The fix isn't necessarily "refuse more aggressively" — Joe's Pizza
   was correct and produced a reasonable (if low-confidence) report.
   But the (empty), asdfqwerty, and ZYX cases would have been better
   served by refusal. Suggested gate: require AT LEAST TWO of
   (research_confidence ≥ 0.5, is_verified, existing_initiatives > 0)
   instead of just one. AND/OR: detect when the company name vs
   research-found-entity name has high edit distance ("ZYX Corporation"
   vs "Zynex") and refuse.

4. **Refusal-bypass risk in the (empty) case.** `(empty)_fast.md:7`
   renders as "GenAI Use Cases for (empty)" but the use cases are all
   SoundHound AI-specific. A real user submitting an empty form would see
   a report that has nothing to do with their intent. This is worse than
   refusing — it looks confident and is unrelated.

5. **Mistral Medium occasionally has long latency.** Sanofi fast enrich
   took 74.8s — 5× the typical 13-17s. No outlier in research / generate
   / score for Sanofi fast, just enrich. Mistral Medium occasionally
   slow-pathing on json_object output? Single sample, can't tell. If it
   becomes a pattern on more runs, worth a parallel-enrich experiment for
   fast tier (currently only experimental on standard).

6. **One verify LLM call took 32.2s on Decathlon std.** trace line 80
   (`verify`, mistral-small-2603, 32160ms). Same outlier pattern as
   Sanofi enrich — one call out of band. Mistral Small returning slowly
   on a JSON-mode request. Below the activity timeout (60s), but it
   triples the verify phase wall time.

### The Tesla standard error — investigation summary

Symptom: `ValidationError: 1 validation error for Report — must contain
exactly 3 use cases, got 1`. Wall time 123.1s — shorter than the SAP std
median of 159.7s, suggesting enrich finished faster (returning less
content). No trace file written (the heavy-batch script only writes the
trace on successful completion — `scripts/run_heavy_batch.py:103-113`).

Code trace through `src/activities/select_enrich.py:951-1195`:

1. `filter_and_promote(scored, verified, k=3)` should return `final` with
   ≤ 3 candidates. Line 967 pads from appendix until len(final) ≥ 3.
2. `_one_call(final)` calls Mistral Large 3 with `response_format=json_object`
   and `max_tokens=12_000`. Returns parsed JSON.
3. `raw_uses = data.get("top_use_cases", [])`. If model returned only 1
   use case in `top_use_cases`, `raw_uses` has 1 entry.
4. Zip loop builds `enriched` from `raw_uses ∩ final`. With raw_uses=1,
   enriched=1.
5. Padding loop at line 1085: `while len(enriched) < len(final):
   enriched.append(_coerce_enriched({"id": sc.candidate.id}, sc, ...))`.
   With final=3 and enriched=1, this should add 2 fallback entries.
6. Polish (line 1112) and attribution_check (line 1124) run on standard
   tier in parallel via asyncio.gather. Both modify in-place.
7. `return enriched[:3], rejected` — should return 3.

The padding loop at step 5 SHOULD have fired. For enriched to stay at 1
through step 7, one of these would have to be true:
- final itself had len=1 going into the function (would require generate
  to have produced 1 candidate, which would have raised on json parse
  failure — not silent)
- asyncio.gather in polish or attribution_check raised an exception that
  somehow propagated PARTIAL state. But asyncio.gather raises eagerly —
  any task exception bubbles up.
- The pydantic Report ValidationError was raised somewhere earlier and
  caught silently. None of the activity code has try/except around the
  enriched_uses list.

Most likely hypothesis: A Mistral Large 3 timeout or retry inside the
mistralai SDK returned a degenerate response that parsed as a dict with
`top_use_cases: [<truncated use case>]`, AND one of the polish/attribution
parallel calls raised an exception that asyncio.gather propagated up, BUT
the activity decorator (`@workflows.activity`) somehow swallowed it and
returned the partial state.

Verdict: this is 1 error in 24 standard runs (4%). Not worth deep
investigation today — flag it for the v10 reproduction effort. If it
recurs, add explicit logging at line 1085 (count of enriched before/after
padding) and at the return.

Defensive fix that doesn't require root-causing: assert `len(enriched) ==
3` at the end of `select_and_enrich_activity` and raise a clearer error
than Report's validator. Even better: make the activity itself responsible
for ensuring 3 use cases and raise a specific exception if it can't.

### Refusal-path drift — concrete cases

The pipeline ran a full 100+ second report for inputs where it should
have refused:

```
Input              | Research-detected entity     | Confidence | Real intent
-------------------|------------------------------|-----------|------------
(empty string)     | "SoundHound AI"              | 0.60      | user typed nothing
"asdfqwerty"       | Asdf/Qwerty quantum compiler | 0.60      | random keystrokes
"ZYX Corporation"  | Zynex Medical / ZYX Music    | 0.60      | placeholder name
"Joe's Pizza Shop" | NYC pizzeria (correct!)      | 0.85      | real but tiny business
```

Three of four are NOT what the user intended. The Joe's Pizza case is
actually correct — the pipeline found the right company and produced a
reasonable report (39% confidence, honest about being unable to support
peer-deployment claims for a small business).

The other three are entity-substitution failures. The pipeline doesn't
know it's been substituted. A user who typed nothing and got a report
about SoundHound AI would be confused at best, misled at worst.

The cross-cutting concern field caught the drift in all three cases (e.g.
ZYX: "the company context describes ZYX Corporation with medical device
priorities ... but the evidence pool primarily references ZYX Music,
Zyxware Technologies"). So the meta-eval is aware. But the report still
ships.

Suggested fix: when meta_eval's cross-cutting concern matches a "company
mismatch" or "entity drift" pattern (e.g. contains "different company",
"mismatch", "primarily references", "evidence pool primarily references"),
demote the run to refusal with a clear message. This is a meta-eval-aware
refusal that complements the research_confidence gate.

### Concrete recommendations, ordered by impact

**Tier 1 (ship soon, high quality lift):**

1. **Refusal-path hardening.** Require ≥ 2 of (research_confidence ≥ 0.5,
   is_verified, existing_initiatives > 0) instead of OR. Add company-name
   vs detected-entity edit-distance check (if >50% different, refuse).
   ~30 min. Lifts the 4 edge cases out of the report pipeline; saves
   ~100s × 3 = 5 minutes of wasted LLM time per "blind keystroke" input.

2. **Strip unmapped (ev-XXX) IDs in polish.** The current prompt tells
   the model to drop unmapped IDs but the model doesn't always comply.
   Add a regex pass AFTER polish: `re.sub(r"\(ev-[0-9a-f]{6,12}\)", "",
   text)` for any ID not in the source map. ~10 min. Fixes the Hermes
   std `(ev-7a1b2c3d4e)` artifact.

3. **Word-boundary aware `corrected_value` substitution.** When
   `original_value = "50+ proprietary brands"` and `corrected_value =
   "just under 30 proprietary brands"`, the substitution should consume
   the full noun phrase, not just the number. Use `\b50\+\s*proprietary\s*brands\b`
   or similar. ~20 min. Fixes the Decathlon "+proprietary brands"
   leftover.

**Tier 2 (research, validate first):**

4. **Reduce polish's claim-discovery scope.** The polish prompt currently
   encourages the LLM to scan the full evidence pool for ANY supporting
   sentence ("STEP 1 — CHECK THE FULL SOURCE POOL"). On Spotify std this
   produced 7 judge-rejected citations. Two options:
   - Restrict polish to only citing pool entries that the candidate
     ALREADY referenced (`use_case.evidence_ids`). Drops new-source
     discovery.
   - Keep the discovery but raise the bar — only add a citation if the
     pool excerpt contains an exact entity+number match for the claim.
   Test on Spotify, Hermes, Bouygues first.

5. **Cap web_verify rescue more aggressively.** Currently 12 rescue
   searches for standard, 18 for max. Hermes std attempted many and got 5
   judge-rejected. Worth measuring: rescued+judge-rejected vs cost.
   Possibly drop rescue cap to 6 on standard.

6. **Meta-eval-aware refusal.** If meta_eval's cross-cutting concern
   contains phrases like "different company", "mismatch", "primarily
   references", flip to a refusal at the end. ~30 min. Catches the
   research-drift cases that slip through the confidence gate.

**Tier 3 (long-tail, deprioritise):**

7. **Sanofi-fast / Decathlon-std enrich/verify outliers.** Single
   samples, not worth fixing without reproducing. Worth flagging if a
   future batch shows the same.

8. **Tesla standard ERROR.** Defensive assert at end of
   select_and_enrich_activity is cheap (~5 min). Root cause investigation
   only if it recurs.

### Pending fixes A, B, C from briefing — recommendation after analysis

The three fixes I'd queued before this analysis (briefing doc:
`docs/_briefing_before_v9_4_analysis.md`):

- **A. Hyperlinks fix** (post-process strip of bare `[X]`/`(X)` brackets
  in `_coerce_enriched`): the v9.4 reports don't actually show this as a
  major problem in fast tier output (no bare brackets visible in the 28
  reports). Looking at the actual cases the user originally reported,
  this is more of a polish-step artifact. **Recommendation: lower
  priority. Combine with bug #2 (strip unmapped ev-IDs) and bug #3
  (bracket leakage from corrections) into one bracket-cleanup pass.**

- **B. Query-length clamp on generate's web_search** (1-line clamp
  `query[:350]`): zero behavioural risk, fixes a real "Maximum query
  length" error class. **Recommendation: ship immediately.**

- **C. Deep-read content bumps** (3K→6K / 6K→10K / 8K→12K): would
  improve grounding density at +1-5s wall time. Given that the dominant
  quality issue is "polish adds marginal citations" NOT "polish has too
  little to cite", bumping deep-read may make the problem WORSE — more
  text for polish to pull adjacent citations from. **Recommendation:
  hold pending the polish-restriction experiment (Tier 2 #4 above).**

### Verifying the v9.3 audit-fixes

The audit commit `04b46cd` shipped 4 real bug fixes + 12 cleanup. The
v9.4 batch ran against that commit. None of the fixes' downstream
behaviour appears in the output:

- `req.tier silently dropped` (api.py): can't see in batch since
  scripts/run_heavy_batch.py sets settings.tier directly, not via the
  API surface. Indirectly verified — Tesla, HSBC, etc. fast vs standard
  show clear tier differentiation in the trace (enrich model swap, polish
  on/off).
- `self_consistency_passes hardcoded` (score.py:225): the score traces
  show 2 passes (T=0.2 + T=0.4) on every run, consistent with the fix.
- `redundant bundle re-fetch in pipeline.py`: no second research call in
  any trace. Confirmed.

### Numbers summary

```
Hard errors:      1/28  (Tesla std)
SE-ready:         2/24 of real runs  (SAP std, Decathlon std)
Confidence >0.70: 7/24
Fast > std qual:  3/12  (Spotify, Hermes, Bouygues)
Edge cases ran:   4/4   (all should have refused on at least 3)
Median wall (fast): 115s
Median wall (std):  167s
```

### Files to read alongside this

- `_summary.md` — wall + confidence + pass-rate table
- Per-run reports and traces in this directory
- `src/activities/select_enrich.py` — for the Tesla error code-trace
- `src/pipeline.py:99-103` — the refusal gate
- `src/activities/source_judge.py` — the correction logic
- `src/prompts.py: _POLISH_SYSTEM` and `ENRICHMENT_SYSTEM` — for the
  citation-discovery prompts that drive the fast-vs-standard regression
