# Changelog

The verification chain is the most distinctive part of this build. Every
iteration sharpened it; below is the version-by-version evolution from the
v5 baseline to the v9.8 production state.

The current README describes the v9.8 system in steady-state; this doc is
the changelog for anyone curious about *why* each piece is the way it is.

## Verification + meta-eval evolution (v5 → v9.8)

| Version | What changed | Why |
|---|---|---|
| **v5** | Baseline: meta-eval reads evidence pool, marks claims supported/unsupported. | ~80% pass rate but fact-checker was too narrow — flagged real claims as fabricated. |
| **v6** | **Web-verify rescue layer** — for unsupported claims, run one targeted Tavily search; 2-tier credibility (allowlist domain → "verified", entity/number anchor → "corroborated"). Plus 5 prompt fixes (atomic claim splitting, polish gets full ledger, `free_text_notes` priority, peer-attribution rule, TTV ballpark tag). | Caught real claims meta-eval missed. Pass rates jumped to ~95%. Confidence math broken (capped at 0.65). |
| **v7** | **Source-judge layer** — Mistral Small reads each (claim, source URL) pair and decides if the source actually supports the claim vs. just mentions related entities. **Final-qualify** — surgical rewrite of still-unsupported numerics. **Numbers symmetry** — polish stops pre-stripping; verify chain decides. | Caught false positives (wrong-source citations) the v6 rescue was rubber-stamping. |
| **v8** | Pass-rate denominator excludes qualified-out claims. Confidence calibration clamps `qual_delta ∈ [-0.15, +0.10]`. Veolia near-dup rendering bug. Peer-attribution rule strengthened to anonymous peers. | Made the headline metric reflect what the prose actually asserts; fixed Mistral 91% pass / 0.55 confidence inversion. |
| **v9** | **Self-correcting judge** — third verdict `corrected`. When source contradicts a numeric/rank/temporal claim with a clean replacement, the judge returns the corrected value as a drop-in phrase; the system patches the prose inline + attaches the source link + marks the claim with a `[corrected ↗ → <value>]` chip. Restricted to numerics (entity contradictions stay unsupported — silent entity-substitution would break downstream prose). Plus drop regen_one (~50s saved), parallelize verify deep-read, source_judge concurrency 4 → 8. | Closes the loop: real fabrications get rewritten with the actual value, not just qualified out. |
| **v9.1** | Phantom-claim post-process strip (LLM was copying prompt template literals as claims). Feature-description filter strengthened with "Acme Corp swap test". Source-judge extends to all supported claims (was URL-only). | Removed extraction noise. Catches the L'Oréal Galderma slip-through (supporting quote didn't actually mention the brands). |
| **v9.2** | Two surgical prompt edits — judge accepts inference-from-context (Paris HQ → EU regulatory alignment); generation prompt blocks superlatives ("ONLY EU-sovereign") via the existing peer-attribution rule. | Recovers reasonable inferences without adding new logic; closes a generation-prompt gap. |
| **v9.3** | N=12 → N=8 candidates (top-3 selectivity held in benchmark). Parallel source-judge across all pairs. Tier differentiation (fast / standard / max). | Saves ~10-15s per run on generate + score. Tier separation enables fast-track / quality-priority surfacing in the FE. |
| **v9.4–v9.6** | 28-run heavy batch + targeted A/B tests. **Bracket / fake `(ev-XXX)` cleanup pass.** **Refusal gate hardened to 2-of-3 signals** (was OR — let edge cases through). **Polish prompt rewritten** to require exact entity-AND-figure match before attaching a citation. **Deep-read content bumps** (3K → 6K, 6K → 10K, 8K → 12K). Banner UX 3-way branched. | Fixed the fast > standard regression on Spotify/Hermès/Bouygues caused by polish over-zealously adding marginal citations the judge then rejected. |
| **v9.7** | **Generate + enrich prompt hardening** — no invented outcome percentages for the proposed system (`achieves 25-40% improvement` style claims). **Max tier insane mode** — polish on Mistral Large 3, second-pass critique-revise call, web_search budget 4 → 6, deep-read 12K → 16K. | The #1 cause of judge-rejected claims across v9.4-v9.6 was the model writing specific outcome percentages with no source. Stopped at the source rather than cleaning up after. |
| **v9.8** | **Upfront entity resolution** — one Mistral Small call resolves user input to a canonical company name BEFORE any research happens (`Apple` → `Apple Inc.`). Replaces the v9.5 rapidfuzz-WRatio heuristic that got fooled by substring matches (`Apple` ⊂ `Applegate`). Refuses gibberish/empty in <2s. **SE-ready bar bumped 0.70 → 0.80** with the meta-eval calibration anchors shifted upward. **INLINE LINK DENSITY rule** — enrich prompt asks for 2-3+ inline markdown links per use case. **"Fact-check" labels softened across the surface** — "Source-anchored claim ratio" / "Per-claim source-anchoring detail" / "Claim source-anchoring breakdown". Le Chat slimmed to generation-only (removed the Action dropdown). | Entity resolution kills the entire class of "Wikipedia returned the wrong article" / "verified-index matched the wrong company" bugs at the top of the pipeline. The 0.80 bar with the new calibration aligns "above bar" with what a sales engineer would actually ship. |

