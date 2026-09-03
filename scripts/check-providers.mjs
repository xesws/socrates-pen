/**
 * 厂商名单：前端那张下拉表 == 后端 pen/providers.py 的 PROVIDERS。
 *
 * 为什么要机械守：读者在下拉里选了一个后端不认的键，**不会报错**——
 * `provider_for()` 认不得就当没选、静默落回 generic。也就是说这种失效
 * 没有任何症状：设置页显示选着 Kimi，实际按通用写法发。
 * 那正是这个仓反复踩过的「两个闸不同源」，只是这次连红字都没有。
 *
 * 顺带守两件跑不掉的：每一家都得有 zh/en 提示语（下拉不解释脾气就只是个装饰），
 * 以及 llmPayload 在"自动"档一个键都不发（老库升级后请求体逐字节不变）。
 */
import { build } from "esbuild";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const out = join(mkdtempSync(join(tmpdir(), "sp-prov-")), "settings.cjs");
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

// ── 后端那张表。照 check-limits.mjs 的手法用正则从 py 文本里抠 ──────────
const py = readFileSync("pen/providers.py", "utf8");
// `PROVIDERS: dict[str, Provider] = { p.key: p for p in ( _X, _Y, … ) }`
const tupleBody = py.slice(py.indexOf("PROVIDERS: dict[str, Provider]"));
const varNames = [...tupleBody.slice(0, tupleBody.indexOf(")")).matchAll(/_([A-Z][A-Z0-9_]*),/g)]
  .map((m) => `_${m[1]}`);
const backend = varNames.map((v) => {
  const at = py.indexOf(`${v} = Provider(`);
  // 窗口卡到下一个 Provider 定义为止，免得某一家漏写 base_url 时把下一家的偷过来。
  const next = py.indexOf(" = Provider(", at + 12);
  const body = py.slice(at, next < 0 ? py.length : next);
  const key = /key=(?:"([a-z]+)"|([A-Z]+))/.exec(body);
  const base = /base_url="([^"]*)"/.exec(body);
  return {
    // key=GENERIC 那一格用的是模块常量，值就是 "generic"。
    key: key[1] || (key[2] === "GENERIC" ? "generic" : key[2]),
    base: base ? base[1] : null,
  };
});

check(
  `后端表抠得出来（${backend.length} 家）`,
  backend.length >= 8 && backend.every((b) => b.key && b.base !== null),
);

// ── 前端那张表 ─────────────────────────────────────────────────────
const front = mod.PROVIDERS.map((p) => p.key);
check("前端第一档是自动", front[0] === "auto");
check(
  `前后端厂商逐键一致（${backend.length} 家）`,
  JSON.stringify(front.slice(1)) === JSON.stringify(backend.map((b) => b.key)),
);

// ── 官方地址也是一条规则，而它现在写了两遍 ──────────────────────────
//
// 前端拿它预填 Base URL，后端 Provider.base_url 是同一件事的另一份拷贝。
// 两边飘开的后果比键名飘开更隐蔽：读者选了 Kimi，预填进去一个**过期的**
// 地址，钥匙是对的、型号是对的，只有主机名不对——报出来是一个 404
// 「这个节点没有这个型号」，指向完全错误的方向。
const baseOf = Object.fromEntries(backend.map((b) => [b.key, b.base]));
const drift = mod.PROVIDERS.filter((p) => p.key !== "auto" && p.base !== baseOf[p.key]).map(
  (p) => `${p.key}(前 ${p.base || "空"} ≠ 后 ${baseOf[p.key] ?? "缺"})`,
);
check(`前后端官方地址逐字一致（飘了 ${drift.join("; ") || "无"}）`, drift.length === 0);

// ── 提示语。每一家都得有，两种语言都得有 ────────────────────────────
const dicts = { zh: readFileSync("src/i18n/zh.ts", "utf8"), en: readFileSync("src/i18n/en.ts", "utf8") };
for (const [lang, src] of Object.entries(dicts)) {
  const block = src.slice(src.indexOf("providerHint: {"), src.indexOf("providerHint: {") + 4000);
  const missing = front.filter((k) => !new RegExp(`\\b${k}:\\s*"[^"]`).test(block));
  check(`${lang}: 每一家都有提示语（缺 ${missing.join(", ") || "无"}）`, missing.length === 0);
}

// ── 上行：默认档一个键都不发 ────────────────────────────────────────
const S = mod.DEFAULT_SETTINGS;
check("DEFAULT_SETTINGS 的厂商是自动", S.provider === "auto" && S.fastProvider === "auto");
const auto = mod.llmPayload(S);
check(
  "自动档的请求体里没有 provider 键（老库逐字节不变）",
  !("provider" in auto) && !("fast_provider" in auto),
);
const picked = mod.llmPayload({ ...S, provider: "google" });
check("选了一家就发上去", picked.provider === "google");
check(
  "Fast Mode 关着时不发 fast_provider",
  !("fast_provider" in mod.llmPayload({ ...S, fastProvider: "meta" })),
);
check(
  "Fast Mode 开着才发",
  mod.llmPayload({ ...S, fastMode: true, fastProvider: "meta" }).fast_provider === "meta",
);
check(
  "认不得的厂商当没选",
  !("provider" in mod.llmPayload({ ...S, provider: "no-such-vendor" })),
);

// ── 预填：绝不覆盖读者自己打的地址 ──────────────────────────────────
check("空着就填", mod.shouldPrefillBase("") === true);
check("还停在另一家的默认值上就填", mod.shouldPrefillBase("https://api.deepseek.com") === true);
check("末尾斜杠不影响判定", mod.shouldPrefillBase("https://api.deepseek.com/") === true);
check(
  "读者自己打的网关地址不许被冲掉",
  mod.shouldPrefillBase("https://my-gateway.example/v1") === false,
);
check("每一家（自动 / 通用除外）都预填得出地址", mod.PROVIDERS.every((p) =>
  p.key === "auto" || p.key === "generic"
    ? mod.providerBase(p.key) === ""
    : mod.providerBase(p.key).startsWith("https://"),
));

let bad = 0;
for (const [name, pass] of checks) {
  if (!pass) bad++;
  console.log(`  ${pass ? "ok  " : "FAIL"} ${name}`);
}
console.log(bad ? `\n${bad}/${checks.length} 项失败` : `\n${checks.length} 项全部通过`);
process.exit(bad ? 1 : 0);
