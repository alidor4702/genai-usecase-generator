/**
 * PhaseIcon — pixel-art-inspired monochrome SVG icons for each pipeline
 * phase. Replaces the emoji icons used through v6 with crisp, on-brand
 * glyphs that read well at every size.
 *
 * Style: 16×16 grid, square stroke ends, chunky 1.5px stroke (or filled
 * blocks for the most pixel-ish ones), monochrome so they tint cleanly
 * via `currentColor` to whatever Mistral hue the surrounding card is
 * using. No fills below stroke width 1; no gradients; no rounded corners
 * inside the icon — only the outer wrapper rounds.
 */

import type { PhaseKey } from "./stepMeta";
type IconKey = PhaseKey | "web_verify" | "source_judge" | "final_qualify" | "polish" | "regen_one" | "attribution_check" | "gap_fill" | "generate.web_search" | "quality_signals";

const SIZE = 16;
const STROKE = 1.5;

// Common props for SVG so every icon shares the same look.
const svgProps = {
  width: SIZE,
  height: SIZE,
  viewBox: `0 0 ${SIZE} ${SIZE}`,
  fill: "none",
  stroke: "currentColor",
  strokeWidth: STROKE,
  strokeLinecap: "square" as const,
  strokeLinejoin: "miter" as const,
  shapeRendering: "crispEdges" as const,
};

// Inline glyphs — kept tight to the 16x16 grid so anti-aliasing stays
// minimal and the icons feel pixel-ish without being literally rasterized.

function ResearchIcon() {
  // magnifying lens
  return (
    <svg {...svgProps} aria-label="research">
      <circle cx="6.5" cy="6.5" r="4" />
      <line x1="9.5" y1="9.5" x2="14" y2="14" />
    </svg>
  );
}
function RetrieveIcon() {
  // stacked storage
  return (
    <svg {...svgProps} aria-label="retrieve">
      <rect x="2" y="3" width="12" height="3" />
      <rect x="2" y="7" width="12" height="3" />
      <rect x="2" y="11" width="12" height="3" />
    </svg>
  );
}
function GenerateIcon() {
  // spark / asterisk burst
  return (
    <svg {...svgProps} aria-label="generate">
      <line x1="8" y1="2" x2="8" y2="14" />
      <line x1="2" y1="8" x2="14" y2="8" />
      <line x1="3.5" y1="3.5" x2="12.5" y2="12.5" />
      <line x1="3.5" y1="12.5" x2="12.5" y2="3.5" />
    </svg>
  );
}
function ScoreIcon() {
  // balance scale
  return (
    <svg {...svgProps} aria-label="score">
      <line x1="8" y1="2" x2="8" y2="14" />
      <line x1="2" y1="5" x2="14" y2="5" />
      <rect x="1" y="9" width="4" height="3" />
      <rect x="11" y="9" width="4" height="3" />
    </svg>
  );
}
function VerifyIcon() {
  // shield with check
  return (
    <svg {...svgProps} aria-label="verify">
      <path d="M8 1 L14 4 L14 9 Q14 13 8 15 Q2 13 2 9 L2 4 Z" />
      <polyline points="5,8 7,10 11,6" />
    </svg>
  );
}
function EnrichIcon() {
  // pen / writing nib
  return (
    <svg {...svgProps} aria-label="enrich">
      <line x1="2" y1="14" x2="14" y2="2" />
      <polyline points="11,2 14,2 14,5" />
      <line x1="3" y1="13" x2="6" y2="13" />
      <line x1="3" y1="13" x2="3" y2="10" />
    </svg>
  );
}
function ReviewIcon() {
  // checklist
  return (
    <svg {...svgProps} aria-label="review">
      <rect x="2" y="2" width="12" height="12" />
      <polyline points="5,7 7,9 11,5" />
      <line x1="5" y1="11" x2="11" y2="11" />
    </svg>
  );
}
function WebVerifyIcon() {
  // globe with reticle
  return (
    <svg {...svgProps} aria-label="web verify">
      <circle cx="8" cy="8" r="6" />
      <line x1="2" y1="8" x2="14" y2="8" />
      <line x1="8" y1="2" x2="8" y2="14" />
      <ellipse cx="8" cy="8" rx="3" ry="6" />
    </svg>
  );
}
function JudgeIcon() {
  // gavel
  return (
    <svg {...svgProps} aria-label="judge">
      <rect x="6" y="2" width="6" height="3" transform="rotate(45 9 3.5)" />
      <line x1="3" y1="11" x2="11" y2="3" />
      <rect x="2" y="13" width="12" height="2" />
    </svg>
  );
}
function PolishIcon() {
  // sparkle
  return (
    <svg {...svgProps} aria-label="polish">
      <polygon points="5,1 6,4 9,5 6,6 5,9 4,6 1,5 4,4" />
      <polygon points="11,8 12,10 14,11 12,12 11,14 10,12 8,11 10,10" />
    </svg>
  );
}
function RegenIcon() {
  // cycle arrows
  return (
    <svg {...svgProps} aria-label="regenerate">
      <path d="M3 8 Q3 3 8 3 L11 3" />
      <polyline points="9,1 11,3 9,5" />
      <path d="M13 8 Q13 13 8 13 L5 13" />
      <polyline points="7,15 5,13 7,11" />
    </svg>
  );
}
function GapFillIcon() {
  // dashed bracket / fill
  return (
    <svg {...svgProps} aria-label="gap fill">
      <line x1="2" y1="3" x2="14" y2="3" strokeDasharray="2,1" />
      <line x1="2" y1="13" x2="14" y2="13" strokeDasharray="2,1" />
      <line x1="6" y1="6" x2="10" y2="6" />
      <line x1="6" y1="10" x2="10" y2="10" />
      <line x1="8" y1="6" x2="8" y2="10" />
    </svg>
  );
}
function SignalsIcon() {
  // chart bars
  return (
    <svg {...svgProps} aria-label="signals">
      <rect x="2" y="9" width="3" height="5" />
      <rect x="6.5" y="6" width="3" height="8" />
      <rect x="11" y="3" width="3" height="11" />
    </svg>
  );
}
function DefaultIcon() {
  return (
    <svg {...svgProps} aria-label="step">
      <rect x="3" y="3" width="10" height="10" />
    </svg>
  );
}

const ICON_MAP: Record<string, () => JSX.Element> = {
  research: ResearchIcon,
  gap_fill: GapFillIcon,
  retrieve: RetrieveIcon,
  generate: GenerateIcon,
  "generate.web_search": WebVerifyIcon,
  score: ScoreIcon,
  verify: VerifyIcon,
  enrich: EnrichIcon,
  polish: PolishIcon,
  attribution_check: PolishIcon,
  regen_one: RegenIcon,
  meta_eval: ReviewIcon,
  web_verify: WebVerifyIcon,
  source_judge: JudgeIcon,
  final_qualify: PolishIcon,
  quality_signals: SignalsIcon,
  review: ReviewIcon,
};

export default function PhaseIcon({ step, className }: { step: string; className?: string }) {
  const Cmp = ICON_MAP[step] ?? DefaultIcon;
  return (
    <span className={className ?? "inline-flex"} aria-hidden>
      <Cmp />
    </span>
  );
}

export type { IconKey };
