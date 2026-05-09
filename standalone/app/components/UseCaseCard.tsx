"use client";
import { useState } from "react";
import type { EnrichedUseCase } from "../lib/api";
import MermaidDiagram from "./MermaidDiagram";

/**
 * Structured card view for a single EnrichedUseCase. Replaces the long
 * flat-markdown render with a clear, scannable hierarchy:
 *
 *   header     — title + Builds-on tag + tier chips (impact/cost/complexity/TTV)
 *   summary    — first 2 sentences of description (always visible)
 *   sections   — accordion-style expandable: Why this company, Example,
 *                Architecture (mermaid), Time-to-value, Risk, Mistral
 *                products, Citations
 *
 * The blueprint accent color (per blueprint_pattern) drives the card's
 * left border + section headers so the categorical color coding reads at
 * a glance.
 */

type SectionKey =
  | "why"
  | "example"
  | "architecture"
  | "ttv"
  | "risk"
  | "products"
  | "citations";

const BLUEPRINT_META: Record<
  string,
  { label: string; tone: string; bar: string }
> = {
  rag: {
    label: "RAG",
    tone: "from-blue-500/20 to-blue-500/0",
    bar: "border-blue-500",
  },
  agent_with_tools: {
    label: "Agent + tools",
    tone: "from-mistral-orange/20 to-mistral-orange/0",
    bar: "border-mistral-orange",
  },
  document_ai_pipeline: {
    label: "Document AI",
    tone: "from-emerald-500/20 to-emerald-500/0",
    bar: "border-emerald-500",
  },
  fine_tuned_domain: {
    label: "Fine-tuned domain model",
    tone: "from-purple-500/20 to-purple-500/0",
    bar: "border-purple-500",
  },
  hybrid_retrieval: {
    label: "Hybrid retrieval",
    tone: "from-teal-500/20 to-teal-500/0",
    bar: "border-teal-500",
  },
};

function ImpactChip({ tier }: { tier: string }) {
  const colors: Record<string, string> = {
    high: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
    medium: "bg-amber-500/15 text-amber-300 border-amber-500/30",
    low: "bg-slate-500/20 text-slate-400 border-slate-500/30",
  };
  return (
    <span className={`px-2 py-0.5 text-[11px] font-bold uppercase tracking-wider rounded border ${colors[tier] || colors.medium}`}>
      Impact: {tier}
    </span>
  );
}
function CostChip({ tier }: { tier: string }) {
  const colors: Record<string, string> = {
    low: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
    medium: "bg-amber-500/15 text-amber-300 border-amber-500/30",
    high: "bg-rose-500/20 text-rose-300 border-rose-500/30",
    unknown: "bg-slate-500/20 text-slate-400 border-slate-500/30",
  };
  return (
    <span className={`px-2 py-0.5 text-[11px] font-bold uppercase tracking-wider rounded border ${colors[tier] || colors.medium}`}>
      Cost: {tier}
    </span>
  );
}
function ComplexityChip({ tier }: { tier: string }) {
  return (
    <span className="px-2 py-0.5 text-[11px] font-bold uppercase tracking-wider rounded border bg-slate-500/15 text-slate-300 border-slate-500/30">
      Complexity: {tier}
    </span>
  );
}
function TtvChip({ uc }: { uc: EnrichedUseCase }) {
  const ballpark = uc.time_to_value.basis === "ballpark_assumption";
  const cls = ballpark
    ? "bg-amber-500/15 text-amber-300 border-amber-500/30"
    : "bg-blue-500/15 text-blue-300 border-blue-500/30";
  return (
    <span className={`px-2 py-0.5 text-[11px] font-bold uppercase tracking-wider rounded border ${cls}`}>
      TTV: {ballpark ? `~${uc.time_to_value.estimate} (est.)` : uc.time_to_value.estimate}
    </span>
  );
}

