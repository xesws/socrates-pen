import { ApiError } from "./apierror";
import { currentLang } from "./i18n";
import { llmPayload, type PenSettings } from "./settings";

export { ApiError };

export type PracticeQuestionType = "single_choice" | "fill_blank" | "short_answer";
export type PracticeQuestionPurpose = "practice" | "exam";
export type PracticeSessionMode = "practice" | "recommended" | "exam";
export type PracticeSessionStatus = "active" | "grading" | "completed";
export type PracticeAttemptStatus = "pending" | "graded" | "failed" | "skipped";
export type PracticeJobStatus =
  | "queued"
  | "running"
  | "completed"
  | "completed_with_issues"
  | "failed"
  | "cancelled"
  | "stale"
  | "interrupted";

export type PracticeEvidence = { mq_id: string; quote: string };

export type PracticeMq = {
  id: string;
  version: string;
  title: string;
  text: string;
  source?: { start_line: number; end_line: number };
  chapter?: string;
};

export type PracticePoint = {
  id: string;
  name: string;
  definition: string;
  source_mq_ids?: string[];
  evidence?: PracticeEvidence[];
};

export type PracticeEdge = {
  from: string;
  to: string;
  type: "requires" | "related" | "contrasts";
  evidence?: PracticeEvidence[];
};

export type PracticeCriterion = {
  id: string;
  point_id: string;
  max_score: number;
  description: string;
  partial_credit?: string;
  tier?: "basic" | "advanced" | "complete";
  levels?: { id: string; score: number; condition: string }[];
  evidence?: PracticeEvidence[];
};

export type PracticeBlank = {
  id: string;
  label: string;
  case_sensitive?: boolean;
  numeric_tolerance?: number;
  numeric_value?: number;
  grading: "exact" | "semantic";
};

export type PracticeQuestion = {
  id: string;
  version: string;
  source_mq_id: string;
  type: PracticeQuestionType;
  purpose: PracticeQuestionPurpose;
  exam_form: number | null;
  prompt: string;
  point_ids: string[];
  status: "accepted";
  estimated_seconds?: number;
  choices?: { id: string; text: string }[];
  blanks?: PracticeBlank[];
  criteria?: PracticeCriterion[];
  reference_answer?: string;
};

export type PracticeAnswer = {
  choice_id?: string;
  blanks?: Record<string, string>;
  text?: string;
  images?: { mime: string; data: string }[];
};

export type PracticeGrade = {
  criteria: {
    criterion_id: string;
    point_id: string;
    score: number;
    max_score: number;
    reason: string;
    evidence_quote?: string;
  }[];
  score: number;
  max_score: number;
  model?: unknown;
  usage?: unknown;
  achievement?: { id: string; label: string } | null;
  transcriptions?: { image_index: number; text: string }[];
};

export type PracticeAttempt = {
  id: string;
  session_id: string;
  question_id: string;
  question_version: string;
  source_version: string;
  purpose: PracticeQuestionPurpose;
  answer?: PracticeAnswer;
  status: PracticeAttemptStatus;
  created_at: string;
  answered_at?: string;
  duration_seconds?: number;
  decision_id?: string;
  grade?: PracticeGrade;
  error?: string;
  error_code?: string;
  question?: PracticeQuestion;
  reference_answer?: string;
  solution_markdown?: string;
  criteria?: PracticeCriterion[];
};

export type PracticeBlueprint = {
  id: string;
  version: string;
  resource_version?: string;
  point_weights: Record<string, number>;
  target_score: number;
  target_date: string;
  daily_minutes: number;
  source_mq_ids: string[];
  stable_tolerance: number;
};

export type PracticeIssue = {
  mq_id?: string;
  message: string;
  code?: string;
  severity?: string;
  [key: string]: unknown;
};

export type PracticeResource = {
  resource_version: string;
  stale?: boolean;
  meta_questions?: PracticeMq[];
  mqs?: PracticeMq[];
  points: PracticePoint[];
  edges: PracticeEdge[];
  issues: PracticeIssue[];
};

export type PracticeServices = {
  analysis?: { status?: string; port?: number; protocol?: number; error?: string };
  scheduling?: { status?: string; port?: number; protocol?: number; error?: string };
};

export type PracticeStats = {
  questions?: number;
  practice_questions?: number;
  exam_questions?: number;
  attempts?: number;
  graded_attempts?: number;
};

export type PracticeJob = {
  id: string;
  status: PracticeJobStatus;
  completed: number;
  total: number;
  issues?: (string | PracticeIssue)[];
  usage?: unknown;
  error?: string;
};

