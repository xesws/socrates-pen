/** Geometry only: never create/dispose a session while reshaping its view. */
export type AgentLayout = {
  count: number;
  wide: boolean;
  kind: string;
  columns: string;
  rows: string;
  areas: string[];
  overflow: boolean;
};

const GAP = 8;
const MIN = 320;
type Candidate = { kind: string; cols: number; rows: number; areas: string[]; minW: number; minH: number };
const strip = (n: number, wide: boolean, roomy = false): Candidate => ({
  kind: `${wide ? "row" : "column"}-${n}`, cols: wide ? n : 1, rows: wide ? 1 : n,
  areas: Array.from({ length: n }, (_, i) => wide ? `1 / ${i + 1}` : `${i + 1} / 1`),
  minW: roomy && wide ? 420 : MIN, minH: roomy && !wide ? 360 : MIN,
});

export function computeAgentLayout(input: {
  count: number; width: number; height: number; previous?: AgentLayout;
}): AgentLayout {
  const { width: w, height: h, previous: prev } = input;
  const n = Math.max(1, Math.min(4, Math.floor(input.count)));
  if ((w <= 0 || h <= 0) && prev?.count === n) return prev;
  const wide = prev ? (prev.wide ? h < w * 1.1 : w >= h * 1.1) : w >= h;
  if (n === 1) return { count: n, wide, kind: "single", columns: "minmax(0, 1fr)",
    rows: "minmax(0, 1fr)", areas: ["1 / 1"], overflow: false };
  const candidates: Candidate[] = n === 2 ? [strip(n, wide), strip(n, !wide)] : [
    strip(n, wide, true),
    { kind: n === 4 ? "grid" : wide ? "primary-left" : "primary-top", cols: 2, rows: 2,
      areas: n === 4 ? ["1 / 1", "1 / 2", "2 / 1", "2 / 2"] : wide
        ? ["1 / 1 / 3 / 2", "1 / 2", "2 / 2"] : ["1 / 1 / 2 / 3", "2 / 1", "2 / 2"],
      minW: MIN, minH: MIN },
  ];
  const fits = (c: Candidate, extra = 0, hard = false) =>
    (w - GAP * (c.cols - 1)) / c.cols >= (hard ? MIN : c.minW) + extra &&
    (h - GAP * (c.rows - 1)) / c.rows >= (hard ? MIN : c.minH) + extra;
  let chosen = candidates.find(c => fits(c));
  if (prev?.count === n && chosen?.kind !== prev.kind) {
    const previous = [...candidates, strip(n, !wide, n > 2),
      { kind: "primary-left", cols: 2, rows: 2, areas: ["1 / 1 / 3 / 2", "1 / 2", "2 / 2"], minW: MIN, minH: MIN },
      { kind: "primary-top", cols: 2, rows: 2, areas: ["1 / 1 / 2 / 3", "2 / 1", "2 / 2"], minW: MIN, minH: MIN },
    ].find(c => c.kind === prev.kind);
    // Stay put near a breakpoint, unless the current cells are actually too small.
    if (previous && fits(previous, 0, true) && (!chosen || !fits(chosen, 24))) chosen = previous;
  }
  if (!chosen) {
    return { count: n, wide, kind: "stack", columns: "minmax(0, 1fr)",
      rows: `repeat(${n}, ${Math.max(MIN, (h - GAP * (n - 1)) / n)}px)`,
      areas: strip(n, false).areas, overflow: h < n * MIN + GAP * (n - 1) };
  }
  return { count: n, wide, kind: chosen.kind,
    columns: `repeat(${chosen.cols}, minmax(0, 1fr))`, rows: `repeat(${chosen.rows}, minmax(0, 1fr))`,
    areas: chosen.areas, overflow: false };
}
