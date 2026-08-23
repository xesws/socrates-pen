import { Notice, Plugin, setTooltip, type Command, type Workspace, type WorkspaceLeaf } from "obsidian";
import { makeApi, purgeExpired } from "./api";
import { ApiError } from "./apierror";
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
import { SidecarManager } from "./sidecar";

// 旧版插件把 PenSettings 键直接写在 data.json 顶层，故顶层也要容忍这些键。
// v0.17.x 及更早的 apiKey 也容忍——读到只装进内存等迁移（见 migrateKeyOut），
// 永不写回。
type PluginData = Partial<PenSettings> & {
  apiKey?: string;
  settings?: Partial<PenSettings> & { apiKey?: string };
  notes?: Record<string, NoteBinding>;
};

export default class SocratesPenPlugin extends Plugin {
  settings: PenSettings = { ...DEFAULT_SETTINGS };
  notes: Record<string, NoteBinding> = {};
  /** 老 data.json 带出来的明文钥匙，等 sidecar 起来就迁走。只活在内存里。 */
  migrateKey = "";
  private saveTimer: number | null = null;
  private lastPick: EditorPick | null = null;
  private ribbonEl: HTMLElement | null = null;
  private cmdAsk: Command | null = null;
  private cmdOpen: Command | null = null;
  readonly sidecar = new SidecarManager();

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
    if (this.settings.sidecarAutoStart !== false) {
      void this.ensureSidecar().catch(() => {});
    }
    if (this.migrateKey) void this.migrateKeyOut();
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
    // 默认保活（多库共用一只 sidecar，A 退场不该杀 B 在用的）。
    // 关掉保活的读者明确选择了「退出就停」——只停我们自己拉起的那只。
    if (this.settings.sidecarKeepAlive === false) this.sidecar.stopOwned();
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
    // 右侧栏折叠时 setActiveLeaf 什么都不露，点丝带像没反应（评测报告 P1）。
    // revealLeaf 1.7.2 才有——minAppVersion 1.5.0，能力探测，老版本掰 rightSplit。
    try {
      const ws = this.app.workspace as Workspace & {
        revealLeaf?: (leaf: WorkspaceLeaf) => void;
      };
      if (typeof ws.revealLeaf === "function") ws.revealLeaf(leaf);
      else {
        const split = this.app.workspace as unknown as {
          rightSplit?: { collapsed?: boolean };
        };
        if (split.rightSplit) split.rightSplit.collapsed = false;
      }
    } catch {
      new Notice(t().noticeRightOpened);
    }
    await leaf.setViewState({ type: VIEW_TYPE_PEN, active: true });
    // revealLeaf 从 1.7.2 才有，minAppVersion 是 1.5.0。
    this.app.workspace.setActiveLeaf(leaf, { focus: true });
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
    if (raw.baseUrl !== undefined) legacy.baseUrl = raw.baseUrl;
    if (raw.model !== undefined) legacy.model = raw.model;
    if (raw.thinking !== undefined) legacy.thinking = raw.thinking;
    this.migrateKey = String(
      (raw.settings && raw.settings.apiKey) ?? raw.apiKey ?? "",
    ).trim();
    this.settings = {
      ...DEFAULT_SETTINGS,
      ...legacy,
      ...(raw.settings || {}),
      // 展开是**浅**的：data.json 里只存了一半的 limits 会把另一半整个吃掉。
      // coerceLimits 自己也会补全，这里再显式深一层是双保险——少写这一层，
      // 「只改过一个数」的库会静默丢掉其余十几个自定义值。
      limits: { ...DEFAULT_SETTINGS.limits, ...((raw.settings || {}).limits || {}) },
    };
    // 嵌套展开在运行时仍会把 apiKey 带进来（类型看不见，磁盘看得见），显式拔掉。
    delete (this.settings as PenSettings & { apiKey?: string }).apiKey;
    this.settings.thinking = coerceThinking(this.settings.thinking);
    this.settings.lang = coerceLangPref(this.settings.lang);
    this.settings.sidecarAutoStart = this.settings.sidecarAutoStart !== false;
    this.settings.sidecarKeepAlive = this.settings.sidecarKeepAlive !== false;
    this.settings.pythonPath =
      typeof this.settings.pythonPath === "string" ? this.settings.pythonPath.trim() : "";
    // 手改过、或者被 Sync 弄坏的 data.json 会带来字符串、null、NaN。
    this.settings.limits = coerceLimits(this.settings.limits);
    this.notes = raw.notes || {};
  }

  sidecarSnap() {
    return this.sidecar.snapshot();
  }

  sidecarError(): string {
    return this.sidecar.lastError();
  }

  sidecarWatch(fn: () => void): () => void {
    return this.sidecar.watch(fn);
  }

  ensureSidecar(): Promise<void> {
    return this.sidecar.ensure({
      sidecarUrl: this.settings.sidecarUrl,
      pythonPath: this.settings.pythonPath,
      version: this.manifest.version,
      autoStart: this.settings.sidecarAutoStart,
    });
  }

  /** 把老 data.json 里的明文钥匙迁进 sidecar 家目录，然后从磁盘抹掉。
   *
   * PUT 重试 45 秒（sidecar 可能还在装/起）。旧版 sidecar 不认这个端点
   * （404/405）时把「先升级那只进程」的动作交给读者——不自动杀别人的
   * 进程是既定政策（README）。失败整场放弃：钥匙仍留在 data.json
   * （saveSettings 保写），下次启动再迁。 */
  private async migrateKeyOut(): Promise<void> {
    const key = this.migrateKey;
    const api = makeApi(this.settings.sidecarUrl);
    const t0 = Date.now();
    let warnedOld = false;
    for (;;) {
      try {
        await api.putLlmKey(key, this.settings.baseUrl);
        break;
      } catch (e) {
        const status = e instanceof ApiError ? e.status : 0;
        if ((status === 404 || status === 405) && !warnedOld) {
          warnedOld = true;
          new Notice(t().noticeSidecarTooOld);
        }
        if (Date.now() - t0 > 45000) return;
        await new Promise((r) => setTimeout(r, 2000));
      }
    }
    this.migrateKey = "";
    // 这次起，saveSettings 写出的 data.json 不再带 apiKey
    await this.saveSettings();
    new Notice(t().noticeKeyMigrated);
  }

  stopOwnedSidecar(): void {
    this.sidecar.stopOwned();
  }

  /** 设置页存/清钥匙之后叫醒所有打开的面板重探 llmOk——不然灰着的 chip
   * 要等下一次「用当前选区」才恢复，读者以为没存上。 */
  refreshPenViews(): void {
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE_PEN)) {
      const view = leaf.view;
      if (view instanceof PenView) void view.probeHealth();
    }
  }

  async saveSettings(): Promise<void> {
    // 迁移没成功之前，盘上那把明文钥匙必须原样跟着写回。PUT 一旦失败，
    // data.json 里那份就是唯一副本（内存里的 migrateKey 关掉 Obsidian 就没了）——
    // 「绝不回写明文」指的是不新增明文落点，不是把已经在盘上的那把弄丢。
    // 首次「用当前选区」就会走这里（bindNote），不能指望迁移先完成。
    const settings = this.migrateKey
      ? { ...this.settings, apiKey: this.migrateKey }
      : this.settings;
    await this.saveData({ settings, notes: this.notes });
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