export type PracticeState = {
  enabled: boolean;
  resource: PracticeResource | null;
  job: PracticeJob | null;
  blueprint: PracticeBlueprint | null;
  sessions: PracticeSession[];
  services?: PracticeServices;
  stats?: PracticeStats;
};

export type PracticeRecommendation = {
  question_id: string;
  decision_id: string;
  point_id: string;
  reason: string;
  estimated_seconds?: number;
  due_at?: string;
  probability?: number;
};

export type PracticeSession = {
  id: string;
  handbook_id: string;
  mode: PracticeSessionMode;
  status: PracticeSessionStatus;
  questions: PracticeQuestion[];
  attempts: PracticeAttempt[];
  drafts: Record<string, PracticeAnswer>;
  recommendations?: PracticeRecommendation[];
  created_at: string;
  blueprint?: PracticeBlueprint;
  exam_form?: number;
  result?: {
    score?: number | null;
    provisional_score?: number | null;
    max_score?: number | null;
    stable?: boolean;
    complete_coverage?: boolean;
    skipped?: number;
    summary?: string;
  };
};

export type PracticeAnalysisDirection =
  | string
  | {
      point_id?: string;
      name?: string;
      reason?: string;
      gap?: number | null;
      n?: number;
      [key: string]: unknown;
    };

export type PracticeAnalysisEvidence =
  | string
  | {
      attempt_id?: string;
      question_id?: string;
      score?: number | null;
      at?: string;
      [key: string]: unknown;
    };

export type PracticeAnalysis = {
  points: {
    id: string;
    name: string;
    n: number;
    score: number | null;
    expected_score: number | null;
    status: "unmeasured" | "insufficient" | "weak" | "stable" | string;
    distinct_questions?: number;
    repeated_attempts?: number;
    evidence?: PracticeAnalysisEvidence[];
    last_seen?: string;
    due_at?: string;
  }[];
  directions?: PracticeAnalysisDirection[];
  model?: { status?: string; version?: string; reason?: string };
  resource_version?: string;
  cursor?: number;
  exams?: {
    stable?: boolean;
    comparable_count?: number;
    target_score?: number;
    target_date?: string;
    score_span?: number;
    stable_tolerance?: number;
    history?: {
      id?: string;
      score?: number | null;
      target_score?: number | null;
      date?: string;
      completed_at?: string;
      comparable?: boolean;
      passed?: boolean;
      result?: PracticeSession["result"] & { passed?: boolean; target_score?: number | null };
      blueprint_version?: string;
      [key: string]: unknown;
    }[];
    [key: string]: unknown;
  };
};

export type PracticeRecommendationResult = {
  items: PracticeRecommendation[];
  model?: { status?: string; version?: string; reason?: string };
  cursor?: number;
};

type BuildOptions = {
  practice_per_type?: number;
  exam_forms?: number;
  regenerate?: boolean;
};

function joinUrl(base: string, path: string): string {
  return `${base.replace(/\/$/, "")}${path}`;
}

function qs(vaultRoot: string, extra?: Record<string, string | undefined>): string {
  const p = new URLSearchParams();
  p.set("vault_root", vaultRoot);
  for (const [k, v] of Object.entries(extra || {})) {
    if (v !== undefined && v !== "") p.set(k, v);
  }
  return p.toString();
}

async function errorFrom(res: Response): Promise<ApiError> {
  let message = res.statusText;
  let code = "";
  try {
    const body = (await res.json()) as { detail?: unknown };
    const d = body?.detail;
    if (d && typeof d === "object") {
      const o = d as { code?: unknown; message?: unknown };
      if (typeof o.code === "string") code = o.code;
      message = typeof o.message === "string" && o.message ? o.message : JSON.stringify(body);
    } else {
      message = typeof d === "string" && d ? d : JSON.stringify(body);
    }
  } catch {
    /* keep statusText */
  }
  return new ApiError(res.status, message, code);
}

async function j<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(joinUrl(base, path), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "Accept-Language": currentLang(),
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) throw await errorFrom(res);
  return res.json() as Promise<T>;
}

function llm(settings: PenSettings): ReturnType<typeof llmPayload> & { lang: string } {
  return { ...llmPayload(settings), lang: currentLang() };
}

