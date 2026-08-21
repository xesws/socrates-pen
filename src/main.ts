import { Notice, Plugin, setTooltip, type Command, type WorkspaceLeaf } from "obsidian";
import { makeApi, purgeExpired } from "./api";
import {
  coerceLimits,
  coerceThinking,
  DEFAULT_SETTINGS,
  PenSettingTab,
  type PenSettings,
} from "./settings";
import { readLivePick, type EditorPick } from "./selection";
import type { NoteBinding } from "./types";
import { PenView, VIEW_TYPE_PEN } from "./views/PenView";
import { coerceLangPref, resolveLang, setLang, t } from "./i18n";

// 旧版插件把 PenSettings 键直接写在 data.json 顶层，故顶层也要容忍这些键
type PluginData = Partial<PenSettings> & {
  settings?: Partial<PenSettings>;
  notes?: Record<string, NoteBinding>;
};

export default class SocratesPenPlugin extends Plugin {
  settings: PenSettings = { ...DEFAULT_SETTINGS };
  notes: Record<string, NoteBinding> = {};
  private saveTimer: number | null = null;
  private lastPick: EditorPick | null = null;
  private ribbonEl: HTMLElement | null = null;
  private cmdAsk: Command | null = null;
  private cmdOpen: Command | null = null;

  async onload(): Promise<void> {
    await this.loadSettings();
    // 必须在注册 ribbon 和命令之前：它们的文案在注册那一刻就定下来了
    setLang(resolveLang(this.settings.lang));
    // 「每次启动插件的时候都要自动清理一下」的插件那一半。
    //
    // 为什么不能只靠 sidecar 的 lifespan：**插件不拉起 sidecar**。它可能已经在
    // 后台跑了好几天，读者重启 Obsidian 时它的 lifespan 早就跑完了。
    //
    // fire-and-forget，而且吞掉所有错：sidecar 没起、端口填错、跨域——
    // 一样都不该让插件加载失败，也不该弹一个读者此刻做不了任何事的通知。
    void purgeExpired(this.settings.sidecarUrl).catch(() => {});
    this.registerView(VIEW_TYPE_PEN, (leaf) => new PenView(leaf, this));
    this.addSettingTab(new PenSettingTab(this.app, this));
    this.registerDomEvent(document, "selectionchange", () => this.cachePick());
    this.registerDomEvent(document, "mouseup", () => this.cachePick());
    // 注册标题必须**语言无关**：ribbon 项的内部 id 是 manifest.id + ":" + title，
    // 用户在「外观 → 功能区」里的排序和隐藏状态按这个 id 存。换语言就换标题
    // 会让那份配置变成孤儿。所以写死品牌名，真正的文案用 setTooltip 覆盖。
    this.ribbonEl = this.addRibbonIcon("highlighter", "Socrates", () => {
      void this.activateView();
    });
    setTooltip(this.ribbonEl, t().ribbonTooltip, { placement: "right" });
    this.cmdAsk = this.addCommand({
      id: "socrates-pen-ask-selection",
      name: t().cmdAskSelection,
      callback: () => {
        const pick = this.takePick();
        void this.activateView().then(async (view) => {
          await view.captureSelection(pick);
        });
      },
    });
    this.cmdOpen = this.addCommand({
      id: "socrates-pen-open",
      name: t().cmdOpenPanel,
      callback: () => {
        void this.activateView();
      },
    });
  }

  onunload(): void {
    /* views unregistered by host */
    if (this.saveTimer !== null) {
      window.clearTimeout(this.saveTimer);
      this.saveTimer = null;
      void this.saveSettings();
    }
  }

