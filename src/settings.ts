import { App, Plugin, PluginSettingTab, Setting } from "obsidian";
import { usageTotal } from "./api";
import { coerceLangPref, t, type LangPref } from "./i18n";

export type ThinkingLevel = "off" | "low" | "medium" | "high";

export interface PenSettings {
  /** 界面语言。"auto" 跟随 Obsidian。 */
  lang: LangPref;
  sidecarUrl: string;
  apiKey: string;
  baseUrl: string;
  model: string;
  thinking: ThinkingLevel;
  /** 后台深挖。关掉时前端不轮询，且请求带 deep:false 让后端也不起线程。 */
  deepQuestions: boolean;
  /** 花销与频率的旋钮。键名用 snake_case，见 LIMIT_SPEC 的注释。 */
  limits: PenLimits;
}

/**
 * 每个旋钮的默认值和夹紧范围。
 *
 * **键名故意用 snake_case，和后端线上格式一模一样。** 破一次驼峰规约，
 * 换掉一张「camelCase ↔ snake_case」映射表——那张表就是下一次
 * 「两个闸不同源」的入口。
 *
 * 这张表和 pen/config.py 的 LIMIT_RANGE + 默认常量是**同一道闸的两个副本**，
 * 由 scripts/check-limits.mjs 机械地守着不许漂。后端仍然是权威（它无论如何
 * 都会再夹一遍）；前端这张表只管 UX，但漂了就意味着「界面让你填 900，
 * 实际生效 300」，读者查不出来。
 */
export type LimitSpec = { def: number; min: number; max: number; step?: number };

export const LIMIT_SPEC = {
  // ── 常用 ──
  probe_max_per_window: { def: 40, min: 0, max: 1000 },
  probe_every_n_rounds: { def: 0, min: 0, max: 20 },
  probe_keep_per_run: { def: 2, min: 1, max: 5 },
  max_tokens_chat: { def: 0, min: 0, max: 4000000, step: 1000 },
  max_tokens_probe: { def: 0, min: 0, max: 1000000, step: 1000 },
  max_tokens_cross_book: { def: 0, min: 0, max: 4000000, step: 1000 },
  // ── 高级 ──
  max_tool_rounds: { def: 100, min: 1, max: 200 },
  cross_book_chars: { def: 24000, min: 0, max: 400000, step: 1000 },
  cross_book_reads: { def: 8, min: 0, max: 100 },
  probe_max_per_session: { def: 8, min: 0, max: 200 },
  probe_pending_cap: { def: 3, min: 1, max: 50 },
  probe_max_reads: { def: 2, min: 0, max: 8 },
  probe_read_lines: { def: 80, min: 10, max: 400 },
  probe_timeout_s: { def: 150, min: 30, max: 300 },
  probe_min_reply_chars: { def: 80, min: 0, max: 2000 },
  probe_concurrency: { def: 2, min: 1, max: 8 },
} satisfies Record<string, LimitSpec>;

export type LimitKey = keyof typeof LIMIT_SPEC;
export type PenLimits = Record<LimitKey, number>;

/** 常用区那几项，按界面顺序。其余全进高级区。 */
export const COMMON_LIMITS: LimitKey[] = [
  "probe_max_per_window",
  "probe_every_n_rounds",
  "probe_keep_per_run",
  "max_tokens_chat",
  "max_tokens_probe",
  "max_tokens_cross_book",
];

export const ADVANCED_LIMITS: LimitKey[] = (
  Object.keys(LIMIT_SPEC) as LimitKey[]
).filter((k) => !COMMON_LIMITS.includes(k));

export function clampLimit(k: LimitKey, v: unknown): number {
  const spec: LimitSpec = LIMIT_SPEC[k];
  // 空串必须走默认。`Number("")` 是 0 且 isFinite，直接判会把「清空输入框」
  // 变成「把上限设成 0」——而跨书那两项设成 0 就是彻底不翻别的书了。
  //
  // **只认 number 和 string，别的一律走默认。** `String([5])` 是 "5"、
  // `Number("0x10")` 是 16——不挡的话数组和十六进制在这边能过，在后端
  // 却走默认值，同一个输入两边给出不同答案。scripts/check-limits.mjs
  // 逐个输入比对这件事。
  if (typeof v !== "number" && typeof v !== "string") return spec.def;
  const raw = typeof v === "number" ? String(v) : v.trim();
  if (raw === "" || /^0[xXbBoO]/.test(raw)) return spec.def;
  const num = Number(raw);
  if (!Number.isFinite(num)) return spec.def;
  // **截断，不是四舍五入**：后端是先夹后 int()，2.6 在那边是 2。
  // 两边算法不一样的话，这两张表就只防了一半的漂。
  return Math.trunc(Math.min(Math.max(num, spec.min), spec.max));
}

export function coerceLimits(raw: unknown): PenLimits {
  const src = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const out = {} as PenLimits;
  for (const k of Object.keys(LIMIT_SPEC) as LimitKey[]) out[k] = clampLimit(k, src[k]);
  return out;
}

