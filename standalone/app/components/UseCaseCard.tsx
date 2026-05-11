"use client";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
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
  | "description"
  | "why"
  | "example"
  | "architecture"
  | "ttv"
  | "risk"
  | "products"
  | "citations";

/**
 * Inline markdown renderer with the prose styling that matches the rest of
 * the card. Crucially this turns `[anchor](url)` citations into clickable
 * <a> tags — without this the model's links show up as literal `[text](url)`
 * which is what the user complained about.
 */
/**
 * Renders example_output nicely. The LLM emits it as one of:
 *   1. A JSON object/array
 *   2. A JSON-ish string with code-fence
 *   3. Plain text (rare)
 *
 * For JSON we render a key/value table that's far easier to scan than
 * raw `{}`. For plain text we use a softer code-block. Either way it
 * reads as "here's what the user would see", not "here's a debug dump".
 */
/**
 * Try to parse `text` as JSON. Returns the parsed value or null. Tolerant
 * of two LLM-emitted formats:
 *   1. Real JSON — JSON.parse works directly.
 *   2. Python-repr dicts (`{'k': 'v', 'b': True, 'n': None}`) — common
 *      output when the model is "thinking in Python". We convert single
 *      quotes to double quotes, Python sentinels to JSON sentinels, and
 *      try again. Conservative: only convert when the input looks
 *      strictly Python-shaped (starts with `{` or `[`, no unescaped `"`
 *      mid-string).
 */
function tryParseLooseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    // fall through
  }
  // Fast-path Python repr: starts with { or [, contains single-quoted
  // keys/strings, uses Python sentinels.
  const trimmed = text.trim();
  if (!(trimmed.startsWith("{") || trimmed.startsWith("["))) return null;
  // Replace Python sentinels with JSON sentinels (whole-word match).
  let body = trimmed
    .replace(/\bTrue\b/g, "true")
    .replace(/\bFalse\b/g, "false")
    .replace(/\bNone\b/g, "null");
  // Convert single-quoted strings to double-quoted, escaping any inner
  // double quotes. State-machine pass: walk chars, flip state on
  // unescaped single quotes.
  const out: string[] = [];
  let inSingle = false;
  let inDouble = false;
  for (let i = 0; i < body.length; i++) {
    const ch = body[i];
    const prev = i > 0 ? body[i - 1] : "";
    if (ch === '"' && !inSingle) {
      inDouble = !inDouble;
      out.push(ch);
      continue;
    }
    if (ch === "'" && !inDouble && prev !== "\\") {
      inSingle = !inSingle;
      out.push('"');
      continue;
    }
    if (inSingle && ch === '"') {
      // Escape a literal double quote that lived inside a Python-quoted string.
      out.push('\\"');
      continue;
    }
    out.push(ch);
  }
  body = out.join("");
  try {
    return JSON.parse(body);
  } catch {
    return null;
  }
}

function ExampleOutput({ raw }: { raw: string }) {
  const cleaned = raw
    .trim()
    .replace(/^```(?:json|python)?\n?/i, "")
    .replace(/\n?```$/, "")
    .trim();
  const parsed = tryParseLooseJson(cleaned);
  if (parsed && typeof parsed === "object") {
    return (
      <div className="bg-mistral-dark/40 border border-mistral-border rounded-lg overflow-hidden">
        <JsonView value={parsed} depth={0} />
      </div>
    );
  }
  // Fallback — non-JSON freeform. Render with monospace + soft styling.
  return (
    <pre className="bg-mistral-dark/40 border border-mistral-border rounded-lg p-3 text-[13px] text-slate-200 whitespace-pre-wrap break-words font-mono leading-relaxed">
      {cleaned}
    </pre>
  );
}

