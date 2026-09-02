/**
 * 自定义泡泡自检。`npm test` 跑它。
 *
 * 这一节的真源在前端（读者的泡泡住在 vault 的 data.json，后端一个字都不存），
 * 所以归一化那层必须真跑一遍，不能靠读代码。另外它和 pen/chips.py 之间有
 * **三个同名常量**——那是同一道闸的两个副本，漂了就意味着「设置页让你写 4000 字，
 * 实际发出去 2000」，而被切掉的恰好是读者写在最后的格式硬约束。这里机械地钉住。
 */
import { build } from "esbuild";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const require = createRequire(import.meta.url);

async function load(entry, tag) {
  const out = join(mkdtempSync(join(tmpdir(), tag)), "mod.cjs");
  await build({
    entryPoints: [entry],
    bundle: true,
    format: "cjs",
    external: ["obsidian"],
    outfile: out,
    logLevel: "error",
  });
  // 这两个模块只 `import type` 地碰 i18n，所以 bundle 里不该有 obsidian。
  // 真被拉进运行时依赖图了，下面这行会直接抛——那正是我们要的报警。
  return require(out);
}

const cc = await load("src/customchips.ts", "sp-chips-");
const presets = await load("src/chippresets.ts", "sp-presets-");

const {
  LABEL_MAX,
  HINT_MAX,
  PROMPT_MAX,
  CUSTOM_CHIP_MAX,
  CUSTOM_ID_RE,
  coerceCustomChips,
  sanitizeChipPrompt,
  chipPayload,
  newChipId,
} = cc;
const { PRESET_CHIPS, chipFromPreset, blankChip, chipRoomLeft } = presets;

const checks = [];
const check = (name, pass) => checks.push([name, pass]);

// C0 控制符（不含 \n \t）。写成 \u 转义而不是字面量：字面控制符落进源码
// 谁都看不出来，正是这条闸要抓的那种 bug。
const C0 = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;

// ── 一、前后端那三个常量必须逐字相等 ──
// 后端是权威（它无论如何都会再夹一遍），但两边漂开就是 UX 谎报。
const py = readFileSync("pen/chips.py", "utf8");
const pyNum = (name) => {
  const m = py.match(new RegExp(`^${name}\\s*=\\s*(\\d+)`, "m"));
  return m ? Number(m[1]) : null;
};
check(`LABEL_MAX 与 pen/chips.py 一致（${LABEL_MAX}）`, pyNum("LABEL_MAX") === LABEL_MAX);
check(`HINT_MAX 与 pen/chips.py 一致（${HINT_MAX}）`, pyNum("HINT_MAX") === HINT_MAX);
check(`PROMPT_MAX 与 pen/chips.py 一致（${PROMPT_MAX}）`, pyNum("PROMPT_MAX") === PROMPT_MAX);
{
  // id 形状也是两份。两边正则写法不同，比的是**判定**：拿同一批样本各跑一遍。
  const m = py.match(/CUSTOM_ID_RE\s*=\s*re\.compile\(r"([^"]+)"\)/);
  const pyRe = m ? new RegExp(m[1]) : null;
  const samples = [
    "u.a1",
    "u.",
    "x.a1",
    "u.a-b",
    "u." + "a".repeat(32),
    "u." + "a".repeat(33),
    "socratic",
  ];
  check(
    "CUSTOM_ID_RE 与 pen/chips.py 判定一致",
    !!pyRe && samples.every((s) => pyRe.test(s) === CUSTOM_ID_RE.test(s)),
  );
}

// ── 二、归一化：脏输入绝不抛，且真的夹住 ──
for (const garbage of [null, undefined, "x", 5, {}, [null], [1, 2], [{}], [{ prompt: "" }]]) {
  let ok = true;
  try {
    ok = Array.isArray(coerceCustomChips(garbage));
  } catch {
    ok = false;
  }
  check(`脏输入不抛且返回数组：${JSON.stringify(garbage) ?? "undefined"}`, ok);
}
check("非数组 → 空表", coerceCustomChips("nope").length === 0);
check("没有 prompt 的整项丢掉", coerceCustomChips([{ label: "有名字没指令" }]).length === 0);
{
  // label 空 → 回落 prompt 首行。**不丢整项**（会被读成「我建的泡泡不见了」），
  // 也**不留空 label**（那是一枚零宽、点得着但看不见的按钮）。
  const r = coerceCustomChips([{ prompt: "第一行就是它的名字\n第二行不算" }]);
  check("label 空时回落到 prompt 首行", r.length === 1 && r[0].label === "第一行就是它的名字");
}
{
  const r = coerceCustomChips([{ prompt: "x", label: "L".repeat(200), hint: "H".repeat(300) }]);
  check("label 截到 LABEL_MAX", r[0].label.length === LABEL_MAX);
  check("hint 截到 HINT_MAX", r[0].hint.length === HINT_MAX);
}
check(
  "prompt 截到 PROMPT_MAX",
  coerceCustomChips([{ prompt: "字".repeat(PROMPT_MAX + 500) }])[0].prompt.length === PROMPT_MAX,
);
{
  const r = coerceCustomChips([
    { id: "u.same", prompt: "a" },
    { id: "u.same", prompt: "b" },
  ]);
  check("重复 id 会被重发", r.length === 2 && r[0].id !== r[1].id);
}
check(
  "非法 id 会被换成合法的（写成固定芯片的名字也不许劫持）",
  CUSTOM_ID_RE.test(coerceCustomChips([{ id: "socratic", prompt: "劫持" }])[0].id),
);
check(
  `总数封顶 ${CUSTOM_CHIP_MAX}`,
  coerceCustomChips(Array.from({ length: 50 }, () => ({ prompt: "x" }))).length === CUSTOM_CHIP_MAX,
);
check(
  "writeback 只认字面 true",
  coerceCustomChips([{ prompt: "x", writeback: "yes" }])[0].writeback === false,
);
check("enabled 默认开", coerceCustomChips([{ prompt: "x" }])[0].enabled === true);

