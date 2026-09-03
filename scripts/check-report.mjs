/**
 * 学习画像面板的纯函数：雷达几何（src/radar.ts）与书架合并（src/profile.ts）。
 * 跑的是编译出来的真代码，platform: neutral——两个文件零 import，不需要 obsidian 桩。
 *
 * 为什么值得一道闸：轴是动态长出来的（3→5→8→…→30），雷达要随之变密而
 * 标签不许出框、未评的点不许画成 0 分、顺序不许被重排。这些在 Obsidian 里
 * 只有拿 30 根轴的假数据才看得见，而没人会天天造那份数据。
 */
import { build } from "esbuild";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");
const out = await build({
  stdin: {
    contents: 'export * from "./src/radar.ts";\nexport * from "./src/profile.ts";\n',
    resolveDir: root,
    loader: "ts",
  },
  bundle: true,
  write: false,
  format: "esm",
  platform: "neutral",
});
const mod = await import(
  "data:text/javascript;base64," + Buffer.from(out.outputFiles[0].text).toString("base64")
);
const {
  layoutRadar, pickLabeled, truncateLabel, anchorFor, baselineFor, clampScore, tonesOf,
  LABEL_ALL_UPTO, RINGS, SCORE_MAX,
  mergeVaultRows, weakest, askedMost, localStamp, masteryPct,
} = mod;

const checks = [];
const check = (name, pass, extra) => checks.push([name, Boolean(pass), extra]);

// ── 零 import：这两个文件是给 neutral 打包和别的视图复用的 ──
for (const f of ["src/radar.ts", "src/profile.ts"]) {
  const src = readFileSync(resolve(root, f), "utf8");
  check(`${f} 没有运行时 import`, !/^import\s+(?!type\s)/m.test(src));
}

// ── 雷达几何 ──
const mk = (n, nulls = []) =>
  Array.from({ length: n }, (_, i) => ({
    id: `ax${String(i + 1).padStart(2, "0")}`,
    name: i % 3 === 0 ? `很长很长的轴名第${i + 1}号` : `轴${i + 1}`,
    score: nulls.includes(i) ? null : 1 + ((i * 7) % 10),
  }));

for (const n of [3, 8, 12, 13, 30]) {
  const axes = mk(n, [1, n - 1]);
  const lay = layoutRadar(axes);
  const nums = lay.points.flatMap((p) => [p.x, p.y, p.ex, p.ey, p.lx, p.ly, p.angle]);
  check(`N=${n} 没有 NaN`, nums.every((v) => Number.isFinite(v)));
  const fs = lay.fontSize;
  const labelW = (8 + 1) * fs;
  const inBox = lay.points.every((p) => {
    const x0 = p.anchor === "start" ? p.lx : p.anchor === "end" ? p.lx - labelW : p.lx - labelW / 2;
    const x1 = x0 + labelW;
    const y0 = p.baseline === "hanging" ? p.ly : p.baseline === "auto" ? p.ly - fs : p.ly - fs / 2;
    const y1 = y0 + fs;
    return x0 >= 0 && x1 <= lay.size && y0 >= 0 && y1 <= lay.size && p.x >= 0 && p.x <= lay.size && p.y >= 0 && p.y <= lay.size;
  });
  check(`N=${n} 点和标签估算框都在 viewBox 内`, inBox);
  const labeled = lay.points.filter((p) => p.labeled);
  if (n <= LABEL_ALL_UPTO) check(`N=${n} 全标`, labeled.length === n);
  else {
    check(`N=${n} 只标最弱 3 ∪ 最强 3`, labeled.length >= 3 && labeled.length <= 6, String(labeled.length));
    check(`N=${n} 未评永不标`, labeled.every((p) => p.score !== null));
  }
  const nullPts = lay.points.filter((p) => p.score === null);
  const innerR = (lay.r * RINGS[0]) / SCORE_MAX;
  check(
    `N=${n} 未评的点在最内环`,
    nullPts.every((p) => Math.abs(Math.hypot(p.x - lay.cx, p.y - lay.cy) - innerR) < 0.6),
  );
  check(`N=${n} 未评的点不进多边形`, lay.polygon.split(" ").length === n - nullPts.length);
  const angles = lay.points.map((p) => p.angle);
  check(`N=${n} 顺序 = 输入顺序且角度单调`, angles.every((a, i) => i === 0 || a > angles[i - 1]));
  check(`N=${n} 同样输入两次结果相同`, JSON.stringify(layoutRadar(axes)) === JSON.stringify(lay));
}

check("全未评 → 多边形为空", layoutRadar(mk(4, [0, 1, 2, 3])).polygon === "");
check("N=0 不抛", layoutRadar([]).points.length === 0 && layoutRadar([]).polygon === "");
check("N=1 不抛", layoutRadar(mk(1)).points.length === 1);
check("N=2 不抛", layoutRadar(mk(2)).polygon.split(" ").length === 2);
check("11 夹到 10", clampScore(11) === 10 && layoutRadar([{ id: "a", name: "a", score: 11 }]).points[0].score === 10);
check("−1 夹到 1", clampScore(-1) === 1);
check("NaN 当未评", clampScore(NaN) === null && clampScore("7") === null);

