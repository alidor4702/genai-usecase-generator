# Pipeline limits, budgets, and timeouts

Single source of truth for every cap, budget, threshold, and timeout in the
pipeline. **When any of these change, update this document in the same commit.**
Stale numbers here are worse than no document — reviewers and operators rely
on this for capacity planning and debugging.

Last verified: 2026-05-08, after the max_tokens / timeout uplift round
(generation 12000→16000, enrichment 8000→12000, meta-eval 6000→10000, etc.).

---

## Performance tiers

Set with `--tier {fast,standard,max}` on `scripts/run_example.py`. Default is
`standard`. The tier value is read at runtime via `settings.tier` and dispatched
at the points listed in the next section.

| Tier | Target wall time | Use when |
|---|---|---|
| `fast` | ~60-90 seconds | Quick exploratory pass, demos, CI smoke tests |
| `standard` (default) | ~2-3 minutes | Default for every customer-facing run |
| `max` | ~5-7 minutes | Highest-quality output, important pitch prep |

---

## What varies by tier

These six dispatch points are the only places the tier flag changes behavior
today. Everything else in this document is identical across the three tiers.

| Behavior | fast | standard | max | Source |
|---|---|---|---|---|
| `web_search` tool budget during generation | **0** | 2 | 4 | `src/activities/generate.py:339` |
| Max LLM iterations in generation loop | 2 | 4 | 6 | `src/activities/generate.py:444` (= budget + 2) |
| Enrichment model | `mistral-medium-2604` | `mistral-large-2512` | `mistral-large-2512` | `src/activities/select_enrich.py` |
| Polish pass (qualitative + markdown links + URL strip) | **skipped** | runs | runs | `src/activities/select_enrich.py` |
| Attribution check (corpus-ID ↔ company) | **skipped** | runs | runs | `src/activities/select_enrich.py` |
| Targeted regen on `meta_eval.confidence < 0.6` | **skipped** | runs (1 round) | runs (1 round) | `scripts/run_example.py` |

---

## LLM output token caps (`max_tokens`)

These cap how long each LLM response can be. Hit too low → truncated JSON →
parse error. Identical across all tiers (today).

| Step | Model | `max_tokens` | Source |
|---|---|---|---|
| Initial research synthesis | `mistral-medium-2604` | 6000 | `src/activities/research.py:250` |
| Gap-fill query generation | `mistral-small-2603` | 600 | `src/activities/research.py:584` |
| Layer-2 per-field extraction | `mistral-small-2603` | 400 | `src/activities/research.py:659` |
| Industry label polish | `mistral-small-2603` | 40 | `src/research/industry_label.py:114` |
| Diversity grader | `mistral-small-2603` | 400 | `src/activities/compute_signals.py:111` |
| Specificity grader | `mistral-small-2603` | 2500 | `src/activities/compute_signals.py:166` |
| Generation (per LLM iteration) | `mistral-medium-2604` | 16,000 | `src/activities/generate.py:450` |
| Per-candidate verification verdict | `mistral-small-2603` | 1500 | `src/activities/verify_per_candidate.py:141` |
| Score pass (each of 2 self-consistency calls) | `mistral-small-2603` | 10,000 | `src/activities/score.py:95` |
| Enrichment (full top-3) | `mistral-large-2512` (or `mistral-medium-2604` on `fast`) | 12,000 | `src/activities/select_enrich.py:916` |
| Targeted regen (one use case) | `mistral-large-2512` | 8000 | `src/activities/select_enrich.py:808` |
| Polish per use case | `mistral-small-2603` | 5000 | `src/activities/select_enrich.py:669` |
| Attribution check per use case | `mistral-small-2603` | 4000 | `src/activities/select_enrich.py:523` |
| Meta-evaluation | `mistral-medium-2604` | 10,000 | `src/activities/meta_evaluate.py:186` |

**Bumps applied 2026-05-08** to eliminate truncation crashes on regen and meta-eval, and to give generation/enrichment/score genuine headroom. Cost note: Mistral (and every major LLM provider) only charges for tokens actually generated, not for unused `max_tokens` ceiling — bumps are free unless they unlock genuinely longer responses.

---

## Per-LLM-call timeouts (`timeout_ms`)

Bound a single LLM request. Mistral runtime retries on timeout per its retry
policy. Identical across tiers.

| Call | Timeout |
|---|---|
| Industry polish | 20 s |
| Diversity grader / Gap-query gen / Layer-2 extract | 30 s |
| Per-candidate verify / Specificity grader / Polish | 90 s |
| Initial research synthesis / Re-synthesis (deprecated) | 120 s |
| Score pass / Attribution check | 180 s / 60 s |
| Targeted regen | 180 s |
| Enrichment / Generation / Meta-evaluation | 240 s |

