"use client";
import { PHASES, STEP_TO_PHASE, type PhaseKey } from "./stepMeta";

/**
 * Horizontal 7-phase pipeline timeline.
 *
 * The current phase glows orange and gets the active subtitle. Phases
 * before it are filled (done). Phases after are dimmed. Each phase shows
 * its emoji + label.
 */
export default function StepIndicator({ currentStep }: { currentStep: string }) {
  const currentPhase: PhaseKey = STEP_TO_PHASE[currentStep] ?? "research";
  const currentIdx = PHASES.findIndex((p) => p.key === currentPhase);

  return (
    <div className="glass rounded-xl p-4">
      <div className="flex items-center justify-between gap-1">
        {PHASES.map((phase, i) => {
          const done = i < currentIdx;
          const active = i === currentIdx;
          return (
            <div key={phase.key} className="flex-1 flex flex-col items-center">
              <div
                className={`w-9 h-9 rounded-full flex items-center justify-center text-base transition-all duration-500 ${
                  active
                    ? "bg-mistral-orange text-white ring-4 ring-mistral-orange/30 scale-110"
                    : done
                      ? "bg-mistral-orange/40 text-mistral-orangeBright"
                      : "bg-mistral-surface text-ink-muted border border-mistral-border"
                }`}
                title={phase.sub}
              >
                {done ? "✓" : phase.emoji}
              </div>
              <div
                className={`mt-1.5 text-[11px] font-medium tracking-wide uppercase ${
                  active ? "text-white" : done ? "text-ink-secondary" : "text-ink-muted"
                }`}
              >
                {phase.label}
              </div>
              {/* connector line, hidden after the last */}
              {i < PHASES.length - 1 && (
                <div className="absolute" aria-hidden />
              )}
            </div>
          );
        })}
      </div>
      {currentIdx >= 0 && (
        <div className="mt-3 text-center text-sm">
          <span className="text-ink-secondary">Now: </span>
          <span className="text-white font-semibold">{PHASES[currentIdx].label}</span>
          <span className="text-ink-secondary"> — {PHASES[currentIdx].sub}</span>
        </div>
      )}
    </div>
  );
}
