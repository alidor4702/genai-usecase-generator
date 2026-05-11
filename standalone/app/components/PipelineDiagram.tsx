"use client";

/**
 * PipelineDiagram — hand-rolled 2D layout for the GenAI Use Case
 * Generator pipeline. Replaces the previous mermaid version because
 * mermaid was rendering as a single column / single row regardless of
 * whether the parent direction was TB or LR — inter-subgraph edges
 * force a rank order and override `direction LR` inside subgraphs.
 *
 * This component lays out phases as horizontal bands stacked
 * vertically. Each band has a phase label on the left and a flex row
 * of step cards going left-to-right with arrows between them. Cards
 * are colour-coded by role using the same legend as the rest of the
 * /architecture page (LLM / live web / preset corpus / I/O).
 *
 * v9.7 — cards are now clickable. Selecting one highlights it and
 * notifies the parent via `onSelect`. The architecture page uses this
 * to swap its right column from the static determinism contract to a
 * step-specific detail panel. See `StepDetailPanel.tsx` and
 * `STEP_DETAILS` below.
 */

import type { ReactElement } from "react";

type Role = "llm" | "live" | "preset" | "io";

const ROLE_STYLES: Record<Role, string> = {
  llm: "bg-mistral-orange/90 border-mistral-orangeBright text-white shadow-mistral-orange/30",
  live: "bg-blue-900 border-blue-400/70 text-blue-50 shadow-blue-900/30",
  preset: "bg-emerald-900 border-emerald-400/70 text-emerald-50 shadow-emerald-900/30",
  io: "bg-mistral-surface border-mistral-orange/60 text-white shadow-black/20",
};

export type StepDetail = {
  fullName: string;
  model?: string;
  temperature?: string;
  timeout?: string;
  reads: string[];
  writes: string[];
  whyActivity: string;
  notes?: string[];
};

export type Step = { id: string; label: string; sub?: string; role: Role };
type Phase = { title: string; steps: Step[] };

const PHASES: Phase[] = [
  {
    title: "Phase 1 · Resolve & research",
    steps: [
      { id: "input", label: "Company + knobs", role: "io" },
      { id: "resolve_entity", label: "0. Resolve entity", sub: "Mistral Small", role: "llm" },
      { id: "research", label: "1. Research", sub: "Mistral Medium 3.5", role: "llm" },
      { id: "gap_fill", label: "1b. Gap-fill", sub: "Tavily", role: "live" },
      { id: "retrieve", label: "2. Retrieve", sub: "cosine top-k", role: "preset" },
    ],
  },
  {
    title: "Phase 2 · Candidate generation",
    steps: [
      { id: "generate", label: "3. Generate N", sub: "Mistral Medium 3.5 + web_search", role: "llm" },
      { id: "score", label: "4. Score · 5 criteria", sub: "Mistral Small × 2", role: "llm" },
      { id: "verify", label: "5. Verify top-3", sub: "Tavily + Small", role: "llm" },
    ],
  },
  {
    title: "Phase 3 · Enrich top-3",
    steps: [
      { id: "enrich", label: "6. Enrich", sub: "Mistral Large 3", role: "llm" },
      { id: "polish", label: "6a. Polish", sub: "strict citation rule", role: "llm" },
      { id: "meta_eval", label: "7. Meta-eval", sub: "per-claim · Mistral Medium 3.5", role: "llm" },
    ],
  },
  {
    title: "Phase 4 · Verification chain",
    steps: [
      { id: "web_verify", label: "7c. Web-verify", sub: "2-tier rescue", role: "live" },
      { id: "source_judge", label: "7d. Judge", sub: "self-correcting · Mistral Small", role: "llm" },
      { id: "final_qualify", label: "7e. Final qualify", sub: "Mistral Small", role: "llm" },
    ],
  },
  {
    title: "Phase 5 · Output",
    steps: [
      { id: "quality_signals", label: "Quality signals", role: "llm" },
      { id: "report", label: "Report + persist", role: "io" },
    ],
  },
];

