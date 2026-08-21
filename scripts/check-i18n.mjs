/**
 * 词表自检。`npm test` 跑它。
 *
 * 插件没有测试框架，也不值得为这点事引一个。但语言解析有几个只在真实边界上
 * 才暴露的坑（Obsidian 选英文时是 removeItem、老版本没有 getLanguage、
 * 用户加载自定义翻译时 language 存的是一个绝对路径），必须有东西守着。
 */
import { build } from "esbuild";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const out = join(mkdtempSync(join(tmpdir(), "sp-i18n-")), "i18n.cjs");
await build({
  entryPoints: ["src/i18n/index.ts"],
  bundle: true,
  format: "cjs",
  external: ["obsidian"],
  outfile: out,
  logLevel: "error",
});

// 用一个可控的 obsidian 桩注入 getLanguage 的各种返回
let fakeLang;
let store = {};
const require = createRequire(import.meta.url);
const Module = require("node:module");
const origLoad = Module._load;
Module._load = (req, parent, isMain) =>
  req === "obsidian" ? { getLanguage: () => fakeLang } : origLoad(req, parent, isMain);
globalThis.window = { localStorage: { getItem: (k) => (k in store ? store[k] : null) } };
globalThis.document = { documentElement: { lang: "" } };

const warnings = [];
const origWarn = console.warn;
console.warn = (...a) => warnings.push(a.join(" "));
const i18n = require(out);
console.warn = origWarn;

const checks = [];
const check = (name, pass) => checks.push([name, pass]);

// 语言探测的边界
fakeLang = ""; store = {};
check("Obsidian 选英文（localStorage 被 removeItem）→ en", i18n.obsidianLang() === "en");
fakeLang = "zh";
check("中文 → zh", i18n.obsidianLang() === "zh");
fakeLang = "zh-TW";
check("繁体走简体表（比退回英文更近）", i18n.obsidianLang() === "zh");
fakeLang = undefined; store = { language: "zh" };
check("Obsidian < 1.8.7 没有 getLanguage → 回落 localStorage", i18n.obsidianLang() === "zh");
store = { language: "/Users/x/custom-lang.json" };
check("自定义翻译文件（language 存的是路径）不崩", i18n.obsidianLang() === "en");
store = {}; fakeLang = "";

check("coerceLangPref 非法值 → auto", i18n.coerceLangPref("bogus") === "auto");
check("coerceLangPref 保留 en", i18n.coerceLangPref("en") === "en");
check("coerceLangPref 保留 auto", i18n.coerceLangPref("auto") === "auto");

// 切表
i18n.setLang("zh");
const zhAsk = i18n.t().btnAsk;
i18n.setLang("en");
check("t() 随 setLang 换表", zhAsk === "问" && i18n.t().btnAsk === "Ask");
check("currentLang 跟着走", i18n.currentLang() === "en");

// 后端下发值的 fallback
check("chipLabel 认识的 id → 本地化", i18n.chipLabel("socratic", "先别揭晓，问我一个问题") !== "先别揭晓，问我一个问题");
check("chipLabel 未知 id → 照抄后端", i18n.chipLabel("brand_new", "后端新芯片") === "后端新芯片");
check("chipHint 未知 id → 照抄后端", i18n.chipHint("brand_new", "新提示") === "新提示");
check("phaseText 认识的 phase → 本地化", i18n.phaseText("reading", "在翻手册…") !== "在翻手册…");
check("phaseText 未知 phase → 照抄后端", i18n.phaseText("brand_new", "新状态") === "新状态");
check("phaseText 未知且无 fallback → 空串", i18n.phaseText("x", "") === "");

// v0.8.2 深挖芯片的文案：两边都得有，且英文表不能忘了翻
i18n.setLang("zh");
const zhDeep = i18n.t().setDeepName;
i18n.setLang("en");
const enDeep = i18n.t().setDeepName;
check("深挖开关标题两边都在", Boolean(zhDeep) && Boolean(enDeep));
check("深挖开关标题确实翻过", zhDeep !== enDeep);
check("深挖说明两边都在", Boolean(i18n.t().setDeepDesc) && i18n.t().setDeepDesc !== zhDeep);
check("深挖芯片前缀是同一个记号", i18n.t().tipDeepPrefix.trim() === "\u25c6");

