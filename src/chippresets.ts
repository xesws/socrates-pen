/**
 * 三个起手模板。读者一键复制成自己的泡泡，再照自己的体例改。
 *
 * **为什么不放 src/i18n/zh.ts**：scripts/check-i18n.mjs:258 那条闸只扫
 * zh.ts / en.ts 两个文件名，禁止字符串字面量里出现 `**` 和反引号——
 * 理由是设置页的 setDesc 不渲染 Markdown，写了读者就看见裸星号。
 * 而模板正文里 `**Qn.**` 和三反引号围栏是**要逐字发给模型的内容**，
 * 不是给设置页显示的文案。两者诉求相反，所以分家；这边由
 * scripts/check-chips.mjs 单独守着（长度、无控制符、折叠块合规）。
 *
 * **正文一律 String.raw**：模板三里有 \begin{aligned} 这样的反斜杠，
 * 普通模板字符串会把 \b 解析成退格符（U+0008）——落进 data.json 谁都看不出来，
 * 模型收到的是一句缺了字的指令。
 */

import { CUSTOM_CHIP_MAX, type CustomChip, newChipId } from "./customchips";
import type { Lang } from "./i18n";

/** 三反引号。**不能直接写在模板字符串里**——它会当场把模板闭合。
 *  写成常量插值进去，比在 String.raw 里反斜杠转义干净：String.raw 会把
 *  那根反斜杠原样留下，正文里就多出一个字符。 */
const F = "```";

export type PresetChip = {
  /** 下拉里的稳定 key，也是 i18n 表里那条名字的键。 */
  key: "quiz" | "explain" | "pseudocode";
  label: Record<Lang, string>;
  hint: Record<Lang, string>;
  prompt: Record<Lang, string>;
  writeback: boolean;
};

