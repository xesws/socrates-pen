import {
  CUSTOM_CHIP_MAX,
  HINT_MAX,
  LABEL_MAX,
  PROMPT_MAX,
  chipDisplayLabel,
  chipIsDraft,
  charCount,
  clampChars,
  type CustomChip,
} from "./customchips";
import { PRESET_CHIPS, blankChip, chipFromPreset } from "./chippresets";
import { App, Notice, Plugin, PluginSettingTab, Setting } from "obsidian";
import { ApiError } from "./apierror";
import { makeApi, usageTotal } from "./api";
import type { LlmStatus } from "./types";
import { coerceLangPref, currentLang, t, type LangPref } from "./i18n";
import type { EnsureKind, SidecarSnap, StopResult } from "./sidecar";

export type ThinkingLevel = "off" | "low" | "medium" | "high";

export interface PenSettings {
  /** 界面语言。"auto" 跟随 Obsidian。 */
  lang: LangPref;
  sidecarUrl: string;
  /** Obsidian 打开时由插件拉起本机服务。 */
  sidecarAutoStart: boolean;
  /** 退出 Obsidian 时是否让本插件拉起的服务继续跑（多库共用，默认跑）。 */
  sidecarKeepAlive: boolean;
  /** 空 = 自动找 python3 / python / py。 */
  pythonPath: string;
  baseUrl: string;
  model: string;
  thinking: ThinkingLevel;
  /** 对话框可贴图。默认关：DeepSeek 文本模型没有视觉。 */
  vision: boolean;
  /** 后台深挖。关掉时前端不轮询，且请求带 deep:false 让后端也不起线程。 */
  deepQuestions: boolean;
  /** 花销与频率的旋钮。键名用 snake_case，见 LIMIT_SPEC 的注释。 */
  limits: PenLimits;
  /** 读者自己定的泡泡。类型与归一化在 src/customchips.ts。 */
  customChips: CustomChip[];
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
  compact_chat_tokens: { def: 32000, min: 0, max: 500000, step: 1000 },
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
  "compact_chat_tokens",
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
  sidecarAutoStart: true,
  sidecarKeepAlive: true,
  pythonPath: "",
  baseUrl: "https://api.deepseek.com",
  model: "deepseek-v4-flash",
  thinking: "off",
  vision: false,
  deepQuestions: true,
  limits: coerceLimits({}),
  customChips: [],
};

const THINKING: ThinkingLevel[] = ["off", "low", "medium", "high"];

export function coerceThinking(raw: unknown): ThinkingLevel {
  return THINKING.includes(raw as ThinkingLevel) ? (raw as ThinkingLevel) : "off";
}

/** 随请求带的 LLM 覆盖项。**v0.18.0 起永远不含 api_key**：钥匙住在 sidecar 的
 *  llm.json 里，请求里那把旧的"设置页钥匙"通道已拆——data.json 会跟着
 *  Sync / iCloud / git 走，明文钥匙放在那儿等于随身携带。 */
export function llmPayload(s: PenSettings): {
  base_url?: string;
  model?: string;
  thinking: ThinkingLevel;
  vision: boolean;
} {
  const base = s.baseUrl.trim().replace(/\/+$/, "");
  return {
    ...(base ? { base_url: base } : {}),
    ...(s.model.trim() ? { model: s.model.trim() } : {}),
    thinking: coerceThinking(s.thinking),
    vision: s.vision === true,
  };
}

