import { ItemView, MarkdownRenderer, Notice, TFile, WorkspaceLeaf, setIcon, setTooltip } from "obsidian";
import type SocratesPenPlugin from "../main";
import { makeApi } from "../api";
import { t } from "../i18n";
import { absFor, handbookIdFromPath, vaultRoot } from "../selection";
import { sidecarUsable } from "../sidecar";
import {
  makePracticeApi,
  type PracticeAnalysis,
  type PracticeAnalysisDirection,
  type PracticeAnalysisEvidence,
  type PracticeAnswer,
  type PracticeAttempt,
  type PracticeBlueprint,
  type PracticeMq,
  type PracticeJob,
  type PracticePoint,
  type PracticeQuestion,
  type PracticeQuestionType,
  type PracticeRecommendation,
  type PracticeRecommendationResult,
  type PracticeSession,
  type PracticeSessionMode,
  type PracticeState,
} from "../practice";

export const VIEW_TYPE_PRACTICE = "socrates-pen-practice";

type Tab = "source" | "practice" | "analysis";
type ViewState = "loading" | "nofile" | "disabled" | "ready" | "down";

type Els = {
  scroll: HTMLElement;
  head: HTMLElement;
  sub: HTMLElement;
  alert: HTMLElement;
  note: HTMLElement;
  tabs: Record<Tab, HTMLButtonElement>;
  source: HTMLElement;
  practice: HTMLElement;
  analysis: HTMLElement;
};

const POLL_MS = 1800;

function pct(score: number | null | undefined): string {
  if (typeof score !== "number" || !Number.isFinite(score)) return t().practiceUnmeasured;
  return `${Math.round(score * 100)}%`;
}

function qTypeLabel(type: PracticeQuestionType): string {
  const s = t();
  if (type === "single_choice") return s.practiceTypeChoice;
  if (type === "fill_blank") return s.practiceTypeFill;
  return s.practiceTypeShort;
}

