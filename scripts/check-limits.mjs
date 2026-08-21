/**
 * 前后端那两张夹紧表必须逐项相等。`npm test` 跑它。
 *
 * TS 的 LIMIT_SPEC 和 pen/config.py 的 LIMIT_RANGE + 默认常量是**同一道闸的
 * 两个副本**——这是 v0.10.x 整个改造里唯一新造出来的「两个闸不同源」，
 * 而本仓栽在这个形状上已经三次了（书架闸 vs read 闸、两处各筛一遍书、
 * 两处 key 不规范化）。不靠自觉，靠这四十行。
 *
 * 后端仍然是权威（它无论如何都会再夹一遍）；前端这张表只管 UX。
 * 但漂了就意味着「界面让你填 900，实际生效 300」，读者查不出来。
 */
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pluginRoot = join(here, "..");

function findAgentRoot() {
  const bundled = join(pluginRoot, "pen", "config.py");
  if (existsSync(bundled)) return resolve(pluginRoot);
  const fromEnv = (process.env.SOCRATES_AGENT || "").trim();
  if (fromEnv && existsSync(join(fromEnv, "pen", "config.py"))) return resolve(fromEnv);
  const sibling = join(pluginRoot, "..", "Socrates-agent");
  if (existsSync(join(sibling, "pen", "config.py"))) return resolve(sibling);
  return null;
}

const repo = findAgentRoot();
if (!repo) {
  console.log(
    "skip check-limits: sidecar source not found (this repo should contain pen/config.py)",
  );
  process.exit(0);
}

const { build } = await import("esbuild");

const out = join(mkdtempSync(join(tmpdir(), "sp-lim-")), "settings.cjs");
await build({
  entryPoints: ["src/settings.ts"],
  bundle: true,
  format: "cjs",
  external: ["obsidian"],
  outfile: out,
  logLevel: "error",
});
const require = createRequire(import.meta.url);
const Module = require("node:module");
const origLoad = Module._load;
Module._load = (req, parent, isMain) =>
  req === "obsidian"
    ? { App: class {}, Plugin: class {}, PluginSettingTab: class {}, Setting: class {} }
    : origLoad(req, parent, isMain);
const S = require(out);

// ── 从 pen/config.py 抠出 LIMIT_RANGE 和 default_limits() 的映射 ──
const py = readFileSync(join(repo, "pen", "config.py"), "utf8");

const rangeBlock = py.slice(
  py.indexOf("LIMIT_RANGE: dict"),
  py.indexOf("def merge_limits"),
);
const pyRange = {};
for (const m of rangeBlock.matchAll(/"([a-z_]+)":\s*\(([-\d_.]+),\s*([-\d_.]+)\)/g)) {
  pyRange[m[1]] = [Number(m[2].replaceAll("_", "")), Number(m[3].replaceAll("_", ""))];
}

const defBlock = py.slice(py.indexOf("def default_limits"), py.indexOf("# 字段 → (下限"));
const consts = {};
for (const m of py.matchAll(/^([A-Z][A-Z0-9_]*)\s*=\s*([\d_.]+)\s*$/gm)) {
  consts[m[1]] = Number(m[2].replaceAll("_", ""));
}
const pyDefault = {};
for (const m of defBlock.matchAll(/^\s+([a-z_]+)=([A-Z][A-Z0-9_]*),/gm)) {
  pyDefault[m[1]] = consts[m[2]];
}

const checks = [];
const check = (name, pass) => checks.push([name, pass]);

/**
 * **刻意不上界面**的旋钮，每一项都要写清为什么（见 docs/v0.10.5）。
 * 这不是给「忘了做界面」用的豁免口——写进来就是一次明确的产品决定。
 */
const NOT_EXPOSED = {
  // 由 keep 推导 max(3, keep+1)。调大它只会让模型多吐几条废题，
  // 多花输出 token 而入池数不变，纯亏。
  probe_parse_cap: true,
  // 它不是预算，是诚实策略：open 题 = 手册里没出处、凭记忆答、答时要挑明。
  // 这条产品承诺不该做成旋钮让读者自己调没。
  probe_open_per_run: true,
};

const tsKeys = Object.keys(S.LIMIT_SPEC).sort();
const pyKeys = Object.keys(pyRange).sort();
const shouldShow = pyKeys.filter((k) => !NOT_EXPOSED[k]).sort();
check(
  `界面覆盖了每一个该暴露的旋钮（TS ${tsKeys.length} 项 / 后端该暴露 ${shouldShow.length} 项）`,
  JSON.stringify(tsKeys) === JSON.stringify(shouldShow),
);
if (JSON.stringify(tsKeys) !== JSON.stringify(shouldShow)) {
  console.error("   界面上多出来的：", tsKeys.filter((k) => !shouldShow.includes(k)));
  console.error("   后端有、界面漏了的：", shouldShow.filter((k) => !tsKeys.includes(k)));
  console.error("   （真要不暴露，就把它写进 NOT_EXPOSED 并说明理由）");
}
check(
  "NOT_EXPOSED 里的键在后端真的存在（写错名字等于豁免了个空气）",
  Object.keys(NOT_EXPOSED).every((k) => pyKeys.includes(k)),
);

