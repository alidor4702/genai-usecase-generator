/**
 * CompastralMark — the brand glyph.
 *
 * Compastral = Company × Mistral, pronounced like "compass". So the mark
 * is a compass rose drawn pixel-art-style on a 16×16 grid, in Mistral's
 * orange palette. Mirror's the pixel-art accent vibe Mistral uses on
 * their site (microphone / robot / etc.) — chunky, on-brand, unmistakably
 * theirs in feel without copying any specific image.
 *
 * The grid is hand-laid: every `#` cell is a body pixel (orange), every
 * `*` is the highlight (lighter orange). Crisp-edges rendering keeps
 * the pixel feel at every size.
 */

const GRID = 16;

const COMPASS = [
  "................",
  "................",
  ".......##.......",
  "......*##*......",
  "......####......",
  ".....######.....",
  "...##*####*##...",
  "..############..",
  "..############..",
  "...##*####*##...",
  ".....######.....",
  "......####......",
  "......*##*......",
  ".......##.......",
  "................",
  "................",
].join("\n");

// Inner cardinal markers (N E S W) — drawn as small ticks around the centre
// to make it more legibly a compass and less generically a star.
const TICKS = [
  // N
  [7, 1], [8, 1],
  // S
  [7, 14], [8, 14],
  // E
  [14, 7], [14, 8],
  // W
  [1, 7], [1, 8],
];

export default function CompastralMark({ size = 32 }: { size?: number }) {
  const rows = COMPASS.split("\n");
  const fills: { x: number; y: number; kind: "body" | "highlight" }[] = [];
  for (let y = 0; y < rows.length; y++) {
    for (let x = 0; x < rows[y].length; x++) {
      const ch = rows[y][x];
      if (ch === "#") fills.push({ x, y, kind: "body" });
      else if (ch === "*") fills.push({ x, y, kind: "highlight" });
    }
  }
  return (
    <span
      className="inline-flex items-center justify-center"
      style={{
        width: size,
        height: size,
        background: "linear-gradient(135deg, #fa552e 0%, #fdba8c 100%)",
        boxShadow: "0 2px 8px rgba(250, 85, 46, 0.40)",
        borderRadius: 4,
      }}
      aria-hidden
    >
      <svg
        width={size - 6}
        height={size - 6}
        viewBox={`0 0 ${GRID} ${GRID}`}
        shapeRendering="crispEdges"
        preserveAspectRatio="xMidYMid meet"
      >
        {/* compass body */}
        {fills.map((f) => (
          <rect
            key={`b-${f.x}-${f.y}`}
            x={f.x}
            y={f.y}
            width={1}
            height={1}
            fill={f.kind === "body" ? "#fff" : "#fff7ed"}
          />
        ))}
        {/* cardinal ticks */}
        {TICKS.map(([x, y], i) => (
          <rect key={`t-${i}`} x={x} y={y} width={1} height={1} fill="#ffffff" opacity="0.8" />
        ))}
        {/* needle accent at centre — single brighter pixel */}
        <rect x={7} y={7} width={2} height={2} fill="#fff" />
      </svg>
    </span>
  );
}
