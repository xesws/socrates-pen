import { ItemView, TFile, WorkspaceLeaf, getIcon as lucideIcon } from "obsidian";
import type SocratesPenPlugin from "../main";
import { ApiError, makeApi } from "../api";
import { absFor, handbookIdFromPath, vaultRoot } from "../selection";
import { t } from "../i18n";
import { layoutRadar, tonesOf } from "../radar";
import { localStamp, masteryPct, mergeVaultRows, type VaultRow } from "../profile";
import { sidecarUsable } from "../sidecar";
import type { ProfileAxis, ProfileView, TokenRow } from "../types";

export const VIEW_TYPE_REPORT = "socrates-pen-report";

/**
 * 连续几枪 `coded == 0` 而 `remaining > 0` 就停。= 服务端 MAX_ATTEMPTS：模型一直
 * 吐坏 JSON 时，第三枪服务端会放弃那批、remaining 归零，循环正常收口；服务端真有
 * bug 不推进时也最多三枪就停——**每一枪都是真实的 API 调用**，不能无限计费。
 */
const STALL_MAX = 3;

let cachedIcon: string | null = null;
/** `radar` 图标 1.5.x 那版 lucide 里未必有；没有就退到 `target`。懒算一次。
 *  PenView 的第六枚按钮和这个视图的页签图标都用它——一个定义点。 */
export function reportIconName(): string {
  if (cachedIcon === null) cachedIcon = lucideIcon("radar") ? "radar" : "target";
  return cachedIcon;
}

const spendTokens = (row: TokenRow | undefined | null): number =>
  (row?.in_tokens ?? 0) + (row?.out_tokens ?? 0);

type State =
  | "loading"
  | "nofile"
  | "unregistered"
  | "empty"
  | "fresh"
  | "coding"
  | "report"
  | "down";

/** 长生命周期的元素句柄。onClose 只写一行就不可能漏。 */
type Els = {
  head: HTMLElement;
  sub: HTMLElement;
  alert: HTMLElement;
  note: HTMLElement;
  actions: HTMLElement;
  analyze: HTMLButtonElement;
  recompute: HTMLButtonElement;
  confirm: HTMLButtonElement;
  cancel: HTMLButtonElement;
  stop: HTMLButtonElement;
  hint: HTMLElement;
  bar: HTMLElement;
  fill: HTMLElement;
  progress: HTMLElement;
  radar: HTMLElement;
  axes: HTMLElement;
  vaultTitle: HTMLElement;
  vaultNote: HTMLElement;
  vault: HTMLElement;
};

/**
 * 学习画像面板（v0.25.0）。
 *
 * 分数一个都不在这儿算：雷达的数、掌握概率、「这一分怎么来的」全是
 * `GET /profile` 给的，这里只画。本视图不写笔记、不碰 working directory，
 * 唯一会花钱的动作是 `codeProfile`——第一次全量要读者点一下，之后打开
 * 面板增量补编（每一枪都有停滞保护）。
 */
export class ReportView extends ItemView {
  plugin: SocratesPenPlugin;
  private els: Els | null = null;
  private path: string | null = null;
  private title = "";
  private hid: string | null = null;
  private state: State = "nofile";
  private view: ProfileView | null = null;
  private err = "";
  private note = "";
  private llmOk = false;
  private progress: { coded: number; total: number; tokens: number } | null = null;
  private confirming = false;
  /** 读者点过「停止」：打开面板不再自动续编，等他点「继续分析」。切笔记就清。 */
  private paused = false;
  private vaultRows: VaultRow[] | null = null;
  private vaultErr = "";
  /** 在途加载/编码的代次。任何新的 retarget 都会使旧代次作废——两个在飞的
   *  请求谁后完成都不许覆盖新的事实。 */
  private runGen = 0;
  private abort: AbortController | null = null;

  constructor(leaf: WorkspaceLeaf, plugin: SocratesPenPlugin) {
    super(leaf);
    this.plugin = plugin;
  }