---

## Activity-level timeouts (`start_to_close_timeout`)

Wraps the whole activity (including all internal sub-calls + retries the
runtime injects). Per CLAUDE.md spec.

| Activity | Timeout | Source |
|---|---|---|
| `retrieve_precedents_activity` | 30 s | `src/activities/retrieve.py:43` |
| `compute_quality_signals_activity` | 60 s | `src/activities/compute_signals.py:194` |
| `research_company_activity` | 120 s | `src/activities/research.py:448` |
| `score_candidates_activity` | 180 s | `src/activities/score.py:199` |
| `enrich_company_context_activity` | 180 s | `src/activities/research.py:764` |
| `select_and_enrich_activity` | **300 s** | `src/activities/select_enrich.py:858` |
| `verify_top_candidates_activity` | 240 s | `src/activities/verify_per_candidate.py:182` |
| `meta_evaluate_activity` | **300 s** | `src/activities/meta_evaluate.py:157` |
| `generate_candidates_activity` | **600 s** | `src/activities/generate.py:590` |

Total workflow execution timeout: **15 minutes** (`src/workflow.py`).

**Activity-timeout bumps applied 2026-05-08** alongside the `max_tokens` uplift,
so the activity wrappers don't expire before the larger LLM calls can complete.
Generation now allows up to 6 LLM iterations × ~100s each on `max` tier (4 web-
search calls + final JSON), enrichment can run mistral-large at 12000 tokens
without hitting the wrapper timeout.

---

## Tavily / web-search caps

| Place | Cap | Source |
|---|---|---|
| `tavily_max_results` (used in news fetch) | 5 | `src/config.py:100` |
| `tavily_deep_read_top_n` (deep-read top N of news) | 2 | `src/config.py:101` |
| Existing initiatives | 3 per query, 3 queries, breaks at 5 total results | `src/research/existing_initiatives.py:73` |
| Jobs signal search | 4 | `src/research/jobs.py:54` |
| Live company verification | 3 per query, 2 queries | `src/research/verification.py:145` |
| Gap-fill targeted search per missing field | 2 | `src/activities/research.py:695` |
| Per-candidate verification | 4 | `src/activities/verify_per_candidate.py:61` |
| `web_search` tool inside generation | 2 results per call | `src/activities/generate.py:366` |

`web_search` tool **call budget** during generation varies by tier — see the
"What varies by tier" section above.

---

## Concurrency caps

Both use `asyncio.Semaphore(3)` — at most 3 in-flight requests at a time.

| Place | Semaphore | Source |
|---|---|---|
| Gap-fill Tavily searches (across missing fields) | 3 | `src/activities/research.py:808` |
| Per-candidate verification (across top-3) | 3 | `src/activities/verify_per_candidate.py:219` |

---

## Iteration / retry caps

| Place | Cap | Source |
|---|---|---|
| Generation regen for diversity (when avg pairwise cosine > 0.85) | 1 round only — keep best of two | `src/activities/generate.py` |
| Targeted regen on meta-eval confidence < 0.6 | 1 round only | `scripts/run_example.py` |
| Generation outer iteration loop | `tool_budget + 2` (fast=2, standard=4, max=6) | `src/activities/generate.py:444` |
| Self-consistency scoring | always 2 parallel passes (T=0.2 and T=0.4) | `src/activities/score.py` |
| Industry label trailing-connector trim | 3 max | `src/research/industry_label.py:84` |
| JSON decode retry inside generation | 1 retry on parse failure | `src/activities/generate.py:558` |

---

## Confidence + depth thresholds

| Setting | Default | Effect | Source |
|---|---|---|---|
| `research_confidence_threshold` | 0.5 | Below this AND not verified AND no existing initiatives → graceful refusal | `src/config.py:69` |
| `meta_eval_confidence_threshold` | 0.6 | Below this triggers targeted regen (skipped on `fast`) | `src/config.py:70` |
| `diversity_threshold` | 0.85 | Above this avg pairwise cosine triggers candidate regen | `src/config.py:71` |
| `min_depth` (precedent retrieval) | 0.4 | Precedents below this depth_score are filtered out | `src/activities/retrieve.py:45`, `src/precedents.py:240` |
| `mmr_threshold` | 0.05 | Apply MMR diversification when top-k similarity spread is below this | `src/precedents.py:242` |
| Verification fuzzy `score_cutoff` | 88 | rapidfuzz threshold for fast-path index match | `src/research/verification.py:179` |
| Live verification credible-domain hits | ≥1 | Mark as verified | `src/research/verification.py:170` |

