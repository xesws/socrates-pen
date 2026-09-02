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

// v0.22.0：**现在有两把钥匙了**，同一条通道得守两遍。快模型那把走
// PUT /v1/llm/fast-key，和基座那把一样永不随请求体上行。
const smuggledFast = { ...mod.DEFAULT_SETTINGS, fastApiKey: "ck-canary-leak-5678" };
const fastPayload = mod.llmPayload(smuggledFast);
check(
  "llmPayload 产物不含 fast_api_key 键",
  !("fast_api_key" in fastPayload) &&
    !JSON.stringify(fastPayload).includes("ck-canary-leak-5678"),
);
// 开关关着时**连节点和型号都不发**：「关掉 Fast Mode 之后请求体与上一版
// 逐字节一致」是这一版对老路的承诺，多两个键就不叫逐字节了。
check(
  "Fast Mode 关着时请求体里没有任何 fast_* 键",
  !Object.keys(fastPayload).some((k) => k.startsWith("fast")),
);
const fastOn = mod.llmPayload({ ...smuggledFast, fastMode: true });
check(
  "开着时节点和型号照发（钥匙之外的那两格）",
  fastOn.fast_base_url === mod.DEFAULT_SETTINGS.fastBaseUrl &&
    fastOn.fast_model === mod.DEFAULT_SETTINGS.fastModel,
);
check(
  "开着时也一样没有 fast_api_key",
  !("fast_api_key" in fastOn) && !JSON.stringify(fastOn).includes("ck-canary-leak-5678"),
);
check("DEFAULT_SETTINGS 没有 fastApiKey 字段", !("fastApiKey" in mod.DEFAULT_SETTINGS));
check("DEFAULT_SETTINGS.fastMode 默认关", mod.DEFAULT_SETTINGS.fastMode === false);
// **不在这儿断言「落盘形状会滤掉 fastApiKey」**：persistableSettings 从没
// 承诺过滤未知字段，apiKey 那条也一样——它靠的是 main.ts 装载时那句
// delete。断言一条代码没做的保证，只会在将来诱导别人去加一段防空气的代码。
// 真正该守的是上面那条：这个字段永远不会被 llmPayload 发上行。

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
