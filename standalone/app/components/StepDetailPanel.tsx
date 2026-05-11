"use client";

/**
 * StepDetailPanel — renders the detail card for a single pipeline step.
 *
 * Shown on the right column of the /architecture page when the user
 * clicks a step in `PipelineDiagram`. Replaces the default static
 * determinism contract for the duration of the selection.
 *
 * Data comes from `STEP_DETAILS` in `PipelineDiagram.tsx`.
 */

import type { ReactElement } from "react";
import type { StepDetail } from "./PipelineDiagram";

export default function StepDetailPanel({
  detail,
  onClose,
}: {
  detail: StepDetail;
  onClose: () => void;
}): ReactElement {
  return (
    <div className="text-sm text-slate-300 leading-relaxed">
      <div className="flex items-start justify-between gap-3 mb-4">
        <h3 className="text-base font-bold text-white leading-tight">
          {detail.fullName}
        </h3>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close step detail"
          className="text-xs text-ink-secondary hover:text-white px-2 py-0.5 rounded border border-mistral-border hover:border-mistral-orangeBright transition-colors shrink-0"
        >
          close ×
        </button>
      </div>

      {(detail.model || detail.temperature || detail.timeout) && (
        <div className="flex flex-wrap gap-1.5 mb-4">
          {detail.model && (
            <Chip label="MODEL" value={detail.model} variant="orange" />
          )}
          {detail.temperature && (
            <Chip label="TEMP" value={detail.temperature} variant="blue" />
          )}
          {detail.timeout && (
            <Chip label="TIMEOUT" value={detail.timeout} variant="purple" />
          )}
        </div>
      )}

      <DetailGroup title="Reads">
        <ul className="list-disc pl-4 space-y-1">
          {detail.reads.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      </DetailGroup>

      <DetailGroup title="Writes">
        <ul className="list-disc pl-4 space-y-1">
          {detail.writes.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      </DetailGroup>

      <DetailGroup title="Why this is an activity">
        <p>{detail.whyActivity}</p>
      </DetailGroup>

      {detail.notes && detail.notes.length > 0 && (
        <DetailGroup title="Notes">
          <ul className="list-disc pl-4 space-y-1">
            {detail.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </DetailGroup>
      )}
    </div>
  );
}

function DetailGroup({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}): ReactElement {
  return (
    <div className="mb-4 last:mb-0">
      <div className="text-[10px] uppercase tracking-[0.15em] text-mistral-orangeBright font-bold mb-1.5">
        {title}
      </div>
      <div className="text-sm text-slate-300">{children}</div>
    </div>
  );
}

function Chip({
  label,
  value,
  variant,
}: {
  label: string;
  value: string;
  variant: "orange" | "blue" | "purple";
}): ReactElement {
  const styles: Record<typeof variant, string> = {
    orange:
      "bg-mistral-orange/15 text-mistral-orangeBright border-mistral-orange/40",
    blue: "bg-blue-500/15 text-blue-300 border-blue-500/40",
    purple: "bg-purple-500/15 text-purple-300 border-purple-500/40",
  };
  return (
    <span
      className={`inline-flex items-baseline gap-1 px-2 py-0.5 text-[11px] rounded border ${styles[variant]} font-mono`}
    >
      <span className="opacity-70 text-[9px] tracking-wider">{label}</span>
      <span className="font-bold">{value}</span>
    </span>
  );
}
