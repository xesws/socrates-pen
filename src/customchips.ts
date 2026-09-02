/**
 * 读者自定义的芯片（用户口中的「泡泡」）。纯函数，无 obsidian 依赖——
 * scripts/check-chips.mjs 会把这份真代码打进 bundle 机械地守着。
 *
 * 它们住在**这个 vault 的 data.json 里**，后端一个字都不存；随每一次
 * POST /v1/chat 的 `custom_chip` 字段上行（见 pen/chips.py）。所以这里
 * 是唯一真源，后端那边只做二次夹紧。
 *
 * 下面三个长度上限和 `pen/chips.py` 的同名常量是**同一道闸的两个副本**，
 * 由 scripts/check-chips.mjs 机械地守着不许漂。后端仍然是权威（它无论如何
 * 都会再夹一遍）；这一份只管 UX——漂了就意味着「设置页让你写 4000 字，
 * 实际发出去 2000」，而被切掉的恰好是写在最后的格式硬约束。
 */

export const LABEL_MAX = 40;
export const HINT_MAX = 80;
export const PROMPT_MAX = 4000;

/** 一排按钮的上限。.sp-chips 是 max-height 8em 的换行滚动区（styles.css:481），
 *  再多就只是在堆滚动条，还会把固定芯片挤出首屏。 */
export const CUSTOM_CHIP_MAX = 20;

/** id 的保留命名空间。前缀写死 "u."，撞不上 FIXED_CHIPS 的任何一个 id，
 *  也撞不上 tutor 内部那个 "free"——pen/app.py 里 `chip === "search"` 的短路、
 *  pen/probe.py 的深挖门禁都按 id 精确匹配，命名空间隔开才不会误伤。
 *  形状必须和 pen/chips.py 的 CUSTOM_ID_RE 逐字一致。 */
export const CUSTOM_ID_RE = /^u\.[A-Za-z0-9]{1,32}$/;

/** C0 控制符，**保留 \n 和 \t**。TS 模板字符串里一个手滑的 `\b` 就是个退格符，
 *  落进 data.json 谁都看不出来，而模型收到的是一句缺了字的指令。 */
const CTRL_KEEP_WS = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g;
/** label / hint 压成一行时用：换行也一起清掉。 */
const CTRL_ALL = /[\u0000-\u001f\u007f]/g;

export type CustomChip = {
  id: string;
  /** 按钮文案。读者自己写的，不进 i18n 词表，两种界面语言下都显示原文。 */
  label: string;
  /** tooltip，可空。 */
  hint: string;
  /** 注入 user packet [意图] 段的那段话。 */
  prompt: string;
  /** 会改写原文：后端据此在意图正文后追加内置写回纪律，并跳过这一轮的后台深挖。
   *  它**不控制工具可见性**——磁盘安全由审批闸兜住，见 pen/agent/permissions.py。 */
  writeback: boolean;
  /** 灰着的仍渲染，只是点不动。和 FIXED_CHIPS 的 enabled 同义，
   *  所以 paintChips 里两张表能走同一段渲染。 */
  enabled: boolean;
  /** 下一版接格式校验器用。这一版**写而不读**：线上形状先定死，
   *  免得下次加校验时还要再改一遍前后端两侧的契约。 */
  format: string;
};

/**
 * 新 id。
 *
 * **不用 crypto.randomUUID / globalThis.crypto**：发布 CI 钉死 Node 18
 * （.github/workflows/release.yml），那里 `globalThis.crypto` 是 undefined，
 * 而 scripts/check-chips.mjs 要在 node 里跑这份真代码。本机 node 22 会跑绿，
 * 一打 tag 就红——最难查的那种红。时间戳 + 随机后缀足够了：这不是安全 id，
 * 只是一张表里的主键，重了也由 coerce 那一层重发。
 */
export function newChipId(seed = Math.random()): string {
  const a = Date.now().toString(36);
  const b = Math.floor(seed * 0xffffff).toString(36);
  return `u.${a}${b}`.slice(0, 34);
}

function oneLine(raw: unknown, cap: number): string {
  if (typeof raw !== "string") return "";
  return raw.replace(CTRL_ALL, " ").split(/\s+/).filter(Boolean).join(" ").slice(0, cap);
}