---

## Content truncations passed into LLMs

These cap how much of fetched text the LLM actually sees. Same across tiers.

| What | Limit | Source |
|---|---|---|
| Wikipedia summary in synthesis prompt | 3000 chars | `src/activities/research.py:136` |
| News body in synthesis prompt | 2000 chars per article | `src/activities/research.py:147` |
| Existing initiative description | 600 chars | `src/activities/research.py:163`, `src/activities/generate.py:82`, `src/activities/score.py` |
| Wikipedia summary in generation raw bundle | 3500 chars | `src/activities/generate.py:109` |
| News body in generation raw bundle | 1500 chars per article × 3 articles | `src/activities/generate.py:112-114` |
| Precedent description in generation prompt | 200 chars | `src/activities/generate.py:68` |
| Precedent title in generation prompt | 120 chars | `src/activities/generate.py:69` |
| Wikipedia excerpt for gap-query gen | 600 chars | `src/activities/research.py:571` |
| Layer-2 extraction input text | 6000 chars per field | `src/activities/research.py:645` |
| Targeted Tavily snippet | 1500 chars | `src/activities/research.py:708`, `src/activities/generate.py:383` |
| `web_search` tool result content returned to model | 2500 chars | `src/activities/generate.py:407` |
| Per-candidate verify result snippet | 1000 chars | `src/activities/verify_per_candidate.py:105` |
| Per-candidate verify deep-read | 4000 chars | `src/activities/verify_per_candidate.py:120` |
| Tavily deep-read in news fetch | 8000 chars | `src/research/news.py:30` |
| Meta-eval cited precedent deep_content | 3000 chars | `src/activities/meta_evaluate.py:81` |
| Meta-eval cited ledger entry content | 2500 chars | `src/activities/meta_evaluate.py:99` |
| Diversity grader use-case core | 240 chars | `src/activities/compute_signals.py:98` |

---

## Pipeline parameters (`src/config.py`)

| Setting | Default |
|---|---|
| `candidates_to_generate` | 12 |
| `top_k_precedents` | 8 |

---

## Cache TTLs

These don't constrain quality, just cache duration. From `src/config.py`.

| Cache | TTL |
|---|---|
| Wikipedia | 30 days |
| News | 24 hours |
| Jobs | 48 hours |
| Existing initiatives | 7 days |
| Per-candidate verification | 7 days |

---

## Suggested future tier differentiation

The `fast`/`standard`/`max` tiers today differ by only six dispatch points
(see the table at the top). If we want them to be more meaningfully different,
the candidates below are pre-vetted for tier dependence. None of these are
implemented yet — they are the next round of work if/when needed.

### Bump for `max`

| Constraint | standard (today) | proposed `max` | Rationale |
|---|---|---|---|
| `top_k_precedents` | 8 | 12 | More peer examples in generation prompt |
| `tavily_deep_read_top_n` | 2 | 4 | Deep-read all news articles, not just top 2 |
| News body truncation in generation | 1500 chars × 3 | 3000 chars × 5 | Richer raw context for the generator |
| Layer-2 extract input | 6000 chars | 10000 chars | More source text per field extraction |
| `min_depth` | 0.4 | 0.3 | Wider precedent retrieval net |
| Self-consistency scoring passes | 2 | 3 (T=0.2 / 0.3 / 0.4) | Stronger consensus |
| Enrichment `max_tokens` | 8000 | 12000 | Room for richer prose |

### Trim for `fast`

| Constraint | standard (today) | proposed `fast` | Rationale |
|---|---|---|---|
| `top_k_precedents` | 8 | 4 | Smaller prompt, faster generation |
| News body truncation in generation | 1500 chars | 600 chars | Faster generation, smaller prompt |
| `tavily_deep_read_top_n` | 2 | 1 | Save research-step latency |
| Self-consistency scoring passes | 2 | 1 (T=0.3) | Save ~20s on scoring |
| Research depth | medium | low | Skip news fetch entirely (~5-10s) |
| Generation `max_tokens` | 12000 | 8000 | Tighter generation |

---

## How to update this document

1. When adding a new LLM call, web fetch, threshold, or truncation, add a row
   to the appropriate table here in the same commit.
2. When changing a number in code, update the matching row here. Drift between
   code and this document is the failure mode this file exists to prevent.
3. When adding a new tier-dispatch point, update the "What varies by tier"
   table, not the global limits sections.
4. Re-verify the date at the top when you do a sweep through the file.
