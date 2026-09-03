/**
 * HTTP 错误的形状。跑的是 src/api.ts 编译出来的真代码。
 *
 * 为什么值得单独一份：v0.12.4 之前 `api.ts` 抛的是裸 `Error(detail)`，而 detail
 * 是**本地化**的服务端文案。于是 `deeppoll.ts` 里那句 `message.includes("404")`
 * 永远为假——一条写了、看着有、其实早就死了的终止条件。会话按时间清理之后
 * 404 会变成常态路径，`send()` / `doApprove()` 的兜底全都建立在
 * 「调用方能认出**是哪一种** 404」这个前提上，所以这个前提必须有东西守着。
 *
 * v0.12.6 补的那半条：光看状态码还不够。`_meta_or_404` 对「笔记被改名或移走」
 * 也抛 404，而那条 detail 里有唯一能救读者的一句「请重新框选一次」。所以
 * 「会话没了」那种 404 的 detail 是 `{code:"session_gone", message}`，
 * 别的 404 还是一句字符串——下面有一条专门守这个分界。
 */
import { build } from "esbuild";
import { createRequire } from "node:module";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const out = join(mkdtempSync(join(tmpdir(), "sp-api-")), "api.cjs");
await build({
  entryPoints: ["src/api.ts"],
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
    ? { getLanguage: () => "zh", Plugin: class {}, PluginSettingTab: class {}, Setting: class {}, App: class {} }
    : origLoad(req, parent, isMain);
globalThis.window = { localStorage: { getItem: () => null } };
globalThis.document = { documentElement: { lang: "" } };

const api = require(out);
const checks = [];
const check = (name, pass) => checks.push([name, Boolean(pass)]);

/** 假 fetch。`body` 走 res.json()，`stream` 走 SSE 那条路。 */
let seen = [];
function stubFetch({ status, detail, ok }) {
  globalThis.fetch = async (url, init) => {
    seen.push({ url: String(url), method: init?.method || "GET", body: init?.body });
    return {
      ok: ok ?? status < 400,
      status,
      statusText: "stub",
      body: null,
      json: async () => (detail === undefined ? {} : { detail }),
    };
  };
}

const grab = async (fn) => {
  try {
    await fn();
    return null;
  } catch (e) {
    return e;
  }
};

// ── 普通 JSON 路由 ────────────────────────────────────────────────
const GONE = { code: "session_gone", message: "会话不存在" };
stubFetch({ status: 404, detail: GONE });
let err = await grab(() => api.makeApi("http://x").getSession("abc"));
check("404 抛的是 ApiError", err instanceof api.ApiError);
check("状态码带出来了", err?.status === 404);
check("服务端文案原样保留", err?.message === "会话不存在");
check("机器码带出来了", err?.code === "session_gone");
check("isGone 认得它", api.isGone(err) === true);
// 这一条是病根本身：靠字符串匹配的写法在这里必然漏
check("本地化文案里根本没有 “404” 三个字", !String(err?.message).includes("404"));

// **两种 404 的分界线。** 读者在 Obsidian 里改个笔记名就会走到这条上，
// 而这条 detail 里那句「请重新框选一次」是唯一能救他的指引——被当成
// 「会话已归档」的话，指引就被吞了，换成一句假因由。
stubFetch({ status: 404, detail: "原文找不到了，请重新框选一次" });
err = await grab(() => api.makeApi("http://x").getSession("abc"));
check("笔记被改名的 404 不算「会话没了」", api.isGone(err) === false);
check("字符串 detail 照旧原样保留", err?.message === "原文找不到了，请重新框选一次");
check("没有 code 时是空串不是 undefined", err?.code === "");

stubFetch({ status: 500, detail: "internal" });
err = await grab(() => api.makeApi("http://x").getSession("abc"));
check("500 不是「没了」", api.isGone(err) === false);
check("500 的状态码也带出来", err?.status === 500);

stubFetch({
  status: 409,
  detail: { code: "approval_pending", message: "有一次编辑在等你审批，先点允许或拒绝。" },
});
err = await grab(() => api.makeApi("http://x").compactSession("abc"));
check("compact pending 是 409", err instanceof api.ApiError && err.status === 409);
check("compact pending 带机器码", err?.code === "approval_pending");
check("compact pending 文案原样保留", err?.message === "有一次编辑在等你审批，先点允许或拒绝。");

check("isGone 对普通 Error 是 false", api.isGone(new Error("404")) === false);
check("isGone 对 undefined 不炸", api.isGone(undefined) === false);
// 光带 code 不带 404，或者光 404 不带 code，都不算
check(
  "code 对了但状态码不对，不算「没了」",
  api.isGone(new api.ApiError(500, "x", "session_gone")) === false,
);

// ── SSE 那条路（streamChat / streamApprove 走的是另一个错误分支）──
stubFetch({ status: 404, detail: GONE, ok: false });
err = await grab(() =>
  api.streamChat("http://x", {
    session_id: "s",
    selected_text: "t",
    start_line: 1,
    end_line: 1,
    chip: "socratic",
    user_text: "",
  }, () => {}),
);
check("streamChat 的 404 也是 ApiError", err instanceof api.ApiError && err.status === 404);
// 两处出错点必须同源：SSE 那条也得会拆 {code,message}，否则又是「两个闸不同源」
check("streamChat 的 404 也拆得出 code", api.isGone(err) === true);

err = await grab(() =>
  api.streamApprove("http://x", { session_id: "s", pending_id: "p", allow: true }, () => {}),
);
check("streamApprove 的 404 也是 ApiError", err instanceof api.ApiError && err.status === 404);
check("streamApprove 的 404 也拆得出 code", api.isGone(err) === true);

// ── 启动清理那一枪 ────────────────────────────────────────────────
seen = [];
stubFetch({ status: 200 });
await api.purgeExpired("http://x/");
check("purgeExpired 打的是 POST /v1/maintenance/purge", seen[0]?.method === "POST");
check("base 尾斜杠不会变成双斜杠", seen[0]?.url === "http://x/v1/maintenance/purge");

seen = [];
stubFetch({ status: 404, detail: "Not Found" });
err = await grab(() => api.purgeExpired("http://x"));
check("旧 sidecar 上 purgeExpired 抛得出来（调用方负责吞）", err?.status === 404);
check("旧 sidecar 没这个路由 ≠ 会话没了", api.isGone(err) === false);

// ── v0.25.0 学习画像：三个新路由的请求形状 ─────────────────────────
// 编码那一枪的 body 与 /v1/chat 同源（llmPayload）。这里守的不是「有没有发对」，
// 而是「**钥匙一个字都不上行**」——设置页那把 apiKey 只走 PUT /v1/llm/key。
const CANARY = "sk-canary-never-travels-0001";
const settings = {
  baseUrl: "https://api.deepseek.com/",
  model: "deepseek-chat",
  thinking: "high",
  vision: false,
  fastMode: false,
  provider: "auto",
  limits: {},
  apiKey: CANARY,
  fastApiKey: CANARY,
};
seen = [];
stubFetch({ status: 200 });
await api.makeApi("http://x").codeProfile("hb-1", settings);
let body = JSON.parse(seen[0]?.body || "{}");
check("codeProfile 打的是 POST …/profile/code", seen[0]?.method === "POST" && seen[0]?.url === "http://x/v1/handbooks/hb-1/profile/code");
check("codeProfile 的 body 没有 api_key", !("api_key" in body) && !("apiKey" in body));
check("codeProfile 的 body 里没有金丝雀钥匙", !String(seen[0]?.body).includes(CANARY));
check("codeProfile 带 thinking（一律主模型，思考档同主对话）", body.thinking === "high");
check("codeProfile 带 base_url 去尾斜杠", body.base_url === "https://api.deepseek.com");
check("codeProfile 缺省 max_batches=3", body.max_batches === 3);
check("codeProfile 不重算时 body 里没有 force 键", !("force" in body));
check("codeProfile 没改过旋钮时 body 里没有 limits 键", !("limits" in body));

seen = [];
await api.makeApi("http://x").codeProfile("hb-1", settings, { force: true, maxBatches: 1 });
body = JSON.parse(seen[0]?.body || "{}");
check("重算时 force:true 出现", body.force === true);
check("maxBatches 透传", body.max_batches === 1);

seen = [];
await api.makeApi("http://x").getProfile("hb-1");
check("getProfile 是 GET …/profile", seen[0]?.method === "GET" && seen[0]?.url === "http://x/v1/handbooks/hb-1/profile");

seen = [];
await api.makeApi("http://x").listProfiles("/a b/中文 vault");
check("listProfiles 的 vault_root 已编码", seen[0]?.url === "http://x/v1/profiles?vault_root=" + encodeURIComponent("/a b/中文 vault"));

stubFetch({ status: 400, detail: { code: "bad-key", message: "钥匙被节点拒了" } });
err = await grab(() => api.makeApi("http://x").codeProfile("hb-1", settings));
check("codeProfile 的 400 是 ApiError 且带厂商码", err instanceof api.ApiError && err.code === "bad-key" && err.message === "钥匙被节点拒了");

let bad = 0;
for (const [name, pass] of checks) {
  if (!pass) bad++;
  console.log(`  ${pass ? "ok  " : "FAIL"} ${name}`);
}
console.log(`\n${bad ? `${bad}/${checks.length} 项失败` : `${checks.length} 项全部通过`}`);
process.exit(bad ? 1 : 0);
