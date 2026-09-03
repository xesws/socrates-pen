/**
 * 阅读视图引文 → 原文行号的看门狗。跑的是 src/locate.ts 编译出来的真代码。
 *
 * v0.24.0：读者在阅读视图划到的是渲染后的字——粗体的星号、代码的反引号、
 * 表格的竖线、标题的井号、链接的方括号全没了。只折叠空白再找子串，在一本
 * 真实手册上划的 13 段**一段都对不上（0/13）**，于是行号一律回退成 1，
 * sidecar 把这些轮次全记在「封面」，一场 65 轮的对话 36 轮记丢。
 * 现在两边只留字母数字汉字再比：整段 → 开头 48 → 结尾 48；找不到返回 null，
 * 不再假装是第 1 行。这份闸守的就是这几条边界。
 */
import { build } from "esbuild";
import { createRequire } from "node:module";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const out = join(mkdtempSync(join(tmpdir(), "sp-locate-")), "locate.cjs");
await build({
  entryPoints: ["src/locate.ts"],
  bundle: true,
  format: "cjs",
  outfile: out,
  logLevel: "error",
});
const require = createRequire(import.meta.url);
const { linesFromQuote, squash, collapseWs } = require(out);

const checks = [];
const check = (name, pass) => checks.push([name, Boolean(pass)]);
const same = (got, want) =>
  !!got && got.startLine === want.startLine && got.endLine === want.endLine;

// 一段带着手册体例里全部渲染差异的原文。行号从 1 起。
const md = [
  /* 1 */ "# Level 7 — 唯一的出口",
  /* 2 */ "",
  /* 3 */ "## 第三拍 · 出身：别人把厨房放在哪",
  /* 4 */ "",
  /* 5 */ "第三层，**API 客户端**，藏在 `self._openai()` 背后。",
  /* 6 */ "它就是 `from openai import OpenAI` 构造出的那个对象，[见文档](https://x.y/z)。",
  /* 7 */ "",
  /* 8 */ "| 对象 | 类型 | 谁拼的 |",
  /* 9 */ "| --- | --- | --- |",
  /* 10 */ "| `ChatCompletionChunk` | pydantic DTO | 服务器 |",
  /* 11 */ "| `ChatCompletion` | 顶层容器 | 服务器 |",
  /* 12 */ "",
  /* 13 */ "```python",
  /* 14 */ "resp = client.chat.completions.create(model=m, stream=True)",
  /* 15 */ "```",
  /* 16 */ "",
  /* 17 */ "<details>",
  /* 18 */ "<summary>🔍 实例 1：磁带在哪</summary>",
  /* 19 */ "",
  /* 20 */ "磁带不是缓存，它是一条已知正确的模型轨迹。",
  /* 21 */ "</details>",
].join("\n");

// ── 渲染后的字对回原文 ──
check(
  "粗体星号、反引号没了照样对上",
  same(linesFromQuote(md, "第三层，API 客户端，藏在 self._openai() 背后。"), { startLine: 5, endLine: 5 }),
);
check(
  "链接只剩文字照样对上",
  same(linesFromQuote(md, "它就是 from openai import OpenAI 构造出的那个对象，见文档。"), {
    startLine: 6,
    endLine: 6,
  }),
);
check(
  "表格一行渲染成制表符分隔照样对上",
  same(linesFromQuote(md, "ChatCompletion\t顶层容器\t服务器"), { startLine: 11, endLine: 11 }),
);
check(
  "跨两行的选区报出起止行",
  same(
    linesFromQuote(md, "藏在 self._openai() 背后。\n它就是 from openai import OpenAI 构造出的"),
    { startLine: 5, endLine: 6 },
  ),
);
check("标题没了井号照样对上", same(linesFromQuote(md, "第三拍 · 出身：别人把厨房放在哪"), { startLine: 3, endLine: 3 }));
check(
  "代码块里的一行照样对上",
  same(linesFromQuote(md, "resp = client.chat.completions.create(model=m, stream=True)"), {
    startLine: 14,
    endLine: 14,
  }),
);
check("折叠块摘要（带 emoji）照样对上", same(linesFromQuote(md, "🔍 实例 1：磁带在哪"), { startLine: 18, endLine: 18 }));

// ── 探针：整段对不上时救头救尾 ──
const twoLines = "第三层，API 客户端，藏在 self._openai() 背后。它就是 from openai import OpenAI 构造出的那个对象，见文档。";
const garbage = "这一大段是选区尾巴跨进了被改写过的段落之后残留的字它在原文里已经不存在了所以整段对不上";
check(
  "选区尾巴已不在原文：开头 48 字救回头几行",
  same(linesFromQuote(md, twoLines + garbage), { startLine: 5, endLine: 6 }),
);
check(
  "选区开头已不在原文：结尾 48 字救回尾几行",
  same(linesFromQuote(md, garbage + twoLines), { startLine: 5, endLine: 6 }),
);

// ── 诚实：找不到就是 null，不是第 1 行 ──
check("原文里没有的话返回 null", linesFromQuote(md, "这句话手册里没有对应的原文出现过一次") === null);
check("不到 4 个有效字符返回 null", linesFromQuote(md, "a b") === null && linesFromQuote(md, "，。！？") === null);

// ── 压扁规则本身 ──
check("squash 只留字母数字汉字", squash("a**b** `c`|d\t#e（f）") === "abcdef");
check("collapseWs 仍导出（selection.ts 用它取选区文本）", collapseWs(" a \n b ") === "a b");

let bad = 0;
for (const [name, pass] of checks) {
  if (!pass) bad++;
  console.log(`${pass ? "  ok  " : "  FAIL"} ${name}`);
}
console.log(bad ? `\n${bad}/${checks.length} 项失败` : `\n${checks.length} 项全部通过`);
process.exit(bad ? 1 : 0);