function sidecarStatusText(snap: SidecarSnap, errTail: string): string {
  const s = t();
  if (snap.phase === "idle") {
    return snap.detail === "stopped" ? s.setSidecarPhaseStopped : s.setSidecarPhaseIdle;
  }
  if (snap.phase === "checking") return s.setSidecarPhaseChecking;
  if (snap.phase === "installing") return s.setSidecarPhaseInstalling;
  if (snap.phase === "starting") return s.setSidecarPhaseStarting;
  if (snap.phase === "stopping") return s.setSidecarPhaseStopping;
  if (snap.phase === "stale") return s.setSidecarPhaseStale(snap.detail);
  if (snap.phase === "running") return s.setSidecarPhaseRunning;
  if (snap.detail === "no-python") return s.setSidecarErrNoPython;
  if (snap.detail === "not-loopback") return s.setSidecarErrNotLoopback;
  if (snap.detail === "bad-url" || snap.detail === "bad-port") return s.setSidecarErrBadUrl;
  if (snap.detail === "install-failed") {
    return errTail ? `${s.setSidecarErrInstall}\n${errTail}` : s.setSidecarErrInstall;
  }
  if (snap.detail === "spawn-failed") {
    return errTail ? `${s.setSidecarErrSpawn}\n${errTail}` : s.setSidecarErrSpawn;
  }
  if (snap.detail === "no-health") {
    return errTail ? `${s.setSidecarErrHealth}\n${errTail}` : s.setSidecarErrHealth;
  }
  if (snap.detail === "stop-failed") {
    return errTail ? `${s.setSidecarErrStop}\n${errTail}` : s.setSidecarErrStop;
  }
  if (snap.detail === "stop-no-pid") return s.setSidecarErrStopNoPid;
  const base = s.setSidecarErrOther(snap.detail || "error");
  return errTail ? `${base}\n${errTail}` : base;
}

function stopNotice(r: StopResult): string {
  const s = t();
  if (r.kind === "idle") return s.noticeSidecarAlreadyStopped;
  if (r.kind === "failed") return s.noticeSidecarStopFailed;
  if (r.who === "other") return s.noticeSidecarStoppedOther(r.command);
  if (r.who === "leftover") return s.noticeSidecarStoppedLeftover(r.command);
  if (r.who === "shared") return s.noticeSidecarStoppedShared(r.command);
  return s.noticeSidecarStoppedOwned;
}

function ensureNotice(kind: EnsureKind): string | null {
  if (kind === "already") return t().noticeSidecarAlready;
  if (kind === "stale") return t().noticeSidecarStale;
  return null;
}

/** 落盘形状。迁移没成功之前（migrateKey 非空），盘上那把明文钥匙必须原样
 *  跟着写回——PUT 失败后 data.json 里那份就是唯一副本，首次「用当前选区」
 *  就会触发保存，不能指望迁移先完成。「绝不回写明文」指不新增明文落点，
 *  不是把已经在盘上的那把弄丢。这份形状由 scripts/check-key.mjs 机械守着。 */
export function persistableSettings(
  s: PenSettings,
  migrateKey: string,
): PenSettings & { apiKey?: string } {
  return migrateKey ? { ...s, apiKey: migrateKey } : s;
}

/** 不要写成 `Plugin & { settings }`：Plugin.settings 在类型里标了 @since 1.13.0，
 *  审查器会把每一个 `this.plugin.settings` 判成「用不了 1.5.0」。我们自己的字段。 */
type PenHost = {
  settings: PenSettings;
  /** 老 data.json 里带出来、等迁移的明文钥匙。设置页存/清成功后置空。 */
  migrateKey: string;
  /** 钥匙 PUT 的单通道（与迁移循环串行，防在途互盖）。 */
  sidecarPutKey: (key: string, baseUrl: string) => Promise<LlmStatus | null>;
  /** 设置页 PUT 失败后的内存暂存，升完自动写入。绝不进 data.json。 */
  pendingPutKey: string;
  saveSettings: () => Promise<void>;
  saveSettingsSoon: () => void;
  applyLanguage: () => void;
  sidecarSnap: () => SidecarSnap;
  sidecarError: () => string;
  sidecarWatch: (fn: () => void) => () => void;
  ensureSidecar: () => Promise<EnsureKind>;
  stopSidecar: () => Promise<StopResult>;
  refreshPenViews: () => void;
  /** 设置页改完自定义泡泡后叫醒侧栏那排按钮。 */
  refreshChips: () => void;
};

export class PenSettingTab extends PluginSettingTab {
  plugin: PenHost;
  private unwatch: (() => void) | null = null;

  constructor(app: App, plugin: PenHost) {
    super(app, plugin as unknown as Plugin);
    this.plugin = plugin;
  }

  hide(): void {
    this.unwatch?.();
    this.unwatch = null;
    super.hide();
  }

