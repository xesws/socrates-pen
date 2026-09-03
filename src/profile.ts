/**
 * 画像面板的纯函数：书架合并、最弱/问得最多、本地时间戳。零 import——
 * scripts/check-report.mjs 用 platform: neutral 打包真代码守着，不需要 obsidian 桩。
 * 分数一个都不在这儿算（那是 pen/profile.py 的事）。
 */

export type Brief = { id: string; name: string; score: number | null };
export type Asked = { id: string; name: string; n: number };

/** 书架里每条轴：score 给「最弱」，asked 给「问得最多」。 */
export type AxisCount = { id: string; name: string; score: number | null; asked: number };

export type BookRow = {
  handbook_id: string;
  title: string;
  n_turns: number;
  n_coded: number;
  n_axes: number;
  /** 这本书的**每一条**轴。合并同标题的登记要从这里算：服务端裁成 top 3 再合并，
   *  两次都排第 4 的那条轴加起来本该第一，却在合并前就丢了。 */
  axes: AxisCount[];
};

export type VaultRow = {
  ids: string[];
  title: string;
  n_turns: number;
  n_coded: number;
  n_axes: number;
  weakest: Brief[];
  asked_most: Asked[];
  /** 合并了几次登记；1 = 没合并。 */
  merged: number;
  current: boolean;
};

/** 已评里分最低的 K 个（同分保持输入顺序）。未评跳过。 */
export function weakest(axes: Brief[], k = 3): Brief[] {
  return axes
    .map((a, i) => ({ a, i }))
    .filter((p) => typeof p.a.score === "number" && Number.isFinite(p.a.score))
    .sort((p, q) => (p.a.score as number) - (q.a.score as number) || p.i - q.i)
    .slice(0, k)
    .map((p) => p.a);
}

/** 问得最多的 K 个轴。 */
export function askedMost(axes: Asked[], k = 3): Asked[] {
  return axes
    .map((a, i) => ({ a, i }))
    .sort((p, q) => q.a.n - p.a.n || p.i - q.i)
    .slice(0, k)
    .map((p) => p.a);
}

/**
 * 同一本书登记过两次（路径变了 id 就变），服务端在 merged_by_title 里指出来，
 * 这里并成一行：轮数相加，最弱/问得最多从两边的全部轴按名字合并后再取前 3。
 * 当前书那行排最前，其余按轮数降序。
 */
export function mergeVaultRows(
  books: BookRow[],
  mergedByTitle: Record<string, string[]>,
  currentId: string | null,
): VaultRow[] {
  const groupOf = new Map<string, string>();
  for (const [key, ids] of Object.entries(mergedByTitle || {})) {
    for (const id of ids) groupOf.set(id, `title:${key}`);
  }
  const rows = new Map<string, VaultRow>();
  for (const b of books) {
    const g = groupOf.get(b.handbook_id) ?? `id:${b.handbook_id}`;
    let row = rows.get(g);
    if (!row) {
      row = {
        ids: [],
        title: b.title,
        n_turns: 0,
        n_coded: 0,
        n_axes: 0,
        weakest: [],
        asked_most: [],
        merged: 0,
        current: false,
      };
      rows.set(g, row);
    }
    row.ids.push(b.handbook_id);
    row.n_turns += b.n_turns;
    row.n_coded += b.n_coded;
    row.n_axes = Math.max(row.n_axes, b.n_axes);
    row.merged = row.ids.length;
    row.current = row.current || b.handbook_id === currentId;
    row.weakest = dedupeByName([...row.weakest, ...b.axes.map((a) => ({ id: a.id, name: a.name, score: a.score }))]);
    row.asked_most = sumByName([...row.asked_most, ...b.axes.map((a) => ({ id: a.id, name: a.name, n: a.asked }))]);
  }
  const out = [...rows.values()].map((r) => ({
    ...r,
    weakest: weakest(r.weakest, 3),
    asked_most: askedMost(r.asked_most, 3),
  }));
  return out.sort(
    (a, b) => Number(b.current) - Number(a.current) || b.n_turns - a.n_turns || a.title.localeCompare(b.title),
  );
}

function dedupeByName(items: Brief[]): Brief[] {
  const seen = new Map<string, Brief>();
  for (const it of items) {
    const prev = seen.get(it.name);
    // 同名取分低的那条：合并的是「最弱」表，弱的那一次更该被看见。
    if (!prev || (it.score !== null && (prev.score === null || it.score < prev.score))) seen.set(it.name, it);
  }
  return [...seen.values()];
}

function sumByName(items: Asked[]): Asked[] {
  const acc = new Map<string, Asked>();
  for (const it of items) {
    const prev = acc.get(it.name);
    if (prev) prev.n += it.n;
    else acc.set(it.name, { ...it });
  }
  return [...acc.values()];
}

const two = (v: number): string => (v < 10 ? `0${v}` : String(v));

/** ISO → 本地 `MM-DD HH:MM`。解析不了给空串，不给 "Invalid Date"。用 getter 不用
 *  toLocaleString：后者的格式随系统语言漂，证据表里两种写法混着看不出先后。 */
export function localStamp(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`;
}

/** BKT 概率 → 百分数字符串；null 给空串（面板上显示成「—」由调用方决定）。 */
export function masteryPct(m: number | null | undefined): string {
  if (typeof m !== "number" || !Number.isFinite(m)) return "";
  return `${Math.round(m * 100)}%`;
}