/**
 * 读者写的那段 prompt → 可以安全发出去的文本。**只夹紧，绝不抛。**
 *
 * 这一份和 pen/chips.py 的 sanitize_prompt 是同一道闸的两个副本。规则逐条对齐：
 *  1. 归一换行、去掉 C0 控制符（保留 \n \t）；
 *  2. 剥掉两个**带内记号**。`<!--pen:compact-->` 会让 compact 把这一轮误判成
 *     滚动摘要（pen/compact.py:110），`<!--pen:chips` 是追问块的起手记号
 *     （pen/tutor.py:251）。这不是防谁使坏——是别让读者随手写的一句话把协议顶穿；
 *  3. 行首的 `[段头]` 前面塞一个空格。读者的 prompt 原样拼进 [意图] 段，
 *     一行 `[框选]` 就能在后面凭空开出第二个「框选」段，而 compact 是按段头找的。
 *     只 defang，不删字；
 *  4. 压掉三行以上的连续空行；
 *  5. 截到 PROMPT_MAX。
 */
export function sanitizeChipPrompt(raw: unknown): string {
  if (typeof raw !== "string") return "";
  let s = raw.replace(/\r\n?/g, "\n");
  s = s.replace(CTRL_KEEP_WS, "");
  s = s.split("<!--pen:compact-->").join("").split("<!--pen:chips").join("");
  s = s.replace(/\n{3,}/g, "\n\n").trim();
  // defang 排在 trim **之后**：反过来的话，第一行就是 `[框选]` 的那一格里，
  // 塞进去的空格正好在开头，会被 trim 清掉——最该防的那一格恰好漏网。
  // 和 pen/chips.py 的 sanitize_prompt 同源，两边的顺序必须一样。
  s = s.replace(/^(\[[^\]\n]{1,40}\])/gm, " $1");
  return s.slice(0, PROMPT_MAX);
}

/**
 * data.json 里那张表 → 可用的芯片数组。脏输入归一化，**绝不抛**——
 * 照 settings.ts 的 coerceLimits 那条家法。
 *
 * label 为空**不丢整项**：读者刚点「新建」还没来得及打字，整项消失会读成
 * 「我建的泡泡不见了」，而空 label 会渲染成一枚零宽、点得着但看不见的按钮。
 *
 * 回落取 **prompt 的首行**，不取一句 i18n 占位文案。两个理由：
 *  1. label 会随请求上行、被后端写进 ui_messages 落盘（pen/app.py 的 `shown`）。
 *     占位文案是有语言的，落进盘就把「当时的界面语言」冻进了历史气泡；
 *  2. main.ts 的 loadSettings() 跑在 setLang() **之前**，那里的 t() 拿到的是
 *     模块初值，不是读者的语言——换句话说占位文案在首次加载时必然是错的语言。
 * prompt 为空的项在上面已经丢掉了，所以这个回落一定拿得到非空的字。
 */
export function coerceCustomChips(raw: unknown): CustomChip[] {
  if (!Array.isArray(raw)) return [];
  const out: CustomChip[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const o = item as Record<string, unknown>;
    const prompt = sanitizeChipPrompt(o.prompt);
    // 没有 prompt 的自定义芯片和 free 一模一样，没有存在的理由
    if (!prompt) continue;
    let id = typeof o.id === "string" && CUSTOM_ID_RE.test(o.id) ? o.id : "";
    if (!id || seen.has(id)) id = newChipId();
    // 极端情况下 newChipId 也可能撞（同一毫秒 + 同一随机数），补个尾巴
    while (seen.has(id)) id = `${id}z`.slice(0, 34);
    seen.add(id);
    out.push({
      id,
      label: oneLine(o.label, LABEL_MAX) || oneLine(prompt.split("\n")[0], LABEL_MAX),
      hint: oneLine(o.hint, HINT_MAX),
      prompt,
      writeback: o.writeback === true,
      enabled: o.enabled !== false,
      format: oneLine(o.format, 32),
    });
    if (out.length >= CUSTOM_CHIP_MAX) break;
  }
  return out;
}

/** 随请求上行的那一份。**只发后端真的会读的字段**，别把整个对象倒过去——
 *  enabled 是前端自己的事（灰着的按钮压根点不动），发过去只是噪声。 */
export function chipPayload(c: CustomChip): {
  id: string;
  label: string;
  prompt: string;
  writeback: boolean;
  format: string;
} {
  return {
    id: c.id,
    label: c.label,
    prompt: c.prompt,
    writeback: c.writeback,
    format: c.format,
  };
}
