"use client";

/**
 * CompanyGlyph — only fires for Mistral AI. Renders the cat-ear M
 * pixel-art mark above the input panel as a "watermark crest" so the
 * panel reads like the brand surface itself when the recognised name
 * is typed. Other companies don't get a glyph: the v9.x guidance is
 * that brand-marking should be a soft Easter-egg for the home brand,
 * not a per-customer logo lookup.
 */

import { useMemo } from "react";

function MistralCatM({ size }: { size: number }) {
  return (
    <span
      className="inline-flex items-center justify-center bg-gradient-to-br from-mistral-orange to-mistral-orangeBright shadow-xl shadow-mistral-orange/40 ring-2 ring-mistral-orange/20"
      style={{ width: size, height: size, borderRadius: 10 }}
      aria-hidden
    >
      <svg
        width={size - 12}
        height={size - 12}
        viewBox="0 0 16 16"
        shapeRendering="crispEdges"
        preserveAspectRatio="xMidYMid meet"
      >
        {[
          // ears
          [2, 1], [3, 1], [12, 1], [13, 1],
          [2, 2], [3, 2], [12, 2], [13, 2],
          // top bars (the M)
          [1, 4], [2, 4], [3, 4], [4, 4], [5, 4],
          [10, 4], [11, 4], [12, 4], [13, 4], [14, 4],
          [1, 5], [2, 5], [13, 5], [14, 5],
          // arches sloping inward
          [1, 6], [2, 6], [4, 6], [5, 6], [10, 6], [11, 6], [13, 6], [14, 6],
          [1, 7], [2, 7], [4, 7], [5, 7], [10, 7], [11, 7], [13, 7], [14, 7],
          [1, 8], [2, 8], [4, 8], [5, 8], [7, 8], [8, 8], [10, 8], [11, 8], [13, 8], [14, 8],
          [1, 9], [2, 9], [4, 9], [5, 9], [7, 9], [8, 9], [10, 9], [11, 9], [13, 9], [14, 9],
          [1, 10], [2, 10], [4, 10], [5, 10], [7, 10], [8, 10], [10, 10], [11, 10], [13, 10], [14, 10],
          // base feet
          [1, 12], [2, 12], [4, 12], [5, 12], [7, 12], [8, 12], [10, 12], [11, 12], [13, 12], [14, 12],
          [1, 13], [2, 13], [4, 13], [5, 13], [7, 13], [8, 13], [10, 13], [11, 13], [13, 13], [14, 13],
        ].map(([x, y]) => (
          <rect key={`${x}-${y}`} x={x} y={y} width={1} height={1} fill="white" />
        ))}
      </svg>
    </span>
  );
}

const MISTRAL_RX = /^\s*mistral(\s*ai)?\s*$/i;

/**
 * Renders the Mistral cat-M crest centered ABOVE the input panel.
 * Use as a sibling that sits half-overlapping the panel via negative
 * margin: the parent should style it like a brand mark "emerging from"
 * the panel.
 *
 * Layout pattern in the parent:
 *   <div className="relative">
 *     <CompanyGlyph name={companyName} />   {/* absolutely positioned to top *\/}
 *     <div className="panel">…</div>
 *   </div>
 */
export default function CompanyGlyph({ name, size = 64 }: { name: string; size?: number }) {
  const matches = useMemo(() => MISTRAL_RX.test(name), [name]);
  if (!matches) return null;
  return (
    <div
      className="pointer-events-none absolute -top-8 left-1/2 -translate-x-1/2 z-10 transition-all duration-300 animate-[slideIn_0.3s_cubic-bezier(0.16,1,0.3,1)]"
      title="Mistral AI — home turf"
    >
      <MistralCatM size={size} />
    </div>
  );
}