// v0.10.0 花销文案：紧凑计数 + 三格明细
i18n.setLang("zh");
const zhS = i18n.t();
check("12.4k 这种紧凑写法（窄侧栏三格放不下 12,400）", zhS.spendSession(12400).includes("12.4k"));
check("一万以上不留小数", zhS.spendSession(128000).includes("128k"));
check("一千以下不加 k", zhS.spendSession(840) === zhS.spendSession(840) && !zhS.spendSession(840).includes("k"));
check("本轮和本会话是两个口径，文案不能一样", zhS.spendTurn(100) !== zhS.spendSession(100));
check("明细行带得上标签和两个数", zhS.spendTipRow("主对话", 96100, 4200).includes("96.1k"));
check("缓存命中单列（它便宜十倍，是有信息量的）", zhS.spendTipCached(71200).includes("71.2k"));
check("三个用途各有名字", new Set([zhS.spendKindChat, zhS.spendKindProbe, zhS.spendKindFold]).size === 3);
i18n.setLang("en");
const enS = i18n.t();
for (const k of ["spendKindChat", "spendKindProbe", "spendKindFold", "spendTipNote"]) {
  check(`${k} 两边都在且确实翻过`, Boolean(zhS[k]) && Boolean(enS[k]) && zhS[k] !== enS[k]);
}
check("英文侧也用紧凑写法", enS.spendSession(12400).includes("12.4k"));
// 不折算货币是拍板过的。谁把货币符号写回文案，这条就红。
for (const [name, fn] of [["spendSession", enS.spendSession], ["spendTurn", enS.spendTurn]]) {
  check(`${name} 不出现货币符号`, !/[¥$€£]/.test(fn(12400)));
}

// v0.10.7 设置页两区
i18n.setLang("zh");
const zhSec = i18n.t();
i18n.setLang("en");
const enSec = i18n.t();
for (const k of ["setSecCommon", "setSecAdvanced", "setAdvancedNote"]) {
  check(`${k} 两边都在且确实翻过`, Boolean(zhSec[k]) && Boolean(enSec[k]) && zhSec[k] !== enSec[k]);
}
check("默认值提示带得上数字", zhSec.setDefaultHint(40).includes("40") && enSec.setDefaultHint(40).includes("40"));
check("不认识的旋钮名回落成键本身而不是崩", zhSec.limitName("没这个键") === "没这个键");
check("不认识的旋钮说明回落成空串", zhSec.limitDesc("没这个键") === "");

// v0.10.10 设置页那块累计统计
for (const k of ["setSecUsage", "setUsageLoading", "setUsageDown", "setUsageNote", "setUsageEmpty"]) {
  check(`${k} 两边都在且确实翻过`, Boolean(zhSec[k]) && Boolean(enSec[k]) && zhSec[k] !== enSec[k]);
}
check("累计总数带得上两个数", zhSec.setUsageTotal(12345, 7).includes("7"));
check("分项三格都在", zhSec.setUsageBreak(1, 2, 3).includes("3"));
check("说清了这是「一共」不是「这一场」", /一共/.test(zhSec.setUsageNote));
check("统计里也不出现货币符号", !/[¥$€£]/.test(zhSec.setUsageTotal(12345, 7) + enSec.setUsageTotal(12345, 7)));

// v0.12.0 推理阶段的活体计数
check("thinkTick 两边都在且翻过", Boolean(zhSec.thinkTick) && Boolean(enSec.thinkTick)
  && zhSec.thinkTick(1200) !== enSec.thinkTick(1200));
check("thinkTick 带得上数字（读者看的就是它在跳）", zhSec.thinkTick(1200).includes("1.2k"));
check("thinkTick 不出现货币符号", !/[¥$€£]/.test(zhSec.thinkTick(1200) + enSec.thinkTick(1200)));

// ── v0.12.10：两条通吃的闸 ─────────────────────────────────────────
//
// 起因：`reviveSession()` 顶上那条早退现在靠「写一句非空的 this.err」
// 撑起一条不变式（返回 false ⇒ this.err 非空），而这条不变式的前提是
// **两张表里那个键都非空**。`en.ts` 上的 `Dict` 类型能挡住漏键，
// 挡不住空串。上面那些 check 全是按名字点到的 spot check，
// 一个新键加进来不会被任何东西看一眼。