function datePlus(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

function emptyAnswer(q: PracticeQuestion): PracticeAnswer {
  if (q.type === "single_choice") return {};
  if (q.type === "fill_blank") {
    const blanks: Record<string, string> = {};
    for (const b of q.blanks || []) blanks[b.id] = "";
    return { blanks };
  }
  return { text: "" };
}

function cloneAnswer(a: PracticeAnswer | undefined, q: PracticeQuestion): PracticeAnswer {
  if (!a) return emptyAnswer(q);
  return {
    ...(a.choice_id ? { choice_id: a.choice_id } : {}),
    ...(a.blanks ? { blanks: { ...a.blanks } } : {}),
    ...(a.text !== undefined ? { text: a.text } : {}),
    ...(a.images?.length ? { images: a.images.map((image) => ({ ...image })) } : {}),
  };
}

function answerText(a: PracticeAnswer | undefined): string {
  if (!a) return "";
  if (a.choice_id) return a.choice_id;
  if (a.text) return a.text;
  if (a.blanks) return Object.values(a.blanks).join(" ");
  return "";
}

function gradeText(a: PracticeAttempt): string {
  if (!a.grade) return attemptStatusLabel(a.status);
  return `${a.grade.score} / ${a.grade.max_score}`;
}

function metaQuestionsOf(state: PracticeState | null): PracticeMq[] {
  const resource = state?.resource;
  return resource?.meta_questions || resource?.mqs || [];
}

function issueText(issue: unknown): string {
  if (typeof issue === "string") return issue;
  if (!issue || typeof issue !== "object") return String(issue);
  const o = issue as { mq_id?: unknown; message?: unknown; code?: unknown };
  const msg = typeof o.message === "string" ? o.message : JSON.stringify(issue);
  return typeof o.mq_id === "string" && o.mq_id ? `${o.mq_id}: ${msg}` : msg;
}

function edgeLabel(type: string): string {
  if (type === "requires") return t().practiceEdgeRequires;
  if (type === "contrasts") return t().practiceEdgeContrasts;
  return t().practiceEdgeRelated;
}

function pointInScope(point: PracticePoint, selectedMq: Set<string>): boolean {
  if (!point.source_mq_ids?.length) return false;
  return point.source_mq_ids.some((id) => selectedMq.has(id));
}

function jobStatusLabel(status: string): string {
  const s = t();
  if (status === "queued") return s.practiceJobQueued;
  if (status === "running") return s.practiceJobRunning;
  if (status === "completed") return s.practiceJobCompleted;
  if (status === "completed_with_issues") return s.practiceJobCompletedWithIssues;
  if (status === "failed") return s.practiceJobFailed;
  if (status === "cancelled") return s.practiceJobCancelled;
  if (status === "stale") return s.practiceJobStale;
  if (status === "interrupted") return s.practiceJobInterrupted;
  return status;
}

function serviceStatusLabel(status: string | undefined): string {
  const s = t();
  if (!status) return s.practiceStatusUnknown;
  if (status === "ok" || status === "ready" || status === "running") return s.practiceServiceReady;
  if (status === "starting") return s.practiceServiceStarting;
  if (status === "down" || status === "failed" || status === "error") return s.practiceServiceDown;
  return status;
}

function modeLabel(mode: string): string {
  const s = t();
  if (mode === "practice") return s.practiceModePractice;
  if (mode === "recommended") return s.practiceModeRecommended;
  if (mode === "exam") return s.practiceModeExam;
  return mode;
}

function sessionStatusLabel(status: string): string {
  const s = t();
  if (status === "active") return s.practiceSessionActive;
  if (status === "grading") return s.practiceSessionGrading;
  if (status === "completed") return s.practiceSessionCompleted;
  return status;
}

function attemptStatusLabel(status: string): string {
  const s = t();
  if (status === "pending") return s.practiceAttemptPending;
  if (status === "graded") return s.practiceAttemptGraded;
  if (status === "failed") return s.practiceAttemptFailed;
  if (status === "skipped") return s.practiceAttemptSkipped;
  return status;
}

function modelStatusLabel(status: string): string {
  const s = t();
  if (status === "cold") return s.practiceModelCold;
  if (status === "active") return s.practiceModelActive;
  if (status === "training") return s.practiceModelTraining;
  if (status === "gated") return s.practiceModelGated;
  if (status === "fallback") return s.practiceModelFallback;
  return status;
}

function pointStatusLabel(status: string): string {
  const s = t();
  if (status === "unmeasured") return s.practicePointUnmeasured;
  if (status === "unknown") return s.practicePointUnmeasured;
  if (status === "insufficient") return s.practicePointInsufficient;
  if (status === "weak") return s.practicePointWeak;
  if (status === "stable") return s.practicePointStable;
  return status;
}

function directionText(d: PracticeAnalysisDirection): string {
  if (typeof d === "string") return d;
  if (!d || typeof d !== "object") return String(d);
  const name = d.name || d.point_id || t().practiceUnknownPoint;
  const status = typeof d.reason === "string" ? pointStatusLabel(d.reason) : "";
  const gap = typeof d.gap === "number" && Number.isFinite(d.gap) ? d.gap : null;
  const n = typeof d.n === "number" && Number.isFinite(d.n) ? d.n : 0;
  return t().practiceDirection(name, status, gap, n);
}

function analysisEvidenceText(ev: PracticeAnalysisEvidence): string {
  if (typeof ev === "string") return ev;
  if (!ev || typeof ev !== "object") return String(ev);
  const score = typeof ev.score === "number" && Number.isFinite(ev.score) ? ev.score : null;
  return t().practiceEvidenceMeta(ev.question_id || ev.attempt_id || "", score, ev.at || "");
}

function activeQuestionIndex(session: PracticeSession): number {
  const done = new Set(
    session.attempts
      .filter((a) => a.status !== "failed" || a.answer !== undefined)
      .map((a) => a.question_id),
  );
  const next = session.questions.findIndex((q) => !done.has(q.id));
  return next >= 0 ? next : Math.max(0, session.questions.length - 1);
}

export class PracticeView extends ItemView {
  plugin: SocratesPenPlugin;
  private els: Els | null = null;
  private tab: Tab = "source";
  private state: ViewState = "nofile";
  private path: string | null = null;
  private title = "";
  private hid: string | null = null;
  private rootPath: string | null = null;
  private err = "";
  private note = "";
  private practiceState: PracticeState | null = null;
  private job: PracticeJob | null = null;
  private session: PracticeSession | null = null;
  private analysis: PracticeAnalysis | null = null;
  private recs: PracticeRecommendationResult | null = null;
  private currentIndex = 0;
  private answerQuestionId = "";
  private answer: PracticeAnswer = {};
  private questionStartedAt = 0;
  private busy = "";
  private runGen = 0;
  private abort: AbortController | null = null;
  private jobTimer: number | null = null;
  private sessionTimer: number | null = null;
  private draftTimer: number | null = null;
  private buildPracticePerType = 2;
  private buildExamForms = 3;
  private recMinutes = 20;
  private vaultEnableSyncedRoot = "";

  constructor(leaf: WorkspaceLeaf, plugin: SocratesPenPlugin) {
    super(leaf);
    this.plugin = plugin;
  }

  getViewType(): string {
    return VIEW_TYPE_PRACTICE;
  }

  getDisplayText(): string {
    return t().viewTitlePractice;
  }

  getIcon(): string {
    return "graduation-cap";
  }

  async onOpen(): Promise<void> {
    this.registerEvent(
      this.app.workspace.on("file-open", (file) => {
        if (!file || file.extension !== "md") return;
        if (this.session?.status === "active" || this.session?.status === "grading") {
          this.note = t().practiceSessionKept;
          this.paint();
          return;
        }
        if (file.path !== this.path) void this.retarget(file);
      }),
    );
    this.renderShell();
    this.follow();
  }

  async onClose(): Promise<void> {
    this.cancel();
    this.clearTimers();
    this.els = null;
  }

  relocalize(): void {
    this.renderShell();
    this.paint();
    if (this.state === "ready") void this.refresh();
  }

  onPracticeExperimentChanged(syncError = ""): void {
    this.err = syncError;
    if (!this.plugin.settings.practiceExperiment) {
      this.cancel();
      this.clearTimers();
      this.state = this.path ? "disabled" : "nofile";
      this.practiceState = null;
      this.job = null;
      this.session = null;
      this.analysis = null;
      this.recs = null;
      this.busy = "";
      this.paint();
      return;
    }
    if (this.els) this.follow();
  }

  private renderMarkdown(el: HTMLElement, text: string): void {
    void MarkdownRenderer.render(this.app, text || " ", el, this.path || "/", this);
  }

  private api() {
    return makePracticeApi(this.plugin.settings.sidecarUrl);
  }

  private alive(gen: number): boolean {
    return gen === this.runGen && this.els !== null;
  }

  private cancel(): void {
    this.runGen++;
    this.abort?.abort();
    this.abort = null;
  }

  private begin(): { gen: number; signal: AbortSignal } {
    this.cancel();
    const ac = new AbortController();
    this.abort = ac;
    return { gen: this.runGen, signal: ac.signal };
  }

  private clearTimers(): void {
    if (this.jobTimer !== null) window.clearTimeout(this.jobTimer);
    if (this.sessionTimer !== null) window.clearTimeout(this.sessionTimer);
    if (this.draftTimer !== null) window.clearTimeout(this.draftTimer);
    this.jobTimer = null;
    this.sessionTimer = null;
    this.draftTimer = null;
  }

  private static msg(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  private resourceStale(): boolean {
    return this.practiceState?.resource?.stale === true;
  }

  private follow(): void {
    const f = this.app.workspace.getActiveFile();
    if (f && f.extension === "md") {
      void this.retarget(f);
      return;
    }
    this.state = "nofile";
    this.path = null;
    this.title = "";
    this.hid = null;
    this.rootPath = null;
    this.paint();
  }

  private async retarget(file: TFile): Promise<void> {
    const { gen } = this.begin();
    this.clearTimers();
    this.state = "loading";
    this.path = file.path;
    this.title = file.basename;
    this.hid = null;
    this.rootPath = null;
    this.err = "";
    this.note = "";
    this.practiceState = null;
    this.job = null;
    this.session = null;
    this.analysis = null;
    this.recs = null;
    this.currentIndex = 0;
    this.paint();
    if (!this.plugin.settings.practiceExperiment) {
      this.state = "disabled";
      this.paint();
      return;
    }
    try {
      const root = vaultRoot(this.app);
      const abs = absFor(this.app, file);
      const hid = handbookIdFromPath(abs);
      await makeApi(this.plugin.settings.sidecarUrl).importHandbook(abs, hid, root);
      if (!this.alive(gen)) return;
      this.rootPath = root;
      this.hid = hid;
      await this.loadState(gen);
    } catch (e) {
      if (!this.alive(gen)) return;
      this.state = "down";
      this.err = t().errPracticeLoad(PracticeView.msg(e));
      this.paint();
    }
  }

  private async loadState(gen = this.runGen): Promise<void> {
    if (!this.rootPath || !this.hid) return;
    try {
      const h = await makeApi(this.plugin.settings.sidecarUrl).health();
      if (!this.alive(gen)) return;
      if (!sidecarUsable(h.version, this.plugin.manifest.version)) {
        this.state = "down";
        const stale = t().healthStale;
        this.err = t().errReportUnreachable(stale);
        this.paint();
        return;
      }
      let st = await this.api().state(this.rootPath, this.hid);
      if (!this.alive(gen)) return;
      if (!st.enabled && this.plugin.settings.practiceExperiment && this.vaultEnableSyncedRoot !== this.rootPath) {
        this.vaultEnableSyncedRoot = this.rootPath;
        try {
          const res = await this.api().enable(this.rootPath, true);
          if (!this.alive(gen)) return;
          st = { ...st, enabled: res.enabled, services: res.services ?? st.services };
          if (res.enabled) st = await this.api().state(this.rootPath, this.hid);
          if (!this.alive(gen)) return;
        } catch (e) {
          if (!this.alive(gen)) return;
          this.practiceState = st;
          this.job = st.job;
          this.state = "disabled";
          this.err = t().errPracticeSync(PracticeView.msg(e));
          this.paint();
          return;
        }
      }
      this.practiceState = st;
      this.job = st.job;
      this.state = st.enabled ? "ready" : "disabled";
      this.err = "";
      this.paint();
      this.scheduleJobPoll();
    } catch (e) {
      if (!this.alive(gen)) return;
      this.state = "down";
      this.err = t().errPracticeLoad(PracticeView.msg(e));
      this.paint();
    }
  }

  private async refresh(): Promise<void> {
    const { gen } = this.begin();
    await this.loadState(gen);
  }

  private async setLocalExperiment(on: boolean): Promise<void> {
    this.busy = "enable";
    this.err = "";
    this.paint();
    try {
      await this.plugin.setPracticeExperiment(on);
    } finally {
      this.busy = "";
      this.paint();
    }
  }

  private async setVaultEnabled(on: boolean): Promise<void> {
    await this.setLocalExperiment(on);
  }

  private async startBuild(regenerate = false): Promise<void> {
    if (!this.rootPath || !this.hid) return;
    this.busy = "build";
    this.err = "";
    this.paint();
    try {
      this.job = await this.api().build(this.rootPath, this.hid, this.plugin.settings, {
        practice_per_type: this.buildPracticePerType,
        exam_forms: this.buildExamForms,
        ...(regenerate ? { regenerate: true } : {}),
      });
      this.tab = "source";
      this.paint();
      this.scheduleJobPoll();
    } catch (e) {
      this.err = t().errPracticeBuild(PracticeView.msg(e));
      this.paint();
    } finally {
      this.busy = "";
      this.paint();
    }
  }

  private async cancelJob(): Promise<void> {
    if (!this.rootPath || !this.job) return;
    try {
      this.job = await this.api().cancelJob(this.rootPath, this.job.id);
      this.paint();
    } catch (e) {
      this.err = t().errPracticeJob(PracticeView.msg(e));
      this.paint();
    }
  }

  private async resumeJob(): Promise<void> {
    if (!this.rootPath || !this.job) return;
    try {
      this.job = await this.api().resumeJob(this.rootPath, this.job.id, this.plugin.settings);
      this.paint();
      this.scheduleJobPoll();
    } catch (e) {
      this.err = t().errPracticeJob(PracticeView.msg(e));
      this.paint();
    }
  }

  private scheduleJobPoll(): void {
    if (this.jobTimer !== null) window.clearTimeout(this.jobTimer);
    const job = this.job;
    if (!job || (job.status !== "queued" && job.status !== "running")) return;
    this.jobTimer = window.setTimeout(() => {
      this.jobTimer = null;
      void this.pollJob(job.id);
    }, POLL_MS);
  }

  private async pollJob(id: string): Promise<void> {
    if (!this.rootPath) return;
    try {
      this.job = await this.api().job(this.rootPath, id);
      if (this.job.status === "completed" || this.job.status === "completed_with_issues") await this.refresh();
      else this.paint();
      this.scheduleJobPoll();
    } catch {
      this.scheduleJobPoll();
    }
  }

  private blueprintDraft(): PracticeBlueprint | null {
    const bp = this.practiceState?.blueprint;
    if (bp) return { ...bp, point_weights: { ...bp.point_weights } };
    const points = this.practiceState?.resource?.points || [];
    if (!this.hid) return null;
    const weights: Record<string, number> = {};
    for (const p of points) weights[p.id] = 1;
    return {
      id: `${this.hid}:default`,
      version: "draft",
      resource_version: this.practiceState?.resource?.resource_version,
      point_weights: weights,
      target_score: 80,
      target_date: datePlus(30),
      daily_minutes: 20,
      source_mq_ids: metaQuestionsOf(this.practiceState).map((m) => m.id),
      stable_tolerance: 10,
    };
  }

  private async saveBlueprint(form: HTMLFormElement): Promise<void> {
    if (!this.rootPath || !this.hid) return;
    const draft = this.blueprintDraft();
    if (!draft) return;
    const data = new FormData(form);
    draft.target_score = Math.max(1, Math.min(100, Number(data.get("target_score")) || 80));
    draft.target_date = String(data.get("target_date") || datePlus(30));
    draft.daily_minutes = Math.max(1, Math.min(240, Number(data.get("daily_minutes")) || 20));
    draft.stable_tolerance = Math.max(0, Math.min(100, Number(data.get("stable_tolerance")) || 10));
    const allMq = metaQuestionsOf(this.practiceState);
    const selectedMqIds = allMq
      .filter((mq) => data.get(`mq:${mq.id}`) === "on")
      .map((mq) => mq.id);
    if (allMq.length && selectedMqIds.length === 0) {
      new Notice(t().noticePracticeSelectMq);
      return;
    }
    draft.source_mq_ids = selectedMqIds;
    const selected = new Set(selectedMqIds);
    const weights: Record<string, number> = {};
    for (const p of this.practiceState?.resource?.points || []) {
      if (!pointInScope(p, selected)) continue;
      const raw = Number(data.get(`w:${p.id}`));
      weights[p.id] = Number.isFinite(raw) ? Math.max(0, raw) : 0;
    }
    draft.point_weights = weights;
    this.busy = "blueprint";
    this.paint();
    try {
      const bp = await this.api().putBlueprint(this.rootPath, this.hid, draft);
      if (this.practiceState) this.practiceState.blueprint = bp;
      new Notice(t().noticePracticeBlueprintSaved);
      this.paint();
    } catch (e) {
      this.err = t().errPracticeBlueprint(PracticeView.msg(e));
      this.paint();
    } finally {
      this.busy = "";
      this.paint();
    }
  }

  private async startSession(mode: PracticeSessionMode, questionIds?: string[]): Promise<void> {
    if (!this.rootPath || !this.hid) return;
    if (this.resourceStale()) {
      new Notice(t().noticePracticeResourceStale);
      return;
    }
    this.busy = `session:${mode}`;
    this.err = "";
    this.paint();
    try {
      this.session = await this.api().createSession(this.rootPath, this.hid, mode, this.plugin.settings, {
        ...(questionIds && questionIds.length ? { question_ids: questionIds } : {}),
        minutes: this.recMinutes,
      });
      this.currentIndex = activeQuestionIndex(this.session);
      this.answerQuestionId = "";
      this.questionStartedAt = Date.now();
      this.tab = "practice";
      this.paint();
      this.scheduleSessionPoll();
    } catch (e) {
      this.err = t().errPracticeSession(PracticeView.msg(e));
      this.paint();
    } finally {
      this.busy = "";
      this.paint();
    }
  }

  private async resumeSession(id: string): Promise<void> {
    if (!this.rootPath) return;
    this.busy = `resume:${id}`;
    this.paint();
    try {
      this.session = await this.api().session(this.rootPath, id);
      this.currentIndex = activeQuestionIndex(this.session);
      this.answerQuestionId = "";
      this.questionStartedAt = Date.now();
      this.tab = "practice";
      this.paint();
      this.scheduleSessionPoll();
    } catch (e) {
      this.err = t().errPracticeSession(PracticeView.msg(e));
      this.paint();
    } finally {
      this.busy = "";
      this.paint();
    }
  }

  private scheduleSessionPoll(): void {
    if (this.sessionTimer !== null) window.clearTimeout(this.sessionTimer);
    const s = this.session;
    if (!s) return;
    const pending = s.status === "grading" || s.attempts.some((a) => a.status === "pending");
    if (!pending) return;
    this.sessionTimer = window.setTimeout(() => {
      this.sessionTimer = null;
      void this.pollSession(s.id);
    }, POLL_MS);
  }

  private async pollSession(id: string): Promise<void> {
    if (!this.rootPath) return;
    try {
      this.session = await this.api().session(this.rootPath, id);
      this.currentIndex = Math.min(this.currentIndex, Math.max(0, this.session.questions.length - 1));
      this.paint();
      this.scheduleSessionPoll();
    } catch {
      this.scheduleSessionPoll();
    }
  }

  private currentQuestion(): PracticeQuestion | null {
    const qs = this.session?.questions || [];
    return qs[this.currentIndex] || null;
  }

  private attemptFor(qid: string): PracticeAttempt | undefined {
    return this.session?.attempts.find((a) => a.question_id === qid);
  }

  private ensureAnswer(q: PracticeQuestion): PracticeAnswer {
    if (this.answerQuestionId !== q.id) {
      const prior = this.session?.drafts?.[q.id] || this.attemptFor(q.id)?.answer;
      this.answer = cloneAnswer(prior, q);
      this.answerQuestionId = q.id;
      this.questionStartedAt = Date.now();
    }
    return this.answer;
  }

  private setAnswer(q: PracticeQuestion, next: PracticeAnswer): void {
    this.answer = cloneAnswer(next, q);
    this.answerQuestionId = q.id;
    this.scheduleDraft(q.id);
  }

  private scheduleDraft(questionId: string): void {
    if (this.draftTimer !== null) window.clearTimeout(this.draftTimer);
    const session = this.session;
    if (!session || !this.rootPath) return;
    const q = this.currentQuestion();
    if (!q) return;
    const answer = cloneAnswer(this.answer, q);
    this.draftTimer = window.setTimeout(() => {
      this.draftTimer = null;
      void this.api().draft(this.rootPath as string, session.id, questionId, answer).catch(() => {});
    }, 450);
  }

  private async submitAnswer(skip = false): Promise<void> {
    if (!this.rootPath || !this.session) return;
    const q = this.currentQuestion();
    if (!q) return;
    if (!skip && this.answer.images?.length && !this.plugin.settings.vision) {
      this.err = t().errNoVision;
      this.paint();
      return;
    }
    if (!skip && !answerText(this.answer).trim() && !this.answer.images?.length) {
      new Notice(t().noticePracticeAnswerEmpty);
      return;
    }
    this.busy = skip ? "skip" : "answer";
    this.paint();
    const duration = Math.max(0, Math.round((Date.now() - this.questionStartedAt) / 1000));
    const key = `${this.session.id}:${q.id}:${Date.now()}`;
    const submittedIndex = this.currentIndex;
    try {
      this.session = await this.api().answer(
        this.rootPath,
        this.session.id,
        q.id,
        skip ? emptyAnswer(q) : this.answer,
        key,
        duration,
        this.plugin.settings,
        skip,
      );
      this.currentIndex = skip ? activeQuestionIndex(this.session) : submittedIndex;
      this.answerQuestionId = "";
      this.questionStartedAt = Date.now();
      this.paint();
      this.scheduleSessionPoll();
    } catch (e) {
      this.err = t().errPracticeAnswer(PracticeView.msg(e));
      this.paint();
    } finally {
      this.busy = "";
      this.paint();
    }
  }

  private async finishSession(): Promise<void> {
    if (!this.rootPath || !this.session) return;
    this.busy = "finish";
    this.paint();
    try {
      this.session = await this.api().finish(this.rootPath, this.session.id, this.plugin.settings);
      this.paint();
      this.scheduleSessionPoll();
    } catch (e) {
      this.err = t().errPracticeFinish(PracticeView.msg(e));
      this.paint();
    } finally {
      this.busy = "";
      this.paint();
    }
  }

  private async retryAttempt(attempt: PracticeAttempt): Promise<void> {
    if (!this.rootPath || !this.session) return;
    this.busy = `retry:${attempt.id}`;
    this.paint();
    try {
      await this.api().retryAttempt(this.rootPath, attempt.id, this.plugin.settings);
      this.session = await this.api().session(this.rootPath, this.session.id);
      this.paint();
      this.scheduleSessionPoll();
    } catch (e) {
      this.err = t().errPracticeRetry(PracticeView.msg(e));
      this.paint();
    } finally {
      this.busy = "";
      this.paint();
    }
  }

  private async loadAnalysis(): Promise<void> {
    if (!this.rootPath || !this.hid) return;
    this.busy = "analysis";
    this.paint();
    try {
      this.analysis = await this.api().analysis(this.rootPath, this.hid);
      this.tab = "analysis";
      this.paint();
    } catch (e) {
      this.err = t().errPracticeAnalysis(PracticeView.msg(e));
      this.paint();
    } finally {
      this.busy = "";
      this.paint();
    }
  }

  private async loadRecommendations(): Promise<void> {
    if (!this.rootPath || !this.hid) return;
    if (this.resourceStale()) {
      new Notice(t().noticePracticeResourceStale);
      return;
    }
    this.busy = "recommend";
    this.paint();
    try {
      this.recs = await this.api().recommendations(this.rootPath, this.hid, this.recMinutes);
      this.tab = "practice";
      this.paint();
    } catch (e) {
      this.err = t().errPracticeRecommend(PracticeView.msg(e));
      this.paint();
    } finally {
      this.busy = "";
      this.paint();
    }
  }

  private renderShell(): void {
    const root = this.contentEl;
    root.empty();
    root.addClass("socrates-pen", "sp-practice");
    const scroll = root.createDiv({ cls: "sp-practice-scroll" });
    const head = scroll.createEl("h2", { cls: "sp-report-head" });
    const sub = scroll.createDiv({ cls: "sp-report-sub" });
    const alert = scroll.createDiv({ cls: "sp-alert is-off" });
    const note = scroll.createDiv({ cls: "sp-report-note is-off" });
    const tabBar = scroll.createDiv({ cls: "sp-practice-tabs" });
    const sourceBtn = tabBar.createEl("button");
    const practiceBtn = tabBar.createEl("button");
    const analysisBtn = tabBar.createEl("button");
    const source = scroll.createDiv({ cls: "sp-practice-panel" });
    const practice = scroll.createDiv({ cls: "sp-practice-panel" });
    const analysis = scroll.createDiv({ cls: "sp-practice-panel" });
    this.els = {
      scroll,
      head,
      sub,
      alert,
      note,
      tabs: { source: sourceBtn, practice: practiceBtn, analysis: analysisBtn },
      source,
      practice,
      analysis,
    };
    sourceBtn.onclick = () => {
      this.tab = "source";
      this.paint();
    };
    practiceBtn.onclick = () => {
      this.tab = "practice";
      this.paint();
    };
    analysisBtn.onclick = () => {
      this.tab = "analysis";
      this.paint();
      if (!this.analysis && this.state === "ready") void this.loadAnalysis();
    };
    this.paint();
  }

  private paint(): void {
    const e = this.els;
    if (!e) return;
    const s = t();
    e.head.setText(this.title || s.viewTitlePractice);
    let sub = "";
    if (this.state === "loading") sub = s.practiceLoading;
    else if (this.state === "nofile") sub = s.practiceNoFile;
    else if (!this.plugin.settings.practiceExperiment) sub = s.practiceLocalOff;
    else if (this.state === "disabled") sub = s.practiceVaultOff;
    else if (this.state === "down") sub = s.practiceUnavailable;
    else sub = this.summaryLine();
    e.sub.setText(sub);
    e.alert.setText(this.err);
    e.alert.toggleClass("is-off", !this.err);
    e.note.setText(this.note);
    e.note.toggleClass("is-off", !this.note);
    e.tabs.source.setText(s.practiceTabSource);
    e.tabs.practice.setText(s.practiceTabPractice);
    e.tabs.analysis.setText(s.practiceTabAnalysis);
    for (const key of Object.keys(e.tabs) as Tab[]) {
      e.tabs[key].toggleClass("is-active", this.tab === key);
    }
    e.source.toggleClass("is-off", this.tab !== "source");
    e.practice.toggleClass("is-off", this.tab !== "practice");
    e.analysis.toggleClass("is-off", this.tab !== "analysis");
    this.paintSource();
    this.paintPractice();
    this.paintAnalysis();
  }

  private summaryLine(): string {
    const s = t();
    const r = this.practiceState?.resource;
    const stats = this.practiceState?.stats;
    if (!r) return s.practiceNoResource;
    const q = stats?.questions ?? stats?.practice_questions ?? 0;
    return s.practiceSummary(metaQuestionsOf(this.practiceState).length, r.points.length, q);
  }

  private paintSource(): void {
    const wrap = this.els?.source;
    if (!wrap) return;
    const s = t();
    wrap.empty();
    if (!this.plugin.settings.practiceExperiment) {
      wrap.createDiv({ cls: "sp-report-note", text: s.practiceLocalOffDetail });
      const b = wrap.createEl("button", { cls: "mod-cta", text: s.practiceEnableLocal });
      b.onclick = () => void this.setLocalExperiment(true);
      return;
    }
    if (this.state === "nofile" || this.state === "loading" || this.state === "down") {
      wrap.createDiv({ cls: "sp-report-sub", text: this.els?.sub.textContent || "" });
      return;
    }
    if (this.state === "disabled" || !this.practiceState?.enabled) {
      wrap.createDiv({ cls: "sp-report-note", text: s.practiceVaultOffDetail });
      const b = wrap.createEl("button", { cls: "mod-cta", text: s.practiceEnableVault });
      b.disabled = this.busy === "enable";
      b.onclick = () => void this.setVaultEnabled(true);
      return;
    }
    this.renderServices(wrap);
    this.renderBuild(wrap);
    this.renderResource(wrap);
    this.renderBlueprint(wrap);
  }

  private renderServices(wrap: HTMLElement): void {
    const s = t();
    const box = wrap.createDiv({ cls: "sp-practice-box" });
    box.createEl("h3", { cls: "sp-report-h3", text: s.practiceServicesTitle });
    const row = box.createDiv({ cls: "sp-practice-kv" });
    const svc = this.practiceState?.services || {};
    row.createSpan({ text: s.practiceServiceAnalysis });
    row.createSpan({ text: serviceStatusLabel(svc.analysis?.status) });
    row.createSpan({ text: s.practiceServiceScheduling });
    row.createSpan({ text: serviceStatusLabel(svc.scheduling?.status) });
  }

  private renderBuild(wrap: HTMLElement): void {
    const s = t();
    const box = wrap.createDiv({ cls: "sp-practice-box" });
    box.createEl("h3", { cls: "sp-report-h3", text: s.practiceBuildTitle });
    box.createDiv({ cls: "sp-report-hint", text: s.practiceImportHint });
    const controls = box.createDiv({ cls: "sp-practice-controls" });
    this.numberControl(controls, s.practicePracticePerType, this.buildPracticePerType, 1, 12, (v) => {
      this.buildPracticePerType = v;
    });
    this.numberControl(controls, s.practiceExamForms, this.buildExamForms, 0, 10, (v) => {
      this.buildExamForms = v;
    });
    const build = controls.createEl("button", { cls: "mod-cta", text: s.practiceBuild });
    build.disabled = Boolean(this.busy) || this.job?.status === "running" || this.job?.status === "queued";
    build.onclick = () => void this.startBuild(false);
    if (this.practiceState?.resource) {
      const regen = controls.createEl("button", { text: s.practiceRegenerate });
      regen.disabled = build.disabled;
      regen.onclick = () => void this.startBuild(true);
    }
    const job = this.job;
    if (!job) return;
    const pct2 = job.total > 0 ? Math.round((100 * job.completed) / job.total) : 0;
    const bar = box.createDiv({ cls: "sp-bar" });
    const fill = bar.createDiv({ cls: "sp-bar-fill is-det" });
    fill.style.setProperty("--sp-pct", `${pct2}%`);
    box.createDiv({ cls: "sp-report-progress", text: s.practiceJob(jobStatusLabel(job.status), job.completed, job.total) });
    if (job.issues?.length) this.issueList(box, job.issues);
    if (job.error) box.createDiv({ cls: "sp-alert", text: job.error });
    const row = box.createDiv({ cls: "sp-practice-actions" });
    const cancel = row.createEl("button", { text: s.btnStop });
    cancel.disabled = job.status !== "running" && job.status !== "queued";
    cancel.onclick = () => void this.cancelJob();
    const resume = row.createEl("button", { text: s.practiceResumeGeneration });
    resume.disabled = !["cancelled", "failed", "stale", "interrupted"].includes(job.status);
    resume.onclick = () => void this.resumeJob();
  }

  private renderResource(wrap: HTMLElement): void {
    const s = t();
    const r = this.practiceState?.resource;
    const box = wrap.createDiv({ cls: "sp-practice-box" });
    box.createEl("h3", { cls: "sp-report-h3", text: s.practiceResourceTitle });
    if (!r) {
      box.createDiv({ cls: "sp-report-sub", text: s.practiceNoResourceDetail });
      return;
    }
    box.createDiv({
      cls: "sp-report-sub",
      text: s.practiceResourceSummary(r.resource_version, metaQuestionsOf(this.practiceState).length, r.points.length, r.edges.length),
    });
    if (r.stale) box.createDiv({ cls: "sp-report-note", text: s.practiceResourceStale });
    if (r.issues?.length) this.issueList(box, r.issues);
    if (r.points.length) {
      const list = box.createDiv({ cls: "sp-practice-points" });
      for (const p of r.points) {
        const det = list.createEl("details", { cls: "sp-axis" });
        det.createEl("summary", { cls: "sp-axis-sum" }).createSpan({ cls: "sp-axis-name", text: p.name });
        const body = det.createDiv({ cls: "sp-axis-body" });
        if (p.definition) body.createDiv({ cls: "sp-axis-def", text: p.definition });
        for (const ev of p.evidence || []) {
          body.createDiv({ cls: "sp-ev-quote", text: ev.quote });
        }
      }
    }
    if (r.edges.length) {
      const names = new Map(r.points.map((p) => [p.id, p.name]));
      const edges = box.createDiv({ cls: "sp-practice-edges" });
      for (const edge of r.edges) {
        edges.createSpan({
          text: `${names.get(edge.from) || s.practiceUnknownPoint} ${edgeLabel(edge.type)} ${names.get(edge.to) || s.practiceUnknownPoint}`,
        });
      }
    }
  }

  private renderBlueprint(wrap: HTMLElement): void {
    const s = t();
    const points = this.practiceState?.resource?.points || [];
    const bp = this.blueprintDraft();
    const box = wrap.createDiv({ cls: "sp-practice-box" });
    box.createEl("h3", { cls: "sp-report-h3", text: s.practiceBlueprintTitle });
    if (!bp || !points.length) {
      box.createDiv({ cls: "sp-report-sub", text: s.practiceBlueprintEmpty });
      return;
    }
    const form = box.createEl("form", { cls: "sp-practice-form" });
    form.onsubmit = (ev) => {
      ev.preventDefault();
      void this.saveBlueprint(form);
    };
    this.formNumber(form, "target_score", s.practiceTargetScore, bp.target_score, 1, 100);
    this.formDate(form, "target_date", s.practiceTargetDate, bp.target_date);
    this.formNumber(form, "daily_minutes", s.practiceDailyMinutes, bp.daily_minutes, 1, 240);
    this.formNumber(form, "stable_tolerance", s.practiceStableTolerance, bp.stable_tolerance, 0, 100);
    const mqs = metaQuestionsOf(this.practiceState);
    if (mqs.length) {
      const scope = form.createDiv({ cls: "sp-practice-scope" });
      scope.createDiv({ cls: "sp-report-hint", text: s.practiceScopeTitle });
      const selected = new Set(bp.source_mq_ids.length ? bp.source_mq_ids : mqs.map((mq) => mq.id));
      for (const mq of mqs) {
        const label = scope.createEl("label");
        const input = label.createEl("input");
        input.type = "checkbox";
        input.name = `mq:${mq.id}`;
        input.checked = selected.has(mq.id);
        label.createSpan({ text: [mq.chapter, mq.title].filter(Boolean).join(" · ") || mq.id });
      }
    }
    const weights = form.createDiv({ cls: "sp-practice-weights" });
    weights.createDiv({ cls: "sp-report-hint", text: s.practiceWeightsTitle });
    for (const p of points) {
      this.formNumber(weights, `w:${p.id}`, p.name, bp.point_weights[p.id] ?? 1, 0, 100);
    }
    const save = form.createEl("button", { cls: "mod-cta", text: s.practiceSaveBlueprint });
    save.type = "submit";
    save.disabled = this.busy === "blueprint";
  }

  private paintPractice(): void {
    const wrap = this.els?.practice;
    if (!wrap) return;
    const s = t();
    wrap.empty();
    if (this.state !== "ready") {
      wrap.createDiv({ cls: "sp-report-sub", text: s.practiceNeedEnable });
      return;
    }
    if (!this.practiceState?.resource) {
      wrap.createDiv({ cls: "sp-report-sub", text: s.practiceNoResourceDetail });
      return;
    }
    this.renderSessionControls(wrap);
    if (this.recs?.items.length) this.renderRecommendations(wrap, this.recs.items);
    if (this.session) this.renderSession(wrap);
  }

  private renderSessionControls(wrap: HTMLElement): void {
    const s = t();
    const box = wrap.createDiv({ cls: "sp-practice-box" });
    box.createEl("h3", { cls: "sp-report-h3", text: s.practiceSessionTitle });
    const stale = this.resourceStale();
    if (stale) box.createDiv({ cls: "sp-report-note", text: s.practiceResourceStalePractice });
    const controls = box.createDiv({ cls: "sp-practice-controls" });
    this.numberControl(controls, s.practiceMinutes, this.recMinutes, 1, 240, (v) => {
      this.recMinutes = v;
    });
    const practice = controls.createEl("button", { cls: "mod-cta", text: s.practiceStartPractice });
    practice.disabled = Boolean(this.busy) || stale;
    practice.onclick = () => void this.startSession("practice");
    const rec = controls.createEl("button", { text: s.practiceGetRecommendations });
    rec.disabled = Boolean(this.busy) || stale;
    rec.onclick = () => void this.loadRecommendations();
    const exam = controls.createEl("button", { text: s.practiceStartExam });
    exam.disabled = Boolean(this.busy) || stale;
    exam.onclick = () => void this.startSession("exam");
    const sessions = (this.practiceState?.sessions || []).filter((sess) => sess.status !== "completed");
    if (sessions.length) {
      const list = box.createDiv({ cls: "sp-practice-session-list" });
      for (const sess of sessions) {
        const b = list.createEl("button", { text: t().practiceResumeSession(modeLabel(sess.mode), sessionStatusLabel(sess.status)) });
        b.onclick = () => void this.resumeSession(sess.id);
      }
    }
  }

  private renderRecommendations(wrap: HTMLElement, recs: PracticeRecommendation[]): void {
    const s = t();
    const box = wrap.createDiv({ cls: "sp-practice-box" });
    box.createEl("h3", { cls: "sp-report-h3", text: s.practiceRecommendationsTitle });
    const ids = recs.map((r) => r.question_id);
    const start = box.createEl("button", { cls: "mod-cta", text: s.practiceStartRecommended });
    start.disabled = this.resourceStale();
    start.onclick = () => void this.startSession("recommended", ids);
    for (const r of recs) {
      const row = box.createDiv({ cls: "sp-practice-rec" });
      row.createDiv({ cls: "sp-axis-name", text: r.reason || r.point_id });
      row.createDiv({
        cls: "sp-report-hint",
        text: s.practiceRecommendationMeta(r.estimated_seconds || 0, r.probability ?? null),
      });
    }
  }

  private renderSession(wrap: HTMLElement): void {
    const s = t();
    const sess = this.session;
    if (!sess) return;
    const box = wrap.createDiv({ cls: "sp-practice-box sp-practice-session" });
    const total = sess.questions.length;
    const q = this.currentQuestion();
    box.createEl("h3", {
      cls: "sp-report-h3",
      text: s.practiceSessionProgress(modeLabel(sess.mode), sessionStatusLabel(sess.status), Math.min(total, this.currentIndex + 1), total),
    });
    this.renderSessionResult(box, sess);
    if (sess.status === "grading") {
      const bar = box.createDiv({ cls: "sp-bar" });
      bar.createDiv({ cls: "sp-bar-fill" });
      box.createDiv({ cls: "sp-report-progress", text: s.practiceGrading });
    }
    if (!q) {
      box.createDiv({ cls: "sp-report-sub", text: s.practiceNoQuestions });
      return;
    }
    this.renderQuestion(box, q);
    this.renderHistory(box, sess);
  }

  private renderQuestion(box: HTMLElement, q: PracticeQuestion): void {
    const s = t();
    const attempt = this.attemptFor(q.id);
    const answer = this.ensureAnswer(q);
    const head = box.createDiv({ cls: "sp-practice-question-head" });
    head.createSpan({ text: qTypeLabel(q.type) });
    head.createSpan({ text: s.practiceEstimate(q.estimated_seconds || 0) });
    const prompt = box.createDiv({ cls: "sp-practice-prompt" });
    this.renderMarkdown(prompt, q.prompt);
    const disabled = Boolean(attempt) || this.session?.status !== "active";
    if (q.type === "single_choice") this.renderChoice(box, q, answer, disabled);
    else if (q.type === "fill_blank") this.renderFill(box, q, answer, disabled);
    else this.renderShort(box, q, answer, disabled);
    if (q.type !== "single_choice") this.renderAnswerImages(box, q, answer, disabled);
    const actions = box.createDiv({ cls: "sp-practice-actions" });
    const prev = actions.createEl("button");
    setIcon(prev, "chevron-left");
    setTooltip(prev, s.practicePrevious);
    prev.disabled = this.currentIndex <= 0;
    prev.onclick = () => {
      this.currentIndex = Math.max(0, this.currentIndex - 1);
      this.answerQuestionId = "";
      this.paint();
    };
    const next = actions.createEl("button");
    setIcon(next, "chevron-right");
    setTooltip(next, s.practiceNext);
    next.disabled = !this.session || this.currentIndex >= this.session.questions.length - 1;
    next.onclick = () => {
      if (!this.session) return;
      this.currentIndex = Math.min(this.session.questions.length - 1, this.currentIndex + 1);
      this.answerQuestionId = "";
      this.paint();
    };
    const submit = actions.createEl("button", { cls: "mod-cta", text: s.practiceSubmitAnswer });
    submit.disabled = disabled || this.busy === "answer";
    submit.onclick = () => void this.submitAnswer(false);
    const skip = actions.createEl("button", { text: s.practiceSkip });
    skip.disabled = disabled || this.busy === "skip";
    skip.onclick = () => void this.submitAnswer(true);
    if (this.session?.mode === "exam" && this.session.status === "active") {
      const finish = actions.createEl("button", { text: s.practiceFinishExam });
      finish.onclick = () => void this.finishSession();
    }
    if (attempt) this.renderAttempt(box, attempt);
  }

  private renderChoice(box: HTMLElement, q: PracticeQuestion, answer: PracticeAnswer, disabled: boolean): void {
    const list = box.createDiv({ cls: "sp-practice-choices" });
    for (const c of q.choices || []) {
      const label = list.createEl("label");
      const input = label.createEl("input");
      input.type = "radio";
      input.name = `sp-choice-${q.id}`;
      input.value = c.id;
      input.checked = answer.choice_id === c.id;
      input.disabled = disabled;
      input.onchange = () => this.setAnswer(q, { choice_id: c.id });
      const text = label.createDiv({ cls: "sp-practice-choice-text" });
      this.renderMarkdown(text, c.text);
    }
  }

  private renderFill(box: HTMLElement, q: PracticeQuestion, answer: PracticeAnswer, disabled: boolean): void {
    const blanks = box.createDiv({ cls: "sp-practice-fill" });
    const vals = answer.blanks || {};
    for (const b of q.blanks || []) {
      const label = blanks.createEl("label");
      label.createSpan({ text: b.label });
      const input = label.createEl("input");
      input.type = "text";
      input.value = vals[b.id] || "";
      input.disabled = disabled;
      input.oninput = () => {
        this.setAnswer(q, { ...this.answer, blanks: { ...(this.answer.blanks || {}), [b.id]: input.value } });
      };
    }
  }

  private renderShort(box: HTMLElement, q: PracticeQuestion, answer: PracticeAnswer, disabled: boolean): void {
    const text = box.createEl("textarea", { cls: "sp-practice-text" });
    text.rows = 7;
    text.value = answer.text || "";
    text.disabled = disabled;
    text.oninput = () => this.setAnswer(q, { ...this.answer, text: text.value });
  }

  private renderAnswerImages(box: HTMLElement, q: PracticeQuestion, answer: PracticeAnswer, disabled: boolean): void {
    const s = t();
    const area = box.createDiv({ cls: "sp-practice-answer-images" });
    area.createDiv({ cls: "sp-report-hint", text: this.plugin.settings.vision ? s.practiceImageHint : s.errNoVision });
    const input = area.createEl("input", { attr: { type: "file", accept: "image/png,image/jpeg,image/webp,image/gif", "aria-label": s.practiceUploadAnswer } });
    input.multiple = true;
    input.disabled = disabled || !this.plugin.settings.vision;
    input.onchange = () => void this.addAnswerImages(q, Array.from(input.files || []));
    box.onpaste = (event: ClipboardEvent) => {
      if (disabled) return;
      const images = Array.from(event.clipboardData?.files || []).filter((file) => file.type.startsWith("image/"));
      if (images.length) {
        event.preventDefault();
        void this.addAnswerImages(q, images);
      }
    };
    for (const [index, image] of (answer.images || []).entries()) {
      const preview = area.createDiv({ cls: "sp-practice-answer-image" });
      preview.createEl("img", { attr: { src: `data:${image.mime};base64,${image.data}`, alt: `${s.practiceAnswerImage} ${index + 1}` } });
      if (!disabled) {
        const remove = preview.createEl("button", { text: s.practiceRemoveImage });
        remove.onclick = () => {
          this.setAnswer(q, { ...this.answer, images: this.answer.images?.filter((_, i) => i !== index) });
          this.paint();
        };
      }
    }
  }

  private async addAnswerImages(q: PracticeQuestion, files: File[]): Promise<void> {
    const s = t();
    try {
      if (!this.plugin.settings.vision) throw new Error(s.errNoVision);
      if (files.length + (this.answer.images?.length || 0) > 4) throw new Error(s.errVisionTooMany);
      const images = [];
      for (const file of files) {
        if (!["image/png", "image/jpeg", "image/webp", "image/gif"].includes(file.type)) throw new Error(s.errVisionBadType);
        if (file.size > 2 * 1024 * 1024) throw new Error(s.errVisionTooBig);
        const data = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
          reader.onerror = () => reject(new Error(s.practiceImageReadError));
          reader.readAsDataURL(file);
        });
        images.push({ mime: file.type, data });
      }
      // Navigation or submission may complete while FileReader is running.
      if (this.answerQuestionId !== q.id || this.attemptFor(q.id) || this.session?.status !== "active") return;
      if (images.length + (this.answer.images?.length || 0) > 4) throw new Error(s.errVisionTooMany);
      this.setAnswer(q, { ...this.answer, images: [...(this.answer.images || []), ...images] });
      this.paint();
    } catch (error) {
      new Notice(error instanceof Error ? error.message : s.practiceImageReadError);
    }
  }

  private renderAttempt(box: HTMLElement, attempt: PracticeAttempt): void {
    const s = t();
    const showGrade = attempt.status !== "pending" && (this.session?.mode !== "exam" || this.session.status === "completed");
    const wrap = box.createDiv({ cls: "sp-practice-grade" });
    wrap.createDiv({ cls: "sp-report-hint", text: s.practiceAttemptStatus(attemptStatusLabel(attempt.status), gradeText(attempt)) });
    if (attempt.status === "pending") {
      const bar = wrap.createDiv({ cls: "sp-bar" });
      bar.createDiv({ cls: "sp-bar-fill" });
      return;
    }
    if (attempt.status === "failed") {
      if (attempt.error) wrap.createDiv({ cls: "sp-alert", text: attempt.error });
      const retry = wrap.createEl("button", { text: s.practiceRetryGrade });
      retry.onclick = () => void this.retryAttempt(attempt);
      return;
    }
    if (!showGrade || !attempt.grade) return;
    if (attempt.grade.achievement) wrap.createDiv({ cls: "sp-axis-h", text: `${s.practiceAchievement}: ${attempt.grade.achievement.label}` });
    for (const item of attempt.grade.transcriptions || []) {
      const details = wrap.createEl("details");
      details.createEl("summary", { text: `${s.practiceImageReadback} ${item.image_index + 1}` });
      details.createDiv({ cls: "sp-ev-quote", text: item.text });
    }
    if (attempt.reference_answer) {
      wrap.createDiv({ cls: "sp-axis-h", text: s.practiceReferenceAnswer });
      const ref = wrap.createDiv({ cls: "sp-ev-quote" });
      this.renderMarkdown(ref, attempt.reference_answer);
    }
    if (attempt.solution_markdown) {
      wrap.createDiv({ cls: "sp-report-hint", text: s.practiceSourceSolution });
      const solution = wrap.createDiv({ cls: "sp-practice-prompt" });
      this.renderMarkdown(solution, attempt.solution_markdown);
    }
    if (attempt.grade.criteria.length) {
      const list = wrap.createDiv({ cls: "sp-practice-criteria" });
      for (const c of attempt.grade.criteria) {
        const row = list.createDiv({ cls: "sp-practice-criterion" });
        const criterion = attempt.criteria?.find((item) => item.id === c.criterion_id);
        if (criterion?.description) row.createDiv({ cls: "sp-axis-h", text: criterion.description });
        row.createDiv({ cls: "sp-score", text: `${c.score} / ${c.max_score}` });
        row.createDiv({ text: c.reason });
        if (c.evidence_quote) row.createDiv({ cls: "sp-ev-quote", text: c.evidence_quote });
      }
    }
  }

  private renderSessionResult(box: HTMLElement, sess: PracticeSession): void {
    const result = sess.result;
    if (!result) return;
    const s = t();
    const score = result.score ?? result.provisional_score ?? null;
    const max = result.max_score ?? 100;
    const target = sess.blueprint?.target_score ?? this.practiceState?.blueprint?.target_score ?? null;
    const passed =
      typeof score === "number" && typeof target === "number" && Number.isFinite(score)
        ? score >= target
        : null;
    const lines = [
      s.practiceSessionResult(score, max, target, passed, result.provisional_score !== undefined && result.score == null),
      s.practiceCoverageLine(result.complete_coverage !== false, result.skipped ?? 0),
    ];
    if (result.stable !== undefined) lines.push(s.practiceStableLine(result.stable === true));
    if (result.summary) lines.push(result.summary);
    box.createDiv({ cls: "sp-practice-result", text: lines.join("\n") });
  }

  private renderHistory(box: HTMLElement, sess: PracticeSession): void {
    const s = t();
    const hist = box.createEl("details", { cls: "sp-practice-history" });
    hist.createEl("summary", { text: s.practiceHistoryTitle });
    for (const a of sess.attempts) {
      const row = hist.createDiv({ cls: "sp-practice-history-row" });
      row.createSpan({ text: a.question_id });
      row.createSpan({ text: gradeText(a) });
    }
  }

  private paintAnalysis(): void {
    const wrap = this.els?.analysis;
    if (!wrap) return;
    const s = t();
    wrap.empty();
    const actions = wrap.createDiv({ cls: "sp-practice-actions" });
    const load = actions.createEl("button", { cls: "mod-cta", text: s.practiceRefreshAnalysis });
    load.disabled = this.state !== "ready" || this.busy === "analysis";
    load.onclick = () => void this.loadAnalysis();
    if (!this.analysis) {
      wrap.createDiv({ cls: "sp-report-sub", text: s.practiceAnalysisEmpty });
      return;
    }
    const model = this.analysis.model;
    wrap.createDiv({
      cls: "sp-report-note",
      text: s.practiceAnalysisModel(modelStatusLabel(model?.status || "cold"), model?.version || "", this.analysis.resource_version || ""),
    });
    this.renderExamAnalysis(wrap);
    if (this.analysis.directions?.length) {
      const dirs = wrap.createEl("ul", { cls: "sp-why" });
      for (const d of this.analysis.directions) dirs.createEl("li", { text: directionText(d) });
    }
    const list = wrap.createDiv({ cls: "sp-axes" });
    const head = list.createDiv({ cls: "sp-axes-head" });
    head.createSpan({ text: s.reportColAxis });
    head.createSpan({ cls: "sp-score", text: s.practiceColHistorical });
    head.createSpan({ cls: "sp-axis-mastery", text: s.practiceColPredicted });
    head.createSpan({ cls: "sp-axis-n", text: s.practiceColEvidence });
    for (const p of this.analysis.points) {
      const det = list.createEl("details", { cls: "sp-axis" });
      const sum = det.createEl("summary", { cls: "sp-axis-sum" });
      sum.createSpan({ cls: "sp-axis-name", text: `${p.name} · ${pointStatusLabel(p.status)}` });
      sum.createSpan({ cls: "sp-score", text: pct(p.score) });
      sum.createSpan({ cls: "sp-axis-mastery", text: pct(p.expected_score) });
      sum.createSpan({ cls: "sp-axis-n", text: String(p.n) });
      const body = det.createDiv({ cls: "sp-axis-body" });
      body.createDiv({ cls: "sp-axis-def", text: s.practiceAnalysisStatus(pointStatusLabel(p.status), p.due_at || "") });
      for (const ev of p.evidence || []) body.createDiv({ cls: "sp-ev-quote", text: analysisEvidenceText(ev) });
    }
  }

  private renderExamAnalysis(wrap: HTMLElement): void {
    const exams = this.analysis?.exams;
    if (!exams) return;
    const s = t();
    const target = exams.target_score ?? this.practiceState?.blueprint?.target_score ?? null;
    const date = exams.target_date ?? this.practiceState?.blueprint?.target_date ?? "";
    const box = wrap.createDiv({ cls: "sp-practice-box" });
    box.createEl("h3", { cls: "sp-report-h3", text: s.practiceExamAnalysisTitle });
    box.createDiv({
      cls: "sp-report-note",
      text: s.practiceExamStable(
        exams.stable === true,
        exams.comparable_count ?? 0,
        target,
        date,
        exams.score_span ?? null,
        exams.stable_tolerance ?? this.practiceState?.blueprint?.stable_tolerance ?? null,
      ),
    });
    const history = exams.history || [];
    if (!history.length) return;
    const list = box.createDiv({ cls: "sp-practice-exam-history" });
    for (const row of history) {
      const item = list.createDiv({ cls: "sp-practice-history-row" });
      item.createSpan({ text: row.completed_at || row.date || row.id || "exam" });
      const result = row.result || {};
      const rowScore = row.score ?? result.score ?? result.provisional_score ?? null;
      const rowTarget = row.target_score ?? result.target_score ?? target;
      const passed =
        typeof row.passed === "boolean"
          ? row.passed
          : typeof result.passed === "boolean"
            ? result.passed
            : typeof rowScore === "number" && typeof rowTarget === "number"
              ? rowScore >= rowTarget
              : null;
      item.createSpan({ text: s.practiceExamHistoryScore(rowScore, rowTarget, passed) });
    }
  }

  private issueList(root: HTMLElement, issues: unknown[]): void {
    const ul = root.createEl("ul", { cls: "sp-practice-issues" });
    for (const issue of issues) ul.createEl("li", { text: issueText(issue) });
  }

  private numberControl(root: HTMLElement, label: string, value: number, min: number, max: number, onChange: (v: number) => void): void {
    const wrap = root.createEl("label", { cls: "sp-practice-num" });
    wrap.createSpan({ text: label });
    const input = wrap.createEl("input");
    input.type = "number";
    input.min = String(min);
    input.max = String(max);
    input.step = "1";
    input.value = String(value);
    input.onchange = () => {
      const raw = Number(input.value);
      const v = Number.isFinite(raw) ? Math.max(min, Math.min(max, Math.trunc(raw))) : value;
      input.value = String(v);
      onChange(v);
    };
  }

  private formNumber(root: HTMLElement, name: string, label: string, value: number, min: number, max: number): void {
    const wrap = root.createEl("label", { cls: "sp-practice-num" });
    wrap.createSpan({ text: label });
    const input = wrap.createEl("input");
    input.type = "number";
    input.name = name;
    input.min = String(min);
    input.max = String(max);
    input.step = "1";
    input.value = String(value);
  }

  private formDate(root: HTMLElement, name: string, label: string, value: string): void {
    const wrap = root.createEl("label", { cls: "sp-practice-num" });
    wrap.createSpan({ text: label });
    const input = wrap.createEl("input");
    input.type = "date";
    input.name = name;
    input.value = value || datePlus(30);
  }
}
