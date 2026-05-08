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
import MermaidDiagram from "./components/MermaidDiagram";
import ProgressFeed from "./components/ProgressFeed";

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

  const cleanupRef = useRef<(() => void) | null>(null);

  // On `done`, fetch the final status + report
  useEffect(() => {
    return () => {
      if (cleanupRef.current) cleanupRef.current();
    };
  }, []);

  async function onSubmit() {
    setErrorMsg(null);
    setEvents([]);
    setReportMd(null);
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
        (ev) => setEvents((prev) => [...prev, ev]),
        (p) =>
          setStatus((prev) => ({
            ...(prev ?? ({} as StatusResponse)),
            current_step: p.step,
            progress_percent: p.progress,
          } as StatusResponse)),
        async (finalStatus) => {
          // On done, fetch the canonical /status (for metrics) + /report
          try {
            const s = await getStatus(run_id);
            setStatus(s);
            if (finalStatus === "completed") {
              const r = await getReport(run_id);
              setReportMd(r.markdown);
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
    if (cleanupRef.current) cleanupRef.current();
    cleanupRef.current = null;
    setPhase("form");
    setRunId(null);
    setStatus(null);
    setEvents([]);
    setReportMd(null);
    setErrorMsg(null);
  }

  return (
    <main className="min-h-screen p-6 md:p-10 max-w-5xl mx-auto">
      <header className="mb-8">
        <h1 className="text-3xl font-bold text-white">
          GenAI Use Case Generator
        </h1>
        <p className="text-slate-400 mt-1 text-sm">
          Mistral Workflows pipeline · 3 customer-ready use cases for any company,
          grounded in 2,150+ peer deployments
        </p>
      </header>

      {phase === "form" && (
        <section className="bg-mistral-surface border border-mistral-border rounded-lg p-6 space-y-4">
          <label className="block">
            <span className="text-sm font-semibold text-slate-200">Company name</span>
            <input
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              placeholder="e.g. Carrefour, BNP Paribas, L'Oréal"
              className="mt-1 w-full px-3 py-2 bg-mistral-dark border border-mistral-border rounded text-white"
            />
          </label>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <label className="block">
              <span className="text-sm font-semibold text-slate-200">Focus area</span>
              <select
                value={focusArea}
                onChange={(e) => setFocusArea(e.target.value as FocusArea)}
                className="mt-1 w-full px-3 py-2 bg-mistral-dark border border-mistral-border rounded text-white"
              >
                <option value="general">General</option>
                <option value="operations">Operations</option>
                <option value="customer">Customer</option>
                <option value="sustainability">Sustainability</option>
              </select>
            </label>
            <label className="block">
              <span className="text-sm font-semibold text-slate-200">Research depth</span>
              <select
                value={depth}
                onChange={(e) => setDepth(e.target.value as ResearchDepth)}
                className="mt-1 w-full px-3 py-2 bg-mistral-dark border border-mistral-border rounded text-white"
              >
                <option value="low">Low (Wikipedia + verified + initiatives)</option>
                <option value="medium">Medium (+ news, default)</option>
                <option value="high">High (+ jobs)</option>
              </select>
            </label>
          </div>

          <div>
            <button
              onClick={() => setShowAdvanced((s) => !s)}
              className="text-sm text-mistral-orange hover:underline"
            >
              {showAdvanced ? "▾" : "▸"} Advanced: criteria weights
            </button>
            {showAdvanced && (
              <div className="grid grid-cols-1 md:grid-cols-5 gap-3 mt-3">
                {(
                  [
                    ["relevance", "Relevance"],
                    ["iconic_potential", "Iconic"],
                    ["estimated_impact", "Impact"],
                    ["feasibility", "Feasibility"],
                    ["mistral_suitability", "Mistral fit"],
                  ] as const
                ).map(([k, label]) => (
                  <label key={k} className="block text-xs">
                    <span className="text-slate-300">
                      {label}{" "}
                      <span className="text-slate-500">{weights[k].toFixed(2)}</span>
                    </span>
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.05"
                      value={weights[k]}
                      onChange={(e) =>
                        setWeights({ ...weights, [k]: Number(e.target.value) })
                      }
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
              className="px-5 py-2 bg-mistral-orange hover:bg-orange-500 disabled:bg-slate-700 disabled:cursor-not-allowed text-white rounded font-semibold"
            >
              Generate
            </button>
          </div>
        </section>
      )}

      {(phase === "running" || phase === "completed" || phase === "refused" || phase === "failed") && (
        <section className="space-y-4">
          <div className="flex items-baseline justify-between">
            <p className="text-sm text-slate-300">
              <span className="text-slate-500">Run:</span>{" "}
              <span className="font-mono text-xs">{runId}</span>{" "}
              <span className="text-slate-500">·</span>{" "}
              <span className="text-mistral-orange">{phase}</span>
            </p>
            {phase !== "running" && (
              <button
                onClick={reset}
                className="text-sm text-slate-300 hover:text-white border border-mistral-border rounded px-3 py-1"
              >
                ← New run
              </button>
            )}
          </div>

          <ProgressFeed
            events={events}
            currentStep={status?.current_step ?? "starting"}
            progressPercent={status?.progress_percent ?? 0}
          />

          {phase === "completed" && status && (
            <div className="bg-mistral-surface border border-mistral-border rounded-lg p-4">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
                <Stat label="Fact-check pass" value={pct(status.fact_check_pass_rate)} />
                <Stat label="Meta-eval confidence" value={status.meta_eval_confidence?.toFixed(2) ?? "—"} />
                <Stat label="Trace events" value={String(status.event_count)} />
                <Stat
                  label="Wall time"
                  value={
                    status.started_at && status.completed_at
                      ? `${Math.round(
                          (new Date(status.completed_at).getTime() -
                            new Date(status.started_at).getTime()) / 1000,
                        )}s`
                      : "—"
                  }
                />
              </div>
            </div>
          )}

          {phase === "refused" && status?.refusal_reason && (
            <div className="bg-amber-950/40 border border-amber-700 rounded-lg p-4">
              <p className="text-amber-200 font-semibold mb-2">System refused to generate</p>
              <p className="text-amber-100/90 text-sm">{status.refusal_reason}</p>
            </div>
          )}

          {phase === "failed" && (
            <div className="bg-red-950/40 border border-red-700 rounded-lg p-4">
              <p className="text-red-200 font-semibold">Run failed</p>
              <p className="text-red-100/90 text-sm">{errorMsg ?? status?.error}</p>
            </div>
          )}

          {reportMd && <ReportRender markdown={reportMd} />}
        </section>
      )}

      <footer className="mt-12 text-xs text-slate-500 text-center">
        Built on Mistral Workflows · open source on{" "}
        <a href="https://github.com/alidor4702/genai-usecase-generator">GitHub</a>
      </footer>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-slate-400">{label}</div>
      <div className="text-lg font-semibold text-white">{value}</div>
    </div>
  );
}

function pct(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return `${Math.round(n * 100)}%`;
}

// Render the report markdown with mermaid blocks promoted to diagrams.
function ReportRender({ markdown }: { markdown: string }) {
  return (
    <article className="bg-mistral-surface border border-mistral-border rounded-lg p-6 prose-mistral max-w-none">
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