  getViewType(): string {
    return VIEW_TYPE_REPORT;
  }

  getDisplayText(): string {
    return t().viewTitleReport;
  }

  getIcon(): string {
    return reportIconName();
  }

  async onOpen(): Promise<void> {
    this.registerEvent(
      this.app.workspace.on("file-open", (file) => {
        // 只跟 Markdown：canvas/图片/embed 也会触发 file-open，跟过去会把面板清成空态。
        if (!file || file.extension !== "md") return;
        if (file.path === this.path) return;
        void this.retarget(file);
      }),
    );
    this.renderShell();
    this.follow();
  }

  async onClose(): Promise<void> {
    this.cancel();
    this.els = null;
  }

  /** 切语言：重建骨架、按现有状态重画。`err` 可能是 sidecar 原文，保留不动。 */
  relocalize(): void {
    this.renderShell();
  }

  // ── 生命周期 ────────────────────────────────────────────────────

  private alive(gen: number): boolean {
    return gen === this.runGen && this.els !== null;
  }

  private cancel(): void {
    this.runGen++;
    this.abort?.abort();
    this.abort = null;
  }

  /** 作废旧代次，开新的一代。 */
  private begin(): { gen: number; signal: AbortSignal } {
    this.cancel();
    const ac = new AbortController();
    this.abort = ac;
    return { gen: this.runGen, signal: ac.signal };
  }

  private api() {
    return makeApi(this.plugin.settings.sidecarUrl);
  }

