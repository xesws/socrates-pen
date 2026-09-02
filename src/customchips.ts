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
/** 落单的代理项。成对的 emoji 不会命中——只清被 slice 劈剩的那半个。 */
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g;

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

/**
 * 按**码点**数，不按 UTF-16 码元。
 *
 * 两个理由，都是真踩过的：
 *  1. `"a".repeat(39) + "😀"` 用 `.slice(0, 40)` 会把那个 emoji 从中间劈开，
 *     留下一个**孤代理**。它能原样 JSON.stringify 成 `\ud83d` 进 data.json、
 *     原样上行，最后在后端按 UTF-8 写会话时炸 UnicodeEncodeError——
 *     一枚泡泡的名字里带个 emoji 就能把那场会话卡死。
 *  2. JS 的 `.length` 数码元、Python 的 `len()` 数码点。前后端那三个同名上限
 *     check-chips.mjs 逐字比过相等，可**单位从来就不一样**：同一段带 emoji 的
 *     prompt，前端以为还没到顶，后端已经截了。
 */
export function charCount(s: string): number {
  return Array.from(s).length;
}

export function clampChars(s: string, cap: number): string {
  const cps = Array.from(s);
  return cps.length <= cap ? s : cps.slice(0, cap).join("");
}

function oneLine(raw: unknown, cap: number): string {
  if (typeof raw !== "string") return "";
  return clampChars(raw.replace(CTRL_ALL, " ").split(/\s+/).filter(Boolean).join(" "), cap);
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
  // 行分隔符**全部**归一成 \n，并剥掉 BOM。这不是洁癖，是让两个正则引擎看见
  // 同一套行结构：JS 的 `^`（/m）认 U+2028 / U+2029 为行首、`trim()` 还吃 BOM，
  // 而 Python 的 `re.M` 只认 \n、`strip()` 不动 BOM。不归一的话，
  // 「行首段头」这条规则在两边的判定就不一样——而它俩的注释都写着
  // 「同一道闸的两个副本」。scripts/check-chips.mjs 的差分闸盯着这件事。
  let s = raw.replace(/\r\n?|\u2028|\u2029/g, "\n").replace(/\uFEFF/g, "");
  s = s.replace(CTRL_KEEP_WS, "");
  s = s.replace(LONE_SURROGATE, "");
  s = s.split("<!--pen:compact-->").join("").split("<!--pen:chips").join("");
  s = s.replace(/\n{3,}/g, "\n\n").trim();
  // 夹紧排在 defang **之前**，defang 之后不再夹第二次。
  // 反过来写的话：defang 每遇到一个行首段头就塞一个空格，**把串撑长**，
  // 然后那一刀就从尾巴上多切掉同样多的字——而读者的格式硬约束恰恰写在最后。
  // 设置页那行「4000 / 4000」按夹紧后的真长度显示，两边就对得上了。
  // 代价是返回值可能比 PROMPT_MAX 多几个空格：无所谓，这个上限是拦「粘一整本书」的，
  // 不是精确配额，而多出来的每一个字节都不是读者写的内容。
  s = clampChars(s, PROMPT_MAX);
  // defang 排在 trim 之后：反过来的话，第一行就是 `[框选]` 的那一格里，
  // 塞进去的空格正好在开头，会被 trim 清掉——最该防的那一格恰好漏网。
  // **方括号里不设长度上限**：下游 pen/compact.py 的 _section_named 认的是
  // `\[名字[^\]]*\]`，长度无限。这边原来写 {1,40}，于是括号里写满 41 字的
  // `[用户补充xxxx…]` 绕过 defang、在 [意图] 段里开出一个真段头，把读者自己
  // 泡泡里的话变成「读者更正」写进滚动摘要。两边必须同源。
  return s.replace(/^(\[[^\]\n]*\])/gm, " $1");
}

/**
 * data.json 里那张表 → 可用的芯片数组。脏输入归一化，**绝不抛**——
 * 照 settings.ts 的 coerceLimits 那条家法。
 *
 * **prompt 为空的项不丢**：那是读者点了「新建」还没写完的草稿。丢掉的话，
 * 「空白新建 → 关掉设置页 → 回来一看没了」，会读成「我建的泡泡不见了」。
 * 草稿只是**不渲染成侧栏按钮**（没 prompt 就没有可注入的东西，没 label 就是
 * 一枚零宽、点得着但看不见的按钮），那道筛在 PenView.myChips()。
 *
 * label 也**照读者写的原样存**，空就是空——显示时的回落规则在 chipDisplayLabel()
 * 里，是活的：读者改了 prompt 首行，没起名的按钮跟着改名。把回落烤进存储的话
 * 那次改名就永远追不上了。
 */
