/**
 * 折叠块标签的展示层处理。纯函数，无 obsidian 依赖——scripts/check-fold.mjs
 * 会把这份真代码打进 bundle 机械地守着。
 *
 * 背景（v0.18.3）：手册按体例含 `<details>` 折叠块，它们会随选区邻域进 prompt。
 * 模型偶尔回显这些标签，而聊天面板不是折叠块的产品面——流式预览用 setText
 * 直写原文，标签就裸着出现在「先别揭晓」那条回复里（用户实测看到过一次，
 * 写完后 MarkdownRenderer 重渲染又正常，所以时有时无）。修法：聊天界面
 * 三条上屏路径（流式 / 最终渲染 / 引文条）统一剥掉标签、保留内文；发给
 * sidecar 的 selected_text 永远是原文，一字不动。
 */

/** 完整的折叠块标签（含属性），以及流式写到一半的残缺尾巴。 */
const FOLD_TAG = /<\/?(?:details|summary)\b[^>]*>/gi;

/** 流式预览：剥掉所有折叠块标签；写到一半的 `<deta` 这类尾巴也剪掉，
 *  不然下一个 48 字符 token 到来之前它会闪一下。 */
export function stripFoldTags(s: string): string {
  let out = s.replace(FOLD_TAG, "");
  const tail = out.match(/<\/?[A-Za-z]*$/);
  if (tail && tail[0]) {
    const t = tail[0].toLowerCase();
    const openers = ["<details", "<summary", "</details", "</summary"];
    if (openers.some((o) => o.startsWith(t))) {
      out = out.slice(0, tail.index ?? 0);
    }
  }
  return out;
}

/** 上屏前的回复清洗。pen:chips 注释整块剥（界面会剥掉的那段模型指令）；
 *  折叠块标签**只剥独占一行的**——`<details>` 与正文同行的散文式提及
 *  （写回流程里"包成 `<details>`"这种）是合法内联代码，必须活着。 */
export function visibleReply(text: string): string {
  return text
    .replace(/<!--pen:chips[\s\S]*?-->/g, "")
    // 同行整包的 <summary>标签文本</summary> → 只留文本（标签剥掉后
    // MarkdownRenderer 收到孤立的 summary 标签仍可能裸显字样）
    .replace(/^[ \t]*(?:<details\b[^>]*>[ \t]*)?<summary\b[^>]*>(.*?)[ \t]*<\/summary>[ \t]*(?:<\/details>)?[ \t]*$/gim, "$1")
    .replace(/^[ \t]*<\/?(?:details|summary)\b[^>]*>[ \t]*$/gim, "")
    .trim();
}