// ── 三、消毒：带内记号和段头 ──
// 这两条不是防谁使坏，是别让读者随手写的一句话把协议顶穿。
check(
  "剥掉 pen:compact 记号（否则 compact 把这一轮误判成滚动摘要）",
  !sanitizeChipPrompt("前<!--pen:compact-->后").includes("<!--pen:compact-->"),
);
check("剥掉 pen:chips 记号", !sanitizeChipPrompt("a<!--pen:chips b").includes("<!--pen:chips"));
check("行首段头被 defang 而不是删字", sanitizeChipPrompt("[框选]\n正文") === " [框选]\n正文");
check(
  "控制符清掉，但 \\n 和 \\t 留着",
  sanitizeChipPrompt("a\u0008b\u0000c\nd\te") === "abc\nd\te",
);
check("三行以上空行压成两行", sanitizeChipPrompt("a\n\n\n\n\nb") === "a\n\nb");
check("非字符串 → 空串", sanitizeChipPrompt(123) === "");

// ── 四、上行的那一份只带后端真的会读的字段 ──
{
  const keys = Object.keys(chipPayload(coerceCustomChips([{ prompt: "x" }])[0])).sort();
  check(
    `chipPayload 的键恰好是那五个（实为 ${keys.join(",")}）`,
    keys.join(",") === "format,id,label,prompt,writeback",
  );
}

// ── 五、newChipId 不许碰 crypto ──
// 发布 CI 钉死 Node 18（.github/workflows/release.yml），那里 globalThis.crypto
// 是 undefined。本机 node 22 跑绿、一打 tag 就红，是最难查的那种红。
{
  // 只扫**代码**：文件里那段注释写的正是「为什么不用 crypto」，
  // 裸 grep 会被自己的解释绊倒。
  const src = readFileSync("src/customchips.ts", "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
  check("newChipId 不依赖 crypto", !/crypto/.test(src));
  const ids = new Set(Array.from({ length: 200 }, (_, i) => newChipId(i / 200)));
  check(
    "newChipId 形状合法且基本不撞",
    ids.size === 200 && [...ids].every((i) => CUSTOM_ID_RE.test(i)),
  );
}

// ── 六、三个预置模板 ──
check("预置模板有三个", PRESET_CHIPS.length === 3);
for (const p of PRESET_CHIPS) {
  for (const lang of ["zh", "en"]) {
    const prompt = p.prompt[lang];
    const label = p.label[lang];
    const hint = p.hint[lang];
    check(
      `${p.key}/${lang} prompt 非空且不超上限（${prompt.length}）`,
      prompt.length > 0 && prompt.length <= PROMPT_MAX,
    );
    check(
      `${p.key}/${lang} label 不超上限（${label.length}）`,
      label.length > 0 && label.length <= LABEL_MAX,
    );
    check(
      `${p.key}/${lang} hint 不超上限（${hint.length}）`,
      hint.length > 0 && hint.length <= HINT_MAX,
    );
    // String.raw 写漏了就在这儿露出来：\begin 的 \b 会被解析成退格符 U+0008，
    // 落进 data.json 谁都看不出来，模型收到的是一句缺了字的指令。
    check(`${p.key}/${lang} 无 C0 控制符`, !C0.test(prompt));
    // 模板自己被消毒改写了，读者复制过去的就和他在设置页看到的不是一份东西。
    check(`${p.key}/${lang} 过消毒后逐字不变`, sanitizeChipPrompt(prompt) === prompt);
  }
}
check(
  "模板三写的是 LaTeX 记号，不是某种真实语言的语法",
  PRESET_CHIPS.find((p) => p.key === "pseudocode").prompt.zh.includes("\\sum_{"),
);
{
  const c = chipFromPreset(PRESET_CHIPS[0], "zh");
  check(
    "模板 → 泡泡：id 合法、默认开、format 留空",
    CUSTOM_ID_RE.test(c.id) && c.enabled === true && c.format === "",
  );
  check("三个模板都默认勾着「会改写原文」", PRESET_CHIPS.every((p) => p.writeback === true));
  const two = [chipFromPreset(PRESET_CHIPS[0], "zh"), chipFromPreset(PRESET_CHIPS[0], "zh")];
  check("同一个模板复制两次是两枚独立的泡泡", two[0].id !== two[1].id);
  // 复制过来的必须能原样活过归一化，否则读者一进设置页就看见自己的模板被改了
  const kept = coerceCustomChips(PRESET_CHIPS.map((p) => chipFromPreset(p, "zh")));
  check(
    "三个模板都能原样活过 coerceCustomChips",
    kept.length === 3 && kept.every((k, i) => k.prompt === PRESET_CHIPS[i].prompt.zh),
  );
}
check(
  "空白新建：label 空、prompt 空、不写回",
  (() => {
    const b = blankChip();
    return b.label === "" && b.prompt === "" && b.writeback === false && b.enabled === true;
  })(),
);
check(
  "chipRoomLeft 到顶是 0",
  chipRoomLeft(Array.from({ length: CUSTOM_CHIP_MAX }, () => ({}))) === 0,
);

let bad = 0;
for (const [name, pass] of checks) {
  if (!pass) bad++;
  console.log(`${pass ? "  ok  " : "  FAIL"} ${name}`);
}
console.log(bad ? `\n${bad}/${checks.length} 项失败` : `\n${checks.length} 项全部通过`);
process.exit(bad ? 1 : 0);
