import { PracticeView } from "../../src/views/PracticeView";
import { DEFAULT_SETTINGS } from "../../src/settings";
import { setLang } from "../../src/i18n";
import { FileSystemAdapter, TFile } from "obsidian";

const win = window as any;
const events = new Map<string, Set<Function>>();
let view: PracticeView;
let serverEnabled = false;
const file = new TFile();
(file as any).basename = "note";
const app: any = {
  workspace: {
    on(name: string, fn: Function) {
      if (!events.has(name)) events.set(name, new Set());
      events.get(name)!.add(fn);
      return () => events.get(name)!.delete(fn);
    },
    getActiveFile: () => file,
    getLeavesOfType: (type: string) => type === "socrates-pen-practice" && view ? [{ view }] : [],
  },
  vault: { adapter: new FileSystemAdapter(), getAbstractFileByPath: () => file },
};

const plugin: any = {
  app,
  manifest: { version: "0.29.0" },
  settings: {
    ...DEFAULT_SETTINGS,
    sidecarUrl: "http://practice.test",
    practiceExperiment: true,
    model: "practice-model",
    vision: true,
  },
  async saveSettings() {},
  saveSettingsSoon() {},
  async setPracticeExperiment(on: boolean) {
    plugin.settings.practiceExperiment = on;
    await plugin.saveSettings();
    if (!on && view) view.onPracticeExperimentChanged();
    let syncError = "";
    try {
      const res = await fetch("http://practice.test/v1/practice/enable", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vault_root: "/vault", enabled: on }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (e) {
      syncError = `Could not sync the practice switch with the sidecar: ${e instanceof Error ? e.message : String(e)}`;
    }
    if (view) view.onPracticeExperimentChanged(syncError);
  },
};

type Question = {
  id: string;
  version: string;
  source_mq_id: string;
  type: "single_choice" | "fill_blank" | "short_answer";
  purpose: "practice" | "exam";
  exam_form: number | null;
  prompt: string;
  point_ids: string[];
  status: "accepted";
  estimated_seconds: number;
  choices?: { id: string; text: string }[];
  blanks?: { id: string; label: string; grading: "exact" | "semantic" }[];
};
type Attempt = {
  id: string;
  session_id: string;
  question_id: string;
  question_version: string;
  source_version: string;
  purpose: "practice" | "exam";
  answer: any;
  status: "pending" | "graded" | "failed" | "skipped";
  created_at: string;
  answered_at?: string;
  duration_seconds?: number;
  grade?: any;
  reference_answer?: string;
  error?: string;
};
type Session = {
  id: string;
  handbook_id: string;
  mode: "practice" | "recommended" | "exam";
  status: "active" | "grading" | "completed";
  questions: Question[];
  attempts: Attempt[];
  drafts: Record<string, any>;
  recommendations: any[];
  created_at: string;
  blueprint?: any;
  result?: any;
};

const practiceQuestions: Question[] = [
  {
    id: "q-choice",
    version: "v1",
    source_mq_id: "mq-1",
    type: "single_choice",
    purpose: "practice",
    exam_form: null,
    prompt: "Which option names the invariant?",
    point_ids: ["p1"],
    status: "accepted",
    estimated_seconds: 20,
    choices: [
      { id: "a", text: "A stable rule" },
      { id: "b", text: "A random aside" },
    ],
  },
  {
    id: "q-fill",
    version: "v1",
    source_mq_id: "mq-1",
    type: "fill_blank",
    purpose: "practice",
    exam_form: null,
    prompt: "Complete the key term.",
    point_ids: ["p1"],
    status: "accepted",
    estimated_seconds: 25,
    blanks: [{ id: "term", label: "Term", grading: "exact" }],
  },
  {
    id: "q-short",
    version: "v1",
    source_mq_id: "mq-2",
    type: "short_answer",
    purpose: "practice",
    exam_form: null,
    prompt: "Explain why the invariant matters.",
    point_ids: ["p2"],
    status: "accepted",
    estimated_seconds: 45,
  },
];
const examQuestions: Question[] = [
  {
    ...practiceQuestions[0],
    id: "e-choice",
    purpose: "exam",
    exam_form: 0,
    prompt: "Exam: choose the invariant.",
  },
];

const resource = {
  resource_version: "res-1",
  meta_questions: [
    { id: "mq-1", version: "m1", title: "Invariant", text: "What stays fixed?", source: { start_line: 1, end_line: 3 }, chapter: "One" },
    { id: "mq-2", version: "m2", title: "Use", text: "Why does it matter?", source: { start_line: 4, end_line: 6 }, chapter: "One" },
  ],
  points: [
    { id: "p1", name: "Invariant", definition: "A rule that stays true.", evidence: [{ mq_id: "mq-1", quote: "What stays fixed?" }] },
    { id: "p2", name: "Application", definition: "Using the rule in context.", evidence: [{ mq_id: "mq-2", quote: "Why does it matter?" }] },
  ],
  edges: [{ from: "p1", to: "p2", type: "requires", evidence: [{ mq_id: "mq-2", quote: "requires the invariant" }] }],
  issues: [{ mq_id: "mq-2", message: "One generated distractor was quarantined." }],
};
const blueprint = {
  id: "bp-1",
  version: "bp-v1",
  point_weights: { p1: 1, p2: 1 },
  target_score: 80,
  target_date: "2026-10-22",
  daily_minutes: 20,
  source_mq_ids: ["mq-1", "mq-2"],
  stable_tolerance: 10,
};

let built = false;
let resourceStale = false;
let currentJob: any = null;
let nextSession = 0;
let nextAttempt = 0;
const sessions = new Map<string, Session>();
const now = () => new Date().toISOString();
const response = (body: any) => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
const clone = <T>(v: T): T => structuredClone(v);
const publicState = () => ({
  enabled: serverEnabled,
  resource: serverEnabled && built ? { ...resource, stale: resourceStale } : null,
  job: serverEnabled ? currentJob : null,
  blueprint: serverEnabled && built ? blueprint : null,
  sessions: serverEnabled ? Array.from(sessions.values()).filter((s) => s.status !== "completed").map((s) => clone(s)) : [],
  services: { analysis: { status: "ok" }, scheduling: { status: "ok" } },
  stats: built ? { questions: 6, practice_questions: 3, exam_questions: 3, attempts: 0, graded_attempts: 0 } : {},
});
function grade(qid: string) {
  return {
    criteria: [{ criterion_id: `c-${qid}`, point_id: qid.includes("short") ? "p2" : "p1", score: qid.includes("short") ? 0.5 : 1, max_score: 1, reason: "Rubric evidence matched.", evidence_quote: "student evidence" }],
    score: qid.includes("short") ? 0.5 : 1,
    max_score: 1,
    model: { status: "mock" },
  };
}
function makeAttempt(session: Session, q: Question, answer: any, status: Attempt["status"]): Attempt {
  return {
    id: `att-${++nextAttempt}`,
    session_id: session.id,
    question_id: q.id,
    question_version: q.version,
    source_version: "res-1",
    purpose: q.purpose,
    answer,
    status,
    created_at: now(),
    answered_at: now(),
    duration_seconds: 3,
    ...(status === "graded" && q.purpose === "practice" ? { grade: grade(q.id), reference_answer: "Reference answer after submission." } : {}),
  };
}
function createSession(mode: Session["mode"], ids?: string[]): Session {
  const all = mode === "exam" ? examQuestions : practiceQuestions;
  const questions = ids?.length ? all.filter((q) => ids.includes(q.id)) : all;
  const session: Session = {
    id: `practice-session-${++nextSession}`,
    handbook_id: "note-1",
    mode,
    status: "active",
    questions: clone(questions),
    attempts: [],
    drafts: {},
    recommendations: [],
    created_at: now(),
    blueprint,
    ...(mode === "exam" ? { exam_form: 0 } : {}),
  };
  sessions.set(session.id, session);
  return clone(session);
}
function completePending(session: Session) {
  for (const attempt of session.attempts) {
    if (attempt.status !== "pending") continue;
    attempt.status = "graded";
    attempt.grade = grade(attempt.question_id);
    attempt.reference_answer = "Reference answer after submission.";
  }
  if (session.status === "grading") {
    for (const attempt of session.attempts) {
      attempt.status = "graded";
      attempt.grade = grade(attempt.question_id);
      attempt.reference_answer = "Exam reference after finish.";
    }
    session.status = "completed";
    session.result = { score: 83, max_score: 100, stable: false, complete_coverage: true, skipped: 0 };
  }
}

win.fetch = async (url: string, init: any = {}) => {
  const u = new URL(url, "http://practice.test");
  const path = u.pathname;
  const body = JSON.parse(init.body || "{}");
  win.requests = [...(win.requests || []), { path, method: init.method || "GET", body }];
  if (path === "/v1/health") {
    return response({ status: "ok", version: plugin.manifest.version, capabilities: { practice: true }, llm: { ok: true, base_url: DEFAULT_SETTINGS.baseUrl, model: DEFAULT_SETTINGS.model, key_source: "sidecar", key_tail: "test" } });
  }
  if (path === "/v1/handbooks/import") return response({ handbook_id: body.handbook_id, title: "note", original_path: body.original_path });
  if (path === "/v1/practice/enable") {
    serverEnabled = body.enabled === true;
    if (!serverEnabled) currentJob = null;
    return response({ enabled: serverEnabled, services: { analysis: { status: serverEnabled ? "ok" : "down" }, scheduling: { status: serverEnabled ? "ok" : "down" } } });
  }
  if (path === "/v1/practice/state") return response(publicState());
  if (path === "/v1/practice/build") {
    currentJob = { id: "job-1", status: "running", completed: 1, total: 3, issues: [] };
    return response(currentJob);
  }
  if (path === "/v1/practice/jobs/job-1") {
    if (currentJob.completed < 3) {
      currentJob = { ...currentJob, completed: currentJob.completed + 1, status: "running" };
    } else {
      currentJob = { ...currentJob, status: "completed_with_issues", issues: resource.issues };
      built = true;
    }
    return response(currentJob);
  }
  if (path === "/v1/practice/recommendations") {
    return response({ items: [{ question_id: "q-choice", decision_id: "d1", point_id: "p1", reason: "Weak and due", estimated_seconds: 20, probability: 0.1 }], model: { status: "cold" }, cursor: 1 });
  }
  if (path === "/v1/practice/analysis") {
    return response({
      points: [
        { id: "p1", name: "Invariant", n: 2, score: 0.8, expected_score: 0.7, status: "weak", evidence: [{ attempt_id: "att-1", question_id: "q-choice", score: 1, at: "2026-09-22T09:00:00" }], due_at: "2026-09-23" },
        { id: "p2", name: "Application", n: 0, score: null, expected_score: null, status: "unmeasured", evidence: [] },
      ],
      directions: [{ point_id: "p2", name: "Application", reason: "unmeasured", gap: 0.8, n: 0 }],
      model: { status: "cold" },
      resource_version: "res-1",
      cursor: 1,
      exams: {
        stable: false,
        comparable_count: 2,
        target_score: 80,
        target_date: "2026-10-22",
        score_span: 9,
        stable_tolerance: 10,
        history: [
          { id: "exam-1", completed_at: "2026-09-20", result: { score: 74, max_score: 100, complete_coverage: true, skipped: 0 }, blueprint_version: "bp-v1" },
          { id: "exam-2", completed_at: "2026-09-22", result: { score: 83, max_score: 100, complete_coverage: true, skipped: 0 }, blueprint_version: "bp-v1" },
        ],
      },
    });
  }
  if (path === "/v1/practice/sessions" && init.method === "POST") return response(createSession(body.mode, body.question_ids));
  const sessionMatch = path.match(/^\/v1\/practice\/sessions\/([^/]+)(?:\/([^/]+))?$/);
  if (sessionMatch) {
    const session = sessions.get(sessionMatch[1])!;
    const action = sessionMatch[2] || "";
    if (!action) {
      completePending(session);
      return response(clone(session));
    }
    if (action === "draft") {
      session.drafts[body.question_id] = body.answer;
      return response({ ok: true });
    }
    if (action === "answers") {
      const q = session.questions.find((item) => item.id === body.question_id)!;
      const pending = q.type === "short_answer" && q.purpose === "practice";
      const attempt = makeAttempt(session, q, body.answer, pending ? "pending" : "graded");
      if (q.purpose === "exam") {
        delete attempt.grade;
        delete attempt.reference_answer;
      }
      session.attempts = session.attempts.filter((a) => a.question_id !== q.id).concat(attempt);
      delete session.drafts[q.id];
      return response(clone(session));
    }
    if (action === "finish") {
      session.status = "grading";
      return response(clone(session));
    }
  }
  throw new Error(`Unexpected API ${path}`);
};

setLang("en");
const leaf: any = { app, contentEl: document.getElementById("workspace") };
view = new PracticeView(leaf, plugin);
win.qa = {
  get view() { return view; },
  sessions,
  get serverEnabled() { return serverEnabled; },
  async togglePractice(on: boolean) {
    await plugin.setPracticeExperiment(on);
  },
  async setStale(on: boolean) {
    resourceStale = on;
    await (view as any).refresh();
  },
  async reopen() {
    win.ready = false;
    await view.onClose();
    view.unload();
    view = new PracticeView(leaf, plugin);
    await view.onOpen();
    win.ready = true;
  },
};
view.onOpen().then(() => {
  win.ready = true;
});
