/**
 * 折叠块标签的展示层看门狗。跑的是 src/foldview.ts 编译出来的真代码。
 *
 * v0.18.3：手册体例含 <details> 折叠块，随选区邻域进 prompt；模型偶尔回显，
 * 聊天面板的流式 setText / 引文条就会裸出 "details" 字样。三条上屏路径
 * 统一剥标签（visibleReply 只剥独占一行的，内联代码提及必须活）；发给
 * sidecar 的原文一字不动。这份闸守的就是这些边界——谁改坏了当场红。
 */
import { build } from "esbuild";
import { createRequire } from "node:module";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const out = join(mkdtempSync(join(tmpdir(), "sp-fold-")), "foldview.cjs");
await build({
  entryPoints: ["src/foldview.ts"],
  bundle: true,
  format: "cjs",
  outfile: out,
  logLevel: "error",
});
const require = createRequire(import.meta.url);
const { stripFoldTags, visibleReply } = require(out);

const checks = [];
const check = (name, pass) => checks.push([name, Boolean(pass)]);

// ── stripFoldTags：流式预览（逐标签全剥，含残缺尾巴） ──
check(
  "完整标签剥掉、内文与摘要文本保留",
  stripFoldTags("<details>\n\n<summary>🔍 实例 1</summary>\n\n内文\n\n</details>") ===
    "\n\n🔍 实例 1\n\n内文\n\n",
);
check("带属性的标签也剥", stripFoldTags('<summary class="x">t</summary>') === "t");
check(
  "写到一半的 <deta 尾巴剪掉",
  stripFoldTags("前文\n<deta") === "前文\n" && stripFoldTags("a</summ") === "a",
);
check("别的标签尾巴不动", stripFoldTags("前文<bod") === "前文<bod");
check("孤立 < 不动", stripFoldTags("1 < 2") === "1 < 2");

// ── visibleReply：最终渲染（只剥独占一行的） ──
const wellFormed = "答案在此。\n\n<details>\n\n<summary>实例</summary>\n\n折叠内文\n\n</details>\n\n完。";
const vr = visibleReply(wellFormed);
check(
  "独占一行的标签剥、摘要文本成正文（空行残留无害，折叠后比对）",
  vr.replace(/\n{2,}/g, "\n\n") === "答案在此。\n\n实例\n\n折叠内文\n\n完。" &&
    !vr.includes("details") && !vr.includes("summary"),
);
check(
  "同行 <summary>文本</summary> 解包成纯文本",
  visibleReply("<summary>看点</summary>") === "看点" &&
    visibleReply("<details><summary>看点</summary></details>") === "看点",
);
check(
  "同行内联提及（写回流程的合法表达）必须活",
  visibleReply("把它包成 `<details>` 再插入。").includes("`<details>`"),
);
check("pen:chips 注释照旧整块剥", !visibleReply("答\n<!--pen:chips\n- q\n-->\n").includes("pen:chips"));

let bad = 0;
for (const [name, pass] of checks) {
  if (!pass) bad++;
  console.log(`${pass ? "  ok  " : "  FAIL"} ${name}`);
}
console.log(bad ? `\n${bad}/${checks.length} 项失败` : `\n${checks.length} 项全部通过`);
process.exit(bad ? 1 : 0);
