# Phase 3 benchmark findings

Seven Carrefour pipeline runs comparing variants against the v9.3 baseline
(N=8, standard tier, double-pass score, serial enrich). All runs use the
same company, same code state, same v9.3 cache. The cache effect on
research/news caching is consistent across runs.

Single-run results — interpret with care; LLM-day variance is meaningful
(±5-10% on wall time, ±0.05-0.15 on meta-eval confidence). Direction of
deltas tends to be reliable; magnitude noisy.

## Phase 3a — N comparison

| Variant | Wall | Confidence | Diversity | Top-3 picks |
|---|---|---|---|---|
| N=5 | 197s | **0.58** ❌ | 0.85 | Forecasting / Loyalty / Training |
| N=8 (default) | 215s | 0.71 ✅ | 0.90 | Private-label / Provenance / Dynamic pricing |
| N=12 | 178s | 0.72 ✅ | 0.85 | Forecasting / Atacadão / Supply chain |

**Surprising: 3 different top-3 sets across 3 runs.** Run-to-run variance in
which use cases get picked is HIGH regardless of N. The N choice is not the
dominant factor in selection.

**Time effect:** ~negligible. Generate phase ranges 17-21s wall, score is
flat at 18s wall, enrich ranges 56-89s — all dominated by LLM latency
variance, not by N. The "savings from cutting N" hypothesis was ~10-15s on
paper but the LLM batch-processing overhead is mostly fixed.

**Quality effect:** N=5 sits clearly below readiness threshold (0.58 vs the
0.70 bar). N=8 and N=12 are interchangeable on this single run. **N=8 stays
as the default** per the audit conclusion.

## Phase 3d — tier differentiation

| Tier | Wall | Confidence | enrich (s) | Notes |
|---|---|---|---|---|
| `fast` | **125s** ⚡ | **0.75** ✅ | 21s (mistral-medium) | Skip polish + attribution; tool budget 0 |
| `standard` (default) | 215s | 0.71 ✅ | 89s (mistral-large) | Tool budget 2; full guardrails |
| `max` | 201s | 0.62 | 70s | Tool budget 4; deep-read top 3; full guardrails |

**Fast tier is the most striking finding.** 42% faster (90s saved) with
confidence equal-or-better in this run. Most of the savings come from
mistral-medium replacing mistral-large for the enrich phase (89s → 21s on
this run). The "fast tier sacrifices quality" framing in the original
docstring is overstated for this company at least; on multi-company runs
the gap may widen.

**Max tier is mildly differentiated.** With `+2 web_search budget`, `+1
deep-read top_n`, max does ~14s more web/verify work than standard. The
0.62 confidence is single-run noise — direction unclear without more
runs. **Worth flagging as "honest measured numbers" not the old 5-7 minute
claim.**

Recommendation: keep all three tiers, document the measured deltas, mark
fast as worth considering for cost-sensitive demos.

## Phase 3c — parallel enrich (test only)

| Variant | Wall | Confidence | Topical diversity | Mistral product diversity |
|---|---|---|---|---|
| Serial (default) | 215s | **0.71** | **0.90** | **6** |
| Parallel (3 calls) | 163s | 0.59 | 0.85 | 5 |

**Parallel enrich saves 52s (24% wall reduction).** But:
- Confidence drops 0.12
- Topical diversity drops 0.05
- One fewer distinct Mistral product picked
- Cross-cutting concern is broader (less coherent across the 3 use cases)

The audit's prediction held: each parallel call lacks cross-use-case
awareness, so the resulting top-3 is less coherent. **Don't ship.** Flag
in next-iteration notes.

## Phase 3e — self-consistency ablation

| Variant | Wall | Confidence | Topical diversity |
|---|---|---|---|
| Double-pass T=0.2/0.4 (default) | 215s | **0.71** | **0.90** |
| Single-pass T=0.3 | 197s | **0.51** ❌ | 0.70 |

**Single-pass saves ~18s but blows up everything downstream.** Confidence
drops 0.20, topical diversity drops 0.20. Without the second pass to
average score noise, the top-3 selection picks worse candidates, the meta-
eval flags it harshly, and the report ships with broader concerns.

**The double-pass is doing real work.** Self-consistency stays.

## What gets shipped from Phase 3

- ✅ N=8 default (already shipped in Phase 2 commit `0188f17`).
- ✅ Tier docstring honest with measured numbers (already shipped).
- ✅ Max tier `+1 deep-read top_n` for marginal but real differentiation.
- ❌ Parallel enrich — gated behind `enable_parallel_enrich=False`. Code
  stays for future iteration; off by default.
- ❌ Single-pass score — gated behind `enable_single_pass_score=False`.
  Code stays; off by default. Self-consistency is doing work.

## Variance caveat

Every comparison here is single-run. The enrich phase has empirically wild
LLM-latency variance (56-89s for the same N=8 standard configuration).
Confidence values move ±0.10 across nominally-identical conditions. To
support strong claims about any variant, a multi-company batch (≥3
companies × ≥3 runs each) is needed. These benchmarks rule out big
regressions and identify directionally-clear wins (fast tier, parallel
enrich's quality cost) but don't pin down 5% wins on either side.