  /** 钥匙状态的唯一事实在 sidecar（llm_public_status）；vault 里永远没有副本。 */
  private async paintKeyStatus(el: HTMLElement): Promise<void> {
    try {
      const h = await makeApi(this.plugin.settings.sidecarUrl).health();
      el.setText(
        h.llm.ok
          ? t().setKeyStatusSaved(h.llm.key_source, h.llm.key_tail || "")
          : t().setKeyStatusNone,
      );
    } catch {
      el.setText(t().setKeyStatusUnreachable);
    }
  }

  /** 钥匙按主机落锁（merge_llm 的「不跨主机挪用」）。刚存的这把要是和设置页
   *  填的 Base URL 不同主机，对话会一直撞「找不到模型配置」——当场说破，
   *  别让读者对着一格明明填了钥匙的设置页猜。 */
  private warnKeyHostMismatch(llm: LlmStatus): void {
    if (!llm.ok || llm.key_source !== "sidecar") return;
    let keyHost = "";
    let urlHost = "";
    try {
      keyHost = new URL(llm.base_url).host;
      urlHost = new URL(this.plugin.settings.baseUrl).host;
    } catch {
      return;
    }
    if (keyHost && urlHost && keyHost !== urlHost) {
      new Notice(t().noticeKeyHostMismatch(keyHost, urlHost));
    }
  }

  /**
   * 「自定义泡泡」一节。
   *
   * **全程不调 this.display()**：那是这个文件的家法（见高级区那段注释）——
   * 重画会把正在编辑的 textarea 的焦点和光标位置一起吃掉，而这一节里
   * 读者恰恰是在长文本框里逐字打字。所以新建就 append 一个 <details>，
   * 删除就 detach 那一个节点，剩下的 DOM 一个字都不动。
   */
  private chipsSection(root: HTMLElement): void {
    const s = t();
    new Setting(root).setName(s.setSecChips).setHeading();
    root.createEl("p", { cls: "setting-item-description", text: s.setChipsDesc });

    const list = root.createDiv({ cls: "sp-set-chips" });
    const empty = root.createEl("p", {
      cls: "setting-item-description sp-set-chips-note",
      text: s.setChipsEmpty,
    });
    // 满了才提示。恒显一句「最多 20 枚」是噪声——绝大多数人建两三枚就够。
    const full = root.createEl("p", { cls: "setting-item-description", text: "" });

    const syncNotes = (): void => {
      const n2 = this.plugin.settings.customChips.length;
      empty.toggleClass("is-off", n2 > 0);
      full.setText(n2 >= CUSTOM_CHIP_MAX ? s.setChipsFull(CUSTOM_CHIP_MAX) : "");
    };

    for (const c of this.plugin.settings.customChips) this.chipEditor(list, c, syncNotes);
    syncNotes();

    // 底下那一行：挑个模板 → 新建。下拉的值是模板 key，"" 是空白。
    let pick = "";
    new Setting(root)
      .setName(s.setChipNewFrom)
      .setDesc(s.setChipNewFromDesc)
      .addDropdown((c) => {
        c.addOption("", s.setChipPresetBlank);
        for (const p of PRESET_CHIPS) c.addOption(p.key, p.label[currentLang()]);
        c.setValue("").onChange((v) => {
          pick = v;
        });
      })
      .addButton((c) =>
        c.setButtonText(s.setChipNewBtn).onClick(() => {
          if (this.plugin.settings.customChips.length >= CUSTOM_CHIP_MAX) {
            syncNotes();
            return;
          }
          const preset = PRESET_CHIPS.find((p) => p.key === pick);
          const fresh = preset ? chipFromPreset(preset, currentLang()) : blankChip();
          this.plugin.settings.customChips.push(fresh);
          // 新建的那个默认展开：读者刚点完就要打字，还要再点一下才张开是白费一步。
          this.chipEditor(list, fresh, syncNotes, true);
          syncNotes();
          this.saveChips();
        }),
      );
  }

