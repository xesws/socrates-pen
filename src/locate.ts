/**
 * 把阅读模式划到的纯文本对回手册原文行号（1-based）。
 *
 * 阅读视图渲染掉的东西比空白多得多：`**粗体**` 的星号、`` `代码` `` 的反引号、
 * 表格的竖线、标题的井号、链接的方括号。读者划到的是渲染后的字，原文里夹着
 * 这些记号。v0.23.x 之前只折叠空白再找子串，实测在一本 3 万行的手册上，
 * 阅读视图划的 13 段**一段都对不上（0/13）**；对不上就回退成第 1 行，sidecar
 * 把这些轮次全记在「封面」——一场 65 轮的对话有 36 轮这样记丢了。
 *
 * 现在两边都只留字母、数字、汉字再比：先整段，再开头 48 字，再结尾 48 字。
 * 开头那一探救「选区尾巴跨进了被改写过的段落」；结尾那一探救「从表格中间
 * 划起」——表头渲染出来的顺序和原文不一样，正文却在。
 *
 * 找不到就返回 null，**不再假装是第 1 行**。0 由 sidecar 解释成「定位不到」，
 * 它会沿用同一本书里上一处锚点并标 sticky。
 */

export function collapseWs(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

/** 只留字母、数字、汉字。渲染和原文之间会变的东西全在这之外。 */
export function squash(s: string): string {
  return s.replace(/[^0-9A-Za-z一-鿿]/g, "");
}

const PROBE = 48;
const MIN = 4;

export function linesFromQuote(
  markdown: string,
  quote: string,
): { startLine: number; endLine: number } | null {
  const q = squash(quote);
  if (q.length < MIN) return null;

  const lines = markdown.split("\n");
  const at: number[] = []; // 第 k 个压扁字符属于原文第几行
  let flat = "";
  for (let i = 0; i < lines.length; i++) {
    const n = squash(lines[i]);
    if (!n) continue;
    flat += n;
    for (let k = 0; k < n.length; k++) at.push(i + 1);
  }

  // 探针命中就只报探针自己覆盖的那几行：整段对不上时，选区的另一头多半已经
  // 不在原文里，硬按整段长度外推会把 end 推进别的小节。窄而准，胜过宽而错。
  const hit = (needle: string) => {
    const idx = flat.indexOf(needle);
    if (idx < 0) return null;
    return { startLine: at[idx], endLine: at[idx + needle.length - 1] };
  };
  return (
    hit(q) ??
    (q.length > PROBE ? hit(q.slice(0, PROBE)) ?? hit(q.slice(-PROBE)) : null)
  );
}