/**
 * 只发**改过**的那几个；一个都没动就返回 undefined，请求体里连 limits
 * 这个键都不出现。
 *
 * 这是让「上线当天逐字节一致」从口号变成可断言事实的唯一办法，
 * 也顺带让服务端将来能改默认值而不被老客户端的全量 payload 钉死。
 */
export function limitsPayload(s: PenSettings): Record<string, number> | undefined {
  const lim = coerceLimits(s.limits);
  const out: Record<string, number> = {};
  for (const k of Object.keys(LIMIT_SPEC) as LimitKey[]) {
    if (lim[k] !== LIMIT_SPEC[k].def) out[k] = lim[k];
  }
  return Object.keys(out).length ? out : undefined;
}

export const DEFAULT_SETTINGS: PenSettings = {
  lang: "auto",
  sidecarUrl: "http://127.0.0.1:8765",
  apiKey: "",
  baseUrl: "https://api.deepseek.com",
  model: "deepseek-v4-flash",
  thinking: "off",
  deepQuestions: true,
  limits: coerceLimits({}),
};

const THINKING: ThinkingLevel[] = ["off", "low", "medium", "high"];

export function coerceThinking(raw: unknown): ThinkingLevel {
  return THINKING.includes(raw as ThinkingLevel) ? (raw as ThinkingLevel) : "off";
}

export function llmPayload(s: PenSettings): {
  api_key?: string;
  base_url?: string;
  model?: string;
  thinking: ThinkingLevel;
} {
  const base = s.baseUrl.trim().replace(/\/+$/, "");
  return {
    ...(s.apiKey.trim() ? { api_key: s.apiKey.trim() } : {}),
    ...(base ? { base_url: base } : {}),
    ...(s.model.trim() ? { model: s.model.trim() } : {}),
    thinking: coerceThinking(s.thinking),
  };
}

type PenHost = Plugin & {
  settings: PenSettings;
  saveSettingsSoon: () => void;
  /** 语言改了之后重刷 ribbon tooltip、命令名、已打开的侧栏。 */
  applyLanguage: () => void;
};

export class PenSettingTab extends PluginSettingTab {
  plugin: PenHost;

  constructor(app: App, plugin: PenHost) {
    super(app, plugin);
    this.plugin = plugin;
  }

  /**
   * 一个数字旋钮。
   *
   * 控件用 addText + inputEl.type="number"，**不用 addSlider**：
   * cross_book_chars 是 0–400000，滑块上根本点不准 24000；而且精确值必须能敲。
   * 本仓从没用过 addSlider（新 API 面），但已经在摸 inputEl（API Key 那项的
   * type="password"），所以这条是家法。
   */
  private num(root: HTMLElement, key: LimitKey, name: string, desc: string): void {
    // 显式标注：satisfies 保留了字面量类型，不标的话没写 step 的那几项上
    // 读不到这个字段。
    const spec: LimitSpec = LIMIT_SPEC[key];
    new Setting(root)
      .setName(name)
      .setDesc(`${desc}${t().setDefaultHint(spec.def)}`)
      .addText((c) => {
        c.inputEl.type = "number";
        c.inputEl.min = String(spec.min);
        c.inputEl.max = String(spec.max);
        c.inputEl.step = String(spec.step ?? 1);
        c.inputEl.inputMode = "numeric";
        c.setValue(String(this.plugin.settings.limits[key])).onChange((v) => {
          // 规矩①：当场夹紧再存。但**不回写输入框**——边打边夹会让「1」在你
          // 打到「150」之前就跳成 min，输入框会跟人打架。
          this.plugin.settings.limits[key] = clampLimit(key, v);
          this.plugin.saveSettingsSoon();
        });
        // 失焦时才把真正存下的值显回去，读者才知道系统认了几。
        c.inputEl.addEventListener("blur", () => {
          c.setValue(String(this.plugin.settings.limits[key]));
        });
      });
  }