export function makePracticeApi(baseUrl: string) {
  const root = "/v1/practice";
  return {
    enable: (vaultRoot: string, enabled: boolean) =>
      j<{ enabled: boolean; services?: PracticeServices }>(baseUrl, `${root}/enable`, {
        method: "PUT",
        body: JSON.stringify({ vault_root: vaultRoot, enabled }),
      }),
    state: (vaultRoot: string, handbookId: string) =>
      j<PracticeState>(baseUrl, `${root}/state?${qs(vaultRoot, { handbook_id: handbookId })}`),
    build: (
      vaultRoot: string,
      handbookId: string,
      settings: PenSettings,
      opts?: BuildOptions,
    ) =>
      j<PracticeJob>(baseUrl, `${root}/build`, {
        method: "POST",
        body: JSON.stringify({
          vault_root: vaultRoot,
          handbook_id: handbookId,
          ...llm(settings),
          ...(opts || {}),
        }),
      }),
    job: (vaultRoot: string, id: string) =>
      j<PracticeJob>(baseUrl, `${root}/jobs/${encodeURIComponent(id)}?${qs(vaultRoot)}`),
    cancelJob: (vaultRoot: string, id: string) =>
      j<PracticeJob>(baseUrl, `${root}/jobs/${encodeURIComponent(id)}/cancel`, {
        method: "POST",
        body: JSON.stringify({ vault_root: vaultRoot }),
      }),
    resumeJob: (vaultRoot: string, id: string, settings: PenSettings) =>
      j<PracticeJob>(baseUrl, `${root}/jobs/${encodeURIComponent(id)}/resume`, {
        method: "POST",
        body: JSON.stringify({ vault_root: vaultRoot, ...llm(settings) }),
      }),
    putBlueprint: (vaultRoot: string, handbookId: string, blueprint: PracticeBlueprint) =>
      j<PracticeBlueprint>(baseUrl, `${root}/blueprint`, {
        method: "PUT",
        body: JSON.stringify({ vault_root: vaultRoot, handbook_id: handbookId, blueprint }),
      }),
    questions: (vaultRoot: string, handbookId: string) =>
      j<{ questions: PracticeQuestion[] }>(
        baseUrl,
        `${root}/questions?${qs(vaultRoot, { handbook_id: handbookId })}`,
      ),
    createSession: (
      vaultRoot: string,
      handbookId: string,
      mode: PracticeSessionMode,
      settings: PenSettings,
      opts?: { question_ids?: string[]; minutes?: number },
    ) =>
      j<PracticeSession>(baseUrl, `${root}/sessions`, {
        method: "POST",
        body: JSON.stringify({
          vault_root: vaultRoot,
          handbook_id: handbookId,
          mode,
          ...llm(settings),
          ...(opts || {}),
        }),
      }),
    session: (vaultRoot: string, id: string) =>
      j<PracticeSession>(baseUrl, `${root}/sessions/${encodeURIComponent(id)}?${qs(vaultRoot)}`),
    draft: (vaultRoot: string, id: string, questionId: string, answer: PracticeAnswer) =>
      j<{ ok: true }>(baseUrl, `${root}/sessions/${encodeURIComponent(id)}/draft`, {
        method: "PUT",
        body: JSON.stringify({ vault_root: vaultRoot, question_id: questionId, answer }),
      }),
    answer: (
      vaultRoot: string,
      id: string,
      questionId: string,
      answer: PracticeAnswer,
      key: string,
      durationSeconds: number,
      settings: PenSettings,
      skip = false,
    ) =>
      j<PracticeSession>(baseUrl, `${root}/sessions/${encodeURIComponent(id)}/answers`, {
        method: "POST",
        body: JSON.stringify({
          vault_root: vaultRoot,
          question_id: questionId,
          answer,
          idempotency_key: key,
          duration_seconds: durationSeconds,
          skip,
          ...llm(settings),
        }),
      }),
    finish: (vaultRoot: string, id: string, settings: PenSettings) =>
      j<PracticeSession>(baseUrl, `${root}/sessions/${encodeURIComponent(id)}/finish`, {
        method: "POST",
        body: JSON.stringify({ vault_root: vaultRoot, ...llm(settings) }),
      }),
    retryAttempt: (vaultRoot: string, id: string, settings: PenSettings) =>
      j<PracticeAttempt>(baseUrl, `${root}/attempts/${encodeURIComponent(id)}/retry`, {
        method: "POST",
        body: JSON.stringify({ vault_root: vaultRoot, ...llm(settings) }),
      }),
    analysis: (vaultRoot: string, handbookId: string) =>
      j<PracticeAnalysis>(baseUrl, `${root}/analysis?${qs(vaultRoot, { handbook_id: handbookId })}`),
    recommendations: (vaultRoot: string, handbookId: string, minutes?: number) =>
      j<PracticeRecommendationResult>(baseUrl, `${root}/recommendations`, {
        method: "POST",
        body: JSON.stringify({
          vault_root: vaultRoot,
          handbook_id: handbookId,
          ...(minutes !== undefined ? { minutes } : {}),
        }),
      }),
  };
}
