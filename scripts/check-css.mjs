/**
 * styles.css 的不变量。三条都是真踩过的坑。
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const css = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../styles.css"),
  "utf8",
);
const checks = [];
const check = (name, pass, extra) => checks.push([name, Boolean(pass), extra]);

// 1) 零硬编码色值：颜色一律走主题变量，否则换主题就穿帮
const hex = css.match(/#[0-9a-fA-F]{3,8}\b/g) || [];
check("零硬编码色值", hex.length === 0, hex.join(" "));

// 2) 容器查询必须写在主规则之后。同特异性下后写的赢——
//    写在前面等于没写，v0.8.0 加的两档芯片区放宽就这么白写了四个版本。
for (const sel of ["sp-chips", "sp-quote"]) {
  const base = css.indexOf(`\n.socrates-pen .${sel} {`);
  const inContainer = [...css.matchAll(/@container[^{]*\{([\s\S]*?)\n\}/g)]
    .filter((m) => m[1].includes(`.socrates-pen .${sel} {`))
    .map((m) => m.index);
  if (base < 0 || !inContainer.length) {
    check(`${sel} 的断点位置`, true, "（没有断点，跳过）");
    continue;
  }
  const bad = inContainer.filter((i) => i < base);
  check(
    `${sel} 的 @container 写在主规则之后`,
    bad.length === 0,
    bad.length ? `有 ${bad.length} 处写在前面，会被主规则覆盖` : "",
  );
}

// 3) 每个 keyframes 动画都要能被「减少动态效果」关掉
const names = [...css.matchAll(/@keyframes\s+([\w-]+)/g)].map((m) => m[1]);
const reduce = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
const used = names.filter((n) => {
  const rules = [...css.matchAll(new RegExp(`animation:[^;]*\\b${n}\\b`, "g"))];
  return rules.length > 0;
});
const uncovered = used.filter((n) => {
  // 找到用它的选择器，看 reduce 块里有没有把那个选择器的 animation 关掉
  const m = css.match(new RegExp(`([^{}]+)\\{[^{}]*animation:[^;]*\\b${n}\\b`));
  if (!m) return false;
  const sel = m[1].trim().split(",")[0].trim().split(/\s+/).pop();
  return !reduce.includes(sel);
});
check(
  "所有动画都被 prefers-reduced-motion 覆盖",
  uncovered.length === 0,
  uncovered.join(" "),
);

// v0.12.2 忙碌进度条：它是那十几秒里读者唯一看得见的东西
// 只查 reduced-motion **之前**那段：整块删掉之后，reduce 里那条关动画的规则
// 还会留着，光用 includes 查全文就是个空转看门狗（第一版就是，当场被抓）。
const beforeReduce = css.slice(0, css.indexOf("@media (prefers-reduced-motion: reduce)"));
check("进度条的主规则还在", /\.sp-bar-fill\s*\{[^}]*animation:[^}]*sp-bar-shuttle/.test(beforeReduce));
check("进度条的槽还在", /\.sp-bar\s*\{/.test(beforeReduce));
check(
  "减少动态效果时不许把它整条藏掉（整条填满比一片空白诚实）",
  /\.sp-bar-fill\s*\{[^}]*animation:\s*none[^}]*width:\s*100%/.test(
    css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)")),
  ),
);

let bad = 0;
for (const [name, pass, extra] of checks) {
  if (!pass) bad++;
  console.log(`${pass ? "  ok  " : "  FAIL"} ${name}${extra && !pass ? " — " + extra : ""}`);
}
console.log(bad ? `\n${bad}/${checks.length} 项失败` : `\n${checks.length} 项全部通过`);
process.exit(bad ? 1 : 0);