// **要递归。** 顶层 `typeof v !== "string" → continue` 会把 `phases`（4 条）
// 和 `chips`（5 × label/hint = 10 条）两个嵌套对象整个跳过——那 14 条是
// 真会显示给读者的：后端不下发标签时 `chipLabel()`/`chipHint()` 回落到
// `chips.*`，`phaseText()` 回落到 `phases.*`。
const zhOnly = [];
let strings = 0;
const walk = (zv, ev, path) => {
  for (const [k, v] of Object.entries(zv)) {
    const e = ev?.[k];
    const at = path ? `${path}.${k}` : k;
    if (typeof v === "function") {
      // **别把这一句省成 `continue`。** 底下那条 arity 自检的条件是
      // 「两边都是函数」，en 那边一旦**不是**函数它就直接跳过、一声不吭；
      // 真正守着这 20 个函数键的是 `en: Dict` + `tsc`，而 `npm test`
      // **不跑 tsc**（只有 `npm run build` 跑）。只跑 npm test 的人会拿到
      // 一个全绿的坏英文表。这是 npm test 里唯一一条通吃的闸，
      // 不该把自己的覆盖度记在一个不在 npm test 里的工具头上。
      if (typeof e !== "function") zhOnly.push(`${at}（en 不是函数）`);
      continue;
    }
    if (v && typeof v === "object") {
      if (!e || typeof e !== "object") zhOnly.push(at);
      else walk(v, e, at);
      continue;
    }
    if (typeof v !== "string") continue;
    strings++;
    // 守的是「zh 非空 ⇒ en 非空」，不是「en 一律非空」。`chips.*.hint` 里
    // 有三条**两边都故意留空**（那几个 chip 本来就没有提示语），
    // 一刀切成「en 必须非空」会把它们误报成漏译。
    if (typeof e !== "string") zhOnly.push(`${at}（en 不是字符串）`);
    else if (v && !e) zhOnly.push(at);
  }
};
walk(zhSec, enSec, "");
// 数字报**真正被断言的那个数**，不是 `Object.keys().length`（那里面还有
// 20 个函数和 2 个对象，读的人会以为都验过了）。
check(`zh 的每条字符串在 en 里都非空（${strings} 条，含嵌套）`, zhOnly.length === 0);
if (zhOnly.length) console.error("   en 缺或为空：" + zhOnly.join(", "));

// **键名要和去处对得上。** v0.12.6 ⑤ 立的规矩：`notice*` 进 `new Notice()`，
// `err*` 进 `this.err`（错误条，由 paintAlert 渲染）。两者的写法不一样——
// Notice 是短祈使句、不收句；错误条要完整句，还可能被 `+=` 追加东西。
// v0.12.9 自己破过一次这条规矩（把 noticeRegisterFirst 塞进了 this.err），
// 拼出来的英文整句不终止。规矩靠人记不住，做成扫描。
// **别只扫一个文件。** 第一版只读 `views/PenView.ts`，`main.ts:182` 的
// `new Notice(t().noticeSidecarDown)` 完全在视野外——今天它是对的，但
// 新加的文件不会被看一眼。排掉 `src/i18n/` 自己（那里是定义，不是用法）。
const tsFiles = readdirSync("src", { recursive: true })
  .map(String)
  .filter((f) => f.endsWith(".ts") && !f.startsWith("i18n/"))
  .sort();
const src = tsFiles.map((f) => readFileSync(`src/${f}`, "utf8")).join("\n;\n");

// **别把 `t().` 锚在 `=` 或 `(` 后面。** v0.12.10 第一版就是那么写的，
// 于是 `new Notice(this.err ? t().a : t().b)`（`:1080`，v0.12.7 为了修
// 「Notice 被吞掉」新写的那一行）整条逃逸——一次都没被看过。它合规不是
// 因为闸守住了，是因为写的人当时对。先切出整条语句，再在**整段右值**里
// 扫所有 `t().key`，三元和任何表达式形式才都进得来。
const keysIn = (segment) => [...segment.matchAll(/t\(\)\.(\w+)/g)].map((m) => m[1]);

