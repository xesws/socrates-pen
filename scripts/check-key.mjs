/**
 * 钥匙守门。跑的是 src/settings.ts 编译出来的真代码。
 *
 * v0.18.0 之前 `llmPayload` 把 data.json 里的明文 apiKey 随每个请求外发，
 * 而 data.json 跟着 Sync / iCloud / git 走。现在钥匙的唯一归宿是 sidecar 家目录
 * 的 llm.json（见 pen/config.py 的托管层）。这份看门狗机械地守住两件事：
 *   1. `llmPayload` 的产物里永远没有 `api_key` 这个键——谁要是把那条通道接回去，
 *      这里当场红，不用等一次真实的泄露。
 *   2. `DEFAULT_SETTINGS` 里没有 apiKey 字段——缺省形状都不该有它。
 */
import { build } from "esbuild";
import { createRequire } from "node:module";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const out = join(mkdtempSync(join(tmpdir(), "sp-key-")), "settings.cjs");
await build({
  entryPoints: ["src/settings.ts"],
  bundle: true,
  format: "cjs",
  external: ["obsidian"],
  outfile: out,
  logLevel: "error",
});

// settings.ts 顶层只有类型与函数，但依赖链上的 i18n 会摸 window/document，
// obsidian 侧给个最小桩（同 check-i18n.mjs 的手法）。
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

// 金丝雀：哪怕调用方硬塞一个 apiKey 进来（运行时多出来的字段类型拦不住），
// 产物里也不许出现它的值或 api_key 键。
const smuggled = { ...mod.DEFAULT_SETTINGS, apiKey: "sk-canary-leak-1234" };
const payload = mod.llmPayload(smuggled);
check(
  "llmPayload 产物不含 api_key 键",
  !("api_key" in payload) && !JSON.stringify(payload).includes("sk-canary-leak-1234"),
);
check(
  "其余覆盖项照发（base_url / model / thinking / vision）",
  payload.base_url === mod.DEFAULT_SETTINGS.baseUrl &&
    payload.model === mod.DEFAULT_SETTINGS.model &&
    "thinking" in payload &&
    payload.vision === false,
);
check("DEFAULT_SETTINGS.vision 默认关", mod.DEFAULT_SETTINGS.vision === false);
check("DEFAULT_SETTINGS 没有 apiKey 字段", !("apiKey" in mod.DEFAULT_SETTINGS));

// 落盘形状（二轮复审补）：迁移未成时钥匙必须原样跟回 data.json（那是唯一
// 副本）；迁移完成后落盘形状里不许再有 apiKey——谁改坏了 persistableSettings
// 的这两个分支，这里当场红。
const pend = mod.persistableSettings(mod.DEFAULT_SETTINGS, "sk-mig-canary-9999");
check("persistableSettings：迁移未成 → apiKey 原样回写", pend.apiKey === "sk-mig-canary-9999");
const done = mod.persistableSettings(mod.DEFAULT_SETTINGS, "");
check(
  "persistableSettings：迁移完成 → 落盘形状无 apiKey",
  !("apiKey" in done) && !JSON.stringify(done).includes("sk-mig-canary-9999"),
);
check(
  "limitsPayload 不受影响",
  typeof mod.limitsPayload(mod.DEFAULT_SETTINGS) === "object" || mod.limitsPayload(mod.DEFAULT_SETTINGS) === undefined,
);

let bad = 0;
for (const [name, pass] of checks) {
  if (!pass) bad++;
  console.log(`${pass ? "  ok  " : "  FAIL"} ${name}`);
}
console.log(bad ? `\n${bad}/${checks.length} 项失败` : `\n${checks.length} 项全部通过`);
process.exit(bad ? 1 : 0);
