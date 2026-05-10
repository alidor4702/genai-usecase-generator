"use client";

/**
 * CompassDial — interactive 8-bit pixel-art compass.
 *
 * Behaviour:
 *   - Idle:  the needle rotates slowly via requestAnimationFrame.
 *   - Drag:  pointer down on the SVG drives the needle directly to the
 *            angle from the centre to the pointer.
 *   - Snap:  N / E / S / W buttons spring the needle to that cardinal.
 *   - Spin:  random 3-7 full turns + a random extra angle.
 *   - Reset: snaps back to 0 (north).
 *
 * After any explicit interaction the needle resumes its idle rotation
 * **from the angle it was left at** — there's no "snap back" to where
 * the auto-rotation would have been.
 *
 * Visuals match the v2 8-bit compass the user designed: 21×21 cell
 * grid, 20px per cell (420×420 SVG), Compastral flame palette. Dial
 * is one static SVG `<g>`; needle is a separate rotating `<g>`.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const PIX = 20;
const GRID = 21;
const CENTER = 10;
const VIEWBOX = PIX * GRID;

const C = {
  cardinal:    "#FFCE00",
  cardinalHi:  "#FFE066",
  nFin:        "#FF4F00",
  ordinal:     "#FF8205",
  ring:        "#B8420A",
  tick:        "#5C2008",
  tickHi:      "#B8420A",
  pivotIn:     "#FFF5E1",
  pivotOut:    "#FFCE00",
  needleN:     "#E63100",
  needleNHi:   "#FF6B1A",
  needleNTip:  "#FF8205",
  needleS:     "#FFE8C2",
  needleSHi:   "#FFCE00",
} as const;

type Pixel = { x: number; y: number; color: string };

const DIRS_16 = [
  "N", "NNE", "NE", "ENE",
  "E", "ESE", "SE", "SSE",
  "S", "SSW", "SW", "WSW",
  "W", "WNW", "NW", "NNW",
] as const;

function buildDialPixels(): Pixel[] {
  // Mirrors the dial-generation logic from the v2 HTML — radial
  // distance buckets pick out the outer ring, cardinal arms, and
  // tick marks. Computing this once at module-load keeps the React
  // render fast.
  const out: Pixel[] = [];
  for (let y = 0; y < GRID; y++) {
    for (let x = 0; x < GRID; x++) {
      const dx = x - CENTER;
      const dy = y - CENTER;
      const d2 = dx * dx + dy * dy;
      const ax = Math.abs(dx);
      const ay = Math.abs(dy);
      const onAxis = dx === 0 || dy === 0;
      const onDiag = ax === ay;

      if (d2 >= 65 && d2 <= 85) {
        let color: string = C.ring;
        if (onDiag && d2 >= 72) color = C.ordinal;
        if (onAxis) color = C.cardinal;
        else if ((ax === 1 && ay >= 8) || (ay === 1 && ax >= 8)) color = C.nFin;
        out.push({ x, y, color });
      }
      if (d2 === 100 && onAxis) out.push({ x, y, color: C.cardinal });
      if (y === 0 && (x === 9 || x === 11)) out.push({ x, y, color: C.cardinalHi });
      if (onAxis && d2 === 49) out.push({ x, y, color: C.tickHi });
      if (onDiag && d2 === 50) out.push({ x, y, color: C.tick });
      if (onAxis && d2 === 25) out.push({ x, y, color: C.tick });
    }
  }
  out.push({ x: 10, y: 0, color: C.nFin });
  return out;
}

const NEEDLE_PIXELS: Pixel[] = (() => {
  const N = C.needleN;
  const NH = C.needleNHi;
  const NT = C.needleNTip;
  const S = C.needleS;
  const SH = C.needleSHi;
  const P0 = C.pivotIn;
  const P1 = C.pivotOut;
  const data: [number, number, string][] = [
    [10, 2, NT],
    [10, 3, N],
    [9, 4, N], [10, 4, NH], [11, 4, N],
    [9, 5, N], [10, 5, NH], [11, 5, N],
    [9, 6, N], [10, 6, NH], [11, 6, N],
    [8, 7, N], [9, 7, N], [10, 7, NH], [11, 7, N], [12, 7, N],
    [9, 8, N], [10, 8, NH], [11, 8, N],
    [10, 9, P1],
    [9, 10, P1], [10, 10, P0], [11, 10, P1],
    [10, 11, SH],
    [9, 12, S], [10, 12, SH], [11, 12, S],
    [8, 13, S], [9, 13, S], [10, 13, SH], [11, 13, S], [12, 13, S],
    [9, 14, S], [10, 14, SH], [11, 14, S],
    [9, 15, S], [10, 15, SH], [11, 15, S],
    [10, 16, S],
    [10, 17, S],
    [10, 18, S],
  ];
  return data.map(([x, y, color]) => ({ x, y, color }));
})();

type Mode = "idle" | "drag" | "snap" | "spin";

export default function CompassDial({
  size = 320,
  showControls = true,
  showReadout = true,
}: {
  /** Rendered width in CSS pixels. The SVG viewBox is fixed at 420×420. */
  size?: number;
  showControls?: boolean;
  showReadout?: boolean;
}) {
  const dialPixels = useMemo(() => buildDialPixels(), []);

  const [angle, setAngle] = useState(0);
  const [mode, setMode] = useState<Mode>("idle");
  // Mirrors `angle` so rAF + pointer handlers can read the latest value
  // without going through stale React state in their closures.
  const angleRef = useRef(0);
  const draggingRef = useRef(false);
  const rafRef = useRef<number | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  // Timer that demotes mode back to "idle" after a snap/spin animation
  // finishes. Stored in a ref so a follow-up interaction can clear the
  // pending demotion before it fires.
  const idleTimerRef = useRef<number | null>(null);

  const writeAngle = useCallback((deg: number) => {
    angleRef.current = deg;
    setAngle(deg);
  }, []);

  const cancelIdleTimer = useCallback(() => {
    if (idleTimerRef.current != null) {
      window.clearTimeout(idleTimerRef.current);
      idleTimerRef.current = null;
    }
  }, []);

  // Idle auto-rotation. Speed: ~12°/sec (gentle, doesn't visually compete
  // with the typewriter wordmark below it). Resumes from the current
  // angle every time we drop back into "idle" mode.
  useEffect(() => {
    if (mode !== "idle") return;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = now - last;
      last = now;
      writeAngle(angleRef.current + dt * 0.012);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [mode, writeAngle]);

  const angleFromPointer = useCallback((clientX: number, clientY: number): number => {
    const svg = svgRef.current;
    if (!svg) return angleRef.current;
    const rect = svg.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const dx = clientX - cx;
    const dy = clientY - cy;
    return ((Math.atan2(dx, -dy) * 180) / Math.PI + 360) % 360;
  }, []);

  const onPointerDown = useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => {
      cancelIdleTimer();
      draggingRef.current = true;
      e.currentTarget.setPointerCapture(e.pointerId);
      setMode("drag");
      writeAngle(angleFromPointer(e.clientX, e.clientY));
    },
    [angleFromPointer, cancelIdleTimer, writeAngle],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent<SVGSVGElement>) => {
      if (!draggingRef.current) return;
      writeAngle(angleFromPointer(e.clientX, e.clientY));
    },
    [angleFromPointer, writeAngle],
  );

  const endDrag = useCallback(() => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    // Resume idle rotation immediately from the angle the user
    // released at — no spring-back, no "snap to nearest cardinal".
    setMode("idle");
  }, []);

  const snapTo = useCallback(
    (deg: number) => {
      cancelIdleTimer();
      setMode("snap");
      writeAngle(deg);
      // After the spring transition (0.45s) settles, resume idle.
      idleTimerRef.current = window.setTimeout(() => setMode("idle"), 500);
    },
    [cancelIdleTimer, writeAngle],
  );

  const spin = useCallback(() => {
    cancelIdleTimer();
    setMode("spin");
    const turns = 3 + Math.floor(Math.random() * 4);
    const extra = Math.floor(Math.random() * 360);
    writeAngle(angleRef.current + turns * 360 + extra);
    // After the long spin transition (2.4s), drop back to idle so the
    // continuous rotation resumes from wherever the spin landed.
    idleTimerRef.current = window.setTimeout(() => setMode("idle"), 2500);
  }, [cancelIdleTimer, writeAngle]);

  // Cleanup any pending idle timer on unmount.
  useEffect(() => () => cancelIdleTimer(), [cancelIdleTimer]);

  const headingDeg = Math.round((((angle % 360) + 360) % 360));
  const cardinal = DIRS_16[Math.round(headingDeg / 22.5) % 16];

  // Per-mode CSS transition for the needle's rotation. drag = none
  // (instant follow); snap = short spring; spin = long ease; idle =
  // none (manual rAF write each frame).
  const needleTransition =
    mode === "snap"
      ? "transform 0.45s cubic-bezier(0.34, 1.4, 0.5, 1)"
      : mode === "spin"
      ? "transform 2.4s cubic-bezier(0.18, 0.7, 0.2, 1)"
      : "none";

  return (
    <div className="flex flex-col items-center gap-4 select-none">
      <div style={{ width: size, maxWidth: "100%" }}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${VIEWBOX} ${VIEWBOX}`}
          width="100%"
          height="auto"
          shapeRendering="crispEdges"
          role="img"
          aria-label="Pixel art compass — drag to rotate, click N/E/S/W to snap, idle rotation when untouched"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
          onPointerLeave={endDrag}
          style={{
            cursor: mode === "drag" ? "grabbing" : "grab",
            display: "block",
            touchAction: "none",
            userSelect: "none",
          }}
        >
          <g>
            {dialPixels.map((p, i) => (
              <rect
                key={`d-${i}`}
                x={p.x * PIX}
                y={p.y * PIX}
                width={PIX}
                height={PIX}
                fill={p.color}
              />
            ))}
          </g>
          <g
            style={{
              transformBox: "view-box",
              transformOrigin: "50% 50%",
              transform: `rotate(${angle}deg)`,
              transition: needleTransition,
              willChange: "transform",
            }}
          >
            {NEEDLE_PIXELS.map((p, i) => (
              <rect
                key={`n-${i}`}
                x={p.x * PIX}
                y={p.y * PIX}
                width={PIX}
                height={PIX}
                fill={p.color}
              />
            ))}
          </g>
        </svg>
      </div>

      {showReadout && (
        <div className="flex gap-5 items-baseline font-mono text-sm text-ink-secondary tracking-wider">
          <span>
            Heading{" "}
            <b className="text-mistral-orangeBright font-medium text-base inline-block min-w-[3.5ch]">
              {headingDeg}°
            </b>
          </span>
          <span className="text-[#FFCE00] font-medium text-base tracking-[0.1em]">
            {cardinal}
          </span>
        </div>
      )}

      {showControls && (
        <div className="flex gap-1.5 flex-wrap justify-center max-w-[420px]">
          {[
            { label: "N", deg: 0 },
            { label: "E", deg: 90 },
            { label: "S", deg: 180 },
            { label: "W", deg: 270 },
          ].map((b) => (
            <button
              key={b.label}
              type="button"
              onClick={() => snapTo(b.deg)}
              className="px-3.5 py-1.5 text-xs font-mono tracking-[0.08em] rounded-md border border-mistral-orange/35 bg-mistral-orange/[0.07] text-mistral-orangeBright hover:bg-mistral-orange/15 hover:border-mistral-orange/60 transition-colors min-w-[42px]"
            >
              {b.label}
            </button>
          ))}
          <button
            type="button"
            onClick={spin}
            className="px-3.5 py-1.5 text-xs rounded-md border border-mistral-border text-ink-secondary hover:text-white hover:border-mistral-orange/60 transition-colors"
          >
            Spin
          </button>
          <button
            type="button"
            onClick={() => snapTo(0)}
            className="px-3.5 py-1.5 text-xs rounded-md border border-mistral-border text-ink-secondary hover:text-white hover:border-mistral-orange/60 transition-colors"
          >
            Reset
          </button>
        </div>
      )}
    </div>
  );
}