/**
 * 这枚泡泡显示出来叫什么。**唯一真源**——侧栏按钮、设置页的 summary、
 * 随请求上行给后端落盘的那个名字，三处都查这里。
 *
 * 读者没起名就用 prompt 的首行，**不用一句 i18n 占位文案**。两个理由：
 *  1. 这个名字会被后端写进 ui_messages 落盘。占位文案是有语言的，
 *     落进盘就把「当时的界面语言」冻进了历史气泡；
 *  2. main.ts 的 loadSettings() 跑在 setLang() **之前**，那里的 t() 拿到的是
 *     模块初值，不是读者的语言——占位文案在首次加载时必然是错的语言。
 *
 * 回落取**首个有字的行**，不是裸首行。裸首行那种写法有个洞：读者在指令框里
 * 先敲一次回车再写正文（或粘贴时带了前导空行），首行是空的 → 显示名为空 →
 * 侧栏整枚筛掉，而设置页的草稿提示只看 prompt.trim() 判定「写完了」把自己藏了，
 * 于是读者得到一枚没有任何解释的隐形泡泡；更糟的是重启之后装载期那道 trim
 * 削掉了前导空行，同一份 data.json 又能渲染了——复现不出来的那种 bug。
 *
 * 取首个有字的行之后，`prompt 有字` ⟺ `显示名非空`，chipIsDraft 那两个判据
 * 塌成同一条，装载期的 trim 也不再改变结论。
 *
 * 两样都空就返回空串：那是一张还没写字的草稿，本来就不该渲染成按钮。
 */
export function chipDisplayLabel(c: { label: string; prompt: string }): string {
  return (
    oneLine(c.label, LABEL_MAX) ||
    oneLine(c.prompt.split("\n").find((l) => l.trim()) ?? "", LABEL_MAX)
  );
}

/**
 * 这枚泡泡还是张草稿吗——**「渲不渲染成侧栏按钮」的唯一定义点**。
 * 侧栏（PenView.myChips）和设置页那条草稿提示都查这里。
 *
 * 判据用**消毒后**的 prompt，不是读者敲进去的原文。因为后端判空也是消毒之后判的
 * （pen/chips.py 的 normalize_custom_chip：sanitize 完是空串就返回 None）。
 * 两边不同源的话有一格会漏：一段只由 `<!--pen:compact-->` 这类带内记号或控制符
 * 组成的 prompt，前端看原文非空、照常渲染成可点按钮，后端消毒完是空 → 整枚丢掉、
 * 静默退回 free，而且 `shown` 会回落成裸 `u.xxx` **落盘进 ui_messages**，
 * 换机器换语言都救不回来。
 */
export function chipIsDraft(c: { label: string; prompt: string }): boolean {
  const prompt = sanitizeChipPrompt(c.prompt);
  // 以**指令**为准，不是以名字为准。只起了名、没写指令的那一枚点下去没有任何
  // 东西可注入（后端 normalize 会整枚丢掉、退回 free），所以它仍是草稿——
  // 光看显示名的话 label 一非空就判它写完了，这一格会漏。
  // 第二个条件在今天恒为假（prompt 有字 ⇒ 显示名非空），留着是因为它才是
  // 「渲染出来会是一枚零宽按钮」的直接判据，将来改回落规则时它会先响。
  return !prompt || !chipDisplayLabel({ label: c.label, prompt });
}

export function coerceCustomChips(raw: unknown): CustomChip[] {
  if (!Array.isArray(raw)) return [];
  const out: CustomChip[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const o = item as Record<string, unknown>;
    const prompt = sanitizeChipPrompt(o.prompt);
    let id = typeof o.id === "string" && CUSTOM_ID_RE.test(o.id) ? o.id : "";
    if (!id || seen.has(id)) id = newChipId();
    // 极端情况下 newChipId 也可能撞（同一毫秒 + 同一随机数），补个尾巴
    while (seen.has(id)) id = `${id}z`.slice(0, 34);
    seen.add(id);
    out.push({
      id,
      label: oneLine(o.label, LABEL_MAX),
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
    // 发**显示用**的那个名字，不是裸 label。后端拿它写进 ui_messages 落盘，
    // 空 label 会让那条历史气泡永远显示成裸 u.xxx（pen/session.py 的
    // chip_label 查不到就返回 id 本身），换机器换语言都救不回来。
    label: chipDisplayLabel(c),
    prompt: c.prompt,
    writeback: c.writeback,
    format: c.format,
  };
}