for (const k of tsKeys) {
  const spec = S.LIMIT_SPEC[k];
  const rng = pyRange[k];
  if (!rng) continue;
  check(`${k} 的范围两边一致`, spec.min === rng[0] && spec.max === rng[1]);
  check(`${k} 的默认值两边一致`, spec.def === pyDefault[k]);
}

// 常用 + 高级必须刚好覆盖所有键，一项都不能落在界面外
const shown = [...S.COMMON_LIMITS, ...S.ADVANCED_LIMITS].sort();
check("常用 + 高级刚好覆盖每一个旋钮", JSON.stringify(shown) === JSON.stringify(tsKeys));
check("两区不重叠", new Set(shown).size === shown.length);

// 每个旋钮都得有中英名字和说明
const i18nOut = join(mkdtempSync(join(tmpdir(), "sp-lim-i18n-")), "i18n.cjs");
await build({
  entryPoints: ["src/i18n/index.ts"],
  bundle: true,
  format: "cjs",
  external: ["obsidian"],
  outfile: i18nOut,
  logLevel: "error",
});
globalThis.window = { localStorage: { getItem: () => null } };
globalThis.document = { documentElement: { lang: "" } };
const i18n = require(i18nOut);
for (const lang of ["zh", "en"]) {
  i18n.setLang(lang);
  const d = i18n.t();
  for (const k of tsKeys) {
    check(`${lang}: ${k} 有名字`, Boolean(d.limitName(k)) && d.limitName(k) !== k);
  }
}
i18n.setLang("zh");
const zh = i18n.t();
i18n.setLang("en");
const en = i18n.t();
for (const k of tsKeys) {
  check(`${k} 的名字确实翻过`, zh.limitName(k) !== en.limitName(k));
}

// 三句诚实话必须在（假装是硬上限，读者第一次看到超出就再也不信了）
check("说清了主对话那个不是硬上限", /不受限/.test(zh.limitDesc("max_tokens_chat")));
check("说清了深挖存多了也只放一条", /只放出 1 条/.test(zh.limitDesc("probe_keep_per_run")));
check("说清了超时调大会看不见当轮结果", /下一轮/.test(zh.limitDesc("probe_timeout_s")));

// ── 夹紧算法本身也得一致，不只是那三个数 ──
// 前端 Math.round 后夹 vs 后端先夹后 int() 截断，同一个畸形输入两边会给出
// 不同答案。从插件走不到（limitsPayload 发出去的永远是范围内整数），但两张表
// 就是为了防漂而存在的，只比数不比算法等于只防了一半。
const CLAMP_CASES = [
  ["", 0], [" ", 0], [null, 0], [undefined, 0], [true, 0], [false, 0],
  ["abc", 0], ["一百", 0], [[5], 0], [{}, 0], ["0x10", 0], ["1e999", 0],
  ["NaN", 0], [Infinity, 0], [-Infinity, 0],
  [2.6, 0], [-2.6, 0], [0, 0], [-1, 0], [7, 0], [999999, 0], ["7", 0], [" 7 ", 0],
];
const pyOut = JSON.parse(
  execFileSync("python3", [join(here, "check-limits-py.py")], {
    cwd: repo,
    encoding: "utf8",
    env: { ...process.env, SOCRATES_AGENT: repo },
  }),
);
for (const [raw] of CLAMP_CASES) {
  for (const k of ["max_tool_rounds", "probe_timeout_s", "cross_book_reads"]) {
    const ts = S.clampLimit(k, raw);
    const py = pyOut[`${k}|${JSON.stringify(raw ?? null)}`];
    check(`夹紧一致 ${k}(${JSON.stringify(raw ?? null)}) → ${ts}`, ts === py);
  }
}

// 全默认时请求体里连 limits 这个键都不该出现
check(
  "一个数都没动 → limitsPayload 返回 undefined（老读者的请求体逐字节不变）",
  S.limitsPayload(S.DEFAULT_SETTINGS) === undefined,
);
check(
  "改过一个就只发那一个",
  JSON.stringify(
    S.limitsPayload({ ...S.DEFAULT_SETTINGS, limits: { ...S.DEFAULT_SETTINGS.limits, max_tool_rounds: 7 } }),
  ) === '{"max_tool_rounds":7}',
);
// 空串走默认，不是走 0——把「清空输入框」变成「上限设成 0」是最难查的一种
check("空串走默认不走 0", S.clampLimit("cross_book_reads", "") === 8);
check("非数字走默认", S.clampLimit("cross_book_reads", "八次") === 8);
check("超范围夹紧", S.clampLimit("probe_concurrency", 999) === 8);
check("负数夹到下限", S.clampLimit("max_tool_rounds", -5) === 1);

let bad = 0;
for (const [name, pass] of checks) {
  if (!pass) bad++;
  console.log(`  ${pass ? "ok  " : "FAIL"} ${name}`);
}
console.log(bad ? `\n${bad}/${checks.length} 项失败` : `\n${checks.length} 项全部通过`);
process.exit(bad ? 1 : 0);