// `\+?=` 只认 `+=` 和 `=`，看不见 `||=` / `??=`——而 `PenView.ts:1066`
// 现在就是 `if (!this.err) this.err = t().errSessionArchivedHard;`，
// 正是任何一个「简化一下」的建议会改写成 `this.err ||= …` 的形状。
// 改完那一刻这条闸就再也看不见它了。（比较形式 `this.err ==` 全仓 0 处，
// 放宽不会误伤。）
// `(?!=)` 挡掉 `this.err === t().x` 这种比较（`!==` / `!=` 本来就不匹配）。
// 全仓比较形式今天是 0 处，所以这条不是在修现行 bug，是白捡的一个零宽断言。
const ERR_ASSIGN = /this\.err\s*(?:\+|\|\||\?\?)?=(?!=)\s*([^;]*);/g;
// **执法的和数数的必须是同一份。** 上一版这两处各写了一遍同样的正则，
// 于是只把执法那条写坏（`this.err` → `this.errr`）时，底下那条防瞎断言
// 测的是它**自己那份副本**，照样报绿——而改的人只会改执法那一行。
// 「两个闸不同源」这个坑，这个仓库栽过不止一次。
const errStmts = [...src.matchAll(ERR_ASSIGN)];
const seenErr = errStmts.flatMap((m) => keysIn(m[1]));
const wrongInErr = seenErr.filter((k) => !k.startsWith("err"));
check("this.err 右边只出现 err* 的键（含三元等表达式形式）", wrongInErr.length === 0);
if (wrongInErr.length) console.error("   走错槽：" + wrongInErr.join(", "));

const noticeArgs = [...src.matchAll(/new Notice\(([\s\S]*?)\);/g)];
const wrongInNotice = noticeArgs
  .flatMap((m) => keysIn(m[1]))
  .filter((k) => !k.startsWith("notice"));
check("new Notice() 里只出现 notice* 的键（含三元等表达式形式）", wrongInNotice.length === 0);
if (wrongInNotice.length) console.error("   走错槽：" + wrongInNotice.join(", "));

// 上面两条自己也可能扫空——正则写错、文件改名，`length === 0` 照样为真。
// 数一下真的扫到了多少个键，掉到 0 就说明闸自己瞎了。
// 两个 seen* 都直接复用上面执法用的那份结果，不再抄一遍正则。
const seenNotice = noticeArgs.flatMap((m) => keysIn(m[1]));
// **下限必须是今天的真数，不能是随手写的魔数。** 富余多少，就是可以静默
// 丢掉多少：上一版写 `notice >= 3`（真数 10，富余 7），实测把 Notice 那条
// 正则收回成 v0.12.10 的形状 → `notice 8 个`，照样全绿，而丢掉的正是
// `:1080` 那条三元——**这一整串的起点 bug 能原样溜回来，防瞎闸一个字不说**。
// err 那边同理：`>= 5`（真数 7）能静默丢掉全仓唯一那条 `+=`，
// 也就是 `this.err += t().errApprovalUntouched`——v0.12.8 整版就是围着它建的。
//
// 维护契约：**只涨不跌**。合法新增文案会把真数推高，`>=` 不会误伤；
// 合法删键时手动把这里的数往下调一格，和 `mutate.py` 的锚点是同一套约定。
const FLOOR = { files: 11, err: 7, notice: 10 };
check(
  `槽位扫描真的看到了键（${tsFiles.length} 个文件 · err ${seenErr.length} 个 / notice ${seenNotice.length} 个）`,
  tsFiles.length >= FLOOR.files &&
    seenErr.length >= FLOOR.err &&
    seenNotice.length >= FLOOR.notice,
);

// 模块加载时的 arity 自检不该报警（报了说明英文表漏用了占位符）
check("中英表函数形参个数一致（无 arity 警告）", warnings.length === 0);
if (warnings.length) warnings.forEach((w) => console.error("   " + w));

let bad = 0;
for (const [name, pass] of checks) {
  if (!pass) bad++;
  console.log(`${pass ? "  ok  " : "  FAIL"} ${name}`);
}
console.log(bad ? `\n${bad}/${checks.length} 项失败` : `\n${checks.length} 项全部通过`);
process.exit(bad ? 1 : 0);
