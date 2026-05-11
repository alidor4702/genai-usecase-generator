"use client";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import CompanyGlyph from "../components/CompanyGlyph";
import PixelMascot from "../components/PixelMascot";
import UseCaseCard from "../components/UseCaseCard";
import type { Report } from "../lib/api";
import {
  postGenerate,
  getStatus,
  getReport,
  subscribeToEvents,
  type FocusArea,
  type ResearchDepth,
  type StatusResponse,
  type TraceEvent,
} from "../lib/api";
import AnimatedBackground from "../components/AnimatedBackground";
import StepIndicator from "../components/StepIndicator";
import ActivityCard from "../components/ActivityCard";
import LiveStats from "../components/LiveStats";
import MermaidDiagram from "../components/MermaidDiagram";
import SiteNav from "../components/SiteNav";
import { progressForStep } from "../components/stepMeta";

type Phase = "form" | "running" | "completed" | "refused" | "failed";

export default function Page() {
  const [phase, setPhase] = useState<Phase>("form");
  const [companyName, setCompanyName] = useState("Carrefour");
  const [focusArea, setFocusArea] = useState<FocusArea>("general");
  const [depth, setDepth] = useState<ResearchDepth>("medium");
  const [tier, setTier] = useState<"fast" | "standard" | "max">("standard");
  const [weights, setWeights] = useState({
    relevance: 0.2,
    iconic_potential: 0.2,
    estimated_impact: 0.2,
    feasibility: 0.2,
    mistral_suitability: 0.2,
  });
  const [showAdvanced, setShowAdvanced] = useState(false);

  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [reportMd, setReportMd] = useState<string | null>(null);
  const [reportData, setReportData] = useState<Report | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [currentStep, setCurrentStep] = useState("starting");

  const cleanupRef = useRef<(() => void) | null>(null);
  useEffect(() => () => { cleanupRef.current?.(); }, []);

  async function onSubmit() {
    setErrorMsg(null);
    setEvents([]);
    setReportMd(null);
    setReportData(null);
    setStatus(null);
    setProgress(2);
    setCurrentStep("starting");
    setStartedAt(new Date().toISOString());
    setPhase("running");
    try {
      const { run_id } = await postGenerate({
        company_name: companyName,
        focus_area: focusArea,
        weights,
        research_depth: depth,
        tier,
      });
      setRunId(run_id);
      cleanupRef.current = subscribeToEvents(
        run_id,
        (ev) => {
          // Merge by id: step_start appends a "running" card; step_complete
          // patches the same card with completed_at + outputs_summary +
          // duration_ms. Without an id (legacy) we just append.
          setEvents((prev) => {
            if (!ev.id) return [...prev, ev];
            const idx = prev.findIndex((e) => e.id === ev.id);
            if (idx === -1) return [...prev, ev];
            const next = prev.slice();
            next[idx] = { ...next[idx], ...ev };
            return next;
          });
          setCurrentStep(ev.step);
          // Functional updater so progress is monotonic across out-of-order
          // events (step_start for an earlier phase shouldn't snap the bar
          // backwards if a later phase has already started).
          setProgress((cur) => Math.max(cur, progressForStep(ev.step)));
        },
        (p) => {
          // Don't take currentStep from the progress event — api.py sets
          // state.current_step once at run start and never updates it,
          // so this would clobber the (correct) currentStep we get from
          // trace events. Progress percent is fine to use.
          setProgress((cur) => Math.max(cur, p.progress));
        },
        async (finalStatus) => {
          try {
            const s = await getStatus(run_id);
            setStatus(s);
            if (finalStatus === "completed") {
              const r = await getReport(run_id);
              setReportMd(r.markdown);
              setReportData(r.report);
              setProgress(100);
              setPhase("completed");
            } else if (finalStatus === "refused") {
              setPhase("refused");
            } else {
              setPhase("failed");
            }
          } catch (e) {
            setErrorMsg(String(e));
            setPhase("failed");
          }
        },
      );
    } catch (e) {
      setErrorMsg(String(e));
      setPhase("failed");
    }
  }

  function reset() {
    cleanupRef.current?.();
    cleanupRef.current = null;
    setPhase("form");
    setRunId(null);
    setStatus(null);
    setEvents([]);
    setReportMd(null);
    setReportData(null);
    setErrorMsg(null);
    setProgress(0);
    setCurrentStep("starting");
    setStartedAt(null);
  }

  return (
    <>
      <AnimatedBackground />
      <main className="relative z-10 min-h-screen px-4 sm:px-8 py-10 max-w-6xl mx-auto">
        <SiteNav />
        <Hero phase={phase} companyName={companyName} status={status} progress={progress} />

        {phase === "form" && (
          <FormView
            companyName={companyName}
            setCompanyName={setCompanyName}
            focusArea={focusArea}
            setFocusArea={setFocusArea}
            tier={tier}
            setTier={setTier}
            depth={depth}
            setDepth={setDepth}
            weights={weights}
            setWeights={setWeights}
            showAdvanced={showAdvanced}
            setShowAdvanced={setShowAdvanced}
            onSubmit={onSubmit}
          />
        )}

        {(phase === "running" || phase === "completed" || phase === "refused" || phase === "failed") && (
          <RunView
            phase={phase}
            runId={runId}
            events={events}
            currentStep={currentStep}
            startedAt={startedAt}
            status={status}
            reportMd={reportMd}
            reportData={reportData}
            errorMsg={errorMsg}
            onReset={reset}
          />
        )}

        <Footer />
      </main>
    </>
  );
}

