/**
 * PixelMascot — small decorative pixel-art images shown sparingly on
 * cards / hero sections, in the same chunky retro style Mistral uses on
 * their site (microphone, robot, etc.). NOT functional UI — purely
 * visual flavour.
 *
 * Each mascot is laid out on a 16×16 grid using a string syntax:
 *   `.` empty
 *   `#` body (currentColor — typically Mistral orange)
 *   `*` highlight (lighter — pulled from the parent's --mistral-cream)
 *   `:` shadow (darker)
 *
 * Usage: `<PixelMascot kind="compass" size={48} />`
 */

const GRID = 16;

const MASCOTS: Record<string, string> = {
  // Compass — brand mascot used in the hero.
  compass: [
    "................",
    "................",
    ".....######.....",
    "....#******#....",
    "...#*#*##*#*#...",
    "..#**#####**#..",
    "..*##*###*##*..",
    "..*##*###*##*..",
    "..#**#####**#..",
    "...#*#*##*#*#...",
    "....#******#....",
    ".....######.....",
    "................",
    "................",
    "................",
    "................",
  ].join("\n"),

  // Microphone — research / "agent listening" cards. Inspired by
  // Mistral's site mascot.
  microphone: [
    "................",
    "................",
    ".....######.....",
    "....#******#....",
    "....*##**##*....",
    "....*##**##*....",
    "....*##**##*....",
    "....#******#....",
    ".....######.....",
    "......####......",
    "......####......",
    "....##########..",
    "...############.",
    "................",
    "................",
    "................",
  ].join("\n"),

  // Beaker — verification / "lab work" cards.
  beaker: [
    "................",
    "................",
    "....##....##....",
    "...####..####...",
    "....##....##....",
    "....##....##....",
    "....#######.....",
    "....#:::::#.....",
    "...#::###::#....",
    "...#:#####:#....",
    "..#::#####::#...",
    "..#::#####::#...",
    "..############..",
    "..############..",
    "................",
    "................",
  ].join("\n"),

  // Chart — quality signals / metrics cards.
  chart: [
    "................",
    "................",
    "................",
    "...#............",
    "...#......##....",
    "...#......##....",
    "...#......##....",
    "...#...##.##....",
    "...#...##.##....",
    "...#.####.##....",
    "...#.####.##....",
    "...#.####.##....",
    "...##############",
    "...##############",
    "................",
    "................",
  ].join("\n"),

  // Stack of papers — precedent corpus.
  stack: [
    "................",
    "................",
    "....#########...",
    "....#:::::::#...",
    "....#:#####:#...",
    "....#:::::::#...",
    "...##########...",
    "...#:::::::::#..",
    "...#:#######:#..",
    "...#:::::::::#..",
    "..############..",
    "..#:::::::::::#.",
    "..#:#########:#.",
    "..#:::::::::::#.",
    "..#############.",
    "................",
  ].join("\n"),

  // Spark / star — generation, novel directions.
  spark: [
    "................",
    "................",
    ".......##.......",
    "......####......",
    "..#...####...#..",
    "..##..####..##..",
    "...##.####.##...",
    "....##########..",
    "...##########...",
    "...##.####.##...",
    "..##..####..##..",
    "..#...####...#..",
    "......####......",
    ".......##.......",
    "................",
    "................",
  ].join("\n"),
};

const COLORS: Record<string, string> = {
  body: "#fa552e",
  highlight: "#ffd2b8",
  shadow: "#7c2d12",
};

export type MascotKind = keyof typeof MASCOTS;

export default function PixelMascot({
  kind,
  size = 48,
  tint,
}: {
  kind: MascotKind | string;
  size?: number;
  tint?: { body?: string; highlight?: string; shadow?: string };
}) {
  const pattern = MASCOTS[kind] ?? MASCOTS.compass;
  const palette = { ...COLORS, ...(tint ?? {}) };
  const rows = pattern.split("\n");
  return (
    <span
      className="inline-flex items-center justify-center"
      style={{ width: size, height: size, lineHeight: 0 }}
      aria-hidden
    >
      <svg
        width="100%"
        height="100%"
        viewBox={`0 0 ${GRID} ${GRID}`}
        shapeRendering="crispEdges"
        preserveAspectRatio="xMidYMid meet"
      >
        {rows.map((row, y) =>
          row.split("").map((ch, x) => {
            if (ch === ".") return null;
            const fill =
              ch === "#" ? palette.body : ch === "*" ? palette.highlight : palette.shadow;
            return <rect key={`${x}-${y}`} x={x} y={y} width={1} height={1} fill={fill} />;
          })
        )}
      </svg>
    </span>
  );
}