export const PRESET_CHIPS: PresetChip[] = [
  {
    key: "quiz",
    label: { zh: "把这段出成一道题", en: "Turn this into a question" },
    hint: { zh: "按 Qn 体例写进题目那一节", en: "Writes a Qn-style item into the question section" },
    writeback: true,
    prompt: {
      zh: String.raw`就我划中的这一段，出一道自测题，写进这一关的题目小节（这本书里叫「第五拍 · Meta Question 门禁」，你的书里多半叫别的名字，照你自己的体例改这一句）。

体例照抄这本手册已有的题：
- 题干独占一行，格式 **Qn. 一句话的问题？**，n 接着这一关已有的最大编号往下排；这一关还没有题就从 1 开始
- 题干下面是若干条以「- 」开头的短析，依次是 **TL;DR：** / **(a) 概念/对比：** / **(b) 机制：** / **(c) 反例：**
- 最后独占一行写 〔回读：…〕，指回本关里该复习的那一小节

只出一道题。问的必须是我划中这段里真正会绊住人的地方，不要问符号怎么写、引号怎么转义这类抠字眼的。`,
      en: String.raw`Take the passage I selected and write one self-check question into this level's question section (in this handbook it is called "Beat 5 - Meta Question Gate"; yours is probably named differently, so edit this sentence to match your own layout).

Follow the format of the questions already in this handbook:
- The stem goes on its own line as **Qn. one-sentence question?** where n continues from the highest number already used in this level; start at 1 if this level has none yet
- Under the stem, several short notes each starting with "- ", in this order: **TL;DR:** / **(a) concept/contrast:** / **(b) mechanism:** / **(c) counterexample:**
- The last line, on its own, reads [review: ...] pointing back to the subsection worth rereading

Write exactly one question. Ask about what actually trips people up in the passage I selected, not about notation or escaping.`,
    },
  },
  {
    key: "explain",
    label: { zh: "把解释折进原文", en: "Fold an explanation in" },
    hint: {
      zh: "包成 details 折叠块，插在我划中那段后面",
      en: "Wraps it in a details block after the passage I selected",
    },
    writeback: true,
    prompt: {
      zh: String.raw`为我划中的这一段写一段解释，收成一个可折叠块，插在这一段的后面。

如果我们刚才已经聊过这段内容，就把那段解答收进去，别重写一遍；如果还没聊过，就现在直接讲清楚。

只输出一个 <details> 块，格式必须是：

<details>

<summary>实例 N：一句话看点</summary>

（正文）

</details>

三条空行契约一条都不能少：<details> 后空一行，</summary> 后空一行，</details> 前空一行。
N 接着本节已有的最大编号往下排，本节还没有就写 1。
不要复制原文已经有的 TL;DR 或 (a)(b)(c)，也不要带 〔回读：…〕——那两样原文里已经有了。
正文里至少有一段 ${F}text 伪代码；概念之间有关系就再加一段 ${F}mermaid。`,
      en: String.raw`Write an explanation of the passage I selected, folded into a collapsible block, inserted right after that passage.

If we already discussed this material earlier in the conversation, fold that answer in rather than rewriting it; if we have not, just explain it now.

Output exactly one <details> block, in this format:

<details>

<summary>Example N: the one-line point</summary>

(body)

</details>

All three blank-line rules are required: a blank line after <details>, a blank line after </summary>, and a blank line before </details>.
N continues from the highest number already used in this section; write 1 if the section has none.
Do not copy the TL;DR or the (a)(b)(c) notes the text already has, and do not add a [review: ...] line - the original already carries both.
Include at least one ${F}text pseudocode block in the body; add a ${F}mermaid block as well if the concepts have a structure worth drawing.`,
    },
  },
  {
    key: "pseudocode",
    label: { zh: "补一段 LaTeX 风格伪代码", en: "Add LaTeX-style pseudocode" },
    hint: { zh: "写进伪代码那一节", en: "Writes into the pseudocode section" },
    writeback: true,
    prompt: {
      zh: String.raw`就我划中的这一段，补一段伪代码，写进这一关的伪代码小节（这本书里叫「第六拍 · 伪代码」，你的书里多半叫别的名字，照你自己的体例改这一句），接在已有伪代码块的后面。

格式：
- 用 ${F}text 围栏包住，不要用 ${F}python
- 第一行是一句以 # 开头的注释，说清这段伪代码在算什么
- 关键字用中文（初始化 / 循环 / 重复直到 / 对每个 / 返回），不要写成某种真实语言的语法
- 数学部分照 LaTeX 记号写，和这本手册正文一致：求和写 \sum_{...}，取最大写 \max_a，期望写 \mathbb{E}[...]，下标写 Q[s,a]，赋值用 <-
- 层次靠缩进表示，每层 4 个空格
- 需要解释的那一行末尾用 # 注释，别另起一段散文

只写伪代码块本身，前后不要再写解释性段落——手册里那段解释已经有了。`,
      en: String.raw`Take the passage I selected and add a pseudocode block to this level's pseudocode section (in this handbook it is called "Beat 6 - Pseudocode"; yours is probably named differently, so edit this sentence to match your own layout), after the pseudocode blocks already there.

Format:
- Wrap it in a ${F}text fence, not ${F}python
- The first line is a comment starting with # saying what this pseudocode computes
- Use plain-word keywords (initialize / loop / repeat until / for each / return) rather than the syntax of any real language
- Write the math in LaTeX notation, matching the prose in this handbook: sums as \sum_{...}, maxima as \max_a, expectations as \mathbb{E}[...], subscripts as Q[s,a], assignment as <-
- Show nesting by indentation, four spaces per level
- Put a # comment at the end of a line that needs explaining; do not write a prose paragraph
 
Write only the pseudocode block itself, with no explanatory paragraphs before or after - the handbook already has that explanation.`,
    },
  },
];

/**
 * 模板 → 一枚新泡泡。id 现生成，所以同一个模板复制两次是两枚独立的泡泡，
 * 各自改各自的。
 *
 * **不在这里夹长度**：coerceCustomChips 落盘时会夹，check-chips.mjs 也
 * 机械地断言每段模板都在 PROMPT_MAX 以内。这儿再夹一次就是第三个闸。
 */
export function chipFromPreset(p: PresetChip, lang: Lang): CustomChip {
  return {
    id: newChipId(),
    label: p.label[lang],
    hint: p.hint[lang],
    prompt: p.prompt[lang],
    writeback: p.writeback,
    enabled: true,
    format: "",
  };
}

/** 空白新建。label 留空 —— coerceCustomChips 会回落到 prompt 首行，
 *  所以读者只写 prompt 也能得到一枚认得出的按钮。 */
export function blankChip(): CustomChip {
  return {
    id: newChipId(),
    label: "",
    hint: "",
    prompt: "",
    writeback: false,
    enabled: true,
    format: "",
  };
}

/** 还能不能再加。上限在 customchips.ts，这儿只是个转述。 */
export function chipRoomLeft(list: CustomChip[]): number {
  return Math.max(0, CUSTOM_CHIP_MAX - list.length);
}