## What changed in the user-facing report between v9.1 and v9.8

| Surface | v9.1 | v9.8 |
|---|---|---|
| Top banner | "Draft — needs revision before customer use" | "Confidence X — revision suggested" (3-way branched depending on conf vs SE-ready alignment) |
| Pass-rate label | "Fact-check pass rate: 92%" | "Source-anchored claim ratio: 92%" with a clarifier paragraph explaining what the % means |
| Per-claim block heading | "Fact-check detail (per claim)" | "Per-claim source-anchoring detail" |
| Unsupported claims label | "Unsupported (N):" | "Not source-anchored (N) — may still be true, flagged for revision" |
| SE-ready threshold | 0.70 | 0.80 (with meta-eval calibration anchors shifted up so reports score in the same band; 0.80 is "sales-engineer-ready as-is") |
| Cross-cutting flag | "Cross-cutting concern:" | "Cross-cutting improvement note:" |
| Weakest use case label | "Weakest use case:" | "Use case most worth tightening:" |
| Le Chat metric box | "EVENTS / LLM CALLS / WEB SEARCHES / FACT-CHECK / META-EVAL" | "ELAPSED / LLM CALLS / WEB SEARCHES / ANCHORED / META-EVAL" (Events dropped; Fact-check → Anchored) |

## End-to-end verification chain today (v9.8)

```
Substantive claim from prose
    │
    └── Anchored in evidence pool? ── yes ──→ supported (source_kind=evidence:ev-id)
                                  └── no  ──→ Web-verify Tavily search
                                                 │
                                                 └── Domain allowlisted? ──→ supported (rescue_tier=verified)
                                                 └── Entity/number anchor? → supported (rescue_tier=corroborated)
                                                 └── Nothing? ────────────→ unsupported
    │
    Source-judge: for every supported claim with a resolvable URL OR
    supporting_signal text, ask Mistral Small "does this source actually support?":
        ├── supported     → render with citation
        ├── corrected     → patch prose with source's actual value, add [corrected ↗] chip
        └── unsupported   → flip back, judge_rejected chip
    │
    Final qualify: any still-unsupported numeric/named claim → surgical
                   qualitative rewrite
    │
    Pass-rate metric: excludes qualified_out claims (the prose no longer
                      asserts them)
    Confidence: re-anchor on new pass-rate, qual_delta clamped [-0.15, +0.10]
    │
    Persist Report to SQLite runs table → /history page can replay later
```

Full chain visualised on the [`/architecture`](https://compastral.vercel.app/architecture) page with clickable step detail.