function JsonView({ value, depth }: { value: unknown; depth: number }) {
  if (value === null) return <span className="text-ink-muted italic">null</span>;
  if (typeof value === "boolean")
    return <span className="text-amber-300 font-mono">{String(value)}</span>;
  if (typeof value === "number")
    return <span className="text-blue-300 font-mono">{value}</span>;
  if (typeof value === "string") {
    // Underline-style chip if the string looks like a URL.
    if (/^https?:\/\//.test(value)) {
      return (
        <a
          href={value}
          target="_blank"
          rel="noopener noreferrer"
          className="text-mistral-orangeBright underline underline-offset-2 break-all"
        >
          {value}
        </a>
      );
    }
    return <span className="text-emerald-200 break-words">{value}</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-ink-muted">[ ]</span>;
    return (
      <ol className={`${depth === 0 ? "p-3" : "pl-3"} space-y-1.5 list-decimal list-inside`}>
        {value.map((v, i) => (
          <li key={i} className="text-[13.5px]">
            <JsonView value={v} depth={depth + 1} />
          </li>
        ))}
      </ol>
    );
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    return (
      <dl className={`${depth === 0 ? "p-3" : ""} divide-y divide-mistral-border/50`}>
        {entries.map(([k, v]) => {
          // The model emits keys like `_disclaimer` / `_note` to mark
          // illustrative metadata in the example output. Render those
          // with the underscore stripped + a friendlier label so the
          // user doesn't see a literal "_DISCLAIMER" in the UI.
          const isMeta = k.startsWith("_");
          const display = isMeta ? k.slice(1).replace(/_/g, " ") : k.replace(/_/g, " ");
          return (
            <div key={k} className="grid grid-cols-[max-content_1fr] gap-3 py-1.5">
              <dt
                className={`text-[11px] uppercase tracking-wider font-semibold whitespace-nowrap ${
                  isMeta
                    ? "text-amber-300/80 italic"
                    : "text-mistral-orangeBright"
                }`}
              >
                {isMeta ? `ⓘ ${display}` : display}
              </dt>
              <dd className="text-[13.5px] min-w-0">
                <JsonView value={v} depth={depth + 1} />
              </dd>
            </div>
          );
        })}
      </dl>
    );
  }
  return <span className="text-slate-300">{String(value)}</span>;
}