/* ─────────────────────────  Hero  ───────────────────────── */

function Hero({
  phase,
  companyName,
  status,
  progress,
}: {
  phase: Phase;
  companyName: string;
  status: StatusResponse | null;
  progress: number;
}) {
  const titleByPhase: Record<Phase, string> = {
    form: "Compastral",
    running: `Generating use cases for ${status?.company_name ?? companyName}`,
    completed: `Report ready · ${status?.company_name ?? companyName}`,
    refused: `Couldn't proceed · ${status?.company_name ?? companyName}`,
    failed: `Run failed · ${status?.company_name ?? companyName}`,
  };
  const subtitle: Record<Phase, string> = {
    form: "Three customer-ready GenAI use cases for any company. Grounded in 2,150+ real peer deployments. Built on Mistral Workflows.",
    running: "The pipeline is running — every agent action streams live below.",
    completed: "Three customer-ready proposals, scored against the Mistral Proto Team rubric.",
    refused: "Research signal was too sparse to confidently proceed.",
    failed: "Something went wrong. The trace log below has the details.",
  };

  return (
    <header className="mb-8">
      <div className="flex items-center gap-3 mb-4">
        <PixelMascot kind="compass" size={56} />
        <div>
          <div className="text-xs uppercase tracking-[0.2em] text-mistral-orange font-semibold">
            Mistral Proto · Take-Home
          </div>
          <div className="text-[11px] text-ink-muted mt-0.5">company × Mistral · pronounced compass</div>
        </div>
      </div>
      <h1 className="text-3xl sm:text-5xl font-bold text-white tracking-tight">
        {titleByPhase[phase]}
      </h1>
      <p className="text-ink-secondary text-sm sm:text-base mt-2 max-w-2xl">{subtitle[phase]}</p>

      {phase === "running" && (
        <div className="mt-4 h-1 bg-mistral-surface rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-mistral-orange to-mistral-orangeBright transition-all duration-700"
            style={{ width: `${progress}%` }}
          />
        </div>
      )}
    </header>
  );
}

/* ─────────────────────────  Form  ───────────────────────── */

