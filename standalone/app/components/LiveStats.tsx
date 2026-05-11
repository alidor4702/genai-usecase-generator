"use client";
import { useEffect, useState } from "react";
import type { TraceEvent } from "../lib/api";

/**
 * Compact stats row that updates as the run progresses:
 *
 *   - elapsed wall time (live ticker)
 *   - count of LLM calls made
 *   - count of web searches
 *   - source-anchored claim ratio (only after meta-eval runs)
 *   - meta-eval confidence (only after meta-eval runs)
 *
 * v9.8: dropped raw "Events" count (low signal — same as agent activity
 * length below). Renamed "Fact-check" → "Anchored" to match the new
 * vocabulary across the rest of the surface.
 */
export default function LiveStats({
  events,
  startedAt,
  factCheckPass,
  metaConfidence,
  done,
}: {
  events: TraceEvent[];
  startedAt: string | null;
  factCheckPass: number | null;
  metaConfidence: number | null;
  done: boolean;
}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (done) return;
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, [done]);

  const elapsed = startedAt ? Math.max(0, now - new Date(startedAt).getTime()) : 0;
  const elapsedStr = formatDuration(elapsed);
  const llmCalls = events.filter((e) => e.action === "chat.complete" || e.action === "embeddings.create").length;
  const webSearches = events.filter((e) => e.actor === "tavily" || e.step === "generate.web_search").length;

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
      <Stat label="Elapsed" value={elapsedStr} accent />
      <Stat label="LLM calls" value={String(llmCalls)} />
      <Stat label="Web searches" value={String(webSearches)} />
      {done && factCheckPass !== null && (
        <Stat
          label="Anchored"
          value={`${Math.round(factCheckPass * 100)}%`}
          tone={factCheckPass >= 0.8 ? "ok" : factCheckPass >= 0.6 ? "warn" : "bad"}
        />
      )}
      {done && metaConfidence !== null && (
        <Stat
          label="Meta-eval"
          value={metaConfidence.toFixed(2)}
          tone={metaConfidence >= 0.8 ? "ok" : metaConfidence >= 0.6 ? "warn" : "bad"}
        />
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  accent,
  tone,
}: {
  label: string;
  value: string;
  accent?: boolean;
  tone?: "ok" | "warn" | "bad";
}) {
  const valueColor =
    tone === "ok"
      ? "text-ok"
      : tone === "warn"
        ? "text-warn"
        : tone === "bad"
          ? "text-bad"
          : accent
            ? "text-mistral-orange"
            : "text-white";
  return (
    <div className="glass rounded-lg p-2.5 text-center">
      <div className="text-[10px] uppercase tracking-wider text-ink-muted">{label}</div>
      <div className={`text-lg font-bold tabular-nums ${valueColor}`}>{value}</div>
    </div>
  );
}

function formatDuration(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${rem.toString().padStart(2, "0")}s`;
}
