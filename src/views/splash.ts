import { t } from "../i18n";
import {
  PORTRAIT_NARROW,
  PORTRAIT_WIDE,
  WORDMARK_NARROW,
  WORDMARK_WIDE,
  type AsciiArt,
} from "../logo";

export type SplashLevel = "hero" | "mini" | "none";

/**
 * 实测当前等宽字体的「字宽 / 字号」比。
 *
 * 字符画的自适应字号是反解出来的：自然宽度 = 列数 × 字号 × 字宽比，
 * 所以字号 = 可用宽度 / (列数 × 字宽比)。这个比值随用户的等宽字体而变
 * （SF Mono 0.6，Menlo 0.6023，Consolas 0.55），必须实测。
 *
 * 用 64 个 "0" 而不是单个 M：数字在所有等宽字体里都是 tabular，且不会触发连字；
 * 100px 字号让测量误差摊薄到 1e-4。
 */
export function measureMonoAdvance(host: HTMLElement): number {
  const probe = host.createSpan({ text: "0".repeat(64) });
  probe.setAttr("aria-hidden", "true");
  probe.style.cssText =
    "position:absolute;left:-9999px;top:0;visibility:hidden;white-space:pre;" +
    "font-family:var(--font-monospace);font-size:100px;letter-spacing:0;" +
    "font-variant-ligatures:none;padding:0;border:0;margin:0";
  const w = probe.getBoundingClientRect().width;
  probe.remove();
  const adv = w / 64 / 100;
  // 面板处于折叠状态时量不到宽度，回退到最常见的 0.6
  return adv > 0.3 && adv < 1.2 ? Number(adv.toFixed(4)) : 0.6;
}

function paintArt(parent: HTMLElement, a: AsciiArt, cls: string): void {
  const box = parent.createDiv({ cls: `sp-art ${cls}` });
  box.setAttr("aria-hidden", "true");
  // 只写 --sp-cols。--sp-art-max 留给 CSS，否则 inline style 会压掉 @container 分档。
  box.style.setProperty("--sp-cols", String(a.cols));
  a.lines.forEach((line, i) => {
    // 空行必须给一个空格：white-space:pre 下空的 div 不产生行盒，高度会塌成 0
    const row = box.createDiv({ cls: "sp-art-row", text: line.length > 0 ? line : " " });
    row.style.setProperty("--i", String(i));
  });
}

export type SplashOpts = {
  level: SplashLevel;
  animate: boolean;
};

/**
 * 把开屏画进 parent。
 *
 * 宽窄两档字符画都进 DOM，由 CSS 容器查询决定显示哪一档——比在 JS 里测宽度
 * 再选资产少一条生命周期，也不需要 ResizeObserver。
 */
export function renderSplash(parent: HTMLElement, o: SplashOpts): void {
  if (o.level === "none") return;
  const wrap = parent.createDiv({ cls: "sp-splash" });
  // role="img" 让读屏把整块当一张图播报 aria-label，后代自动变 presentational，
  // 不会被逐字念出来；比 aria-hidden 好，因为品牌名仍然会被念到。
  wrap.setAttr("role", "img");
  wrap.setAttr("aria-label", `${t().appName} — ${t().splashTagline}`);
  if (o.animate) wrap.addClass("is-enter");

  if (o.level === "hero") {
    paintArt(wrap, PORTRAIT_WIDE, "is-portrait is-wide");
    paintArt(wrap, PORTRAIT_NARROW, "is-portrait is-narrow");
  }
  paintArt(wrap, WORDMARK_WIDE, "is-word is-wide");
  paintArt(wrap, WORDMARK_NARROW, "is-word is-narrow");
  // 标语就是一行普通文字。字符画只用在肖像和字标上——那两个是"图"，
  // 标语是"字"，让它保持可读、可选、可被读屏正常念出来。
  wrap.createDiv({ cls: "sp-splash-tag", text: t().splashTagline });
  wrap.createDiv({ cls: "sp-splash-sub", text: t().splashSubline });
}