  /**
   * 一枚泡泡的内联编辑区。
   *
   * 存盘一律走 saveChips()（防抖 + 叫醒侧栏），**不在这里直接 saveSettings**：
   * prompt 那个 textarea 是逐字触发 onChange 的，每个字一次 await 落盘会把
   * data.json 写穿。
   */
  private chipEditor(
    root: HTMLElement,
    chip: CustomChip,
    onCount: () => void,
    open = false,
  ): void {
    const s = t();
    const box = root.createEl("details", { cls: "sp-set-chip" });
    if (open) box.setAttr("open", "");
    const head = box.createEl("summary");
    // summary 上显示的是**当前**名字，和侧栏那枚按钮、以及落盘进 ui_messages
    // 的文案同一条规矩（chipDisplayLabel），不在这儿另写一份。
    const retitle = (): void => {
      head.setText(chipDisplayLabel(chip) || s.setChipUnnamed);
    };
    retitle();

    new Setting(box)
      .setName(s.setChipLabelName)
      .setDesc(s.setChipLabelDesc)
      .addText((c) => {
        c.setValue(chip.label).onChange((v) => {
          // 当场夹紧再存，但**不当场回写输入框**——边打边夹会让光标跳。
          // 和 num() 那条家法同源。
          chip.label = clampChars(v, LABEL_MAX);
          this.saveChips();
        });
        // 失焦时把真正存下的值显回去，读者才知道系统认了几个字；
        // summary 也只在这时候改，逐字改标题会让人一边打字一边看见上面那行在抖。
        c.inputEl.addEventListener("blur", () => {
          c.setValue(chip.label);
          retitle();
          // label 也算进 chipIsDraft：只写了名字没写指令仍是草稿，
          // 而只写了指令没写名字**不是**。两栏都要能翻转这条提示。
          syncDraft();
        });
      });

    new Setting(box)
      .setName(s.setChipHintName)
      .setDesc(s.setChipHintDesc)
      .addText((c) => {
        c.setValue(chip.hint).onChange((v) => {
          chip.hint = clampChars(v, HINT_MAX);
          this.saveChips();
        });
        c.inputEl.addEventListener("blur", () => c.setValue(chip.hint));
      });

    const promptRow = new Setting(box).setName(s.setChipPromptName).setDesc(s.setChipPromptDesc);
    const count = promptRow.descEl.createDiv({ cls: "setting-item-description" });
    const syncCount = (): void => {
      count.setText(s.setChipChars(charCount(chip.prompt), PROMPT_MAX));
    };
    promptRow.addTextArea((c) => {
      c.inputEl.rows = 8;
      c.inputEl.addClass("sp-set-chip-prompt");
      c.setPlaceholder(s.setChipPromptPlaceholder)
        .setValue(chip.prompt)
        .onChange((v) => {
          chip.prompt = clampChars(v, PROMPT_MAX);
          syncCount();
          syncDraft();
          this.saveChips();
        });
      // 这一条比 label 那条要紧：超了上限被切掉的**恰好是写在最后的格式硬约束**。
      // 失焦时把真正存下的那段显回去，读者当场就看见尾巴没了，而不是等到
      // 模型不照格式做的时候才去猜为什么。
      c.inputEl.addEventListener("blur", () => {
        c.setValue(chip.prompt);
        syncCount();
        syncDraft();
        retitle();
      });
    });
    syncCount();

    // 草稿说明。设置页留得住半成品，侧栏却筛掉它（没 prompt 就没有可注入的东西）——
    // 不说一声的话，「我建了泡泡侧栏没有」和 v0.21.0 修掉的那个真 bug 长得一模一样。
    const draftNote = box.createEl("p", {
      cls: "setting-item-description sp-set-chips-note",
      text: s.setChipDraftNote,
    });
    const syncDraft = (): void => {
      draftNote.toggleClass("is-off", !chipIsDraft(chip));
    };
    syncDraft();

    new Setting(box)
      .setName(s.setChipWritebackName)
      .setDesc(s.setChipWritebackDesc)
      .addToggle((c) =>
        c.setValue(chip.writeback).onChange((v) => {
          chip.writeback = v;
          this.saveChips();
        }),
      );

    new Setting(box)
      .setName(s.setChipEnabledName)
      .setDesc(s.setChipEnabledDesc)
      .addToggle((c) =>
        c.setValue(chip.enabled).onChange((v) => {
          chip.enabled = v;
          this.saveChips();
        }),
      );

    // 删除要点两下。**不用 confirm() 弹窗**：那是浏览器模态，Obsidian 设置页
    // 里弹出来很突兀，而且本仓一次都没用过。第二下之前按钮自己就是提示。
    //
    // 确认态**只要焦点还在这枚按钮上就不缩回**，移开焦点立刻缩回，
    // 另配一道兜底超时。原来只有一道 4 秒的计时器，实测不好点：读完
    // 「再点一次确认删除」再把指针移回来就超时了，得连点两下才提交——
    // 而连点两下恰恰是这道闸本来要防的手滑。焦点是比秒表更准的「人还在不在」。
    let armed = false;
    let disarmTimer: number | null = null;
    new Setting(box)
      .setName(s.setChipDelete)
      .addButton((c) => {
        const disarm = (): void => {
          if (disarmTimer !== null) {
            window.clearTimeout(disarmTimer);
            disarmTimer = null;
          }
          if (!armed) return;
          armed = false;
          c.setButtonText(s.setChipDeleteBtn);
        };
        // 点完就走开的，下次回来是干净的「删除」，不是一枚半按下的雷。
        c.buttonEl.addEventListener("blur", disarm);
        c.setButtonText(s.setChipDeleteBtn)
          .setWarning()
          .onClick(() => {
            if (!armed) {
              armed = true;
              c.setButtonText(s.setChipDeleteConfirm);
              // 兜底：焦点因为别的原因没触发 blur 时，别把确认态永久挂在那儿。
              disarmTimer = window.setTimeout(disarm, 10000);
              return;
            }
            // 先停表：这一枚马上就从 DOM 上摘下去了，让计时器再去 setButtonText
            // 是在动一个已经 detach 的节点。
            if (disarmTimer !== null) {
              window.clearTimeout(disarmTimer);
              disarmTimer = null;
            }
            armed = false;
            const i = this.plugin.settings.customChips.indexOf(chip);
            if (i >= 0) this.plugin.settings.customChips.splice(i, 1);
            box.detach();
            onCount();
            this.saveChips();
          });
      });
  }

