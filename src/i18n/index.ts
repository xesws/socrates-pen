/**
 * 词表入口与语言解析。
 *
 * 调用约定：**永远 `t().xxx` 现取现用**，不要把 `t()` 的返回值存进长生命周期的
 * 字段里——否则切换语言后那份引用还是旧表。
 */

import { getLanguage } from "obsidian";
import { zh, type Dict } from "./zh";
import { en } from "./en";

export type Lang = "zh" | "en";
export type LangPref = "auto" | Lang;
export type { Dict };

const DICTS: Record<Lang, Dict> = { zh, en };
let current: Dict = zh;

export function coerceLangPref(raw: unknown): LangPref {
  return raw === "zh" || raw === "en" || raw === "auto" ? raw : "auto";
}

/**
 * Obsidian 的界面语言。
 *
 * `getLanguage()` 自 1.8.7 才有，而 manifest 的 minAppVersion 是 1.5.0，
 * 所以必须 guard。它的实现就是 `localStorage.getItem("language") || "en"`。
 *
 * 关键细节：**用户选英文时 Obsidian 是 removeItem**，所以读到 null 要当英文，
 * 不能当"没设置过"再去猜系统语言。
 *
 * 不需要监听变化：Obsidian 自己改语言必定走 window.location.reload()，
 * 一次会话里这个值不会变。
 */
export function obsidianLang(): Lang {
  let raw = "";
  try {
    if (typeof getLanguage === "function") raw = getLanguage() || "";
  } catch {
    /* 老版本没有这个导出 */
  }
  if (!raw) {
    try {
      raw = window.localStorage.getItem("language") || "";
    } catch {
      /* 某些环境禁用 localStorage */
    }
  }
  if (!raw) raw = document.documentElement.lang || "";
  // zh 与 zh-TW 都吃简体表：比退回英文更接近繁体用户
  return raw.toLowerCase().startsWith("zh") ? "zh" : "en";
}

export function resolveLang(pref: LangPref): Lang {
  return pref === "auto" ? obsidianLang() : pref;
}

export function setLang(lang: Lang): void {
  current = DICTS[lang];
}

export function currentLang(): Lang {
  return current === zh ? "zh" : "en";
}

export function t(): Dict {
  return current;
}

/**
 * 后端下发的 status 事件带稳定的 `phase` 字段（pen/tutor.py）。
 * 认识就换成本地文案，不认识就照抄后端下发的 text——这样后端将来新增 phase
 * 不会让状态行变空白。
 */
export function phaseText(phase: string, fallback: string): string {
  return t().phases[phase] || fallback;
}

/**
 * 芯片 label / hint 同理：后端下发中文，但带稳定的英文 id
 * （socratic / explain_zero / examples / search / writeback）。
 * 查得到就本地化，查不到就用后端下发的原文。
 */
export function chipLabel(id: string, fallback: string): string {
  return t().chips[id]?.label || fallback || id;
}

export function chipHint(id: string, fallback: string): string {
  return t().chips[id]?.hint || fallback;
}

// 开发期自检：函数式文案的形参个数必须与中文表一致。
//
// 类型系统在这里有个挡不住的洞：TS 允许把 (a) => x 赋给 (a, b) => x
// （JS 里忽略多余实参本来就合法），所以「英文版少用了一个占位符」这种
// 静默文案 bug 不会被 tsc 拦下——比如 healthOkFallback 少收一个 model，
// 英文用户就永远看不到模型名。这里补上。
for (const k of Object.keys(zh) as (keyof Dict)[]) {
  const a = zh[k];
  const b = en[k];
  if (typeof a === "function" && typeof b === "function" && a.length !== b.length) {
    console.warn(
      `[socrates-pen] i18n: en.${String(k)} 收 ${b.length} 个参数，zh 收 ${a.length} 个——` +
        "英文文案很可能漏用了一个占位符",
    );
  }
}
