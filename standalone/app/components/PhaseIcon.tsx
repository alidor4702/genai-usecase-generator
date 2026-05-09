/**
 * PhaseIcon — true 8-bit pixel-art icons for each pipeline phase.
 *
 * Style: every glyph is laid out on a strict 12×12 cell grid. Each cell
 * is a full square (no anti-aliasing, no fractional positions, no
 * curves). `shape-rendering: crispEdges` keeps the browser from
 * smoothing the edges, so they read as actual pixels at every size.
 *
 * Each icon is described as a string of 12 rows × 12 chars:
 *   `.` = empty cell
 *   `#` = filled cell (uses currentColor)
 *
 * Render path: split the string into rows, walk each row, emit a <rect>
 * per `#`. Every rect is exactly one cell. The result is a chunky NES-
 * era look that scales cleanly and tints to whatever color the parent
 * card uses.
 */

const GRID = 12;
const CELL = 1; // each cell is 1 SVG unit; viewBox is 12×12 so size scales via CSS.

const ICONS: Record<string, string> = {
  // Magnifying lens — research
  research: [
    "............",
    ".####.......",
    ".#..##......",
    "##...#......",
    "##...#......",
    ".#..##......",
    ".####.......",
    "....##......",
    ".....##.....",
    "......##....",
    ".......##...",
    "............",
  ].join("\n"),

  // Stacked storage — retrieve
  retrieve: [
    "............",
    "############",
    "#...#...#..#",
    "############",
    "............",
    "############",
    "#...#...#..#",
    "############",
    "............",
    "############",
    "#...#...#..#",
    "############",
  ].join("\n"),

  // Spark / asterisk — generate
  generate: [
    "............",
    ".....##.....",
    "..#..##..#..",
    "..##.##.##..",
    "...##.###...",
    "############",
    "############",
    "...###.##...",
    "..##.##.##..",
    "..#..##..#..",
    ".....##.....",
    "............",
  ].join("\n"),

  // Balance scale — score
  score: [
    "............",
    ".....##.....",
    ".....##.....",
    "############",
    "....####....",
    "...##..##...",
    "..##....##..",
    ".####..####.",
    ".####..####.",
    "............",
    "............",
    "............",
  ].join("\n"),

  // Shield + checkmark — verify
  verify: [
    "............",
    "..########..",
    ".##......##.",
    "##........##",
    "##...##...##",
    "##..####..##",
    "##.##.##.##.",
    ".##.....##..",
    "..##...##...",
    "...##.##....",
    "....###.....",
    "............",
  ].join("\n"),

  // Pen / nib — enrich
  enrich: [
    "............",
    "..........##",
    ".........##.",
    "........##..",
    ".......##...",
    "......##....",
    ".....##.....",
    "....##......",
    "...##.......",
    "..##.##.....",
    ".#####......",
    "##..........",
  ].join("\n"),

  // Checklist — review
  review: [
    "............",
    "############",
    "##........##",
    "##.##.....##",
    "###.....###.",
    "##.....##.##",
    "##....##..##",
    "##........##",
    "##........##",
    "##........##",
    "############",
    "............",
  ].join("\n"),

  // Globe — web verify
  web_verify: [
    "............",
    "...######...",
    "..##.##.##..",
    ".##..##..##.",
    "############",
    "##...##...##",
    "##...##...##",
    "############",
    ".##..##..##.",
    "..##.##.##..",
    "...######...",
    "............",
  ].join("\n"),

  // Gavel — judge
  source_judge: [
    "............",
    ".......####.",
    "......####..",
    "....######..",
    "...####.....",
    "..####......",
    ".####.......",
    "####........",
    "##..........",
    "############",
    "############",
    "............",
  ].join("\n"),

  // Sparkle — polish / final qualify
  polish: [
    "............",
    "....##......",
    "...####.....",
    "....##......",
    ".####..####.",
    "...########.",
    "....######..",
    "...########.",
    ".####.....##",
    "....##......",
    "...####.....",
    "....##......",
  ].join("\n"),
  final_qualify: [
    "............",
    "....##......",
    "...####.....",
    "....##......",
    ".####..####.",
    "...########.",
    "....######..",
    "...########.",
    ".####.....##",
    "....##......",
    "...####.....",
    "....##......",
  ].join("\n"),

  // Cycle arrows — regen
  regen_one: [
    "............",
    "....######..",
    "...##....##.",
    "..##......##",
    "..##......##",
    "..##......##",
    "##..######..",
    "##..##......",
    "##............",
    "..##......##",
    "...########.",
    "............",
  ].join("\n"),

  // Dashed-bracket — gap fill
  gap_fill: [
    "............",
    "##.##.##.##.",
    "............",
    "............",
    "..##....##..",
    "..##....##..",
    "..########..",
    "..##....##..",
    "..##....##..",
    "............",
    "............",
    "##.##.##.##.",
  ].join("\n"),

  // Bar chart — quality signals
  quality_signals: [
    "............",
    "...........#",
    "..........##",
    "....##....##",
    ".####.##..##",
    "######.##.##",
    "###.######.#",
    "##.########.",
    "##.########.",
    "##.########.",
    "############",
    "############",
  ].join("\n"),

  // Magnifier-with-spark — generate.web_search (pulls live from web)
  "generate.web_search": [
    "............",
    ".####....##.",
    ".#..##..##..",
    "##...####...",
    "##...####...",
    ".#..##..##..",
    ".####....##.",
    "....##......",
    ".....##.....",
    "......##....",
    ".......##...",
    "............",
  ].join("\n"),

  // Default — solid block
  __default: [
    "............",
    ".##########.",
    ".##########.",
    ".##########.",
    ".##########.",
    ".##########.",
    ".##########.",
    ".##########.",
    ".##########.",
    ".##########.",
    ".##########.",
    "............",
  ].join("\n"),
};

function PixelGlyph({ pattern }: { pattern: string }) {
  // Pre-compute the rect list once per pattern. Every cell drawn as a
  // 1×1 rect; the SVG viewBox is 12×12 so the parent's width/height
  // scales the whole grid uniformly.
  const rects: { x: number; y: number }[] = [];
  const rows = pattern.split("\n");
  for (let y = 0; y < rows.length; y++) {
    const row = rows[y];
    for (let x = 0; x < row.length; x++) {
      if (row[x] === "#") rects.push({ x, y });
    }
  }
  return (
    <svg
      width="100%"
      height="100%"
      viewBox={`0 0 ${GRID} ${GRID}`}
      shapeRendering="crispEdges"
      preserveAspectRatio="xMidYMid meet"
      aria-hidden
    >
      {rects.map((r) => (
        <rect key={`${r.x}-${r.y}`} x={r.x} y={r.y} width={CELL} height={CELL} fill="currentColor" />
      ))}
    </svg>
  );
}

export default function PhaseIcon({
  step,
  className,
  size = 16,
}: {
  step: string;
  className?: string;
  size?: number;
}) {
  const pattern = ICONS[step] ?? ICONS.__default;
  return (
    <span
      className={className ?? "inline-flex"}
      style={{ width: size, height: size, lineHeight: 0 }}
      aria-hidden
    >
      <PixelGlyph pattern={pattern} />
    </span>
  );
}