export const STEP_DETAILS: Record<string, StepDetail> = {
  input: {
    fullName: "Company name + tier + focus + weights",
    reads: ["User input via Le Chat form OR Next.js generate page"],
    writes: ["WorkflowInput (Pydantic model)"],
    whyActivity:
      "Entry point — not an activity itself. The workflow's `run` method receives this as its typed input.",
    notes: [
      "research_depth is server-side default (medium); Le Chat form hides it.",
      "Tier override mutates settings.tier at the start of each run.",
    ],
  },
  resolve_entity: {
    fullName: "Step 0 — Resolve entity (upfront canonicalisation)",
    model: "mistral-small-2603",
    temperature: "0.1",
    timeout: "30s",
    reads: ["The user's raw company-name input"],
    writes: [
      "Canonical company name (e.g. 'Apple' → 'Apple Inc.', 'Hermes' → 'Hermès International S.A.')",
      "Refusal verdict for gibberish / empty / unidentifiable inputs",
    ],
    whyActivity:
      "One Mistral Small call. Replaces v9.5's rapidfuzz-WRatio heuristic which was substring-biased (matched 'Apple' to 'Applegate'). The LLM has world knowledge of real companies and can disambiguate short names cleanly.",
    notes: [
      "Refuses in ~2s when input doesn't map to an identifiable company — saves the ~100s of pipeline work the wrong-entity case used to burn.",
      "When confidence is medium/low, still proceeds with the canonical name — the downstream confidence gate catches genuine drift.",
      "Cost: ~$0.001 per call. Cheap insurance against entity drift.",
    ],
  },
  research: {
    fullName: "Step 1 — Research synthesis",
    model: "mistral-medium-2604",
    temperature: "0.2",
    timeout: "120s",
    reads: [
      "Wikipedia REST API (entity summary + Wikidata)",
      "Tavily news search (depth medium+)",
      "Career pages via httpx + Playwright/Lightpanda fallback (depth high)",
      "Tavily targeted search for existing AI initiatives",
      "Verified-companies SQLite index (rapidfuzz)",
    ],
    writes: [
      "CompanyContext (typed Pydantic synthesis)",
      "EvidenceLedger seed (one entry per fetched source)",
      "ResearchBundle (raw signals before synthesis)",
    ],
    whyActivity:
      "All I/O — HTTP fetches in parallel via asyncio.gather, one Mistral synthesis call. Workflow class can't do I/O.",
    notes: [
      "Parallel sub-tasks fire concurrently; synthesis runs once on the combined bundle.",
      "Entity-identity check (v9.5) refuses early if user input doesn't fuzzy-match the research-found entity.",
    ],
  },
  gap_fill: {
    fullName: "Step 1b — Context completion (gap-fill)",
    model: "mistral-small-2603",
    temperature: "0.2",
    timeout: "180s",
    reads: [
      "Current CompanyContext (identifies missing/empty fields)",
      "Tavily search per missing field (parallel, semaphore 3)",
      "Deep-read top 1 result per gap up to 6000 chars",
    ],
    writes: [
      "Updated CompanyContext (filled stated_priorities, data_assets, products lists)",
      "Ledger entries (kind=GAP_FILL) for every fetched source",
    ],
    whyActivity:
      "Tavily + HTTP + LLM. Adaptive — only runs if research left gaps. Skipped when TAVILY_API_KEY missing.",
    notes: [
      "Layer-1 re-synthesis was removed in v9.x — was decorative and returned empty lists.",
      "Layer-2 per-field extraction targets the missing list fields directly.",
    ],
  },
  retrieve: {
    fullName: "Step 2 — Retrieve peer precedents",
    model: "mistral-embed (for query embedding)",
    timeout: "5s",
    reads: [
      "Precedent corpus (~2,150 deployments) — Google Cloud customer stories + Evidently AI blueprints + Google Cloud blueprints",
      "Industry filter (relaxes if too restrictive)",
    ],
    writes: ["RetrievedPrecedents (top-8 by cosine similarity, optional MMR)"],
    whyActivity:
      "Embedding call + numpy matmul over the in-memory corpus matrix. Activity boundary keeps the matrix loading cached.",
    notes: [
      "Top-k = 8 by default (Settings.top_k_precedents). MMR optional for diversity.",
      "Every inspired_by ID later must reference an entry from this set.",
    ],
  },
  generate: {
    fullName: "Step 3 — Generate N candidate use cases",
    model: "mistral-medium-2604",
    temperature: "0.7",
    timeout: "600s",
    reads: [
      "CompanyContext + RetrievedPrecedents + free_text_notes + existing_initiatives",
      "Tavily web_search tool (Mistral function-calling, budget 0/2/4 per fast/standard/max)",
      "Few-shot examples from src/prompts.py",
    ],
    writes: [
      "CandidateBatch (N=8 default, configurable, ≥3 must be novel_direction)",
      "Diversity score (avg pairwise cosine over embeddings)",
      "Ledger entries (kind=GENERATION_TOOL) for every web_search hit",
    ],
    whyActivity:
      "LLM with function-calling loop + Tavily I/O + embedding I/O. The post-process gauntlet (drop hallucinated inspired_by / grounded_in / evidence_ids) runs here.",
    notes: [
      "v9.5 clamp: web_search query truncated to 399 chars (Tavily's 400 cap).",
      "Diversity regen fires only if avg pairwise cosine > 0.92 (rarely).",
      "Each web_search deep-reads top result up to 6000 chars.",
    ],
  },
  score: {
    fullName: "Step 4 — Score against 5 criteria (self-consistency)",
    model: "mistral-small-2603",
    temperature: "0.2 then 0.4 (two parallel passes)",
    timeout: "180s",
    reads: [
      "CandidateBatch from generate",
      "CriteriaWeights (defaults 0.2 each, configurable per run)",
      "Five-criteria rubric with positive + negative examples",
    ],
    writes: [
      "ScoredBatch with per-candidate per-criterion scores + aggregate (weighted average)",
    ],
    whyActivity:
      "Two parallel LLM calls (T=0.2 + T=0.4) for self-consistency. Activity owns both calls and the averaging.",
    notes: [
      "self_consistency_passes=2 is the locked default; can be set to 1 via enable_single_pass_score (saves 17s, less stable scores).",
      "Top-3 by aggregate goes to verify; rest land in rejected_appendix.",
    ],
  },
  verify: {
    fullName: "Step 5 — Per-candidate verification of top-3",
    model: "mistral-small-2603",
    temperature: "0.1",
    timeout: "240s",
    reads: [
      "Top-3 ScoredCandidate set",
      "Tavily search (3 parallel, semaphore 3) — 1 query per candidate",
      "Deep-read top 2 results per candidate up to 10000 chars (top 5 on max tier)",
    ],
    writes: [
      "VerificationBatch — one verdict per candidate (pass / partial_overlap / confirmed_existing)",
      "Supporting snippets (≤5 per candidate, ≤400 chars each)",
      "Ledger entries (kind=PER_CANDIDATE_VERIFICATION)",
    ],
    whyActivity:
      "Tavily + HTTP + LLM. Activity ensures all 3 verifications run concurrently with the bounded semaphore.",
    notes: [
      "Verifier defaults to PASS on inconclusive evidence — quality gate not gatekeeper.",
      "Supporting snippets feed into enrich (Step 6) as primary grounding.",
    ],
  },
  enrich: {
    fullName: "Step 6 — Selection + enrichment",
    model: "mistral-large-2512 (standard/max) · mistral-medium-2604 (fast)",
    temperature: "0.4",
    timeout: "300s",
    reads: [
      "ScoredBatch + VerificationBatch + full CompanyContext + RetrievedPrecedents + EvidenceLedger",
      "Verifier supporting_snippets per candidate",
      "Few-shot examples + valid corpus IDs whitelist",
    ],
    writes: [
      "Top-3 EnrichedUseCase objects (customer-ready prose, blueprint mermaid, TTV, risk, evidence_ids)",
      "RejectedCandidate appendix (de-duplicated against top-3)",
    ],
    whyActivity:
      "Single LLM call drafting all three use cases — Mistral Large 3 is the customer-facing prose engine. Activity boundary handles the numeric scrub + polish + attribution post-processing.",
    notes: [
      "Numeric scrubber wraps unverified figures as [unanchored: X] for downstream verification.",
      "Polish (Step 6a) follows immediately, then attribution_check.",
      "v9.5 defensive assert: raises clearly if fewer than 3 use cases come back.",
      "near_dup swap drops use cases that the generator self-marked as duplicates.",
    ],
  },
  polish: {
    fullName: "Step 6a — Polish prose (strict citation discipline)",
    model: "mistral-small-2603",
    temperature: "0.1",
    timeout: "90s",
    reads: [
      "Each EnrichedUseCase prose",
      "Full evidence pool excerpts (ledger entries)",
      "Source map for opaque (ev-XXX) IDs",
    ],
    writes: [
      "Cleaned prose: strips [unanchored: X] markers, converts (ev-XXX) → markdown links",
      "Optional new citations IF the prompt's strict 'entity AND figure exact match' rule fires",
      "Stripped fabricated URLs (defense in depth)",
    ],
    whyActivity:
      "LLM call + URL validation + bracket cleanup pass. Skipped entirely on fast tier.",
    notes: [
      "v9.5 prompt rewrite: only attach a citation when source contains BOTH the named entity AND the specific figure — stops the LVMH-YouTube-title kind of marginal citation.",
      "Fast tier skips polish; the bracket / ev-ID cleanup pass still runs in _coerce_enriched.",
      "Bracket cleanup (v9.5) strips fake (ev-XXX) IDs, bare [X] non-links, and correction-tail leftovers.",
    ],
  },
  meta_eval: {
    fullName: "Step 7 — Meta-evaluation (per-claim fact-check)",
    model: "mistral-medium-2604",
    temperature: "0.1",
    timeout: "240s",
    reads: [
      "Top-3 EnrichedUseCase prose",
      "Full EvidenceLedger (every fetched source)",
      "CompanyContext + RetrievedPrecedents",
    ],
    writes: [
      "MetaEvalReview (confidence, sales_engineer_ready, weakest_use_case_id, cross_cutting_concern)",
      "FactCheckEntry list — one per substantive claim with source_kind + source_url",
    ],
    whyActivity:
      "Atomic claim splitting + per-claim source matching. One large LLM call processes the whole report.",
    notes: [
      "Claims extracted are substantive only (numbers, named entities, named actions).",
      "ballpark_assumption TTV estimates are explicitly excluded from claims.",
      "Output drives the verification chain (Steps 7c-7e).",
    ],
  },
  web_verify: {
    fullName: "Step 7c — Web-verify rescue",
    timeout: "180s",
    reads: [
      "Claims marked passed=False by meta-eval",
      "Tavily search per claim (semaphore 3, cap 12 standard / 18 max)",
      "Deep-read tier-2 candidates up to 12000 chars",
    ],
    writes: [
      "Promoted claims with rescue_tier (verified / corroborated) + rescue_url",
      "Ledger entries (kind=CLAIM_VERIFICATION)",
      "Updated confidence (bounded qual_delta in [-0.15, +0.10])",
    ],
    whyActivity:
      "Pure Tavily I/O + deterministic credibility classifier. No LLM call.",
    notes: [
      "Two-tier credibility: allowlisted domain → auto-promote. Non-allowlist + entity/number anchor → corroborated.",
      "Rescue-share cap: if rescues do >50% of support work, confidence capped at 0.85.",
    ],
  },
  source_judge: {
    fullName: "Step 7d — Source judge (final-render gate)",
    model: "mistral-small-2603",
    temperature: "0.1 (0.05 on max tier)",
    timeout: "240s",
    reads: [
      "All claims passed=True with a resolvable URL",
      "Ledger entry content for each URL (up to 1500 chars)",
      "Or rationale text when URL doesn't resolve",
    ],
    writes: [
      "Three verdicts per pair: supported / corrected / unsupported",
      "Corrected claims get prose-patched inline with source value + citation",
      "Unsupported claims flip to passed=False with judge_rejected=true",
    ],
    whyActivity:
      "30-50 parallel Mistral Small calls (semaphore 8). Activity boundary handles the gather + bounded confidence delta.",
    notes: [
      "Catches the false-positive citation class: 'source mentions LVMH' ≠ 'source supports LVMH efficiency gains'.",
      "Corrected verdict only applies to numeric / rank / temporal facts — not entity contradictions.",
      "Bracket cleanup post-substitution catches duplicated-noun leftovers from corrections.",
    ],
  },
  final_qualify: {
    fullName: "Step 7e — Final qualitative replacement",
    model: "mistral-small-2603",
    temperature: "0.1",
    timeout: "60s",
    reads: ["Claims still passed=False after the rescue + judge chain", "Each use case's prose"],
    writes: [
      "Surgically-rewritten prose: numbers and named entities the chain couldn't anchor → qualitative phrasing",
      "qualified_out=true on the corresponding claims (excludes them from pass-rate denominator)",
    ],
    whyActivity:
      "Targeted LLM rewrite of specific phrases. The activity ensures the rewrite preserves grammar.",
    notes: [
      "Replaces v6's pre-strip-at-polish behaviour, which over-stripped real numbers.",
      "Final-qualify runs AFTER source-judge so every chance to anchor has been taken.",
    ],
  },
  quality_signals: {
    fullName: "Quality signals computation",
    model: "mistral-small-2603",
    temperature: "0.1",
    timeout: "60s",
    reads: [
      "Top-3 EnrichedUseCase set",
      "All FactCheckEntry claims",
      "Title + blueprint pattern list (for diversity grading)",
    ],
    writes: [
      "QualitySignals: diversity, per-use-case specificity, product diversity count, TTV spread, cost-tier spread, fact-check pass rate",
    ],
    whyActivity:
      "Two LLM calls (specificity + diversity). Computed signals get rendered as Le Chat badges + the report footer.",
    notes: [
      "Diversity is LLM-graded over titles + blueprint patterns (not pure embedding similarity).",
      "Specificity per use case feeds into the report card display.",
    ],
  },
  report: {
    fullName: "Final render + persist",
    timeout: "60s",
    reads: [
      "Complete Report object (Pydantic)",
    ],
    writes: [
      "Markdown report (CLI + standalone web app)",
      "UIComponent tree (Le Chat surface)",
      "Mermaid canvases per use case",
      "SQLite runs table (history page replay)",
    ],
    whyActivity:
      "Pure rendering + DB write. Lives in an activity because the workflow class can't do I/O.",
    notes: [
      "Le Chat output is a UIComponent Column tree + per-use-case mermaid canvases.",
      "The activity returns a plain dict so the result crosses the workflow sandbox boundary cleanly.",
    ],
  },
};

