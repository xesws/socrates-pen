/**
 * 深挖轮询的终止条件。跑的是 src/deeppoll.ts 编译出来的真代码，不是复刻——
 * 这一段是整个功能里最容易悄悄泄漏的地方：少一个终止条件，读者关掉面板
 * 之后它还会在后台一直敲 sidecar。
 */
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
// 入口是一段现编的门面，把 deeppoll 和 ApiError 打进**同一个** bundle。
// 分两次 build 的话会得到两个不同的 ApiError 类，`instanceof` 恒假——
// 那样这份看门狗测的又是一个和产物无关的幻觉（上一版正是如此，见下）。
const out = await build({
  stdin: {
    contents:
      'export * from "./src/deeppoll.ts";\nexport { ApiError } from "./src/apierror.ts";\n',
    resolveDir: resolve(here, ".."),
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
const { pollDeep, mergeDeep, keepDeep, dropAsked, MAX_VISIBLE_DEEP, DEEP_POLL_BUDGET_MS, ApiError } =
  mod;

const checks = [];
const check = (name, pass) => checks.push([name, Boolean(pass)]);
const q = (t) => ({ id: t, kind: "deep", text: t, why: "w" });

/** 把 90 秒预算压成毫秒级：sleep 走真定时器，now 走虚拟时钟。 */
function harness(inbox, opts = {}) {
  const st = { calls: 0, items: [], cursor: 0, painted: 0, alive: true, clock: 0 };
  const step = DEEP_POLL_BUDGET_MS / (opts.ticks ?? 12);
  return {
    st,
    run: () =>
      pollDeep({
        fetch: (since) => {
          st.calls++;
          return inbox(since, st.calls, st);
        },
        alive: () => st.alive,
        since: () => st.cursor,
        sleep: async () => {
          st.clock += step;
        },
        now: () => st.clock,
        onItems: (items, cursor) => {
          st.items = mergeDeep(st.items, items);   // 跑真的 mergeDeep，不是复刻
          st.cursor = cursor;
          st.painted++;
        },
        onBudget: (b) => {
          st.budget = b;
        },
        onSpend: (row) => {
          st.spend = row;
        },
      }),
  };
}

let h = harness(async (_s, n) =>
  n < 3 ? { items: [], cursor: 0, running: ["p"] } : { items: [q("跨关那个问题？")], cursor: 5, running: [] },
);
await h.run();
check("running 变空后停止，深题上屏", h.st.calls === 3 && h.st.items.length === 1 && h.st.cursor === 5);

h = harness(async () => ({ items: [], cursor: 0, running: ["p"] }), { ticks: 12 });
await h.run();
check("服务端一直 running 时到点自停", h.st.calls === 12);

// v0.12.4：抛的必须是**产物真会抛的那种错**。上一版这里造的是
// `new Error("HTTP 404 unknown session")`，而 api.ts 抛的 detail 是本地化文案
// （「会话不存在」），一个 "404" 字样都没有——看门狗和实现各自绿着，
// 中间那条终止条件其实是死的。
h = harness(async () => {
  throw new ApiError(404, "会话不存在", "session_gone");
});
await h.run();
check("404 立刻停（会话没了）", h.st.calls === 1);

// 反向：本地化文案里没有 "404"，靠字符串匹配的写法在这条上会红。
h = harness(async () => {
  throw new ApiError(404, "unknown session", "session_gone");
});
await h.run();
check("404 判的是状态码不是文案", h.st.calls === 1);

// **反向**：不带 code 的 404 不许被当成「会话没了」。`/deep` 目前只有一处
// 404 源，所以这条现在恒绿——它守的是将来：那条路一旦长出第二种 404
// （比如某天也去查手册），只看状态码的写法会静默把轮询掐死。
// check-api.mjs 里有对称的一条，两边形状要一样。
h = harness(async () => {
  throw new ApiError(404, "原文找不到了，请重新框选一次");
});
await h.run();
check("不带 code 的 404 不当成会话没了", h.st.calls === 3);

// 500 不是「没了」，该走重试那条路。
h = harness(async () => {
  throw new ApiError(500, "internal");
});
await h.run();
check("500 不当成会话没了，照常重试到放弃", h.st.calls === 3);

h = harness(async () => {
  throw new Error("ECONNREFUSED");
});
await h.run();
check("连失败 3 次放弃（sidecar 连不上）", h.st.calls === 3);

h = harness(async (_s, _n, st) => {
  st.alive = false;
  return { items: [q("x")], cursor: 1, running: ["p"] };
});
await h.run();
check("视图关掉后不再上屏", h.st.items.length === 0 && h.st.painted === 0);

h = harness(async (_s, n) =>
  n === 1 ? { items: [q("同一条？")], cursor: 3, running: ["p"] } : { items: [q("同一条？")], cursor: 3, running: [] },
);
await h.run();
check("重复投递不会变成两个按钮", h.st.items.length === 1);

h = harness(async (since) => ({ items: since === 0 ? [q("甲？")] : [], cursor: 7, running: [] }));
await h.run();
check("收到后游标推进", h.st.cursor === 7);

h = harness(async () => ({ items: [], cursor: 0, running: [] }));
await h.run();
check("一开始就没有在跑的 → 敲一次就停", h.st.calls === 1);

h = harness(async () => ({ items: [], cursor: 0, running: [], budget: { used: 8, max: 8, window_used: 40, window_max: 40 } }));
await h.run();
check("配额会报给调用方（用满时读者得知道为什么深题不来了）", h.st.budget?.window_used === 40);

h = harness(async () => ({ items: [], cursor: 0, running: [] }));
await h.run();
check("没有 budget 字段也不炸", h.st.budget === undefined);

// v0.10.0：深挖花掉的 token 走同一条轮询报上来
h = harness(async () => ({
  items: [], cursor: 0, running: [],
  spend: { calls: 2, in_tokens: 26400, out_tokens: 1100, cached_tokens: 0, reasoning_tokens: 0 },
}));
await h.run();
check("深挖花销会报给调用方", h.st.spend?.in_tokens === 26400);

h = harness(async () => ({ items: [], cursor: 0, running: [] }));
await h.run();
check("旧 sidecar 不带 spend 字段也不炸", h.st.spend === undefined);

// ── mergeDeep / keepDeep 的直测 ──
const quick = (t) => ({ id: t, kind: "quick", text: t });
check(
  "mergeDeep 按文本去重",
  mergeDeep([q("甲？")], [q("甲？"), q("乙？")]).length === 2,
);
check(
  "mergeDeep 把深题排到实时题前面",
  mergeDeep([quick("实时？")], [q("深？")]).map((c) => c.kind).join() === "deep,quick",
);
check(
  "mergeDeep 守同时可见上限，留最新的",
  (() => {
    const got = mergeDeep([], [q("一？"), q("二？"), q("三？"), q("四？")]);
    const deep = got.filter((c) => c.kind === "deep");
    return deep.length === MAX_VISIBLE_DEEP && deep[deep.length - 1].text === "四？";
  })(),
);
check(
  "mergeDeep 不动实时题",
  mergeDeep([quick("甲实时？"), quick("乙实时？")], []).filter((c) => c.kind === "quick").length === 2,
);
check(
  "keepDeep 换轮时只留深题",
  keepDeep([q("深？"), quick("实时？")]).length === 1,
);
check(
  "keepDeep 也守上限",
  keepDeep([q("一？"), q("二？"), q("三？")]).length === MAX_VISIBLE_DEEP,
);

// v0.12.1 点过的深题当场消失
check(
  "点过的那条当场摘掉",
  dropAsked([q("甲？"), q("乙？")], "甲？").length === 1,
);
check(
  "摘的是点中的那条，不是随便一条",
  dropAsked([q("甲？"), q("乙？")], "甲？")[0].text === "乙？",
);
check("没点中就一条都不动", dropAsked([q("甲？")], "别的话").length === 1);
check("空文本不动（点固定芯片走的是这条）", dropAsked([q("甲？")], "").length === 1);
check(
  "摘掉之后 keepDeep 不会把它捞回来",
  keepDeep(dropAsked([q("甲？"), q("乙？")], "甲？")).every((c) => c.text !== "甲？"),
);

let bad = 0;
for (const [name, pass] of checks) {
  if (!pass) bad++;
  console.log(`${pass ? "  ok  " : "  FAIL"} ${name}`);
}
console.log(bad ? `\n${bad}/${checks.length} 项失败` : `\n${checks.length} 项全部通过`);
process.exit(bad ? 1 : 0);
