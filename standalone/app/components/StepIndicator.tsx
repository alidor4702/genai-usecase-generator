"use client";
import PhaseIcon from "./PhaseIcon";
import { PHASES, STEP_TO_PHASE, type PhaseKey } from "./stepMeta";

/**
 * Horizontal 7-phase pipeline timeline.
 *
 * The current phase glows orange and gets the active subtitle. Phases
 * before it are filled (done). Phases after are dimmed. Each phase shows
 * its monochrome glyph + uppercase label. The connector bar between
 * phases visualises completion (orange filled, slate empty).
 */
export default function StepIndicator({ currentStep }: { currentStep: string }) {
  const currentPhase: PhaseKey = STEP_TO_PHASE[currentStep] ?? "research";
  const currentIdx = PHASES.findIndex((p) => p.key === currentPhase);

  return (
    <div className="glass rounded-xl p-4 sm:p-5">
      <div className="relative flex items-start justify-between gap-1">
        {/* Connector rail */}
        <div className="absolute top-[18px] left-[18px] right-[18px] h-[2px] bg-mistral-border z-0" />
        <div
          className="absolute top-[18px] left-[18px] h-[2px] bg-gradient-to-r from-mistral-orange to-mistral-orangeBright z-0 transition-all duration-700"
          style={{
            width: currentIdx > 0
              ? `calc((100% - 36px) * ${currentIdx / (PHASES.length - 1)})`
              : "0%",
          }}
        />
        {PHASES.map((phase, i) => {
          const done = i < currentIdx;
          const active = i === currentIdx;
          return (
            <div key={phase.key} className="relative z-10 flex flex-col items-center min-w-0">
              <div
                className={`w-9 h-9 rounded-md flex items-center justify-center transition-all duration-500 ${
                  active
                    ? "bg-mistral-orange text-white ring-4 ring-mistral-orange/30 scale-110 shadow-lg shadow-mistral-orange/40"
                    : done
                      ? "bg-mistral-orange/80 text-white"
                      : "bg-mistral-surface text-ink-muted border border-mistral-border"
                }`}
                title={phase.sub}
              >
                {done ? (
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor"
                    strokeWidth="2" strokeLinecap="square" shapeRendering="crispEdges" aria-hidden>
                    <polyline points="3,7 6,10 11,4" />
                  </svg>
                ) : (
                  <PhaseIcon step={phase.key} />
                )}
              </div>
              <div
                className={`mt-2 text-[10px] sm:text-[11px] font-bold tracking-[0.12em] uppercase whitespace-nowrap ${
                  active ? "text-white" : done ? "text-ink-secondary" : "text-ink-muted"
                }`}
              >
                {phase.label}
              </div>
            </div>
          );
        })}
      </div>
      {currentIdx >= 0 && (
        <div className="mt-4 text-center text-sm">
          <span className="text-ink-secondary">Now: </span>
          <span className="text-white font-semibold">{PHASES[currentIdx].label}</span>
          <span className="text-ink-secondary"> — {PHASES[currentIdx].sub}</span>
        </div>
      )}
    </div>
  );
}
