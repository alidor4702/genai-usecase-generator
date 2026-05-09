"use client";
import { useState } from "react";
import type { TraceEvent } from "../lib/api";
import PhaseIcon from "./PhaseIcon";
import { STEP_DISPLAY, actorBadge } from "./stepMeta";

/**
 * One agent action. Shows the phase glyph + friendly title + actor +
 * one-line summary. Click the chevron to expand the raw inputs/outputs.
 */
export default function ActivityCard({ event, index }: { event: TraceEvent; index: number }) {
  const [open, setOpen] = useState(false);
  const meta = STEP_DISPLAY[event.step] ?? {
    title: event.action,
    verb: "",
    tone: "from-slate-500/20 to-slate-500/0",
  };

  // `running` is the explicit flag set by the SSE merge logic (step_start →
  // running, step_complete → !running). `duration_ms === null` is a fallback
  // for legacy events without an id that never receive a separate complete.
  const inFlight =
    event.running === true || (event.running === undefined && event.duration_ms === null);
  const ts = new Date(event.started_at).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  return (
    <div
      className={`slide-in relative overflow-hidden glass rounded-lg border-l-4 ${
        event.error
          ? "border-bad"
          : inFlight
            ? "border-mistral-orange"
            : "border-mistral-orange/40"
      } p-3 hover:border-mistral-orange transition-colors`}
      style={{ animationDelay: `${Math.min(index * 20, 200)}ms` }}
    >
      {inFlight && (
        <div
          className="absolute inset-0 pointer-events-none shimmer opacity-40"
          aria-hidden
        />
      )}
      <div className="relative flex items-start gap-3">
        <div
          className={`shrink-0 w-9 h-9 rounded-md bg-gradient-to-br ${meta.tone} flex items-center justify-center text-white`}
        >
          <PhaseIcon step={event.step} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="text-sm font-semibold text-white truncate">
              {meta.title}
            </span>
            <span className="text-[11px] uppercase tracking-wider text-ink-muted">
              {actorBadge(event.actor)}
            </span>
            <span className="text-[11px] text-ink-muted ml-auto font-mono">
              {ts}
              {event.duration_ms !== null && (
                <span className="ml-1.5 text-ink-secondary">
                  · {(event.duration_ms / 1000).toFixed(1)}s
                </span>
              )}
              {inFlight && (
                <span className="ml-1.5 inline-flex items-center gap-1 text-mistral-orangeBright">
                  <span className="w-1.5 h-1.5 rounded-full bg-mistral-orange animate-pulse" />
                  running
                </span>
              )}
            </span>
          </div>
          {event.outputs_summary && (
            <div className="text-xs text-ink-secondary mt-0.5 truncate">
              → {event.outputs_summary}
            </div>
          )}
          {event.error && (
            <div className="text-xs text-bad mt-0.5">! {event.error}</div>
          )}
          {(event.inputs_summary || event.outputs_summary) && (
            <button
              onClick={() => setOpen((o) => !o)}
              className="text-[11px] text-mistral-orangeBright hover:text-mistral-orange mt-1 font-mono"
            >
              {open ? "▾ collapse" : "▸ expand"}
            </button>
          )}
          {open && (
            <div className="mt-2 space-y-1 font-mono text-[11px] bg-mistral-dark/60 rounded p-2 border border-mistral-border">
              <div className="text-ink-secondary">{meta.verb}</div>
              {event.inputs_summary && (
                <div className="break-words">
                  <span className="text-ink-muted">in: </span>
                  <span className="text-ink-primary">{event.inputs_summary}</span>
                </div>
              )}
              {event.outputs_summary && (
                <div className="break-words">
                  <span className="text-ink-muted">out: </span>
                  <span className="text-ink-primary">{event.outputs_summary}</span>
                </div>
              )}
              <div className="text-ink-muted">
                step: <span className="text-mistral-orangeBright">{event.step}</span>
                {" · "}
                action: <span className="text-mistral-orangeBright">{event.action}</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