function StepCard({
  step,
  selected,
  onSelect,
}: {
  step: Step;
  selected: boolean;
  onSelect: (id: string) => void;
}): ReactElement {
  const cls = ROLE_STYLES[step.role];
  const round = step.role === "io" ? "rounded-full" : "rounded-lg";
  const ring = selected
    ? "ring-2 ring-mistral-orangeBright ring-offset-2 ring-offset-mistral-bg scale-[1.04]"
    : "hover:scale-[1.02]";
  return (
    <button
      type="button"
      onClick={() => onSelect(step.id)}
      className={`${cls} ${round} ${ring} border-2 px-3 py-2 shadow-md text-center min-w-[7.5rem] flex-1 max-w-[12rem] transition-all cursor-pointer focus:outline-none focus:ring-2 focus:ring-mistral-orangeBright`}
      aria-pressed={selected}
    >
      <div className="text-xs font-bold leading-tight">{step.label}</div>
      {step.sub && (
        <div className="text-[10px] mt-0.5 opacity-80 leading-tight">
          {step.sub}
        </div>
      )}
    </button>
  );
}

function ArrowRight(): ReactElement {
  return (
    <div className="flex items-center text-mistral-orange/70 shrink-0" aria-hidden>
      <svg width="14" height="10" viewBox="0 0 14 10" fill="none">
        <path
          d="M0 5h12M8 1l4 4-4 4"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
}

function ArrowDown(): ReactElement {
  return (
    <div className="flex justify-center my-1.5 text-mistral-orange/60" aria-hidden>
      <svg width="10" height="14" viewBox="0 0 10 14" fill="none">
        <path
          d="M5 0v12M1 8l4 4 4-4"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
}

export default function PipelineDiagram({
  selectedId,
  onSelect,
}: {
  selectedId?: string | null;
  onSelect?: (id: string | null) => void;
}): ReactElement {
  const handleSelect = (id: string) => {
    if (onSelect) {
      onSelect(selectedId === id ? null : id);
    }
  };
  return (
    <div className="space-y-2.5">
      <p className="text-xs text-ink-secondary leading-relaxed mb-2">
        Click any step → the right panel swaps to that step&apos;s details
        (model, temperature, what it reads, what it writes, why it&apos;s an
        activity).
      </p>
      {PHASES.map((phase, pi) => (
        <div key={phase.title}>
          <div className="rounded-xl border border-mistral-border/60 bg-mistral-dark/40 px-3 py-3">
            <div className="text-[10px] uppercase tracking-[0.18em] text-mistral-orangeBright font-bold mb-2">
              {phase.title}
            </div>
            <div className="flex items-stretch gap-2 flex-wrap">
              {phase.steps.map((step, si) => (
                <div key={step.id} className="flex items-center gap-2 flex-1 min-w-[7.5rem]">
                  <StepCard
                    step={step}
                    selected={selectedId === step.id}
                    onSelect={handleSelect}
                  />
                  {si < phase.steps.length - 1 && <ArrowRight />}
                </div>
              ))}
            </div>
          </div>
          {pi < PHASES.length - 1 && <ArrowDown />}
        </div>
      ))}
    </div>
  );
}
