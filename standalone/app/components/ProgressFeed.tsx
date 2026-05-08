"use client";
import type { TraceEvent } from "../lib/api";

const STEP_COLORS: Record<string, string> = {
  research: "bg-blue-900/40 border-blue-700",
  gap_fill: "bg-purple-900/40 border-purple-700",
  retrieve: "bg-cyan-900/40 border-cyan-700",
  generate: "bg-emerald-900/40 border-emerald-700",
  "generate.web_search": "bg-emerald-900/20 border-emerald-700",
  score: "bg-amber-900/40 border-amber-700",
  verify: "bg-orange-900/40 border-orange-700",
  enrich: "bg-rose-900/40 border-rose-700",
  polish: "bg-pink-900/40 border-pink-700",
  attribution_check: "bg-fuchsia-900/40 border-fuchsia-700",
  meta_eval: "bg-violet-900/40 border-violet-700",
  quality_signals: "bg-slate-700/40 border-slate-500",
};

export default function ProgressFeed({
  events,
  currentStep,
  progressPercent,
}: {
  events: TraceEvent[];
  currentStep: string;
  progressPercent: number;
}) {
  return (
    <div className="bg-mistral-surface border border-mistral-border rounded-lg p-4">
      <div className="mb-3">
        <div className="flex items-baseline justify-between">
          <h3 className="text-sm font-semibold text-slate-200">
            Pipeline activity{" "}
            <span className="text-mistral-orange font-mono text-xs">[{currentStep}]</span>
          </h3>
          <span className="text-xs text-slate-400">{Math.round(progressPercent)}%</span>
        </div>
        <div className="mt-2 h-1.5 bg-mistral-dark rounded">
          <div
            className="h-1.5 bg-mistral-orange rounded transition-all"
            style={{ width: `${progressPercent}%` }}
          />
        </div>
      </div>
      <div className="max-h-72 overflow-y-auto pr-1">
        {events.length === 0 && (
          <p className="text-xs text-slate-500 italic">Waiting for activity…</p>
        )}
        <ul className="space-y-1">
          {events.map((ev, i) => {
            const cls = STEP_COLORS[ev.step] || "bg-slate-800/40 border-slate-600";
            const ts = new Date(ev.started_at).toLocaleTimeString();
            return (
              <li
                key={i}
                className={`text-xs border-l-2 ${cls} pl-2 py-1 rounded-r font-mono`}
              >
                <span className="text-slate-500">{ts}</span>{" "}
                <span className="text-slate-300">[{ev.step}]</span>{" "}
                <span className="text-slate-100">
                  {ev.actor}.{ev.action}
                </span>{" "}
                {ev.duration_ms !== null && (
                  <span className="text-slate-500">— {Math.round(ev.duration_ms)}ms</span>
                )}
                {ev.outputs_summary && (
                  <div className="text-slate-400 ml-2 truncate">{ev.outputs_summary}</div>
                )}
                {ev.error && <div className="text-red-400 ml-2">⚠ {ev.error}</div>}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
