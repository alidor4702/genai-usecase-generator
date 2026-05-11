"use client";

/**
 * CompanyGlyph — only fires for Mistral AI. Renders the Mistral pixel-M
 * mark as a small badge anchored to the top-right of the input panel.
 * Pure opacity fade — no movement once it lands. Other companies don't
 * get a glyph: brand-marking is a soft Easter-egg for the home brand.
 */

import { useMemo } from "react";

function MistralM({ size }: { size: number }) {
  // 10×5 pixel grid — two tall pillars + a wide bottom bar with two
  // dark notches that read as the M's valley/feet.
  const pixels: Array<[number, number]> = [
    [1, 0], [2, 0], [7, 0], [8, 0],
    [1, 1], [2, 1], [7, 1], [8, 1],
    [1, 2], [2, 2], [7, 2], [8, 2],
    [1, 3], [2, 3], [7, 3], [8, 3],
    [0, 4], [1, 4], [2, 4], [4, 4], [5, 4], [7, 4], [8, 4], [9, 4],
  ];
  return (
    <span
      className="inline-flex items-center justify-center bg-[#1a1a1a] shadow-lg ring-1 ring-white/5"
      style={{ width: size, height: size, borderRadius: 8 }}
      aria-hidden
    >
      <svg
        width={size * 0.66}
        height={size * 0.4}
        viewBox="0 0 10 5"
        shapeRendering="crispEdges"
        preserveAspectRatio="xMidYMid meet"
      >
        {pixels.map(([x, y]) => (
          <rect key={`${x}-${y}`} x={x} y={y} width={1} height={1} fill="#fafafa" />
        ))}
      </svg>
    </span>
  );
}

const MISTRAL_RX = /^\s*mistral(\s*ai)?\s*$/i;

/**
 * Anchored to the top-right of the input panel via the parent's
 * `relative` wrapper. Pure opacity fade-in (no translate).
 */
export default function CompanyGlyph({ name, size = 56 }: { name: string; size?: number }) {
  const matches = useMemo(() => MISTRAL_RX.test(name), [name]);
  if (!matches) return null;
  return (
    <div
      className="pointer-events-none absolute right-6 top-6 z-10 animate-[fadeIn_400ms_ease-out_both]"
      title="Mistral AI"
    >
      <MistralM size={size} />
    </div>
  );
}
