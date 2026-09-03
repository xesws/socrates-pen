import { MarkdownView, Notice, Plugin, setTooltip, type Command, type WorkspaceLeaf } from "obsidian";
import { makeApi, purgeExpired } from "./api";
import { coerceCustomChips } from "./customchips";
import { ApiError } from "./apierror";
import {
  coerceLimits,
  coerceProvider,
  coerceThinking,
  DEFAULT_SETTINGS,
  PenSettingTab,
  persistableSettings,
  type PenSettings,
} from "./settings";
import { readLivePick, type EditorPick } from "./selection";
import type { NoteBinding, LlmStatus } from "./types";
import { PenView, VIEW_TYPE_PEN } from "./views/PenView";
import { ReportView, VIEW_TYPE_REPORT } from "./views/ReportView";
import { coerceLangPref, resolveLang, setLang, t } from "./i18n";
import { SidecarManager, type EnsureKind, type StopResult } from "./sidecar";

// 旧版插件把 PenSettings 键直接写在 data.json 顶层，故顶层也要容忍这些键。
// v0.17.x 及更早的 apiKey 也容忍——读到只装进内存等迁移（见 migrateKeyOut）。
// 迁移**没成功之前**，saveSettings 会把它原样写回（盘上那份是唯一副本）；
// 成功之后才不再出现在落盘形状里（persistableSettings 负责这个分支）。
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
  /** 设置页 PUT 失败后的内存暂存，升完自动写入。绝不进 data.json。 */
  pendingPutKey = "";
  private migrating = false;
  /** saveSettings 串行链：迁移收尾和 bindNote 会几乎同时各存一次，
   *  让后快照的写入总是最后落盘——不然迁移成功后，一张在途的带钥匙
   *  快照可能后落，明文又回到 data.json（二轮复审 P1）。 */
  private saveChain: Promise<void> = Promise.resolve();
  /** 钥匙 PUT 串行链（见 sidecarPutKey）。 */
  private putChain: Promise<unknown> = Promise.resolve();
  private saveTimer: number | null = null;
  private lastPick: EditorPick | null = null;
  private ribbonEl: HTMLElement | null = null;
  private cmdAsk: Command | null = null;
  private cmdOpen: Command | null = null;
  private cmdCompact: Command | null = null;
  private cmdReport: Command | null = null;
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
    this.registerView(VIEW_TYPE_REPORT, (leaf) => new ReportView(leaf, this));
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
    this.cmdCompact = this.addCommand({
      id: "socrates-pen-compact",
      name: t().cmdCompactSession,
      callback: () => {
        void this.activateView().then((view) => view.compactSession());
      },
    });
    this.cmdReport = this.addCommand({
      id: "socrates-pen-report",
      name: t().cmdOpenReport,
      callback: () => {
        void this.activateReport();
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
    if (this.cmdCompact) this.cmdCompact.name = prefix + s.cmdCompactSession;
    if (this.cmdReport) this.cmdReport.name = prefix + s.cmdOpenReport;
    const leaves = [
      ...this.app.workspace.getLeavesOfType(VIEW_TYPE_PEN),
      ...this.app.workspace.getLeavesOfType(VIEW_TYPE_REPORT),
    ];
    for (const leaf of leaves) {
      const view = leaf.view;
      if (view instanceof PenView || view instanceof ReportView) view.relocalize();
      // updateHeader 未进 .d.ts，但它是刷新 tab 标题的直接办法；
      // 兜底走公开 API：type 相同且非 deferred 时 setViewState 不会重建 view，
      // 且 getViewState() 不含 active，不会抢焦点。
      const withHeader = leaf as WorkspaceLeaf & { updateHeader?: () => void };
      if (typeof withHeader.updateHeader === "function") withHeader.updateHeader();
      else void leaf.setViewState(leaf.getViewState());
    }
  }

  /**
   * 把某种视图露到右侧栏。两个视图（对话面板、学习画像）共用这一份，
   * 「三步」只写在这儿：
   * 右侧栏折叠时 setActiveLeaf 什么都不露，点丝带像没反应（评测报告 P1）。
   * 只用 rightSplit.collapsed：revealLeaf 1.7.2 才有，商店审核的
   * no-unsupported-api 闸按 @since 标注拦引用（哪怕在能力探测的 cast 里），
   * minAppVersion 1.5.0 下不能出现它（v0.18.6 审核打回的正是这行）。
   */
  private async revealSide(type: string): Promise<WorkspaceLeaf> {
    const existing = this.app.workspace.getLeavesOfType(type);
    const leaf = existing[0] ?? this.app.workspace.getRightLeaf(false);
    if (!leaf) throw new Error(t().errNoRightLeaf);
    try {
      this.app.workspace.rightSplit.collapsed = false;
    } catch {
      new Notice(t().noticeRightOpened);
    }
    await leaf.setViewState({ type, active: true });
    // revealLeaf 从 1.7.2 才有，minAppVersion 是 1.5.0。
    this.app.workspace.setActiveLeaf(leaf, { focus: true });
    return leaf;
  }

  async activateView(): Promise<PenView> {
    const leaf = await this.revealSide(VIEW_TYPE_PEN);
    const view = leaf.view;
    if (!(view instanceof PenView)) throw new Error(t().errViewNotMounted);
    return view;
  }

  /** 学习画像页签。已开着就切过去，没开就在右侧栏新开一个（对话面板留在原位）。 */
  async activateReport(): Promise<ReportView> {
    const leaf = await this.revealSide(VIEW_TYPE_REPORT);
    const view = leaf.view;
    if (!(view instanceof ReportView)) throw new Error(t().errViewNotMounted);
    return view;
  }

  cachePick(): void {
    const p = readLivePick(this.app);
    if (p) this.lastPick = p;
  }

  takePick(): EditorPick | null {
    const live = readLivePick(this.app);
    if (live) return live;
    const active = this.app.workspace.getActiveFile();
    if (!active || !this.lastPick || this.lastPick.file.path !== active.path) {
      return null;
    }
    // lastPick 只救预览模式（0.18.5 复测）：编辑器选区是状态、失焦不丢，
    // 拿不到就是读者真把选区收了——回退会把上一次的选段「召回」，无选中
    // 跑命令本该提示「先划一段」。预览模式的 DOM 选区一点面板就塌，
    // 缓存在那条路上才是必要的。
    // 模式从目标笔记自己的 markdown leaf 上读（复审 P2）：面板聚焦时
    // getActiveViewOfType 是 null，不能拿它当探测点。
    const leaf = this.app.workspace
      .getLeavesOfType("markdown")
      .find((l) => (l.view as MarkdownView).file?.path === active.path);
    const view = leaf?.view;
    return view instanceof MarkdownView && view.getMode?.() === "preview"
      ? this.lastPick
      : null;
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
      // 同理：数组不是对象，浅展开碰不到它，旧库里根本没这个键 —— 不显式给一层，
      // this.settings.customChips 是 undefined，paintChips 里一个 .filter 就炸。
      customChips: (raw.settings || {}).customChips || [],
    };
    // 嵌套展开在运行时仍会把 apiKey 带进来（类型看不见，磁盘看得见），显式拔掉。
    delete (this.settings as PenSettings & { apiKey?: string }).apiKey;
    this.settings.thinking = coerceThinking(this.settings.thinking);
    // 认不得的厂商当没选（"auto"），不抛——设置项绝不能把一轮对话弄挂。
    this.settings.provider = coerceProvider(this.settings.provider);
    this.settings.fastProvider = coerceProvider(this.settings.fastProvider);
    this.settings.vision = this.settings.vision === true;
    // 三格快模型配置。**开关默认关**：没配钥匙时开着也只是不生效，
    // 但默认打开等于替读者做了一个「这一轮由小模型执笔」的决定。
    this.settings.fastMode = this.settings.fastMode === true;
    this.settings.fastBaseUrl =
      typeof this.settings.fastBaseUrl === "string"
        ? this.settings.fastBaseUrl.trim().replace(/\/+$/, "") || DEFAULT_SETTINGS.fastBaseUrl
        : DEFAULT_SETTINGS.fastBaseUrl;
    this.settings.fastModel =
      typeof this.settings.fastModel === "string"
        ? this.settings.fastModel.trim() || DEFAULT_SETTINGS.fastModel
        : DEFAULT_SETTINGS.fastModel;
    this.settings.lang = coerceLangPref(this.settings.lang);
    this.settings.sidecarAutoStart = this.settings.sidecarAutoStart !== false;
    this.settings.sidecarKeepAlive = this.settings.sidecarKeepAlive !== false;
    this.settings.pythonPath =
      typeof this.settings.pythonPath === "string" ? this.settings.pythonPath.trim() : "";
    this.settings.baseUrl =
      typeof this.settings.baseUrl === "string"
        ? this.settings.baseUrl.trim().replace(/\/+$/, "") || DEFAULT_SETTINGS.baseUrl
        : DEFAULT_SETTINGS.baseUrl;
    // 手改过、或者被 Sync 弄坏的 data.json 会带来字符串、null、NaN。
    this.settings.limits = coerceLimits(this.settings.limits);
    // 同上：读者手改的、或被 Sync 合坏的自定义泡泡表。绝不抛，脏的就地夹紧。
    this.settings.customChips = coerceCustomChips(this.settings.customChips);
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

  ensureSidecar(): Promise<EnsureKind> {
    const p = this.sidecar.ensure({
      sidecarUrl: this.settings.sidecarUrl,
      pythonPath: this.settings.pythonPath,
      version: this.manifest.version,
      autoStart: this.settings.sidecarAutoStart,
    });
    // 上一次迁移 45 秒到点放弃后，读者在设置页点「启动」把 sidecar 换成
    // 新版——这一刻该接着迁，而不是等他重启 Obsidian（二轮复审 P2）。
    void p
      .then((kind) => {
        if (kind === "already" || kind === "started") {
          if (this.pendingPutKey) void this.flushPendingKey();
          else if (this.migrateKey) void this.migrateKeyOut();
        }
      })
      .catch(() => {});
    return p;
  }

  /** 把老 data.json 里的明文钥匙迁进 sidecar 家目录，然后从磁盘抹掉。
   *
   * PUT 重试 45 秒（sidecar 可能还在装/起）。每轮**重读** this.migrateKey：
   * 读者若在这个窗口里自己去设置页贴了新钥匙（noticeSidecarTooOld 指的路
   * 正是这个），旧钥匙的 PUT 不许再落进 llm.json 把它盖掉——发现已被清空
   * 或换过就整场退出。404/405 或 stale：指路停再启动，不再对着旧服务锤 45 秒。
   * 自动路径绝不杀非本插件拉起的进程；设置页「停止」才按端口停。 */
  private async migrateKeyOut(): Promise<void> {
    if (this.migrating) return;
    this.migrating = true;
    try {
      const t0 = Date.now();
      let warnedOld = false;
      for (;;) {
        const key = this.migrateKey;
        if (!key) return; // 设置页已存/清过，那份事实比我们手里的旧钥匙新
        if (this.sidecar.snapshot().phase === "stale") {
          if (!warnedOld) new Notice(t().noticeSidecarTooOld);
          return;
        }
        let llm: LlmStatus | null;
        try {
          llm = await this.sidecarPutKey(key, this.settings.baseUrl, key);
        } catch (e) {
          const status = e instanceof ApiError ? e.status : 0;
          if (status === 404 || status === 405) {
            new Notice(t().noticeSidecarTooOld);
            return;
          }
          if (Date.now() - t0 > 45000) {
            new Notice(t().noticeKeyMigrateTimeout);
            return;
          }
          await new Promise((r) => setTimeout(r, 2000));
          continue;
        }
        // null = 排队期间读者自己存了新钥匙，这场迁移让位。
        if (llm === null || this.migrateKey !== key) return;
        break;
      }
      this.migrateKey = "";
      // 这次起，saveSettings 写出的 data.json 不再带 apiKey
      await this.saveSettings();
      new Notice(t().noticeKeyMigrated);
    } finally {
      this.migrating = false;
    }
  }

  stopSidecar(): Promise<StopResult> {
    return this.sidecar.stopListen(this.settings.sidecarUrl);
  }

  /** 设置页 PUT 失败后的内存暂存，版本对齐后再写一次。不落 data.json。 */
  private async flushPendingKey(): Promise<void> {
    const key = this.pendingPutKey;
    if (!key) return;
    if (this.sidecar.snapshot().phase !== "running") return;
    try {
      const llm = await this.sidecarPutKey(key, this.settings.baseUrl);
      if (!llm || this.pendingPutKey !== key) return;
      this.pendingPutKey = "";
      this.migrateKey = "";
      await this.saveSettings();
      new Notice(t().noticeKeySaved);
      this.refreshPenViews();
    } catch {
      /* 下次 Start 再试；设置页输入还在 */
    }
  }

  /** 钥匙 PUT 的唯一通道：串行 + 迁移方落地前重读。
   *
   * 迁移循环和设置页共用这一条队列，谁的 PUT 都插不进另一方的在途请求
   * （否则后落地的旧钥匙会盖掉读者刚存的新钥匙）；迁移方（onlyIfMigrateIs）
   * 在真正发出前再核一次 migrateKey，已被换掉就返回 null 整场退出。 */
  sidecarPutKey(
    key: string,
    baseUrl: string,
    onlyIfMigrateIs?: string,
  ): Promise<LlmStatus | null> {
    const run = this.putChain.then(async () => {
      if (onlyIfMigrateIs !== undefined && this.migrateKey !== onlyIfMigrateIs) return null;
      return makeApi(this.settings.sidecarUrl).putLlmKey(key, baseUrl);
    });
    this.putChain = run.catch(() => {});
    return run;
  }

  /** 快模型钥匙的 PUT。走**同一条** putChain：和基座那把串行，谁也盖不掉谁。
   *
   * 两把钥匙落在同一个 llm.json 的两个槽里，后端是读-改-写。并发 PUT 会让
   * 后写的那次拿着旧内容覆盖——串起来就没有这条缝。 */
  sidecarPutFastKey(key: string, baseUrl: string): Promise<LlmStatus | null> {
    const run = this.putChain.then(async () =>
      makeApi(this.settings.sidecarUrl).putFastKey(key, baseUrl),
    );
    this.putChain = run.catch(() => {});
    return run;
  }

  /** 顶栏那枚 Fast Mode 开关的同步口。
   *
   * 和 refreshPenViews 分开的理由同 refreshChips：那个走 probeHealth()，是一次
   * 网络往返。为了刷一个 class 打一发 /v1/health 不值当，而读者完全可能是在
   * 流式期间去设置页改的。 */
  refreshFast(): void {
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE_PEN)) {
      const view = leaf.view;
      if (view instanceof PenView) view.onFastModeChanged();
    }
  }

  /** 设置页改完自定义泡泡之后叫醒所有打开的面板重画那排按钮。
   *
   * 和上面的 refreshPenViews 分开而不是合并：那个走 probeHealth()，是一次
   * 网络往返；改个泡泡名字不该顺带打一次 /v1/health。视图那边也只重画芯片
   * 一条，不碰底座——读者完全可能是在流式期间去设置页改的。 */
  refreshChips(): void {
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE_PEN)) {
      const view = leaf.view;
      if (view instanceof PenView) view.onCustomChipsChanged();
    }
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
    const run = this.saveChain.then(() =>
      this.saveData({ settings: persistableSettings(this.settings, this.migrateKey), notes: this.notes }),
    );
    // 链只吞这次 saveData 的错，别让一次失败掐死后面所有保存。
    this.saveChain = run.catch(() => {});
    return run;
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
