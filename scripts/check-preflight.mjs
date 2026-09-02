/**
 * 体检什么时候花钱。跑的是 src/settings.ts 编译出来的真代码。
 *
 * 配置体检（POST /v1/llm/preflight）是一次**真实的 API 调用**：它让 sidecar
 * 往读者的节点打一枪，好在发对话之前就说清「钥匙是不是废的、这个节点有没有
 * 这个型号、它收不收图」。这也正是它危险的地方——面板的 probeHealth() 每划
 * 一次选区就会被调一次（PenView 的 captureSelection），体检要是跟着跑，读者
 * 就成了按选区计费。
 *
 * 唯一拦着这件事的是 `configSignature()`：签名没变就一枪不打。所以这份看门狗
 * 只守两件事：
 *   1. 八项配置里**任何一项**变了，签名必须变（漏一项 = 那一项的热切换失效，
 *      读者改完看不到新判词，正是 v0.22.2 那个 bug 的形状）
 *   2. 不相干的设置变了，签名**不许**变（多算一项 = 按选区烧钱）
 */
import { build } from "esbuild";
import { createRequire } from "node:module";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const out = join(mkdtempSync(join(tmpdir(), "sp-pf-")), "settings.cjs");
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
    ? { getLanguage: () => "zh", Notice: class {}, PluginSettingTab: class {}, Setting: class {} }
    : origLoad(req, parent, isMain);
globalThis.window = { localStorage: { getItem: () => null } };
globalThis.document = { documentElement: { lang: "" } };
const mod = require(out);

const checks = [];
const check = (name, pass) => checks.push([name, Boolean(pass)]);

const S = mod.DEFAULT_SETTINGS;
const sig = (over = {}, tail = "abcd", fastTail = "wxyz") =>
  mod.configSignature({ ...S, ...over }, tail, fastTail);
const BASE = sig();

// ① 这八项每一项都得让签名变。**每加一格上行的模型配置，这张表要跟着长。**
const MOVES = [
  ["Base URL", { baseUrl: "https://other.example" }],
  ["model", { model: "some-other-model" }],
  ["图像理解", { vision: !S.vision }],
  ["Fast Mode 开关", { fastMode: !S.fastMode }],
  ["Fast Base URL", { fastBaseUrl: "https://fast.other.example" }],
  ["Fast model", { fastModel: "other-fast" }],
];
for (const [name, over] of MOVES) {
  check(`改${name} → 重新体检`, sig(over) !== BASE);
}
check("换了一把基座钥匙 → 重新体检", sig({}, "zzzz") !== BASE);
check("换了一把快模型钥匙 → 重新体检", sig({}, "abcd", "0000") !== BASE);

// ② 不相干的设置不许触发体检。多算一项就是按操作烧钱。
const NOISE = [
  ["sidecarUrl", { sidecarUrl: "http://127.0.0.1:9999" }],
  ["thinking 档", { thinking: S.thinking === "off" ? "high" : "off" }],
  ["语言", { lang: "en" }],
];
for (const [name, over] of NOISE) {
  check(`改${name} → 不打枪`, sig(over) === BASE);
}
check("什么都没改 → 不打枪", sig() === BASE);

// ③ 空值归一：末尾斜杠和空白不该被当成「配置变了」。
check(
  "首尾空白不算改动",
  sig({ model: `  ${S.model}  ` }) === BASE,
);

let bad = 0;
for (const [name, pass] of checks) {
  if (!pass) bad++;
  console.log(`${pass ? "  ok  " : "  FAIL"} ${name}`);
}
console.log(bad ? `\n${bad}/${checks.length} 项失败` : `\n${checks.length} 项全部通过`);
process.exit(bad ? 1 : 0);
