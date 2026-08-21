/** 把阅读模式划到的纯文本对回手册原文行号（1-based）。 */

export function collapseWs(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

export function linesFromQuote(
  markdown: string,
  quote: string,
): { startLine: number; endLine: number } | null {
  const q = collapseWs(quote);
  if (q.length < 4) return null;

  const lines = markdown.split("\n");
  const chars: number[] = [];
  let collapsed = "";
  for (let i = 0; i < lines.length; i++) {
    const n = collapseWs(lines[i]);
    if (!n) continue;
    if (collapsed.length) {
      collapsed += " ";
      chars.push(i + 1);
    }
    collapsed += n;
    for (let k = 0; k < n.length; k++) chars.push(i + 1);
  }

  let idx = collapsed.indexOf(q);
  if (idx < 0) {
    const needle = q.slice(0, Math.min(48, q.length));
    idx = collapsed.indexOf(needle);
    if (idx < 0) return null;
    const endIdx = Math.min(idx + q.length, chars.length) - 1;
    return { startLine: chars[idx], endLine: chars[Math.max(idx, endIdx)] };
  }
  const last = Math.min(idx + q.length - 1, chars.length - 1);
  return { startLine: chars[idx], endLine: chars[last] };
}