  /**
   * 语言改了之后就地刷新，不需要重启插件。
   *
   * 命令名：Plugin.addCommand 在注册那一刻做过一次 `manifest.name + ": "`，
   * 之后改名要自己把前缀补回来。id 不变，所以用户已绑的快捷键不受影响；
   * 命令面板每次打开都重读 name，下次打开就是新语言。
   */
  applyLanguage(): void {
    setLang(resolveLang(this.settings.lang));
    const s = t();
    if (this.ribbonEl) setTooltip(this.ribbonEl, s.ribbonTooltip, { placement: "right" });
    const prefix = `${this.manifest.name}: `;
    if (this.cmdAsk) this.cmdAsk.name = prefix + s.cmdAskSelection;
    if (this.cmdOpen) this.cmdOpen.name = prefix + s.cmdOpenPanel;
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE_PEN)) {
      const view = leaf.view;
      if (view instanceof PenView) view.relocalize();
      // updateHeader 未进 .d.ts，但它是刷新 tab 标题的直接办法；
      // 兜底走公开 API：type 相同且非 deferred 时 setViewState 不会重建 view，
      // 且 getViewState() 不含 active，不会抢焦点。
      const withHeader = leaf as WorkspaceLeaf & { updateHeader?: () => void };
      if (typeof withHeader.updateHeader === "function") withHeader.updateHeader();
      else void leaf.setViewState(leaf.getViewState());
    }
  }

  async activateView(): Promise<PenView> {
    const existing = this.app.workspace.getLeavesOfType(VIEW_TYPE_PEN);
    const leaf = existing[0] ?? this.app.workspace.getRightLeaf(false);
    if (!leaf) throw new Error(t().errNoRightLeaf);
    await leaf.setViewState({ type: VIEW_TYPE_PEN, active: true });
    this.app.workspace.revealLeaf(leaf);
    const view = leaf.view;
    if (!(view instanceof PenView)) throw new Error(t().errViewNotMounted);
    return view;
  }

  cachePick(): void {
    const p = readLivePick(this.app);
    if (p) this.lastPick = p;
  }

  takePick(): EditorPick | null {
    return readLivePick(this.app) ?? this.lastPick;
  }

  clearPick(): void {
    this.lastPick = null;
  }

  noteBind(path: string): NoteBinding | undefined {
    return this.notes[path];
  }

  async bindNote(path: string, bind: NoteBinding): Promise<void> {
    this.notes[path] = bind;
    await this.saveSettings();
  }

  async loadSettings(): Promise<void> {
    const raw = ((await this.loadData()) || {}) as PluginData;
    // 旧版顶层键收进来当后备；嵌套 settings 里已给的键以嵌套为准
    const legacy: Partial<PenSettings> = {};
    if (raw.sidecarUrl !== undefined) legacy.sidecarUrl = raw.sidecarUrl;
    if (raw.apiKey !== undefined) legacy.apiKey = raw.apiKey;
    if (raw.baseUrl !== undefined) legacy.baseUrl = raw.baseUrl;
    if (raw.model !== undefined) legacy.model = raw.model;
    if (raw.thinking !== undefined) legacy.thinking = raw.thinking;
    this.settings = {
      ...DEFAULT_SETTINGS,
      ...legacy,
      ...(raw.settings || {}),
      // 展开是**浅**的：data.json 里只存了一半的 limits 会把另一半整个吃掉。
      // coerceLimits 自己也会补全，这里再显式深一层是双保险——少写这一层，
      // 「只改过一个数」的库会静默丢掉其余十几个自定义值。
      limits: { ...DEFAULT_SETTINGS.limits, ...((raw.settings || {}).limits || {}) },
    };
    this.settings.thinking = coerceThinking(this.settings.thinking);
    this.settings.lang = coerceLangPref(this.settings.lang);
    // 手改过、或者被 Sync 弄坏的 data.json 会带来字符串、null、NaN。
    this.settings.limits = coerceLimits(this.settings.limits);
    this.notes = raw.notes || {};
  }

  async saveSettings(): Promise<void> {
    await this.saveData({ settings: this.settings, notes: this.notes });
  }

  saveSettingsSoon(): void {
    if (this.saveTimer !== null) window.clearTimeout(this.saveTimer);
    this.saveTimer = window.setTimeout(() => {
      this.saveTimer = null;
      void this.saveSettings();
    }, 350);
  }

  async pingOrNotice(): Promise<boolean> {
    try {
      await makeApi(this.settings.sidecarUrl).health();
      return true;
    } catch {
      new Notice(t().noticeSidecarDown);
      return false;
    }
  }
}