  display(): void {
    const { containerEl } = this;
    const s = t();
    containerEl.empty();
    // 标题用 setHeading 而不是裸 h2：Obsidian 现行插件规范。
    // 也不重复插件名——设置侧栏已经写着 Socrates 了。
    containerEl.createEl("p", { cls: "setting-item-description", text: s.setIntro1 });
    containerEl.createEl("p", { cls: "setting-item-description", text: s.setIntro2 });

    new Setting(containerEl).setName(s.setSecCommon).setHeading();

    new Setting(containerEl)
      .setName(s.setLangName) // 两张表都写成双语，切错了还找得回来
      .setDesc(s.setLangDesc)
      .addDropdown((d) => {
        d.addOption("auto", s.setLangAuto)
          .addOption("zh", "中文") // 语言选项按惯例各用本语言书写，不翻译
          .addOption("en", "English")
          .setValue(coerceLangPref(this.plugin.settings.lang))
          .onChange((v) => {
            this.plugin.settings.lang = coerceLangPref(v);
            this.plugin.saveSettingsSoon();
            this.plugin.applyLanguage();
            this.display(); // 原地重画，设置页自己也要跟着变
          });
      });

    new Setting(containerEl)
      .setName("API Key")
      .setDesc(s.setApiKeyDesc)
      .addText((c) => {
        c.inputEl.type = "password";
        c.inputEl.autocomplete = "off";
        c.setPlaceholder("sk-…")
          .setValue(this.plugin.settings.apiKey)
          .onChange((v) => {
            this.plugin.settings.apiKey = v.trim();
            this.plugin.saveSettingsSoon();
          });
      });

    new Setting(containerEl)
      .setName("Base URL")
      .setDesc(s.setBaseUrlDesc)
      .addText((c) =>
        c
          .setPlaceholder("https://api.deepseek.com")
          .setValue(this.plugin.settings.baseUrl)
          .onChange((v) => {
            this.plugin.settings.baseUrl = (v.trim().replace(/\/+$/, "") || DEFAULT_SETTINGS.baseUrl);
            this.plugin.saveSettingsSoon();
          }),
      );

    new Setting(containerEl)
      .setName(s.setModelName)
      .setDesc(s.setModelDesc)
      .addText((c) =>
        c
          .setPlaceholder("deepseek-v4-flash")
          .setValue(this.plugin.settings.model)
          .onChange((v) => {
            this.plugin.settings.model = v.trim() || DEFAULT_SETTINGS.model;
            this.plugin.saveSettingsSoon();
          }),
      );

    new Setting(containerEl)
      .setName("Thinking")
      .setDesc(s.setThinkingDesc)
      .addDropdown((d) => {
        d.addOption("off", s.setThinkingOff)
          .addOption("low", "low")
          .addOption("medium", "medium")
          .addOption("high", "high")
          .setValue(coerceThinking(this.plugin.settings.thinking))
          .onChange((v) => {
            this.plugin.settings.thinking = coerceThinking(v);
            this.plugin.saveSettingsSoon();
          });
      });

    new Setting(containerEl)
      .setName(s.setDeepName)
      .setDesc(s.setDeepDesc)
      .addToggle((c) =>
        c.setValue(this.plugin.settings.deepQuestions !== false).onChange((v) => {
          this.plugin.settings.deepQuestions = v;
          this.plugin.saveSettingsSoon();
        }),
      );

    for (const k of COMMON_LIMITS) {
      this.num(containerEl, k, s.limitName(k), s.limitDesc(k));
    }

    new Setting(containerEl)
      .setName("Sidecar URL")
      .setDesc(s.setSidecarDesc)
      .addText((c) =>
        c
          .setPlaceholder("http://127.0.0.1:8765")
          .setValue(this.plugin.settings.sidecarUrl)
          .onChange((v) => {
            this.plugin.settings.sidecarUrl = v.trim() || DEFAULT_SETTINGS.sidecarUrl;
            this.plugin.saveSettingsSoon();
          }),
      );

    // 高级区。用 <details> 而不是 toggle + this.display()：重画会违背
    // 「只有语言那一项才重画」的规矩，而且会把正在编辑的数字输入框的
    // 焦点和光标位置一起吃掉。展开状态不落盘 = 每次进来默认折叠。
    const adv = containerEl.createEl("details", { cls: "sp-set-advanced" });
    adv.createEl("summary", { text: s.setSecAdvanced });
    adv.createEl("p", { cls: "setting-item-description", text: s.setAdvancedNote });
    for (const k of ADVANCED_LIMITS) {
      this.num(adv, k, s.limitName(k), s.limitDesc(k));
    }

    // ── 花销：跨会话累计 ──
    // display() 是同步的，所以先把 DOM 建出来占位，再异步填。
    // **不能同步阻塞 display()**：sidecar 没起来的时候设置页就打不开了。
    new Setting(containerEl).setName(s.setSecUsage).setHeading();
    const box = containerEl.createDiv({ cls: "sp-set-usage" });
    box.createEl("p", { cls: "setting-item-description", text: s.setUsageNote });
    const line1 = box.createEl("p", { text: s.setUsageLoading });
    const line2 = box.createEl("p", { cls: "setting-item-description" });
    const line3 = box.createEl("p", { cls: "setting-item-description" });
    void this.fillUsage(line1, line2, line3);
  }

  /** 拉一次累计账填进去。拉不到就说清楚，别留一片空白让读者以为是零。 */
  private async fillUsage(
    line1: HTMLElement,
    line2: HTMLElement,
    line3: HTMLElement,
  ): Promise<void> {
    const s = t();
    try {
      const got = await usageTotal(this.plugin.settings.sidecarUrl);
      const b = got.spend || {};
      const row = (r?: { in_tokens?: number; out_tokens?: number }): number =>
        (r?.in_tokens ?? 0) + (r?.out_tokens ?? 0);
      if (!got.total) {
        line1.setText(s.setUsageEmpty);
        return;
      }
      line1.setText(s.setUsageTotal(got.total, got.sessions));
      line2.setText(s.setUsageBreak(row(b.chat), row(b.probe), row(b.fold)));
      const cached =
        (b.chat?.cached_tokens ?? 0) +
        (b.probe?.cached_tokens ?? 0) +
        (b.fold?.cached_tokens ?? 0);
      if (cached > 0) line3.setText(s.setUsageCached(cached));
    } catch {
      line1.setText(s.setUsageDown);
    }
  }
}
