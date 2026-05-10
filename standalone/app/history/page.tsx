"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import AnimatedBackground from "../components/AnimatedBackground";
import SiteNav from "../components/SiteNav";

/**
 * /history — list of all past pipeline runs persisted in the SQLite
 * runs table. Each row links into /history/[runId] for the full
 * structured report (re-renders without re-running the pipeline).
 */

type RunRow = {
  run_id: string;
  company_name: string;
  status: "completed" | "refused" | "failed";
  started_at: number;          // unix epoch
  completed_at: number;
  fact_check_pass_rate: number | null;
  meta_eval_confidence: number | null;
  sales_engineer_ready: number | null;
  refusal_reason: string | null;
  error: string | null;
};

const API =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_URL) || "/api";

function fmtDuration(start: number, end: number): string {
  const s = Math.max(0, end - start);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  return r === 0 ? `${m}m` : `${m}m ${r}s`;
}

function fmtRelative(ts: number): string {
  const now = Math.floor(Date.now() / 1000);
  const d = now - ts;
  if (d < 60) return `${d}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

function StatusChip({ status }: { status: RunRow["status"] }) {
  const styles: Record<string, string> = {
    completed: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
    refused: "bg-amber-500/15 text-amber-300 border-amber-500/40",
    failed: "bg-rose-500/15 text-rose-300 border-rose-500/40",
  };
  return (
    <span className={`px-2 py-0.5 text-[10px] uppercase tracking-wider font-bold rounded border ${styles[status] || styles.failed}`}>
      {status}
    </span>
  );
}

function ConfidenceChip({ value, ready }: { value: number | null; ready: number | null }) {
  if (value === null) return null;
  const tone =
    ready === 1
      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/40"
      : value >= 0.7
        ? "bg-amber-500/15 text-amber-300 border-amber-500/40"
        : "bg-slate-500/15 text-slate-300 border-slate-500/30";
  return (
    <span className={`px-2 py-0.5 text-[11px] font-bold tracking-wide rounded border font-mono ${tone}`}>
      {value.toFixed(2)}
    </span>
  );
}

export default function HistoryPage() {
  const [runs, setRuns] = useState<RunRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/runs?limit=200`)
      .then((r) => {
        if (!r.ok) throw new Error(`history fetch failed: ${r.status}`);
        return r.json();
      })
      .then((d: { total: number; runs: RunRow[] }) => {
        setRuns(d.runs ?? []);
        setTotal(d.total ?? 0);
      })
      .catch((e: unknown) => setError(String(e)));
  }, []);

  return (
    <>
      <AnimatedBackground />
      <main className="relative z-10 min-h-screen px-4 sm:px-8 py-10 max-w-5xl mx-auto">
        <SiteNav />
        <header className="mb-8">
          <span className="text-[11px] uppercase tracking-[0.2em] text-mistral-orangeBright font-bold">
            History
          </span>
          <h1 className="mt-2 text-4xl sm:text-5xl font-bold text-white tracking-tight">
            Every past run, replayable.
          </h1>
          <p className="mt-3 text-slate-300 max-w-3xl leading-relaxed">
            Each row is a completed pipeline run persisted in the runs
            table. Click any one to re-open the full structured report
            (cards, blueprints, fact-check transparency) without
            re-running the pipeline.
          </p>
        </header>

        {error && (
          <div className="glass border-l-4 border-bad rounded-lg p-4 mb-6">
            <p className="text-bad font-semibold">Couldn't load history</p>
            <p className="text-ink-secondary text-sm font-mono mt-1">{error}</p>
          </div>
        )}

        {!runs && !error && (
          <div className="glass rounded-lg p-6 text-center text-ink-secondary italic">
            Loading run history…
          </div>
        )}

        {runs && runs.length === 0 && (
          <div className="glass rounded-lg p-6 text-center text-ink-secondary">
            No runs yet. Generate one from the{" "}
            <Link href="/generate" className="text-mistral-orangeBright hover:text-mistral-orange">
              Generate page ↗
            </Link>
            .
          </div>
        )}

        {runs && runs.length > 0 && (
          <>
            <div className="flex items-baseline justify-between mb-4">
              <p className="text-xs text-ink-secondary">
                <span className="text-mistral-orangeBright font-mono">{runs.length}</span>
                {" "}/ {total} runs
              </p>
              <Link
                href="/generate"
                className="text-xs px-3 py-1.5 rounded-lg border border-mistral-orange/40 text-mistral-orangeBright hover:bg-mistral-orange/10 transition-colors uppercase tracking-wider font-bold"
              >
                + New run
              </Link>
            </div>
            <div className="space-y-2">
              {runs.map((r) => (
                <Link
                  key={r.run_id}
                  href={`/history/${r.run_id}`}
                  className="block glass rounded-lg border-l-4 border-mistral-orange/40 hover:border-mistral-orange transition-colors p-4 group"
                >
                  <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div className="flex items-center gap-3 flex-wrap min-w-0">
                      <h3 className="text-base font-semibold text-white truncate">
                        {r.company_name}
                      </h3>
                      <StatusChip status={r.status} />
                      <ConfidenceChip value={r.meta_eval_confidence} ready={r.sales_engineer_ready} />
                      {r.fact_check_pass_rate !== null && (
                        <span className="text-[11px] text-ink-secondary font-mono">
                          fact-check {(r.fact_check_pass_rate * 100).toFixed(0)}%
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 text-[11px] text-ink-muted font-mono">
                      <span>{fmtDuration(r.started_at, r.completed_at)}</span>
                      <span>·</span>
                      <span>{fmtRelative(r.started_at)}</span>
                      <span className="text-mistral-orangeBright group-hover:translate-x-0.5 transition-transform">→</span>
                    </div>
                  </div>
                  {r.refusal_reason && (
                    <p className="text-xs text-amber-300/80 mt-2 italic line-clamp-1">
                      Refused: {r.refusal_reason}
                    </p>
                  )}
                  {r.error && (
                    <p className="text-xs text-rose-300/80 mt-2 font-mono line-clamp-1">
                      Error: {r.error}
                    </p>
                  )}
                </Link>
              ))}
            </div>
          </>
        )}
      </main>
    </>
  );
}
