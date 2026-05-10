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
 */

import type { ReactElement } from "react";

type Role = "llm" | "live" | "preset" | "io";

const ROLE_STYLES: Record<Role, string> = {
  llm: "bg-mistral-orange/90 border-mistral-orangeBright text-white shadow-mistral-orange/30",
  live: "bg-blue-900 border-blue-400/70 text-blue-50 shadow-blue-900/30",
  preset: "bg-emerald-900 border-emerald-400/70 text-emerald-50 shadow-emerald-900/30",
  io: "bg-mistral-surface border-mistral-orange/60 text-white shadow-black/20",
};

type Step = { label: string; sub?: string; role: Role };
type Phase = { title: string; steps: Step[] };

const PHASES: Phase[] = [
  {
    title: "Phase 1 · Research & retrieve",
    steps: [
      { label: "Company + knobs", role: "io" },
      { label: "1. Research", sub: "Mistral Medium 3.5", role: "llm" },
      { label: "1b. Gap-fill", sub: "Tavily", role: "live" },
      { label: "2. Retrieve", sub: "cosine top-k", role: "preset" },
    ],
  },
  {
    title: "Phase 2 · Candidate generation",
    steps: [
      { label: "3. Generate 12", sub: "Mistral Medium 3.5 + web_search", role: "llm" },
      { label: "4. Score · 5 criteria", sub: "Mistral Small × 2", role: "llm" },
      { label: "5. Verify top-3", sub: "Tavily + Small", role: "llm" },
    ],
  },
  {
    title: "Phase 3 · Enrich top-3",
    steps: [
      { label: "6. Enrich", sub: "Mistral Large 3", role: "llm" },
      { label: "6a. Polish", sub: "full-pool", role: "llm" },
      { label: "7. Meta-eval", sub: "per-claim · Mistral Medium 3.5", role: "llm" },
    ],
  },
  {
    title: "Phase 4 · Verification chain",
    steps: [
      { label: "7c. Web-verify", sub: "2-tier rescue", role: "live" },
      { label: "7d. Judge", sub: "self-correcting · Mistral Small", role: "llm" },
      { label: "7e. Final qualify", sub: "Mistral Small", role: "llm" },
    ],
  },
  {
    title: "Phase 5 · Output",
    steps: [
      { label: "Quality signals", role: "llm" },
      { label: "Report + persist", role: "io" },
    ],
  },
];

function StepCard({ step }: { step: Step }): ReactElement {
  const cls = ROLE_STYLES[step.role];
  const round = step.role === "io" ? "rounded-full" : "rounded-lg";
  return (
    <div
      className={`${cls} ${round} border-2 px-3 py-2 shadow-md text-center min-w-[7.5rem] flex-1 max-w-[12rem]`}
    >
      <div className="text-xs font-bold leading-tight">{step.label}</div>
      {step.sub && (
        <div className="text-[10px] mt-0.5 opacity-80 leading-tight">
          {step.sub}
        </div>
      )}
    </div>
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

export default function PipelineDiagram(): ReactElement {
  return (
    <div className="space-y-2.5">
      {PHASES.map((phase, pi) => (
        <div key={phase.title}>
          <div className="rounded-xl border border-mistral-border/60 bg-mistral-dark/40 px-3 py-3">
            <div className="text-[10px] uppercase tracking-[0.18em] text-mistral-orangeBright font-bold mb-2">
              {phase.title}
            </div>
            <div className="flex items-stretch gap-2 flex-wrap">
              {phase.steps.map((step, si) => (
                <div key={`${pi}-${si}`} className="flex items-center gap-2 flex-1 min-w-[7.5rem]">
                  <StepCard step={step} />
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