function ProseMd({ content }: { content: string }) {
  return (
    <div className="prose-card text-slate-300 leading-relaxed text-[14px]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, href }) => (
            <a
              href={href ?? "#"}
              target="_blank"
              rel="noopener noreferrer"
              className="text-mistral-orangeBright hover:text-mistral-orange underline underline-offset-2 decoration-mistral-orange/40 hover:decoration-mistral-orange transition-colors"
            >
              {children}
            </a>
          ),
          p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
          code: ({ children }) => (
            <code className="px-1.5 py-0.5 rounded bg-mistral-surface border border-mistral-border text-[13px] text-mistral-orangeBright">
              {children}
            </code>
          ),
          ul: ({ children }) => <ul className="list-disc ml-5 my-2 space-y-1">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal ml-5 my-2 space-y-1">{children}</ol>,
          strong: ({ children }) => <strong className="text-white font-semibold">{children}</strong>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

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
  runId,
}: {
  uc: EnrichedUseCase;
  index: number;
  runId?: string;
}) {
  const [open, setOpen] = useState<Record<SectionKey, boolean>>({
    description: false,
    why: false,
    example: false,
    architecture: false,
    ttv: false,
    risk: false,
    products: false,
    citations: false,
  });

  // Defensive reads: backend versions may differ on optional fields. The
  // crash that motivated this — `reportData.use_cases.length` going boom
  // because the field was actually `top_use_cases` — taught us not to
  // trust shape assumptions.
  const meta =
    BLUEPRINT_META[uc.blueprint_pattern] ?? {
      label: uc.blueprint_pattern || "blueprint",
      tone: "from-slate-500/20 to-slate-500/0",
      bar: "border-slate-500",
    };
  const description = uc.description || "";
  // Plain-text summary lives WITHOUT markdown chrome — used for the 2-line
  // teaser. Strip basic markdown link syntax so the chip-form summary
  // doesn't show `[anchor](url)` literal in the always-visible part.
  const plainSummary = description.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1");
  const summary = plainSummary.split(/(?<=[.!?])\s+/).slice(0, 2).join(" ") || plainSummary.slice(0, 280);
  const anchoredTo = uc.time_to_value?.anchored_to ?? [];
  const ttvBasis = uc.time_to_value?.basis;
  const products = uc.suggested_mistral_products ?? [];
  const inspiredBy = uc.inspired_by ?? [];
  const groundedIn = uc.grounded_in ?? [];

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
          <Section k="description" title="Full description">
            <ProseMd content={description} />
          </Section>
          <Section k="why" title="Why this company">
            <ProseMd content={uc.why_this_company || ""} />
          </Section>
          <Section k="example" title="Example interaction">
            <div className="space-y-3">
              <div>
                <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-ink-muted mb-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-mistral-orange" aria-hidden />
                  User input
                </div>
                <div className="bg-mistral-dark/60 border-l-2 border-mistral-orange rounded-r p-3 text-[14px] text-white italic">
                  "{uc.example_input}"
                </div>
              </div>
              <div>
                <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-ink-muted mb-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" aria-hidden />
                  System output
                  <span className="ml-1 text-[10px] text-amber-300/80 normal-case tracking-normal italic">
                    illustrative
                  </span>
                </div>
                <ExampleOutput raw={uc.example_output ?? ""} />
              </div>
            </div>
          </Section>
          <Section k="architecture" title="Architecture blueprint">
            <BlueprintBlock uc={uc} />
          </Section>
          <Section k="ttv" title="Time-to-value">
            <p>
              <span className="font-semibold text-white">
                {uc.time_to_value?.estimate || "unknown"}
              </span>
              {ttvBasis === "ballpark_assumption" && (
                <span className="ml-2 text-amber-300 text-xs uppercase tracking-wider font-bold">
                  Estimated (no precedent)
                </span>
              )}
              {ttvBasis === "precedent" && anchoredTo.length > 0 && (
                <span className="ml-2 text-blue-300 text-xs uppercase tracking-wider font-bold">
                  Precedent-anchored
                </span>
              )}
            </p>
            {uc.time_to_value?.rationale && (
              <p className="mt-1.5 text-ink-secondary italic text-sm">
                {uc.time_to_value.rationale}
              </p>
            )}
          </Section>
          <Section k="risk" title="Top implementation risk">
            <ProseMd content={uc.top_implementation_risk || ""} />
          </Section>
          <Section k="products" title="Mistral products">
            <ul className="flex flex-wrap gap-1.5">
              {products.map((p) => (
                <li
                  key={p}
                  className="px-2.5 py-1 rounded bg-mistral-orange/10 border border-mistral-orange/30 text-[13px] text-mistral-orangeBright"
                >
                  {p}
                </li>
              ))}
            </ul>
          </Section>
          {(inspiredBy.length > 0 || groundedIn.length > 0 || (uc.evidence_ids ?? []).length > 0) && (
            <Section k="citations" title="Grounding">
              {runId && (
                <p className="mb-3 text-[12px] text-ink-secondary">
                  Click any citation to see the source content in the{" "}
                  <a
                    href={`/grounding/${runId}`}
                    className="text-mistral-orangeBright hover:text-mistral-orange underline underline-offset-2"
                  >
                    grounding database ↗
                  </a>
                  .
                </p>
              )}
              {inspiredBy.length > 0 && (
                <div className="mb-2">
                  <span className="text-[11px] uppercase tracking-wider text-ink-muted block mb-1">
                    Inspired by precedents
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {inspiredBy.map((id) =>
                      runId ? (
                        <a
                          key={id}
                          href={`/grounding/${runId}#${encodeURIComponent(id)}`}
                          className="px-2 py-0.5 bg-mistral-dark/60 border border-mistral-border rounded text-[12px] text-slate-300 font-mono hover:border-mistral-orange hover:text-mistral-orangeBright transition-colors"
                        >
                          {id}
                        </a>
                      ) : (
                        <code key={id} className="px-2 py-0.5 bg-mistral-dark/60 border border-mistral-border rounded text-[12px] text-slate-300">
                          {id}
                        </code>
                      )
                    )}
                  </div>
                </div>
              )}
              {(uc.evidence_ids ?? []).length > 0 && (
                <div className="mb-2">
                  <span className="text-[11px] uppercase tracking-wider text-ink-muted block mb-1">
                    Web evidence
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {(uc.evidence_ids ?? []).map((id) =>
                      runId ? (
                        <a
                          key={id}
                          href={`/grounding/${runId}#${encodeURIComponent(id)}`}
                          className="px-2 py-0.5 bg-mistral-orange/10 border border-mistral-orange/30 rounded text-[12px] text-mistral-orangeBright font-mono hover:bg-mistral-orange/20 transition-colors"
                        >
                          {id}
                        </a>
                      ) : (
                        <code key={id} className="px-2 py-0.5 bg-mistral-dark/60 border border-mistral-border rounded text-[12px] text-slate-300">
                          {id}
                        </code>
                      )
                    )}
                  </div>
                </div>
              )}
              {groundedIn.length > 0 && (
                <div>
                  <span className="text-[11px] uppercase tracking-wider text-ink-muted block mb-1">
                    Grounded in CompanyContext fields
                  </span>
                  <div className="flex flex-wrap gap-1.5">
                    {groundedIn.map((g) => (
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
      <MermaidDiagram source={uc.blueprint_mermaid} id={uc.id} pattern={uc.blueprint_pattern} />
    </div>
  );
}