  /** 改完一枚泡泡：防抖落盘 → 叫醒侧栏那排按钮。
   *
   * **这里绝不能调 coerceCustomChips。** 那是装载期的脏数据闸，它做两件
   * 在编辑期都是错的事：丢掉 prompt 还空着的项（读者刚点「新建」那一帧），
   * 以及 `out.push({...})` **重建每一个对象**——下面每个 onChange 闭包都攥着
   * 建行时那个 chip 引用，数组一被换掉，闭包写的就是一个已经不在表里的孤儿。
   *
   * v0.21.0 实测出来的三个症状，全是这一行：空白新建等于没存；同一次打开
   * 设置页里改第二次就丢；删除按钮只 detach 了 DOM、数据还在
   * （`indexOf(chip)` 必然是 -1）。
   *
   * 夹紧只在两处：loadSettings() 装载时，和后端（权威，无论如何都会再夹一遍）。
   * 这里只负责把读者当下写的字**原地**存下去。 */
  private saveChips(): void {
    this.plugin.saveSettingsSoon();
    this.plugin.refreshChips();
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

    new Setting(containerEl).setName(s.setSidecarSvc).setHeading();
    containerEl.createEl("p", { cls: "setting-item-description", text: s.setSidecarSvcDesc });
    const statusEl = containerEl.createEl("p", { cls: "setting-item-description" });
    const btns: {
      start?: { setDisabled: (v: boolean) => unknown };
      stop?: { setDisabled: (v: boolean) => unknown; setButtonText: (s: string) => unknown };
      save?: { setDisabled: (v: boolean) => unknown };
      clear?: { setDisabled: (v: boolean) => unknown };
    } = {};
    const paintStatus = () => {
      const snap = this.plugin.sidecarSnap();
      statusEl.setText(sidecarStatusText(snap, this.plugin.sidecarError()));
      const busy =
        snap.phase === "checking" ||
        snap.phase === "installing" ||
        snap.phase === "starting" ||
        snap.phase === "stopping";
      btns.start?.setDisabled(busy);
      btns.stop?.setButtonText(snap.phase === "stopping" ? s.setSidecarStopping : s.setSidecarStop);
      btns.stop?.setDisabled(snap.phase === "stopping");
      const canSave = snap.phase === "running";
      btns.save?.setDisabled(!canSave);
      btns.clear?.setDisabled(!canSave);
    };
    paintStatus();
    this.unwatch?.();
    this.unwatch = this.plugin.sidecarWatch(paintStatus);

    new Setting(containerEl)
      .setName(s.setSidecarSvc)
      .addButton((b) => {
        btns.start = b;
        b.setButtonText(s.setSidecarStart).onClick(() => {
          void this.plugin.ensureSidecar().then((kind) => {
            const msg = ensureNotice(kind);
            if (msg) new Notice(msg);
          });
        });
      })
      .addButton((b) => {
        btns.stop = b;
        b.setButtonText(s.setSidecarStop).onClick(() => {
          void this.plugin.stopSidecar().then((r) => new Notice(stopNotice(r)));
        });
      });
    paintStatus();

    new Setting(containerEl)
      .setName(s.setSidecarAutoName)
      .setDesc(s.setSidecarAutoDesc)
      .addToggle((c) =>
        c.setValue(this.plugin.settings.sidecarAutoStart !== false).onChange((v) => {
          this.plugin.settings.sidecarAutoStart = v;
          this.plugin.saveSettingsSoon();
        }),
      );

    new Setting(containerEl)
      .setName(s.setKeepAliveName)
      .setDesc(s.setKeepAliveDesc)
      .addToggle((c) =>
        c.setValue(this.plugin.settings.sidecarKeepAlive !== false).onChange((v) => {
          this.plugin.settings.sidecarKeepAlive = v;
          this.plugin.saveSettingsSoon();
        }),
      );

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

    // v0.18.0：钥匙只写不读。输入即 PUT 给 sidecar（落在它家目录的 llm.json，
    // 0600），本地任何文件——包括这份 data.json——都不存。
    const keyStatusEl = containerEl.createEl("p", { cls: "setting-item-description" });
    void this.paintKeyStatus(keyStatusEl);
    let clearBtnEl: HTMLElement | null = null;
    let saveBtnEl: HTMLElement | null = null;
    let submitKey = (): void => {};
    new Setting(containerEl)
      .setName("API Key")
      .setDesc(s.setApiKeyDesc)
      .addText((c) => {
        c.inputEl.type = "password";
        c.inputEl.autocomplete = "off";
        c.setPlaceholder("sk-…").setValue("");
        submitKey = () => {
          const v = c.getValue().trim();
          if (!v) return; // 清空输入 ≠ 清钥匙；要清点旁边的按钮
          const phase = this.plugin.sidecarSnap().phase;
          if (phase !== "running") {
            this.plugin.pendingPutKey = v;
            new Notice(phase === "stale" ? t().noticeKeySaveOldSidecar : t().noticeSidecarDown);
            return;
          }
          // 走插件的单通道（sidecarPutKey）：和挂起的启动迁移串行，
          // 谁也不会盖掉谁（二轮复审 P1）。
          void this.plugin
            .sidecarPutKey(v, this.plugin.settings.baseUrl)
            .then((llm) => {
              if (!llm) return;
              c.setValue("");
              this.plugin.pendingPutKey = "";
              // 设置页存进来的就是最新事实：挂起的启动迁移不许拿旧钥匙盖它
              this.plugin.migrateKey = "";
              void this.plugin.saveSettings();
              new Notice(t().noticeKeySaved);
              this.plugin.refreshPenViews();
              void this.paintKeyStatus(keyStatusEl);
              this.warnKeyHostMismatch(llm);
            })
            .catch((e: unknown) => {
              // 失败就失败在这儿：绝不回退写 data.json。输入留在框里。
              this.plugin.pendingPutKey = v;
              const status = e instanceof ApiError ? e.status : 0;
              if (status === 404 || status === 405) {
                new Notice(t().noticeKeySaveOldSidecar);
                return;
              }
              new Notice(
                t().noticeKeySaveFailed(e instanceof Error ? e.message : String(e)),
              );
            });
        };
        c.inputEl.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter") {
            ev.preventDefault();
            submitKey();
          }
        });
        c.inputEl.addEventListener("blur", (ev) => {
          // 焦点是去「保存」或「清除」：别让 blur 再 PUT 一次。
          const next = ev.relatedTarget as Node | null;
          if (clearBtnEl && next && clearBtnEl.contains(next)) return;
          if (saveBtnEl && next && saveBtnEl.contains(next)) return;
          submitKey();
        });
      })
      .addButton((b) => {
        btns.save = b;
        saveBtnEl = b.buttonEl;
        b.setButtonText(s.setKeySave).onClick(() => submitKey());
      })
      .addButton((b) => {
        btns.clear = b;
        clearBtnEl = b.buttonEl;
        b.setButtonText(s.setKeyClear).onClick(() => {
          if (this.plugin.sidecarSnap().phase !== "running") {
            new Notice(
              this.plugin.sidecarSnap().phase === "stale"
                ? t().noticeKeySaveOldSidecar
                : t().noticeSidecarDown,
            );
            return;
          }
          void makeApi(this.plugin.settings.sidecarUrl)
            .deleteLlmKey()
            .then(() => {
              this.plugin.migrateKey = "";
              this.plugin.pendingPutKey = "";
              void this.plugin.saveSettings();
              new Notice(t().noticeKeyCleared);
              this.plugin.refreshPenViews();
              void this.paintKeyStatus(keyStatusEl);
            })
            .catch((e: unknown) => {
              const status = e instanceof ApiError ? e.status : 0;
              new Notice(
                status === 404 || status === 405
                  ? t().noticeKeySaveOldSidecar
                  : t().noticeSidecarDown,
              );
            });
        });
      });
    paintStatus();

    new Setting(containerEl)
      .setName("Base URL")
      .setDesc(s.setBaseUrlDesc)
      .addText((c) => {
        c.setPlaceholder("https://api.deepseek.com")
          .setValue(this.plugin.settings.baseUrl)
          .onChange((v) => {
            // 空串先留着：全选再贴的瞬间若立刻回落默认 DeepSeek，
            // 会先存一次旧主机再触发换节点 Notice。
            this.plugin.settings.baseUrl = v.trim().replace(/\/+$/, "");
            if (this.plugin.settings.baseUrl) this.plugin.saveSettingsSoon();
          });
        // 换了主机就得换钥匙（钥匙按主机落锁）。on blur 而不是 onChange：
        // onChange 每个键击都探一次，敲一个长 URL 会被 Notice 轰炸。
        c.inputEl.addEventListener("blur", () => {
          if (!this.plugin.settings.baseUrl) {
            this.plugin.settings.baseUrl = DEFAULT_SETTINGS.baseUrl;
            c.setValue(this.plugin.settings.baseUrl);
            this.plugin.saveSettingsSoon();
          }
          void makeApi(this.plugin.settings.sidecarUrl)
            .health()
            .then((h) => this.warnKeyHostMismatch(h.llm))
            .catch(() => {});
        });
      });

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
      .setName(s.setVisionName)
      .setDesc(s.setVisionDesc)
      .addToggle((c) =>
        c.setValue(this.plugin.settings.vision === true).onChange((v) => {
          this.plugin.settings.vision = v;
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

    this.chipsSection(containerEl);

    // 高级区。用 <details> 而不是 toggle + this.display()：重画会违背
    // 「只有语言那一项才重画」的规矩，而且会把正在编辑的数字输入框的
    // 焦点和光标位置一起吃掉。展开状态不落盘 = 每次进来默认折叠。
    const adv = containerEl.createEl("details", { cls: "sp-set-advanced" });
    adv.createEl("summary", { text: s.setSecAdvanced });
    adv.createEl("p", { cls: "setting-item-description", text: s.setAdvancedNote });
    new Setting(adv)
      .setName(s.setSidecarPythonName)
      .setDesc(s.setSidecarPythonDesc)
      .addText((c) =>
        c
          .setPlaceholder("/usr/bin/python3")
          .setValue(this.plugin.settings.pythonPath)
          .onChange((v) => {
            this.plugin.settings.pythonPath = v.trim();
            this.plugin.saveSettingsSoon();
          }),
      );
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