function FormView({
  companyName, setCompanyName,
  focusArea, setFocusArea,
  tier, setTier,
  depth, setDepth,
  weights, setWeights,
  showAdvanced, setShowAdvanced,
  onSubmit,
}: {
  companyName: string; setCompanyName: (s: string) => void;
  focusArea: FocusArea; setFocusArea: (s: FocusArea) => void;
  tier: "fast" | "standard" | "max";
  setTier: (s: "fast" | "standard" | "max") => void;
  depth: ResearchDepth; setDepth: (s: ResearchDepth) => void;
  weights: { relevance: number; iconic_potential: number; estimated_impact: number; feasibility: number; mistral_suitability: number };
  setWeights: (s: { relevance: number; iconic_potential: number; estimated_impact: number; feasibility: number; mistral_suitability: number }) => void;
  showAdvanced: boolean; setShowAdvanced: (b: boolean) => void;
  onSubmit: () => void;
}) {
  const SUGGESTIONS = ["Carrefour", "BNP Paribas", "L'Oréal", "Veolia", "Mistral AI"] as const;
  const FOCUS_OPTIONS: { value: FocusArea; label: string; desc: string }[] = [
    { value: "general", label: "General", desc: "Balanced across surfaces" },
    { value: "operations", label: "Operations", desc: "Supply chain · ops · cost" },
    { value: "customer", label: "Customer", desc: "Personalisation · CX" },
    { value: "sustainability", label: "Sustainability", desc: "ESG · compliance · CSR" },
  ];
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey && companyName.trim()) {
      e.preventDefault();
      onSubmit();
    }
  };
  return (
    <div className="relative">
      {/* Mistral cat-M crest — only shows for "Mistral" / "Mistral AI",
          positioned ABOVE the panel so it reads like a brand mark
          emerging from it. Lives in this relative wrapper (not inside
          the panel) because the panel uses overflow-hidden for its
          gradient bloom and would clip the glyph otherwise. */}
      <CompanyGlyph name={companyName} />
      <section className="relative glass rounded-3xl p-6 sm:p-10 space-y-7 slide-in overflow-hidden">
      <div
        className="pointer-events-none absolute inset-0 opacity-40"
        style={{
          background:
            "radial-gradient(circle at 0% 0%, rgba(250,85,46,0.18), transparent 40%), radial-gradient(circle at 100% 100%, rgba(245,158,11,0.10), transparent 40%)",
        }}
        aria-hidden
      />
      <div className="relative">
        <label className="block">
          <span className="text-[11px] uppercase tracking-[0.18em] text-mistral-orangeBright font-bold">
            Step 1 · Pick a company
          </span>
          <div className="mt-3 relative flex items-center gap-3">
            <input
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              onKeyDown={onKey}
              placeholder="Type any public company — e.g. Carrefour"
              className="flex-1 min-w-0 pl-5 pr-12 py-4 bg-mistral-dark/70 border-2 border-mistral-border rounded-xl text-white text-xl placeholder:text-ink-muted focus:border-mistral-orange focus:outline-none focus:ring-4 focus:ring-mistral-orange/20 transition-all font-medium"
              autoFocus
            />
            <kbd className="absolute right-4 top-1/2 -translate-y-1/2 text-[11px] text-ink-muted font-mono px-2 py-0.5 bg-mistral-surface rounded border border-mistral-border">
              ↵
            </kbd>
          </div>
        </label>
        <div className="mt-3 flex flex-wrap gap-2">
          <span className="text-[11px] text-ink-muted self-center mr-1">Try:</span>
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setCompanyName(s)}
              className="px-3 py-1 text-xs rounded-full border border-mistral-border text-ink-secondary hover:text-white hover:border-mistral-orange hover:bg-mistral-orange/10 transition-all"
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      <div className="relative">
        <span className="text-[11px] uppercase tracking-[0.18em] text-mistral-orangeBright font-bold">
          Step 2 · Focus area
        </span>
        <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2">
          {FOCUS_OPTIONS.map((o) => {
            const active = focusArea === o.value;
            return (
              <button
                key={o.value}
                type="button"
                onClick={() => setFocusArea(o.value)}
                className={`text-left p-3 rounded-xl border-2 transition-all ${
                  active
                    ? "border-mistral-orange bg-mistral-orange/10 shadow-lg shadow-mistral-orange/10"
                    : "border-mistral-border hover:border-mistral-orange/40 bg-mistral-dark/40"
                }`}
              >
                <div className="flex items-center gap-2 text-sm font-semibold text-white">
                  <span
                    className={`w-1.5 h-4 rounded-sm transition-colors ${
                      active ? "bg-mistral-orange" : "bg-mistral-border"
                    }`}
                    aria-hidden
                  />
                  <span>{o.label}</span>
                </div>
                <div className="text-[11px] text-ink-secondary mt-1 ml-3.5">{o.desc}</div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="relative">
        <span className="text-[11px] uppercase tracking-[0.18em] text-mistral-orangeBright font-bold">
          Step 3 · Performance tier
        </span>
        <div className="mt-3 grid grid-cols-1 sm:grid-cols-3 gap-2">
          {([
            {
              value: "fast" as const,
              label: "Fast",
              speed: "~125s",
              detail: "Mistral Medium for prose, no polish/attribution. Phase 3 benchmark: equal-or-better confidence on Carrefour.",
            },
            {
              value: "standard" as const,
              label: "Standard",
              speed: "~215s",
              detail: "Default. Mistral Large 3 for prose, full guardrails, web_search budget 2.",
            },
            {
              value: "max" as const,
              label: "Max",
              speed: "~225s",
              detail: "More grounding: web_search 4, deep-read top-5, judge T=0.05, rescue cap 18.",
            },
          ]).map((o) => {
            const active = tier === o.value;
            return (
              <button
                key={o.value}
                type="button"
                onClick={() => setTier(o.value)}
                className={`text-left p-3 rounded-xl border-2 transition-all ${
                  active
                    ? "border-mistral-orange bg-mistral-orange/10 shadow-lg shadow-mistral-orange/10"
                    : "border-mistral-border hover:border-mistral-orange/40 bg-mistral-dark/40"
                }`}
              >
                <div className="flex items-center justify-between gap-2 text-sm font-semibold text-white">
                  <span className="flex items-center gap-2">
                    <span
                      className={`w-1.5 h-4 rounded-sm transition-colors ${
                        active ? "bg-mistral-orange" : "bg-mistral-border"
                      }`}
                      aria-hidden
                    />
                    {o.label}
                  </span>
                  <span className="text-[10px] font-mono text-mistral-orangeBright">{o.speed}</span>
                </div>
                <div className="text-[11px] text-ink-secondary mt-1 ml-3.5 leading-snug">
                  {o.detail}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="relative">
        <label className="block">
          <span className="text-[11px] uppercase tracking-[0.18em] text-mistral-orangeBright font-bold">
            Step 4 · Research depth
          </span>
          <select
            value={depth}
            onChange={(e) => setDepth(e.target.value as ResearchDepth)}
            // Custom chevron via inline SVG background so the arrow sits
            // vertically centered + on the right padding line. Native
            // browser arrow positions vary by OS — this normalises it.
            className="mt-3 w-full pl-4 pr-12 py-3 bg-mistral-dark/70 border-2 border-mistral-border rounded-xl text-white focus:border-mistral-orange focus:outline-none transition-all appearance-none bg-no-repeat"
            style={{
              backgroundImage:
                "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8' fill='none' stroke='%23fa552e' stroke-width='2' stroke-linecap='square'><polyline points='1,1 6,7 11,1'/></svg>\")",
              backgroundPosition: "right 1rem center",
              backgroundSize: "12px 8px",
            }}
          >
            <option value="low">Low — Wikipedia + verified-index + initiatives (~30s)</option>
            <option value="medium">Medium — adds news deep-read (default, ~90s)</option>
            <option value="high">High — adds jobs signal (~2 min)</option>
          </select>
        </label>
      </div>

      <div className="relative">
        <button
          type="button"
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="text-[11px] uppercase tracking-[0.18em] text-mistral-orangeBright hover:text-mistral-orange font-bold"
        >
          {showAdvanced ? "▾" : "▸"} Advanced · criteria weights
        </button>
        {showAdvanced && (
          <div className="grid grid-cols-1 sm:grid-cols-5 gap-3 mt-4 slide-in">
            {(
              [
                ["relevance", "Relevance"],
                ["iconic_potential", "Iconic"],
                ["estimated_impact", "Impact"],
                ["feasibility", "Feasibility"],
                ["mistral_suitability", "Mistral fit"],
              ] as const
            ).map(([k, label]) => (
              <label key={k} className="block bg-mistral-dark/40 rounded-lg p-3 border border-mistral-border">
                <div className="flex items-baseline justify-between text-[11px]">
                  <span className="text-ink-secondary uppercase tracking-wider">{label}</span>
                  <span className="text-mistral-orange font-mono font-bold">{weights[k].toFixed(2)}</span>
                </div>
                <input
                  type="range" min="0" max="1" step="0.05"
                  value={weights[k]}
                  onChange={(e) => setWeights({ ...weights, [k]: Number(e.target.value) })}
                  className="w-full mt-2 accent-mistral-orange"
                />
              </label>
            ))}
          </div>
        )}
      </div>

      <div className="relative flex items-center justify-between pt-2">
        <p className="text-[11px] text-ink-muted">
          ~3 min · 14 LLM calls · live agent activity feed
        </p>
        <button
          onClick={onSubmit}
          disabled={!companyName.trim()}
          className="group relative px-8 py-3.5 bg-gradient-to-r from-mistral-orange to-mistral-orangeBright hover:from-mistral-orangeBright hover:to-mistral-orange disabled:from-slate-700 disabled:to-slate-700 disabled:cursor-not-allowed text-white rounded-xl font-bold tracking-wide shadow-xl shadow-mistral-orange/30 hover:shadow-mistral-orange/50 transition-all"
        >
          Generate report
          <span className="ml-2 inline-block group-hover:translate-x-0.5 transition-transform">→</span>
        </button>
      </div>
      </section>
    </div>
  );
}

/* ─────────────────────────  Run view  ───────────────────────── */

function RunView({
  phase, runId, events, currentStep, startedAt, status, reportMd, reportData, errorMsg, onReset,
}: {
  phase: Phase;
  runId: string | null;
  events: TraceEvent[];
  currentStep: string;
  startedAt: string | null;
  status: StatusResponse | null;
  reportMd: string | null;
  reportData: Report | null;
  errorMsg: string | null;
  onReset: () => void;
}) {
  const recentFirst = [...events].reverse();
  const done = phase !== "running";
  return (
    <div className="space-y-5">
      <div className="flex items-baseline justify-between">
        <p className="text-xs text-ink-secondary">
          Run id <span className="font-mono text-ink-primary">{runId}</span>
        </p>
        {done && (
          <button
            onClick={onReset}
            className="px-4 py-1.5 text-sm rounded-lg border border-mistral-border hover:border-mistral-orange hover:text-white transition-colors"
          >
            ← New run
          </button>
        )}
      </div>

      <StepIndicator currentStep={currentStep} />
      <LiveStats
        events={events}
        startedAt={startedAt}
        factCheckPass={status?.fact_check_pass_rate ?? null}
        metaConfidence={status?.meta_eval_confidence ?? null}
        done={done}
      />

      {phase === "refused" && status?.refusal_reason && (
        <div className="glass border-l-4 border-warn rounded-lg p-4">
          <p className="text-warn font-semibold mb-1">Refused to generate</p>
          <p className="text-ink-primary text-sm">{status.refusal_reason}</p>
        </div>
      )}
      {phase === "failed" && (
        <div className="glass border-l-4 border-bad rounded-lg p-4">
          <p className="text-bad font-semibold mb-1">Run failed</p>
          <p className="text-ink-primary text-sm font-mono">{errorMsg ?? status?.error}</p>
        </div>
      )}

      <section>
        <div className="flex items-baseline justify-between mb-2">
          <h2 className="text-sm uppercase tracking-wider text-ink-secondary font-semibold">
            Live agent activity
          </h2>
          <span className="text-xs text-ink-muted">{events.length} actions</span>
        </div>
        <div className="space-y-2 max-h-[60vh] overflow-y-auto pr-1">
          {events.length === 0 && (
            <div className="glass rounded-lg p-6 text-center text-sm text-ink-secondary italic">
              <span className="pulse-dot mr-2" />
              Connecting to the pipeline…
            </div>
          )}
          {recentFirst.map((ev, i) => (
            <ActivityCard key={`${ev.started_at}-${i}`} event={ev} index={recentFirst.length - i - 1} />
          ))}
        </div>
      </section>

      {reportData && Array.isArray(reportData.top_use_cases) && reportData.top_use_cases.length > 0 && (
        <section className="space-y-4">
          {reportData.meta_review &&
            (!reportData.meta_review.sales_engineer_ready ||
              reportData.meta_review.confidence < 0.70) && (
              <DraftBanner review={reportData.meta_review} />
            )}
          <div className="flex items-baseline justify-between">
            <h2 className="text-sm uppercase tracking-[0.18em] text-ink-secondary font-bold">
              Generated use cases
            </h2>
            <span className="text-xs text-ink-muted">
              {reportData.top_use_cases.length} customer-ready proposals
            </span>
          </div>
          <div className="space-y-4">
            {reportData.top_use_cases.map((uc, i) => (
              <UseCaseCard key={uc.id ?? i} uc={uc} index={i} runId={runId ?? undefined} />
            ))}
          </div>
        </section>
      )}
      {/* When we have structured data, hide the use-case section from the
          markdown rendering (cards above already cover it). The footer —
          quality signals + fact-check transparency block — still renders. */}
      {reportMd && (
        <ReportRender
          markdown={reportData ? stripUseCaseSection(reportMd) : reportMd}
        />
      )}
    </div>
  );
}

/**
 * Strip the H2 section that contains the per-use-case prose (already
 * rendered above as cards). Backend emits "## Use cases" or
 * "## The three use cases" or similar — we match any H2 between the intro
 * and the next H2 that contains H3 use case headers, and remove it.
 */
function stripUseCaseSection(md: string): string {
  // The backend's render path ALWAYS emits exactly one H2 block of
  // use cases followed by their H3s. Find the first H2 whose body
  // contains H3 entries, strip that H2 + everything until the next H2.
  const lines = md.split("\n");
  let inUseCaseBlock = false;
  let dropping = false;
  const out: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith("## ")) {
      // Next H2 always ends a use case block we were dropping.
      if (dropping) dropping = false;
      // Decide whether THIS H2 is a use-case block by looking ahead for an
      // H3 within the next ~40 lines (use cases are H3 children).
      const lookahead = lines.slice(i + 1, i + 40).join("\n");
      if (/^### /m.test(lookahead) && /use case/i.test(line)) {
        inUseCaseBlock = true;
        dropping = true;
        continue;
      }
    }
    if (dropping) continue;
    out.push(line);
    void inUseCaseBlock;
  }
  return out.join("\n");
}

/* ─────────────────────────  Report renderer  ───────────────────────── */

function DraftBanner({
  review,
}: {
  review: NonNullable<Report["meta_review"]>;
}) {
  const conf = review.confidence;
  const seReady = review.sales_engineer_ready;

  // Three branches matching src/ui/render.py:_draft_banner_md.
  // The banner is a REVISION SUGGESTION, not a failure — every claim has
  // already been through the full verification chain, so the prose
  // doesn't ship unverified specifics regardless of the score. The
  // threshold gap is about citation density, not factual correctness.
  let chipLabel = "Confidence below SE-ready bar — revision suggested";
  let body = (
    <>
      Confidence{" "}
      <span className="text-amber-300 font-mono font-bold">{conf.toFixed(2)}</span>
      {" "}is below the{" "}<span className="font-mono">0.70</span> sales-engineer-ready
      bar. The use cases have been through the full verification chain
      (numeric anchoring · per-claim source check · web-verify rescue ·
      source-judge · qualitative rewrite). The threshold gap reflects
      citation density, not factual correctness — every specific claim is
      either source-anchored or rewritten qualitatively. Suggestions for
      revision below.
    </>
  );

  if (conf >= 0.70 && !seReady) {
    chipLabel = "Above the 0.70 bar — strategic revision suggested";
    body = (
      <>
        Confidence{" "}
        <span className="text-amber-300 font-mono font-bold">{conf.toFixed(2)}</span>
        {" "}is at or above the{" "}<span className="font-mono">0.70</span> numerical bar,
        but the meta-evaluator flagged a strategic concern requiring
        revision before customer use. See the cross-cutting note below.
        This gap is qualitative (report-level reasoning), not numerical or
        factual.
      </>
    );
  } else if (conf < 0.70 && seReady) {
    chipLabel = "Signals disagree — light review suggested";
    body = (
      <>
        Confidence{" "}
        <span className="text-amber-300 font-mono font-bold">{conf.toFixed(2)}</span>
        {" "}is below the{" "}<span className="font-mono">0.70</span> numerical bar even
        though the meta-evaluator marked the report sales-engineer-ready.
        Review the per-claim breakdown below to decide whether to ship —
        the signals disagree.
      </>
    );
  }

  return (
    <div className="relative glass rounded-xl border-l-4 border-amber-500 p-4 sm:p-5">
      <div className="flex items-start gap-3">
        <div className="shrink-0 w-9 h-9 rounded-md bg-amber-500/20 text-amber-300 flex items-center justify-center">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor"
            strokeWidth="1.5" strokeLinecap="square" shapeRendering="crispEdges" aria-hidden>
            <polygon points="8,2 14,13 2,13" />
            <line x1="8" y1="6" x2="8" y2="10" />
            <line x1="8" y1="11.5" x2="8" y2="12" />
          </svg>
        </div>
        <div className="flex-1">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="text-[11px] uppercase tracking-[0.18em] text-amber-300 font-bold">
              {chipLabel}
            </span>
          </div>
          <p className="text-sm text-slate-200 mt-1.5 leading-relaxed">{body}</p>
        </div>
      </div>
    </div>
  );
}

function ReportRender({ markdown }: { markdown: string }) {
  return (
    <article className="glass rounded-2xl p-6 sm:p-8 prose-mistral max-w-none slide-in">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          code(props) {
            const { className, children, ...rest } = props as {
              className?: string;
              children?: React.ReactNode;
            };
            const match = /language-mermaid/.exec(className || "");
            if (match) {
              const src = String(children ?? "").trim();
              return <MermaidDiagram source={src} id={hash(src)} />;
            }
            return (
              <code className={className} {...rest}>
                {children}
              </code>
            );
          },
        }}
      >
        {markdown}
      </ReactMarkdown>
    </article>
  );
}

function hash(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h).toString(36);
}

/* ─────────────────────────  Footer  ───────────────────────── */

function Footer() {
  return (
    <footer className="mt-16 pt-6 border-t border-mistral-border/40 text-center text-xs text-ink-muted">
      <p>
        Built on Mistral Workflows · open source on{" "}
        <a href="https://github.com/alidor4702/genai-usecase-generator">GitHub</a>
      </p>
    </footer>
  );
}