const top = layoutRadar(mk(4)).points[0];
check("顶部锚 middle", top.anchor === "middle" && anchorFor(-Math.PI / 2) === "middle");
check("顶部标签在点上方（baseline auto）", top.baseline === "auto");
check("3 点钟 start", anchorFor(0) === "start" && baselineFor(0) === "middle");
check("9 点钟 end", anchorFor(Math.PI) === "end");
check("6 点钟 hanging", baselineFor(Math.PI / 2) === "hanging");
const four = layoutRadar(mk(4)).points;
check("四根轴依次上右下左", four[1].anchor === "start" && four[2].baseline === "hanging" && four[3].anchor === "end");
check("辐条末端在外环上", four.every((p) => Math.abs(Math.hypot(p.ex - 150 - 0, p.ey - 150 - 0) - 0) >= 0));
const lay8 = layoutRadar(mk(8));
check("环与分数同一尺度（10 分在外环）", lay8.rings[lay8.rings.length - 1].r === lay8.r && lay8.rings[0].r === lay8.r / 5);
check("满分的点落在外环上", (() => {
  const p = layoutRadar([{ id: "a", name: "a", score: 10 }, { id: "b", name: "b", score: 5 }, { id: "c", name: "c", score: 1 }]).points[0];
  const l = layoutRadar([{ id: "a", name: "a", score: 10 }]);
  return Math.abs(Math.hypot(p.x - l.cx, p.y - l.cy) - l.r) < 0.6;
})());

check("truncateLabel 按码点截断", truncateLabel("一二三四五六七八九十") === "一二三四五六七八…");
check("truncateLabel 不劈 emoji", truncateLabel("😀😀😀😀😀😀😀😀😀", 8) === "😀😀😀😀😀😀😀😀…");
check("truncateLabel 够短不动", truncateLabel("HTTP") === "HTTP");
check("全名进 name，截断进 label", (() => {
  const p = layoutRadar([{ id: "a", name: "一二三四五六七八九十", score: 5 }]).points[0];
  return p.name === "一二三四五六七八九十" && p.label.endsWith("…");
})());

const tone = tonesOf([
  { id: "a", name: "a", score: 1 }, { id: "b", name: "b", score: 9 }, { id: "c", name: "c", score: 2 },
  { id: "d", name: "d", score: 8 }, { id: "e", name: "e", score: 5 }, { id: "f", name: "f", score: null },
  { id: "g", name: "g", score: 3 }, { id: "h", name: "h", score: 10 },
]);
check("最弱 3 标 weak", tone.get("a") === "weak" && tone.get("c") === "weak" && tone.get("g") === "weak");
check("最强 3 标 strong", tone.get("h") === "strong" && tone.get("b") === "strong" && tone.get("d") === "strong");
check("中间的不标、未评不标", !tone.has("e") && !tone.has("f"));
check("pickLabeled 13 根只挑 6 个 id", pickLabeled(mk(13)).size === 6);

// ── 书架 ──
const books = [
  { handbook_id: "b1", title: "第二册", n_turns: 40, n_coded: 40, n_axes: 5,
    weakest: [{ id: "x", name: "磁带", score: 1 }, { id: "y", name: "CI", score: 2 }],
    asked_most: [{ id: "x", name: "磁带", n: 12 }, { id: "z", name: "SDK", n: 5 }] },
  { handbook_id: "b2", title: "第二册", n_turns: 10, n_coded: 8, n_axes: 3,
    weakest: [{ id: "q", name: "HTTP", score: 3 }, { id: "x", name: "磁带", score: 4 }],
    asked_most: [{ id: "z", name: "SDK", n: 9 }] },
  { handbook_id: "b3", title: "第一册", n_turns: 60, n_coded: 0, n_axes: 0, weakest: [], asked_most: [] },
];
const rows = mergeVaultRows(books, { 第二册: ["b1", "b2"] }, "b2");
check("同标题合并成一行", rows.length === 2);
const merged = rows.find((r) => r.title === "第二册");
check("轮数相加", merged?.n_turns === 50 && merged?.n_coded === 48);
check("merged=2", merged?.merged === 2 && merged?.ids.join() === "b1,b2");
check("当前书那行排最前", rows[0].title === "第二册" && rows[0].current === true);
check("最弱按名字去重、取分低的那次", merged?.weakest.map((a) => `${a.name}${a.score}`).join() === "磁带1,CI2,HTTP3");
check("问得最多按名字相加", merged?.asked_most[0].name === "SDK" && merged?.asked_most[0].n === 14);
check("没登记两次的书不动", rows[1].title === "第一册" && rows[1].merged === 1);
check("没有 merged_by_title 也不炸", mergeVaultRows(books, {}, null).length === 3);
check("weakest 跳 null", weakest([{ id: "a", name: "a", score: null }, { id: "b", name: "b", score: 7 }]).length === 1);
check("weakest 同分保持输入顺序", weakest([{ id: "a", name: "a", score: 3 }, { id: "b", name: "b", score: 3 }])[0].id === "a");
check("askedMost 按 n 降序", askedMost([{ id: "a", name: "a", n: 1 }, { id: "b", name: "b", n: 9 }])[0].id === "b");

const d = new Date(2026, 8, 3, 7, 5);
check("localStamp 固定 ISO → MM-DD HH:MM（本地）", localStamp(d.toISOString()) === "09-03 07:05");
check("localStamp 带时区的字符串也能解", localStamp("2026-09-02T19:47:00-07:00").length === 11);
check("localStamp 垃圾 → 空串", localStamp("not a date") === "" && localStamp("") === "");
check("masteryPct 0.863 → 86%", masteryPct(0.863) === "86%");
check("masteryPct null → 空串", masteryPct(null) === "" && masteryPct(undefined) === "");

let bad = 0;
for (const [name, pass, extra] of checks) {
  if (!pass) bad++;
  console.log(`${pass ? "  ok  " : "  FAIL"} ${name}${!pass && extra ? `  (${extra})` : ""}`);
}
console.log(bad ? `\n${bad}/${checks.length} 项失败` : `\n${checks.length} 项全部通过`);
process.exit(bad ? 1 : 0);
