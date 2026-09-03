/**
 * 雷达图的几何。**零 import、零 DOM**：这里只算数，画交给 ReportView 用
 * `createSvg` 逐节点建（Obsidian 不许 innerHTML 灌外来 SVG）。
 * scripts/check-report.mjs 打包真代码守着下面每条不变量。
 *
 * 轴是动态长出来的（3→5→8→…→30），所以：
 *  - 第 i 根轴永远在 θ = −π/2 + 2πi/n；顺序 = 服务端顺序（first_seen），永不重排；
 *  - 环 2/4/6/8/10 与分数同一尺度，n 变了环不变；
 *  - 未评（score: null）的点画在最内环、空心、**不进多边形**——画在 0 会把
 *    「没数据」读成「崩了」；
 *  - 标签在外环外 LABEL_GAP 处，锚点按角度定，不量文字宽度（SVG 里量不到）；
 *    viewBox 按「最长名字 × 字号」留边，300px 宽的侧栏也不裁；
 *  - n > LABEL_ALL_UPTO 只标已评里最弱 3 ∪ 最强 3，其余靠 <title> / aria-label。
 */

export const SCORE_MIN = 1;
export const SCORE_MAX = 10;
export const RINGS = [2, 4, 6, 8, 10];
export const LABEL_MAX = 8;
export const LABEL_GAP = 10;
export const LABEL_ALL_UPTO = 12;
export const PICK_K = 3;

export type RadarAxis = { id: string; name: string; score: number | null };
export type Anchor = "start" | "middle" | "end";
export type Baseline = "hanging" | "middle" | "auto";
export type Tone = "weak" | "strong" | "";

export type RadarPoint = {
  id: string;
  name: string;
  /** 截断后的显示名；全名进 <title>。 */
  label: string;
  score: number | null;
  angle: number;
  /** 数据点。 */
  x: number;
  y: number;
  /** 辐条末端（外环上）。 */
  ex: number;
  ey: number;
  /** 标签锚点。 */
  lx: number;
  ly: number;
  anchor: Anchor;
  baseline: Baseline;
  labeled: boolean;
  tone: Tone;
};

export type RadarLayout = {
  viewBox: string;
  size: number;
  cx: number;
  cy: number;
  r: number;
  fontSize: number;
  rings: { value: number; r: number }[];
  points: RadarPoint[];
  /** 已评的点按顺序连成的 points 属性；全未评是空串。 */
  polygon: string;
};

export type RadarOptions = { r?: number; fontSize?: number; nameMax?: number };

/** 分数夹到 1–10；不是有限数就当未评。 */
export function clampScore(v: unknown): number | null {
  if (typeof v !== "number" || !Number.isFinite(v)) return null;
  return Math.min(SCORE_MAX, Math.max(SCORE_MIN, v));
}

/** 按码点截断，不按 UTF-16 单元——emoji 和生僻字不许被劈成半个。 */
export function truncateLabel(name: string, max = LABEL_MAX): string {
  const cps = Array.from(name);
  return cps.length <= max ? name : `${cps.slice(0, max).join("")}…`;
}

/** 标签锚点：3 点钟方向 start、9 点钟 end、正上正下 middle。 */
export function anchorFor(angle: number): Anchor {
  const c = Math.cos(angle);
  return c > 0.35 ? "start" : c < -0.35 ? "end" : "middle";
}

export function baselineFor(angle: number): Baseline {
  const s = Math.sin(angle);
  return s > 0.35 ? "hanging" : s < -0.35 ? "auto" : "middle";
}

function rated(axes: RadarAxis[]): { i: number; score: number }[] {
  const out: { i: number; score: number }[] = [];
  axes.forEach((a, i) => {
    const s = clampScore(a.score);
    if (s !== null) out.push({ i, score: s });
  });
  return out;
}

/** 已评里最弱 K 与最强 K（先弱后强，同分按输入顺序）。 */
export function tonesOf(axes: RadarAxis[], k = PICK_K): Map<string, Tone> {
  const out = new Map<string, Tone>();
  const rs = rated(axes);
  const asc = [...rs].sort((a, b) => a.score - b.score || a.i - b.i);
  for (const p of asc.slice(0, k)) out.set(axes[p.i].id, "weak");
  const desc = [...rs].sort((a, b) => b.score - a.score || a.i - b.i);
  for (const p of desc.slice(0, k)) {
    const id = axes[p.i].id;
    if (!out.has(id)) out.set(id, "strong");
  }
  return out;
}

/** 该标哪些轴的名字：≤12 全标；更多就只标最弱 K ∪ 最强 K，未评永不标。 */
export function pickLabeled(axes: RadarAxis[], k = PICK_K): Set<string> {
  if (axes.length <= LABEL_ALL_UPTO) return new Set(axes.map((a) => a.id));
  return new Set(tonesOf(axes, k).keys());
}

const fmt = (v: number): number => Math.round(v * 10) / 10;

export function layoutRadar(axes: RadarAxis[], opts: RadarOptions = {}): RadarLayout {
  const r = opts.r ?? 100;
  const fontSize = opts.fontSize ?? 12;
  const nameMax = opts.nameMax ?? LABEL_MAX;
  // 中文一字约等于字号宽；+1 是省略号那一格。
  const labelW = (nameMax + 1) * fontSize;
  const pad = r + LABEL_GAP + labelW + fontSize;
  const size = Math.ceil(pad * 2);
  const cx = size / 2;
  const cy = size / 2;
  const n = axes.length;
  const labeled = pickLabeled(axes);
  const tones = tonesOf(axes);
  const points: RadarPoint[] = axes.map((a, i) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / (n || 1);
    const score = clampScore(a.score);
    const rr = (r * (score === null ? RINGS[0] : score)) / SCORE_MAX;
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    const lr = r + LABEL_GAP;
    return {
      id: a.id,
      name: a.name,
      label: truncateLabel(a.name, nameMax),
      score,
      angle,
      x: fmt(cx + rr * cos),
      y: fmt(cy + rr * sin),
      ex: fmt(cx + r * cos),
      ey: fmt(cy + r * sin),
      lx: fmt(cx + lr * cos),
      ly: fmt(cy + lr * sin),
      anchor: anchorFor(angle),
      baseline: baselineFor(angle),
      labeled: labeled.has(a.id),
      tone: tones.get(a.id) ?? "",
    };
  });
  return {
    viewBox: `0 0 ${size} ${size}`,
    size,
    cx,
    cy,
    r,
    fontSize,
    rings: RINGS.map((value) => ({ value, r: fmt((r * value) / SCORE_MAX) })),
    points,
    polygon: points
      .filter((p) => p.score !== null)
      .map((p) => `${p.x},${p.y}`)
      .join(" "),
  };
}
