// Maps trace `step` strings to the 7 user-visible pipeline phases plus
// per-event presentation metadata. Single source of truth so
// StepIndicator and ActivityCard render consistent labels.

export type PhaseKey =
  | "research"
  | "retrieve"
  | "generate"
  | "score"
  | "verify"
  | "enrich"
  | "review";

export const PHASES: { key: PhaseKey; label: string; sub: string; emoji: string }[] = [
  { key: "research", label: "Research", sub: "fetching company signal", emoji: "🔍" },
  { key: "retrieve", label: "Retrieve", sub: "finding peer precedents", emoji: "📚" },
  { key: "generate", label: "Generate", sub: "drafting 12 candidates", emoji: "💡" },
  { key: "score", label: "Score", sub: "rating against 5 criteria", emoji: "⚖️" },
  { key: "verify", label: "Verify", sub: "checking top-3 against the live web", emoji: "🛡️" },
  { key: "enrich", label: "Enrich", sub: "polishing customer-ready prose", emoji: "✨" },
  { key: "review", label: "Review", sub: "sales-engineer-ready check", emoji: "🎓" },
];

// Map every fine-grained trace step to a top-level phase
export const STEP_TO_PHASE: Record<string, PhaseKey> = {
  research: "research",
  gap_fill: "research",
  retrieve: "retrieve",
  generate: "generate",
  "generate.web_search": "generate",
  score: "score",
  verify: "verify",
  enrich: "enrich",
  polish: "enrich",
  attribution_check: "enrich",
  regen_one: "enrich",
  meta_eval: "review",
  web_verify: "review",
  quality_signals: "review",
};

// Friendly per-step display title + verb for the activity feed
export const STEP_DISPLAY: Record<
  string,
  { title: string; verb: string; emoji: string; tone: string }
> = {
  research: {
    title: "Synthesizing company context",
    verb: "Reading Wikipedia + recent news, fetching existing AI initiatives, classifying industry",
    emoji: "🔍",
    tone: "from-blue-500/30 to-blue-500/0",
  },
  gap_fill: {
    title: "Filling in missing context",
    verb: "Generating targeted search queries, pulling Tavily results to fill gaps",
    emoji: "🎯",
    tone: "from-purple-500/30 to-purple-500/0",
  },
  retrieve: {
    title: "Retrieving peer precedents",
    verb: "Searching the 2,150-deployment corpus for industry-relevant peer examples",
    emoji: "📚",
    tone: "from-cyan-500/30 to-cyan-500/0",
  },
  generate: {
    title: "Generating candidates",
    verb: "Brainstorming 12 candidate use cases with the company's data + priorities + precedents",
    emoji: "💡",
    tone: "from-emerald-500/30 to-emerald-500/0",
  },
  "generate.web_search": {
    title: "Pulling live web evidence",
    verb: "Generator decided it needed more grounding — running a Tavily search mid-flight",
    emoji: "🔎",
    tone: "from-emerald-500/20 to-emerald-500/0",
  },
  score: {
    title: "Scoring against 5 criteria",
    verb: "Self-consistency: rating 12 candidates × 5 criteria across 2 parallel passes",
    emoji: "⚖️",
    tone: "from-amber-500/30 to-amber-500/0",
  },
  verify: {
    title: "Verifying top-3 candidates",
    verb: "Targeted Tavily search + deep-read to check if the company already does this",
    emoji: "🛡️",
    tone: "from-orange-500/30 to-orange-500/0",
  },
  enrich: {
    title: "Writing customer-ready prose",
    verb: "Mistral Large drafting refined descriptions, blueprints, examples, risks",
    emoji: "✨",
    tone: "from-rose-500/30 to-rose-500/0",
  },
  polish: {
    title: "Polishing prose",
    verb: "Converting [unanchored: X] markers to qualitative language, swapping opaque IDs for markdown links",
    emoji: "🪄",
    tone: "from-pink-500/30 to-pink-500/0",
  },
  attribution_check: {
    title: "Checking citation attribution",
    verb: "Verifying corpus-ID citations match the right companies",
    emoji: "🔗",
    tone: "from-fuchsia-500/30 to-fuchsia-500/0",
  },
  regen_one: {
    title: "Regenerating weakest use case",
    verb: "Meta-eval flagged a weakness — Mistral Large rewriting that one use case",
    emoji: "🔄",
    tone: "from-rose-500/20 to-rose-500/0",
  },
  meta_eval: {
    title: "Reviewing the report",
    verb: "Senior-reviewer pass: would a Mistral SE pitch this? Per-claim fact-check.",
    emoji: "🎓",
    tone: "from-violet-500/30 to-violet-500/0",
  },
  web_verify: {
    title: "Rescuing flagged claims via web search",
    verb: "Two-tier credibility: allowlist domains auto-promote, others promote on entity/number anchor match",
    emoji: "🌐",
    tone: "from-indigo-500/30 to-indigo-500/0",
  },
  quality_signals: {
    title: "Computing quality signals",
    verb: "LLM-graded diversity + specificity, fact-check pass rate, TTV/cost spreads",
    emoji: "📊",
    tone: "from-slate-500/30 to-slate-500/0",
  },
};

// Friendly description of an actor (the model or service doing the work)
export function actorBadge(actor: string): string {
  if (actor.startsWith("mistral-medium")) return "Mistral Medium";
  if (actor.startsWith("mistral-large")) return "Mistral Large";
  if (actor.startsWith("mistral-small")) return "Mistral Small";
  if (actor.startsWith("mistral-embed")) return "Mistral Embed";
  if (actor === "tavily") return "Tavily web search";
  if (actor === "wikipedia") return "Wikipedia";
  if (actor === "precedent_corpus") return "Precedent corpus";
  if (actor === "interactive") return "User input";
  return actor;
}

// Pipeline progress percentage based on the latest step
export function progressForStep(step: string): number {
  const phase = STEP_TO_PHASE[step] ?? "research";
  const idx = PHASES.findIndex((p) => p.key === phase);
  if (idx < 0) return 5;
  // 7 phases mapped to 5%..95% so the bar never sits at 0 or 100 mid-run
  return 5 + Math.round((idx / (PHASES.length - 1)) * 90);
}
