"use client";

/**
 * CompanyGlyph — show a brand mark when the user types a recognised
 * company name in the Pick-a-company input. Currently covers Mistral AI
 * (the cat-ear M); the dispatch table is trivial to extend.
 *
 * The Mistral mark is laid out on a 16×16 pixel grid with the small
 * "ears" on top so it reads as cat-shaped — same brand vibe as the
 * site mascot pixel art.
 */

import { useMemo } from "react";

type Glyph = (size: number) => React.ReactElement | null;

function MistralCatM({ size }: { size: number }) {
  return (
    <span
      className="inline-flex items-center justify-center bg-gradient-to-br from-mistral-orange to-mistral-orangeBright shadow-lg shadow-mistral-orange/30"
      style={{ width: size, height: size, borderRadius: 6 }}
      aria-hidden
    >
      <svg
        width={size - 8}
        height={size - 8}
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

function carrefourLogo({ size }: { size: number }) {
  // Two opposing chevrons in red + blue — Carrefour's iconic mark, simplified.
  return (
    <span
      className="inline-flex items-center justify-center bg-white shadow-lg"
      style={{ width: size, height: size, borderRadius: 6 }}
      aria-hidden
    >
      <svg width={size - 8} height={size - 8} viewBox="0 0 16 16" shapeRendering="geometricPrecision">
        <polygon points="1,2 8,8 1,14" fill="#0042a5" />
        <polygon points="15,2 8,8 15,14" fill="#e30613" />
      </svg>
    </span>
  );
}

function loralL({ size }: { size: number }) {
  return (
    <span
      className="inline-flex items-center justify-center bg-black shadow-lg"
      style={{ width: size, height: size, borderRadius: 6 }}
      aria-hidden
    >
      <span className="text-white font-bold tracking-wider text-[11px]">L'OREAL</span>
    </span>
  );
}

function veoliaV({ size }: { size: number }) {
  return (
    <span
      className="inline-flex items-center justify-center bg-[#fa3c3c] shadow-lg"
      style={{ width: size, height: size, borderRadius: 6 }}
      aria-hidden
    >
      <span className="text-white font-bold text-lg">V</span>
    </span>
  );
}

function bnpStar({ size }: { size: number }) {
  return (
    <span
      className="inline-flex items-center justify-center bg-[#009639] shadow-lg"
      style={{ width: size, height: size, borderRadius: 6 }}
      aria-hidden
    >
      <span className="text-white font-bold text-[11px]">BNP</span>
    </span>
  );
}

const GLYPHS: { match: RegExp; render: ({ size }: { size: number }) => React.ReactElement }[] = [
  { match: /^\s*mistral(\s*ai)?\s*$/i, render: MistralCatM },
  { match: /^\s*carrefour\b/i, render: carrefourLogo },
  { match: /^\s*l['']?\s*or[ée]al\b/i, render: loralL },
  { match: /^\s*veolia\b/i, render: veoliaV },
  { match: /^\s*bnp(\s+paribas)?\b/i, render: bnpStar },
];

export default function CompanyGlyph({ name, size = 56 }: { name: string; size?: number }) {
  const Glyph = useMemo(() => {
    for (const g of GLYPHS) if (g.match.test(name)) return g.render;
    return null;
  }, [name]);
  if (!Glyph) return null;
  return (
    <div
      className="shrink-0 transition-all duration-300 animate-[slideIn_0.3s_cubic-bezier(0.16,1,0.3,1)]"
      title={`Detected: ${name.trim()}`}
    >
      <Glyph size={size} />
    </div>
  );
}
