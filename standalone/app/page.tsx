"use client";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  postGenerate,
  getStatus,
  getReport,
  subscribeToEvents,
  type FocusArea,
  type ResearchDepth,
  type StatusResponse,
  type TraceEvent,
} from "./lib/api";
import AnimatedBackground from "./components/AnimatedBackground";
import StepIndicator from "./components/StepIndicator";
import ActivityCard from "./components/ActivityCard";
import LiveStats from "./components/LiveStats";
import MermaidDiagram from "./components/MermaidDiagram";
import { progressForStep } from "./components/stepMeta";

type Phase = "form" | "running" | "completed" | "refused" | "failed";

export default function Page() {
  const [phase, setPhase] = useState<Phase>("form");
  const [companyName, setCompanyName] = useState("Carrefour");
  const [focusArea, setFocusArea] = useState<FocusArea>("general");
  const [depth, setDepth] = useState<ResearchDepth>("medium");
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
      });
      setRunId(run_id);
      cleanupRef.current = subscribeToEvents(
        run_id,
        (ev) => {
          setEvents((prev) => [...prev, ev]);
          setCurrentStep(ev.step);
          setProgress(progressForStep(ev.step));
        },
        (p) => {
          setCurrentStep(p.step);
          setProgress(Math.max(progress, p.progress));
        },
        async (finalStatus) => {
          try {
            const s = await getStatus(run_id);
            setStatus(s);
            if (finalStatus === "completed") {
              const r = await getReport(run_id);
              setReportMd(r.markdown);
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
    setErrorMsg(null);
    setProgress(0);
    setCurrentStep("starting");
    setStartedAt(null);
  }

  return (
    <>
      <AnimatedBackground />
      <main className="relative min-h-screen px-4 sm:px-8 py-10 max-w-6xl mx-auto">
        <Hero phase={phase} companyName={companyName} status={status} progress={progress} />

        {phase === "form" && (
          <FormView
            companyName={companyName}
            setCompanyName={setCompanyName}
            focusArea={focusArea}
            setFocusArea={setFocusArea}
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
    form: "GenAI Use Case Generator",
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
      <div className="flex items-center gap-3 mb-3">
        <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-mistral-orange to-mistral-orangeSoft flex items-center justify-center text-xl">
          {phase === "running" ? <span className="pulse-dot" /> : "M"}
        </div>
        <div className="text-xs uppercase tracking-[0.2em] text-mistral-orange font-semibold">
          Mistral Proto · Take-Home
        </div>
      </div>
      <h1 className="text-3xl sm:text-4xl font-bold text-white tracking-tight">
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
  depth, setDepth,
  weights, setWeights,
  showAdvanced, setShowAdvanced,
  onSubmit,
}: {
  companyName: string; setCompanyName: (s: string) => void;
  focusArea: FocusArea; setFocusArea: (s: FocusArea) => void;
  depth: ResearchDepth; setDepth: (s: ResearchDepth) => void;
  weights: { relevance: number; iconic_potential: number; estimated_impact: number; feasibility: number; mistral_suitability: number };
  setWeights: (s: { relevance: number; iconic_potential: number; estimated_impact: number; feasibility: number; mistral_suitability: number }) => void;
  showAdvanced: boolean; setShowAdvanced: (b: boolean) => void;
  onSubmit: () => void;
}) {
  return (
    <section className="glass rounded-2xl p-6 sm:p-8 space-y-5 slide-in">
      <label className="block">
        <span className="text-xs uppercase tracking-wider text-ink-secondary font-semibold">Company name</span>
        <input
          value={companyName}
          onChange={(e) => setCompanyName(e.target.value)}
          placeholder="e.g. Carrefour, BNP Paribas, L'Oréal, Mistral AI"
          className="mt-2 w-full px-4 py-3 bg-mistral-dark/60 border border-mistral-border rounded-lg text-white text-lg focus:border-mistral-orange focus:outline-none focus:ring-2 focus:ring-mistral-orange/30 transition-all"
          autoFocus
        />
      </label>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <label className="block">
          <span className="text-xs uppercase tracking-wider text-ink-secondary font-semibold">Focus area</span>
          <select
            value={focusArea}
            onChange={(e) => setFocusArea(e.target.value as FocusArea)}
            className="mt-2 w-full px-3 py-2.5 bg-mistral-dark/60 border border-mistral-border rounded-lg text-white focus:border-mistral-orange focus:outline-none"
          >
            <option value="general">General</option>
            <option value="operations">Operations</option>
            <option value="customer">Customer</option>
            <option value="sustainability">Sustainability</option>
          </select>
        </label>
        <label className="block">
          <span className="text-xs uppercase tracking-wider text-ink-secondary font-semibold">Research depth</span>
          <select
            value={depth}
            onChange={(e) => setDepth(e.target.value as ResearchDepth)}
            className="mt-2 w-full px-3 py-2.5 bg-mistral-dark/60 border border-mistral-border rounded-lg text-white focus:border-mistral-orange focus:outline-none"
          >
            <option value="low">Low — Wikipedia + verified-index + initiatives</option>
            <option value="medium">Medium — adds news (default)</option>
            <option value="high">High — adds jobs signal</option>
          </select>
        </label>
      </div>

      <div>
        <button
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="text-xs uppercase tracking-wider text-mistral-orangeBright hover:text-mistral-orange font-semibold"
        >
          {showAdvanced ? "▾" : "▸"} Advanced — criteria weights
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
              <label key={k} className="block">
                <div className="flex items-baseline justify-between text-[11px]">
                  <span className="text-ink-secondary uppercase tracking-wider">{label}</span>
                  <span className="text-mistral-orange font-mono">{weights[k].toFixed(2)}</span>
                </div>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={weights[k]}
                  onChange={(e) => setWeights({ ...weights, [k]: Number(e.target.value) })}
                  className="w-full accent-mistral-orange"
                />
              </label>
            ))}
          </div>
        )}
      </div>

      <div className="flex justify-end pt-2">
        <button
          onClick={onSubmit}
          disabled={!companyName.trim()}
          className="px-7 py-3 bg-gradient-to-r from-mistral-orange to-mistral-orangeBright hover:from-mistral-orangeBright hover:to-mistral-orange disabled:from-slate-700 disabled:to-slate-700 disabled:cursor-not-allowed text-white rounded-lg font-semibold tracking-wide shadow-lg shadow-mistral-orange/20 transition-all"
        >
          Generate report →
        </button>
      </div>
    </section>
  );
}

/* ─────────────────────────  Run view  ───────────────────────── */

function RunView({
  phase, runId, events, currentStep, startedAt, status, reportMd, errorMsg, onReset,
}: {
  phase: Phase;
  runId: string | null;
  events: TraceEvent[];
  currentStep: string;
  startedAt: string | null;
  status: StatusResponse | null;
  reportMd: string | null;
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

      {reportMd && <ReportRender markdown={reportMd} />}
    </div>
  );
}

/* ─────────────────────────  Report renderer  ───────────────────────── */

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
