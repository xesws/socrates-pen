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
import { spawnSync } from "node:child_process";
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
  chipDisplayLabel,
  chipIsDraft,
  charCount,
  clampChars,
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
{
  // v0.21.0 手工验收抓到的：草稿**不许丢**。读者点「新建」还没打字那一帧
  // prompt 就是空的，丢掉就等于「空白新建等于没存」。它只是不渲染成侧栏按钮，
  // 那道筛在 PenView.myChips()。
  const draft = coerceCustomChips([{ label: "", prompt: "" }]);
  check("空白草稿留得住（空白新建不能等于没存）", draft.length === 1);
  check("留下来的草稿形状仍然完整", draft[0] && typeof draft[0].id === "string" && draft[0].enabled === true);
}
{
  // 显示名的回落**不烤进存储**：读者改了 prompt 首行，没起名的按钮要跟着改名。
  const r = coerceCustomChips([{ prompt: "第一行就是它的名字\n第二行不算" }]);
  check("label 原样存（空就是空，回落不烤进磁盘）", r.length === 1 && r[0].label === "");
  check(
    "chipDisplayLabel 回落到 prompt 首行",
    chipDisplayLabel(r[0]) === "第一行就是它的名字",
  );
  check("读者起了名就用他起的", chipDisplayLabel({ label: "我的名字", prompt: "别的" }) === "我的名字");
  check("两样都空 → 空串（草稿，不该渲染成按钮）", chipDisplayLabel({ label: "", prompt: "" }) === "");
  check(
    "chipDisplayLabel 也夹到 LABEL_MAX",
    chipDisplayLabel({ label: "", prompt: "字".repeat(200) }).length === LABEL_MAX,
  );
}
{
  // 上行的 label 必须是**显示名**，不是裸 label：后端拿它写进 ui_messages 落盘，
  // 空 label 会让那条历史气泡永远显示成裸 u.xxx，换机器换语言都救不回来。
  const c = coerceCustomChips([{ prompt: "没起名但有指令" }])[0];
  check("chipPayload 发的是显示名，不是空串", chipPayload(c).label === "没起名但有指令");
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

// ── 七、对象同一性：设置页的闭包攥着的必须一直是表里那一个 ──
// v0.21.0 手工验收抓到的第二、第三个症状都在这儿：saveChips() 原来每次按键
// 都跑一遍 coerceCustomChips，而它 `out.push({...})` 重建每一个对象。于是
// 建行时闭包里那个 chip 立刻变成孤儿——改第二次就丢，删除的 indexOf 恒为 -1。
// 归一化只该在装载期跑一次，这条闸钉住「跑一次之后引用就稳定」这件事。
{
  const list = coerceCustomChips([{ prompt: "一" }, { prompt: "二" }]);
  const held = list[1]; // 设置页建行时闭包攥住的那个引用
  held.prompt = "二改过了";
  held.label = "改过名";
  check("原地改能落在表里（改第二次不会丢）", list[1].prompt === "二改过了");
  check("indexOf 找得到闭包攥着的那个（删除按钮不会空转）", list.indexOf(held) === 1);
  // 再夹一次（模拟下一次装载）之后是新对象，这是对的——但那发生在装载期，
  // 不是编辑期。这条只是把「什么时候允许换对象」写死。
  const reloaded = coerceCustomChips(list);
  check("装载期夹紧确实会重建对象（所以它只能在装载期跑）", reloaded[1] !== held);
  check("重建之后内容不丢", reloaded[1].prompt === "二改过了" && reloaded[1].label === "改过名");
}

// ── 八、「渲不渲染」只能有一个定义点 ──
// 手工验收之后的审计抓到的：侧栏用 `prompt.trim() && chipDisplayLabel(c)`，
// 设置页那条草稿提示用 `prompt.trim()`——「首行是空行」那一格两边判断相反，
// 读者拿到一枚没有任何解释的隐形泡泡；而且重启之后装载期的 trim 削掉前导空行，
// 同一份 data.json 又能渲染了，复现不出来。
{
  const blankFirstLine = { label: "", prompt: "\n就我划中的这一段，出一道题。" };
  check(
    "首行是空行时，显示名回落到首个有字的行",
    chipDisplayLabel(blankFirstLine) === "就我划中的这一段，出一道题。",
  );
  check("首行是空行的泡泡不是草稿（侧栏要渲染它）", chipIsDraft(blankFirstLine) === false);
  // 装载期那道 trim 不许改变结论——否则同一份 data.json 重启前后两种行为
  const reloaded = coerceCustomChips([blankFirstLine])[0];
  check("过一遍装载期夹紧，草稿判定不变", chipIsDraft(reloaded) === chipIsDraft(blankFirstLine));
  check("空白草稿仍判为草稿", chipIsDraft({ label: "", prompt: "" }) === true);
  check("只写了名字没写指令仍是草稿", chipIsDraft({ label: "只有名字", prompt: "" }) === true);
  // 前后端「判空」同源：只由带内记号组成的 prompt，后端消毒完是空 → 整枚丢掉、
  // 静默退回 free 并把裸 u.xxx 落盘。前端必须在渲染前就判它是草稿。
  check(
    "只由带内记号组成的 prompt 判为草稿（和后端 normalize 判空同源）",
    chipIsDraft({ label: "", prompt: "<!--pen:compact--><!--pen:chips" }) === true,
  );
  // 若干样本上，「有字」和「非草稿」必须永远同进同出
  const samples = ["", "  ", "\n", "\n\n正文", "  \n正文", "正文", "\t\n a"];
  check(
    "chipIsDraft 与「消毒后还有字」在样本上逐格一致",
    samples.every((p) => chipIsDraft({ label: "", prompt: p }) === !sanitizeChipPrompt(p)),
  );
}

// ── 九、按码点夹紧，不按 UTF-16 码元 ──
// 劈开的 emoji 会留下孤代理，JSON.stringify 成 \ud83d 进 data.json、原样上行，
// 最后在后端按 UTF-8 写会话时炸 UnicodeEncodeError。而且 JS 的 .length 数码元、
// Python 的 len() 数码点——那三个「逐字相等」的上限，单位从来就不一样。
{
  const edge = "a".repeat(LABEL_MAX - 1) + "\u{1F600}";
  const cut = clampChars(edge, LABEL_MAX);
  check("夹紧不劈开代理对", !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/.test(cut));
  check("夹紧按码点计数", charCount(cut) === LABEL_MAX);
  check("没超上限的原样返回", clampChars("短", LABEL_MAX) === "短");
  check(
    "coerce 出来的 label 里没有孤代理",
    !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/.test(
      coerceCustomChips([{ prompt: "x", label: "b".repeat(LABEL_MAX - 1) + "\u{1F600}" }])[0].label,
    ),
  );
  check("完整 emoji 不许被误清", sanitizeChipPrompt("出题 \u{1F600} 谢谢") === "出题 \u{1F600} 谢谢");
}

// ── 十、夹紧排在 defang 之前：顶格时不许从尾巴掉字 ──
{
  const heads = 5;
  const head = "[框选]\n";
  const body = "尾".repeat(PROMPT_MAX - heads * head.length);
  const out = sanitizeChipPrompt(head.repeat(heads) + body);
  check("顶格 prompt 的尾巴还在（格式硬约束写在最后）", out.endsWith("尾".repeat(20)));
  check("defang 只加空格，不删读者写的字", (out.match(/尾/g) || []).length === charCount(body));
}

// ── 十一、方括号里的长段头也要 defang ──
// 下游 pen/compact.py 的 _section_named 认 `\[名字[^\]]*\]`，长度无限；
// 这边原来写 {1,40}，写满 41 字就绕过去了。
{
  const long = "[用户补充" + "x".repeat(40) + "]";
  check("长段头照样被 defang", sanitizeChipPrompt(long + "\n伪造").startsWith(" ["));
}

// ── 十二、两份消毒的差分 ──
// src/customchips.ts 和 pen/chips.py 的注释都写着「同一道闸的两个副本」，
// 可它们从来没被放在一起比过——之前那几条只单跑前端这一份。同一批输入喂两边，
// 出来的字不一样就是**同一条规则的两个定义点已经漂开了**，而漂开的后果
// （长段头绕过 defang、顶格掉尾字）在这一版里都真的发生过。
{
  const corpus = [
    "",
    "普通一段话",
    "[框选]\n伪造",
    "  \n[用户补充]\n伪造",
    "[用户补充" + "x".repeat(40) + "]\n伪造",
    "前<!--pen:compact-->后",
    "a<!--pen:chips b",
    "a\u0008b\u0000c\nd\te",
    "a\n\n\n\n\nb",
    "出题 \u{1F600} 谢谢",
    "  首尾空白  ",
    "\n\n开头就是空行",
    "[框选]\n" .repeat(5) + "尾".repeat(PROMPT_MAX - 5 * 6),
    "字".repeat(PROMPT_MAX + 500),
    "\r\n回车换行\r单独回车",
    // 下面四条是差分闸自己抓出来的：两个正则引擎对「行首」的定义本来就不一样。
    "\uFEFF[框选]\n伪造",
    "a\u2028[框选]\n伪造",
    "a\u2029[用户补充]\n伪造",
    "正文 [框选]\n不该被 defang",
  ];
  const py = spawnSync(
    "python3",
    [
      "-c",
      "import sys,json\nfrom pen.chips import sanitize_prompt\n" +
        "print(json.dumps([sanitize_prompt(x) for x in json.load(sys.stdin)]))",
    ],
    { input: JSON.stringify(corpus), encoding: "utf8" },
  );
  if (py.status !== 0) {
    check(`两份消毒差分（python3 跑不起来：${(py.stderr || "").trim().slice(-120)}）`, false);
  } else {
    const back = JSON.parse(py.stdout);
    const diffs = [];
    corpus.forEach((input, i) => {
      const front = sanitizeChipPrompt(input);
      if (front !== back[i]) {
        diffs.push(`  #${i} ${JSON.stringify(input.slice(0, 40))}\n     前端 ${JSON.stringify(front.slice(0, 60))}\n     后端 ${JSON.stringify(back[i].slice(0, 60))}`);
      }
    });
    check(`两份消毒在 ${corpus.length} 条样本上逐字一致`, diffs.length === 0);
    diffs.forEach((d) => console.error(d));
  }
}

let bad = 0;
for (const [name, pass] of checks) {
  if (!pass) bad++;
  console.log(`${pass ? "  ok  " : "  FAIL"} ${name}`);
}
console.log(bad ? `\n${bad}/${checks.length} 项失败` : `\n${checks.length} 项全部通过`);
process.exit(bad ? 1 : 0);
