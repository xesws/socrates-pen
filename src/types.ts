export type Chip = {
  id: string;
  label: string;
  enabled: boolean;
  hint?: string;
};

/**
 * 一条动态追问。
 * quick = 主回复搭车产出的基础追问；deep = 后台探索挖出的（v0.8.2 起）。
 * why 只有 deep 有，挂 tooltip，不进对话。
 */
export type DynChip = {
  id: string;
  kind: "quick" | "deep";
  text: string;
  why?: string;
};

export type HandbookMeta = {
  handbook_id: string;
  title: string;
  original_path: string;
  imported_at: string;
  mtime: number;
  n_lines?: number;
  toc?: { level: string; beat: string | null; start_line: number; heading: string }[];
};

export type PendingEdit = {
  pending_id: string;
  name: string;
  args: { path?: string; old_string?: string; new_string?: string };
};

export type SessionView = {
  session_id: string;
  handbook_id: string;
  chips: Chip[];
  has_substantive: boolean;
  last_anchor: {
    selected_text?: string;
    start_line?: number;
    end_line?: number;
    level?: string;
    q_title?: string | null;
    kind?: string;
    beat?: string | null;
  } | null;
  ui_messages: ChatMessage[];
  last_assistant?: string;
  pending?: PendingEdit | null;
  /** 上一轮的动态追问。以前只在内存里，刷新就没了。sidecar >= v0.8.0 才有。 */
  dyn_chips?: DynChip[];
  /**
   * 本会话累计花掉的 token。关掉侧栏再打开时第三格靠它恢复，
   * 而不是归零。sidecar >= v0.10.0 才有。
   */
  spend?: SpendBook;
};

export type ChatMessage = {
  role: "user" | "assistant" | "tool";
  text: string;
  ok?: boolean;
  /**
   * 点芯片发起时后端会带上芯片 id（sidecar >= v0.7.1）。
   * 有它就能把落盘的中文 label 换成当前语言，旧快照没有则回落 text。
   */
  chip?: string;
};

/** GET /v1/sessions/{id}/deep 的回包。running 为空 = 可以停轮询了。 */
/** 一格 token 账。字段名和后端 pen/meter.py 的 _FIELDS 一一对应。 */
export type TokenRow = {
  calls: number;
  in_tokens: number;
  out_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
};

/** 本会话的三格账。probe 那格由后端从账本合进来。 */
export type SpendBook = { chat?: TokenRow; probe?: TokenRow; fold?: TokenRow };

/** 跨会话累计。和 SpendBook 是两个口径：那个是「这一场」，这个是「一共」。 */
export type UsageTotal = {
  spend: SpendBook;
  total: number;
  sessions: number;
  skipped: number;
  handbook_id: string;
};

export type DeepInbox = {
  session_id: string;
  items: DynChip[];
  cursor: number;
  running: string[];
  budget: { used: number; max: number; window_used?: number; window_max?: number };
  /** 深挖累计花掉的 token。和 budget 是两件事：budget 数还能探几次。 */
  spend?: TokenRow;
};

export type SnapshotStatus = {
  can_undo: boolean;
  can_redo: boolean;
  undo_n: number;
  redo_n: number;
};

export type LlmStatus = {
  ok: boolean;
  base_url: string;
  model: string;
  key_source: string;
  thinking?: string;
};

export type NoteBinding = {
  handbook_id: string;
  session_id: string;
};