export default function UseCaseCard({
  uc,
  index,
}: {
  uc: EnrichedUseCase;
  index: number;
}) {
  const [open, setOpen] = useState<Record<SectionKey, boolean>>({
    why: index === 0,
    example: false,
    architecture: false,
    ttv: false,
    risk: false,
    products: false,
    citations: false,
  });

  const meta =
    BLUEPRINT_META[uc.blueprint_pattern] ?? {
      label: uc.blueprint_pattern,
      tone: "from-slate-500/20 to-slate-500/0",
      bar: "border-slate-500",
    };
  const summary = uc.description.split(/(?<=[.!?])\s+/).slice(0, 2).join(" ");

  const Section = ({
    k,
    title,
    children,
    defaultOpen,
  }: {
    k: SectionKey;
    title: string;
    children: React.ReactNode;
    defaultOpen?: boolean;
  }) => {
    const isOpen = open[k] ?? defaultOpen ?? false;
    return (
      <div className="border-t border-mistral-border/60 first:border-t-0">
        <button
          type="button"
          onClick={() => setOpen((o) => ({ ...o, [k]: !o[k] }))}
          className="w-full flex items-center justify-between gap-2 py-2.5 text-left group"
        >
          <span className="text-[11px] uppercase tracking-[0.18em] font-bold text-mistral-orangeBright">
            {title}
          </span>
          <span
            className={`text-ink-muted transition-transform ${
              isOpen ? "rotate-90" : ""
            } group-hover:text-mistral-orange`}
          >
            ▸
          </span>
        </button>
        {isOpen && (
          <div className="pb-3 text-[14px] text-slate-300 leading-relaxed">
            {children}
          </div>
        )}
      </div>
    );
  };

  return (
    <article
      className={`relative glass rounded-2xl overflow-hidden border-l-4 ${meta.bar} slide-in`}
      style={{ animationDelay: `${index * 80}ms` }}
    >
      <div
        className={`absolute inset-0 pointer-events-none bg-gradient-to-br ${meta.tone} opacity-50`}
        aria-hidden
      />
      <div className="relative p-5 sm:p-6">
        <header className="flex flex-wrap items-start justify-between gap-3 mb-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.18em] text-mistral-orangeBright font-bold">
              <span>Use case {index + 1}</span>
              <span className="text-ink-muted">·</span>
              <span className="text-ink-secondary">{meta.label}</span>
            </div>
            <h3 className="mt-1 text-xl sm:text-2xl font-bold text-white tracking-tight">
              {uc.title}
            </h3>
            {uc.builds_on_existing && uc.builds_on_note && (
              <div className="mt-2 inline-flex items-center gap-1.5 px-2.5 py-1 text-[11px] text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded">
                {uc.builds_on_note}
              </div>
            )}
          </div>
        </header>

        <div className="flex flex-wrap gap-2 mb-4">
          <ImpactChip tier={uc.impact_tier} />
          <CostChip tier={uc.operating_cost_tier} />
          <ComplexityChip tier={uc.complexity_tier} />
          <TtvChip uc={uc} />
        </div>

        <p className="text-slate-200 leading-relaxed text-[15px] mb-2">{summary}</p>

        <div className="mt-3 -mx-1 px-1">
          <Section k="why" title="Why this company" defaultOpen>
            <p>{uc.why_this_company}</p>
          </Section>
          <Section k="example" title="Example interaction">
            <div className="space-y-2">
              <div>
                <span className="text-[11px] uppercase tracking-wider text-ink-muted block mb-1">
                  Input
                </span>
                <code className="block bg-mistral-dark/60 border border-mistral-border rounded p-2 text-[13px] text-mistral-orangeBright">
                  {uc.example_input}
                </code>
              </div>
              <div>
                <span className="text-[11px] uppercase tracking-wider text-ink-muted block mb-1">
                  Output (illustrative)
                </span>
                <pre className="bg-mistral-dark/60 border border-mistral-border rounded p-2 text-[12.5px] text-slate-200 whitespace-pre-wrap break-words font-mono">
                  {uc.example_output}
                </pre>
              </div>
            </div>
          </Section>
          <Section k="architecture" title="Architecture blueprint">
            <BlueprintBlock uc={uc} />
          </Section>
          <Section k="ttv" title="Time-to-value">
            <p>
              <span className="font-semibold text-white">{uc.time_to_value.estimate}</span>
              {uc.time_to_value.basis === "ballpark_assumption" && (
                <span className="ml-2 text-amber-300 text-xs uppercase tracking-wider font-bold">
                  Estimated (no precedent)
                </span>
              )}
              {uc.time_to_value.basis === "precedent" && uc.time_to_value.anchored_to.length > 0 && (
                <span className="ml-2 text-blue-300 text-xs uppercase tracking-wider font-bold">
                  Precedent-anchored
                </span>
              )}
            </p>
            {uc.time_to_value.rationale && (
              <p className="mt-1.5 text-ink-secondary italic text-sm">
                {uc.time_to_value.rationale}
              </p>
            )}
          </Section>
          <Section k="risk" title="Top implementation risk">
            <p>{uc.top_implementation_risk}</p>
          </Section>
          <Section k="products" title="Mistral products">
            <ul className="flex flex-wrap gap-1.5">
              {uc.suggested_mistral_products.map((p) => (
                <li
                  key={p}
                  className="px-2.5 py-1 rounded bg-mistral-orange/10 border border-mistral-orange/30 text-[13px] text-mistral-orangeBright"
                >
                  {p}
                </li>
              ))}
            </ul>
          </Section>
          {(uc.inspired_by.length > 0 || uc.grounded_in.length > 0) && (
            <Section k="citations" title="Grounding">
              {uc.inspired_by.length > 0 && (
                <div className="mb-2">
                  <span className="text-[11px] uppercase tracking-wider text-ink-muted block mb-1">
                    Inspired by precedents
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {uc.inspired_by.map((id) => (
                      <code
                        key={id}
                        className="px-2 py-0.5 bg-mistral-dark/60 border border-mistral-border rounded text-[12px] text-slate-300"
                      >
                        {id}
                      </code>
                    ))}
                  </div>
                </div>
              )}
              {uc.grounded_in.length > 0 && (
                <div>
                  <span className="text-[11px] uppercase tracking-wider text-ink-muted block mb-1">
                    Grounded in CompanyContext fields
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {uc.grounded_in.map((g) => (
                      <code
                        key={g}
                        className="px-2 py-0.5 bg-mistral-dark/60 border border-mistral-border rounded text-[12px] text-slate-300"
                      >
                        {g}
                      </code>
                    ))}
                  </div>
                </div>
              )}
            </Section>
          )}
        </div>
      </div>
    </article>
  );
}

function BlueprintBlock({ uc }: { uc: EnrichedUseCase }) {
  // The mermaid body comes from the backend already decorated with the
  // blueprint's classDef colors (see ui/render._decorate_mermaid).
  const meta =
    BLUEPRINT_META[uc.blueprint_pattern] ?? {
      label: uc.blueprint_pattern,
      tone: "",
      bar: "",
    };
  return (
    <div>
      <div className="flex items-center gap-2 mb-2 text-[11px] uppercase tracking-[0.18em] text-ink-muted">
        <span
          className={`w-2 h-2 rounded-sm ${
            uc.blueprint_pattern === "rag"
              ? "bg-blue-500"
              : uc.blueprint_pattern === "agent_with_tools"
                ? "bg-mistral-orange"
                : uc.blueprint_pattern === "document_ai_pipeline"
                  ? "bg-emerald-500"
                  : uc.blueprint_pattern === "fine_tuned_domain"
                    ? "bg-purple-500"
                    : "bg-teal-500"
          }`}
          aria-hidden
        />
        <span className="font-bold">{meta.label}</span>
        <span className="text-ink-muted">pattern</span>
      </div>
      <MermaidDiagram source={uc.blueprint_mermaid} id={uc.id} />
    </div>
  );
}