  private static msg(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  /** 开面板时同步当前文件：file-open 在面板开着之前就发过了。 */
  private follow(): void {
    const f = this.app.workspace.getActiveFile();
    if (f && f.extension === "md") {
      void this.retarget(f);
      return;
    }
    const { gen, signal } = this.begin();
    this.path = null;
    this.title = "";
    this.hid = null;
    this.view = null;
    this.err = "";
    this.note = "";
    this.state = "nofile";
    this.paint();
    void this.loadVault(gen, signal);
  }

  private async retarget(file: TFile): Promise<void> {
    const { gen, signal } = this.begin();
    this.path = file.path;
    this.title = file.basename;
    let hid: string | null = null;
    try {
      // 已登记的笔记用登记时那个 id；没登记的按同一套推导算——id 只有一份定义。
      hid = this.plugin.noteBind(file.path)?.handbook_id ?? handbookIdFromPath(absFor(this.app, file));
    } catch {
      hid = null; // 非桌面库
    }
    this.hid = hid;
    this.view = null;
    this.err = "";
    this.note = "";
    this.progress = null;
    this.confirming = false;
    this.paused = false;
    this.state = "loading";
    this.paint();
    await Promise.all([this.loadProfile(gen, signal), this.loadVault(gen, signal)]);
  }

  private async loadProfile(gen: number, signal: AbortSignal): Promise<void> {
    const api = this.api();
    try {
      const h = await api.health({ signal });
      if (!this.alive(gen)) return;
      if (!sidecarUsable(h.version, this.plugin.manifest.version)) {
        this.llmOk = false;
        this.state = "down";
        const stale = t().healthStale;
        this.err = t().errReportUnreachable(stale);
        this.paint();
        return;
      }
      this.llmOk = Boolean(h.llm.ok);
    } catch (e) {
      if (!this.alive(gen)) return;
      this.llmOk = false;
      this.state = "down";
      this.err = t().errReportUnreachable(ReportView.msg(e));
      this.paint();
      return;
    }
    if (!this.hid) {
      this.state = "nofile";
      this.paint();
      return;
    }
    let v: ProfileView;
    try {
      v = await api.getProfile(this.hid, signal);
    } catch (e) {
      if (!this.alive(gen)) return;
      // 404 不是错误：这篇笔记还没登记过，画像自然没有。
      if (e instanceof ApiError && e.status === 404) {
        this.state = "unregistered";
        this.paint();
        return;
      }
      this.state = "down";
      this.err = t().errReportFailed(ReportView.msg(e));
      this.paint();
      return;
    }
    if (!this.alive(gen)) return;
    this.view = v;
    if (v.n_turns === 0) {
      this.state = "empty";
      this.paint();
      return;
    }
    if (v.n_coded === 0) {
      // 第一次全量要点一下：按轮计费，不能一打开面板就替读者花钱。
      this.state = "fresh";
      this.note = this.llmOk ? "" : t().reportNoKeyHint;
      this.paint();
      return;
    }
    if (v.n_uncoded > 0 && this.llmOk && !this.paused) {
      await this.runCoding(gen, signal, false);
      return;
    }
    this.note = v.n_uncoded > 0 ? t().reportDegraded(v.n_uncoded) : "";
    this.state = "report";
    this.paint();
  }

  /**
   * 增量循环：一枪最多编 3 批，直到 remaining == 0。**停滞保护**：连着
   * STALL_MAX 枪没编出新轮次就停并报错，防服务端 bug 造成无限计费。
   * `force` 只在第一枪带上（服务端清掉这本书的编码后从头跑）。
   */
  private async runCoding(gen: number, signal: AbortSignal, force: boolean): Promise<void> {
    const api = this.api();
    const hid = this.hid;
    if (!hid) return;
    const start = spendTokens(this.view?.spend);
    this.state = "coding";
    this.err = "";
    this.note = "";
    this.confirming = false;
    this.progress = {
      coded: force ? 0 : (this.view?.n_coded ?? 0),
      total: this.view?.n_turns ?? 0,
      tokens: 0,
    };
    this.paint();
    let stalls = 0;
    let first = true;
    let stalled = false;
    try {
      for (;;) {
        const r = await api.codeProfile(hid, this.plugin.settings, { force: force && first, signal });
        first = false;
        if (!this.alive(gen)) return;
        this.progress = {
          coded: r.n_coded,
          total: r.n_turns,
          tokens: Math.max(0, spendTokens(r.spend) - start),
        };
        this.paintProgress();
        if (r.remaining <= 0) break;
        if (r.coded <= 0) {
          if (++stalls >= STALL_MAX) {
            stalled = true;
            break;
          }
        } else {
          stalls = 0;
        }
      }
    } catch (e) {
      if (!this.alive(gen)) return;
      // 部分进度服务端已经落盘；下面照常取一次画像，能看多少看多少。
      this.err = t().errReportFailed(ReportView.msg(e));
    }
    if (stalled) this.err = t().errReportStalled;
    try {
      const v = await api.getProfile(hid, signal);
      if (!this.alive(gen)) return;
      this.view = v;
      if (v.n_uncoded > 0 && !this.err) this.note = t().reportDegraded(v.n_uncoded);
    } catch (e) {
      if (!this.alive(gen)) return;
      if (!this.err) this.err = t().errReportFailed(ReportView.msg(e));
    }
    this.state = this.view && this.view.n_coded > 0 ? "report" : "fresh";
    this.paint();
    void this.loadVault(gen, signal); // 书架上这本书的轮数变了
  }

  private async loadVault(gen: number, signal: AbortSignal): Promise<void> {
    let root: string;
    try {
      root = vaultRoot(this.app);
    } catch {
      this.vaultRows = null;
      this.vaultErr = t().reportVaultDown;
      this.paintVault();
      return;
    }
    try {
      const l = await this.api().listProfiles(root, signal);
      if (!this.alive(gen)) return;
      this.vaultRows = mergeVaultRows(l.books, l.merged_by_title, this.hid);
      this.vaultErr = "";
    } catch {
      if (!this.alive(gen)) return;
      // 书架拿不到只在自己那一区说一声，不进 this.err：它不该盖住上面的画像。
      this.vaultRows = null;
      this.vaultErr = t().reportVaultDown;
    }
    this.paintVault();
  }

  // ── 读者动作 ────────────────────────────────────────────────────

  private startAnalysis(): void {
    if (!this.llmOk || !this.hid) return;
    this.paused = false;
    const { gen, signal } = this.begin();
    void this.runCoding(gen, signal, false);
  }

  private confirmRecompute(): void {
    if (!this.llmOk || !this.hid) return;
    this.paused = false;
    const { gen, signal } = this.begin();
    void this.runCoding(gen, signal, true);
  }

  private stopCoding(): void {
    // 服务端每批都落盘，停下来不丢已编的；重新取一次画像把已有的画出来。
    this.paused = true;
    const { gen, signal } = this.begin();
    this.state = "loading";
    this.paint();
    void this.loadProfile(gen, signal);
  }

  // ── 画 ──────────────────────────────────────────────────────────

  /**
   * 建骨架。只在 onOpen 和 relocalize 跑；之后每次刷新只改属性和重建
   * 雷达/表格那两块的子树。第二个视图**必须挂同一个 .socrates-pen 类**，
   * 才继承 .sp-icon / .sp-bar / .sp-alert / .is-off 那些规则。
   */
  private renderShell(): void {
    const root = this.contentEl;
    root.empty();
    root.addClass("socrates-pen", "sp-report");
    // 根是 overflow:hidden，这里是唯一的滚动容器。
    const scroll = root.createDiv({ cls: "sp-report-scroll" });
    const head = scroll.createEl("h2", { cls: "sp-report-head" });
    const sub = scroll.createDiv({ cls: "sp-report-sub" });
    const alert = scroll.createDiv({ cls: "sp-alert is-off" });
    const note = scroll.createDiv({ cls: "sp-report-note is-off" });
    const actions = scroll.createDiv({ cls: "sp-report-actions is-off" });
    const analyze = actions.createEl("button", { cls: "mod-cta" });
    const recompute = actions.createEl("button");
    const confirm = actions.createEl("button", { cls: "mod-warning" });
    const cancel = actions.createEl("button");
    const stop = actions.createEl("button");
    const bar = scroll.createDiv({ cls: "sp-bar is-off" });
    const fill = bar.createDiv({ cls: "sp-bar-fill is-det" });
    const progress = scroll.createDiv({ cls: "sp-report-progress is-off" });
    const hint = scroll.createDiv({ cls: "sp-report-hint is-off" });
    const radar = scroll.createDiv({ cls: "sp-radar-wrap is-off" });
    const axes = scroll.createDiv({ cls: "sp-axes is-off" });
    const vaultTitle = scroll.createEl("h3", { cls: "sp-report-h3" });
    const vaultNote = scroll.createDiv({ cls: "sp-report-note is-off" });
    const vault = scroll.createDiv({ cls: "sp-vault-wrap" });
    this.els = {
      head, sub, alert, note, actions, analyze, recompute, confirm, cancel, stop,
      hint, bar, fill, progress, radar, axes, vaultTitle, vaultNote, vault,
    };
    analyze.onclick = () => this.startAnalysis();
    recompute.onclick = () => {
      // 面板内两步确认，不用 window.confirm（那会卡住整个 Obsidian）。
      this.confirming = true;
      this.paint();
    };
    confirm.onclick = () => this.confirmRecompute();
    cancel.onclick = () => {
      this.confirming = false;
      this.paint();
    };
    stop.onclick = () => this.stopCoding();
    this.paint();
    this.paintVault();
  }

  private paint(): void {
    const e = this.els;
    if (!e) return;
    const s = t();
    const v = this.view;
    e.head.setText(this.title || s.viewTitleReport);
    let sub = "";
    if (this.state === "loading") sub = s.reportLoading;
    else if (this.state === "nofile") sub = s.reportNoFile;
    else if (this.state === "unregistered") sub = s.reportNotRegistered;
    else if (this.state === "empty") sub = s.reportNoTurns;
    else if (this.state === "fresh" && v) sub = s.reportNotAnalyzed(v.n_turns);
    else if (v) sub = s.reportTurns(v.n_turns, v.n_coded, v.n_meta);
    e.sub.setText(sub);
    e.alert.setText(this.err);
    e.alert.toggleClass("is-off", !this.err);

    const notes: string[] = [];
    if (this.note) notes.push(this.note);
    if (v && v.n_legacy > 0 && this.state !== "fresh") notes.push(s.reportLegacy(v.n_legacy));
    if (v && v.n_given_up > 0) notes.push(s.reportGivenUp(v.n_given_up));
    e.note.setText(notes.join("\n"));
    e.note.toggleClass("is-off", notes.length === 0);

    const coding = this.state === "coding";
    const hasView = v !== null;
    const canAnalyze =
      this.llmOk && !coding && hasView &&
      (this.state === "fresh" || (this.state === "report" && v.n_uncoded > 0));
    const canRecompute = this.llmOk && !coding && this.state === "report" && hasView && v.n_coded > 0;
    e.analyze.setText(this.state === "fresh" ? s.btnAnalyze : s.btnResume);
    e.analyze.toggleClass("is-off", !canAnalyze || this.confirming);
    e.recompute.setText(s.btnRecompute);
    e.recompute.toggleClass("is-off", !canRecompute || this.confirming);
    e.confirm.setText(s.btnRecomputeSure);
    e.confirm.toggleClass("is-off", !this.confirming);
    e.cancel.setText(s.btnCancel);
    e.cancel.toggleClass("is-off", !this.confirming);
    e.stop.setText(s.btnStop);
    e.stop.toggleClass("is-off", !coding);
    e.actions.toggleClass("is-off", !(canAnalyze || canRecompute || this.confirming || coding));

    const hints: string[] = [];
    if (v && v.coded_at) hints.push(s.reportCodedAt(localStamp(v.coded_at)));
    if (v && spendTokens(v.spend) > 0) hints.push(s.reportSpend(spendTokens(v.spend)));
    e.hint.setText(hints.join(" · "));
    e.hint.toggleClass("is-off", hints.length === 0 || coding);

    this.paintProgress();
    this.paintReport();
  }

  /** 确定型进度：知道编到第几轮就画到第几轮——和主面板那条不确定型的穿梭条是两回事。 */
  private paintProgress(): void {
    const e = this.els;
    if (!e) return;
    const coding = this.state === "coding";
    const p = this.progress;
    e.bar.toggleClass("is-off", !coding);
    e.progress.toggleClass("is-off", !coding || !p);
    if (!p) return;
    const pct = p.total > 0 ? Math.round((100 * p.coded) / p.total) : 0;
    e.fill.style.setProperty("--sp-pct", `${pct}%`);
    e.progress.setText(t().reportProgress(p.coded, p.total, p.tokens));
  }

  private paintReport(): void {
    const e = this.els;
    if (!e) return;
    e.radar.empty();
    e.axes.empty();
    const v = this.view;
    const show = v !== null && v.axes.length > 0 && (this.state === "report" || this.state === "coding");
    e.radar.toggleClass("is-off", !show);
    e.axes.toggleClass("is-off", !show);
    if (!show || !v) return;
    this.drawRadar(e.radar, v.axes);
    this.drawAxes(e.axes, v.axes);
  }

  /** 雷达用 createSvg 逐节点建——Obsidian 不许 innerHTML 灌外来 SVG。几何在 src/radar.ts。 */
  private drawRadar(wrap: HTMLElement, axes: ProfileAxis[]): void {
    const s = t();
    const lay = layoutRadar(axes.map((a) => ({ id: a.id, name: a.name, score: a.score })));
    const svg = wrap.createSvg("svg", {
      cls: "sp-radar",
      attr: { viewBox: lay.viewBox, role: "img", "aria-label": s.reportRadarLabel(axes.length) },
    });
    for (const ring of lay.rings) {
      svg.createSvg("circle", { cls: "sp-radar-ring", attr: { cx: lay.cx, cy: lay.cy, r: ring.r } });
    }
    for (const p of lay.points) {
      svg.createSvg("line", { cls: "sp-radar-spoke", attr: { x1: lay.cx, y1: lay.cy, x2: p.ex, y2: p.ey } });
    }
    if (lay.polygon) svg.createSvg("polygon", { cls: "sp-radar-area", attr: { points: lay.polygon } });
    const scale = svg.createSvg("text", {
      cls: "sp-radar-scale",
      attr: { x: lay.cx + 3, y: lay.cy - lay.r + 2, "font-size": lay.fontSize * 0.75, "dominant-baseline": "hanging" },
    });
    scale.textContent = "10";
    for (const p of lay.points) {
      const dot = svg.createSvg("circle", {
        cls: p.score === null ? ["sp-radar-pt", "is-null"] : "sp-radar-pt",
        attr: { cx: p.x, cy: p.y, r: 3.5 },
      });
      dot.createSvg("title").textContent =
        `${p.name} · ${p.score === null ? s.reportUnrated : s.reportScoreTip(p.score)}`;
      if (!p.labeled) continue;
      const cls = ["sp-radar-label"];
      if (p.tone === "weak") cls.push("is-weak");
      if (p.tone === "strong") cls.push("is-strong");
      const text = svg.createSvg("text", {
        cls,
        attr: {
          x: p.lx,
          y: p.ly,
          "text-anchor": p.anchor,
          "dominant-baseline": p.baseline,
          "font-size": lay.fontSize,
        },
      });
      text.textContent = p.label;
      if (p.label !== p.name) text.createSvg("title").textContent = p.name;
    }
  }

  /** 轴表：最弱在前、未评在后（重排只在表不在图）。每轴一个 details，展开看证据。 */
  private drawAxes(wrap: HTMLElement, axes: ProfileAxis[]): void {
    const s = t();
    const tones = tonesOf(axes.map((a) => ({ id: a.id, name: a.name, score: a.score })));
    const head = wrap.createDiv({ cls: "sp-axes-head" });
    head.createSpan({ text: s.reportColAxis });
    head.createSpan({ cls: "sp-score", text: s.reportColScore });
    head.createSpan({ cls: "sp-axis-mastery", text: s.reportColMastery });
    head.createSpan({ cls: "sp-axis-n", text: s.reportColN });
    const sorted = axes
      .map((a, i) => ({ a, i }))
      .sort((p, q) => {
        const ps = p.a.score;
        const qs = q.a.score;
        if (ps === null && qs === null) return p.i - q.i;
        if (ps === null) return 1;
        if (qs === null) return -1;
        return ps - qs || p.i - q.i;
      })
      .map((p) => p.a);
    for (const a of sorted) {
      const det = wrap.createEl("details", { cls: "sp-axis" });
      const sum = det.createEl("summary", { cls: "sp-axis-sum" });
      const name = sum.createSpan({ cls: "sp-axis-name", text: a.name });
      if (a.definition) name.setAttr("title", a.definition);
      const tone = tones.get(a.id) ?? "";
      const scoreCls = ["sp-score"];
      if (a.score === null) scoreCls.push("is-null");
      if (tone === "weak") scoreCls.push("is-weak");
      if (tone === "strong") scoreCls.push("is-strong");
      sum.createSpan({ cls: scoreCls, text: a.score === null ? s.reportUnrated : String(a.score) });
      sum.createSpan({ cls: "sp-axis-mastery", text: masteryPct(a.mastery) || s.reportNoMastery });
      sum.createSpan({ cls: "sp-axis-n", text: s.reportEvidenceCount(a.n, a.n_legacy) });
      const body = det.createDiv({ cls: "sp-axis-body" });
      if (a.definition) body.createDiv({ cls: "sp-axis-def", text: a.definition });
      if (a.mastery !== null) {
        body.createDiv({ cls: "sp-axis-line", text: s.reportMastery(masteryPct(a.mastery), a.n_obs) });
      }
      if (a.why.length) {
        body.createDiv({ cls: "sp-axis-h", text: s.reportWhyTitle });
        const ul = body.createEl("ul", { cls: "sp-why" });
        for (const w of a.why) ul.createEl("li", { text: w });
      }
      if (a.gaps.length) {
        body.createDiv({ cls: "sp-axis-h", text: s.reportGapsTitle });
        const ul = body.createEl("ul", { cls: "sp-evs" });
        for (const g of a.gaps) {
          const li = ul.createEl("li");
          li.createSpan({ cls: "sp-ev-time", text: localStamp(g.asked_at) });
          li.createSpan({ cls: "sp-ev-ref", text: s.reportTurnRef(g.idx) });
          li.createDiv({ cls: "sp-ev-quote", text: g.quote });
        }
      }
      if (a.evidence.length) {
        body.createDiv({ cls: "sp-axis-h", text: s.reportEvidenceTitle });
        const ul = body.createEl("ul", { cls: "sp-evs" });
        for (const ev of a.evidence) {
          const li = ul.createEl("li");
          li.createSpan({ cls: "sp-ev-time", text: localStamp(ev.asked_at) });
          li.createSpan({ cls: "sp-ev-ref", text: s.reportTurnRef(ev.idx) });
          const bits = [s.reportType(ev.type)];
          let tone2 = "";
          if (ev.type === "VERIFY" && ev.verify) {
            bits.push(s.reportVerify(ev.verify));
            tone2 = ev.verify === "confirmed" ? "is-good" : ev.verify === "corrected" ? "is-bad" : "";
          }
          if (ev.type === "REJECT" && ev.reject_right !== null) {
            bits.push(ev.reject_right ? s.reportRejectRight : s.reportRejectWrong);
            tone2 = ev.reject_right ? "is-good" : "is-bad";
          }
          if (ev.type === "GAP") tone2 = "is-bad";
          li.createSpan({ cls: tone2 ? ["sp-ev-type", tone2] : "sp-ev-type", text: bits.filter(Boolean).join(" · ") });
          if (ev.quote) li.createDiv({ cls: "sp-ev-quote", text: ev.quote });
        }
      }
    }
  }

  private paintVault(): void {
    const e = this.els;
    if (!e) return;
    const s = t();
    e.vaultTitle.setText(s.reportVaultTitle);
    e.vault.empty();
    const rows = this.vaultRows;
    const line = this.vaultErr || (rows === null ? s.reportLoading : rows.length === 0 ? s.reportVaultEmpty : "");
    e.vaultNote.setText(line);
    e.vaultNote.toggleClass("is-off", !line);
    if (line || !rows) return;
    const table = e.vault.createEl("table", { cls: "sp-vault" });
    const tr = table.createEl("thead").createEl("tr");
    for (const col of [s.reportColBook, s.reportColTurns, s.reportColAxes, s.reportColWeakest, s.reportColAskedMost]) {
      tr.createEl("th", { text: col });
    }
    const tbody = table.createEl("tbody");
    for (const r of rows) {
      const row = tbody.createEl("tr", { cls: r.current ? "is-current" : "" });
      const book = row.createEl("td");
      book.createSpan({ text: r.title });
      if (r.merged > 1) book.createSpan({ cls: "sp-vault-merged", text: s.reportMerged(r.merged) });
      row.createEl("td", { cls: "is-num", text: String(r.n_turns) });
      row.createEl("td", { cls: "is-num", text: String(r.n_axes) });
      const weak = row.createEl("td");
      for (const a of r.weakest) {
        if (a.score === null) continue;
        weak.createSpan({ cls: "sp-vault-chip", text: s.reportAxisScore(a.name, a.score) });
      }
      const asked = row.createEl("td");
      for (const a of r.asked_most) asked.createSpan({ cls: "sp-vault-chip", text: s.reportAxisN(a.name, a.n) });
    }
  }
}
