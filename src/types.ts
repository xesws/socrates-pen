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
  /** 这场已经把旧回合折进滚动摘要。sidecar >= v0.19.0 才有。 */
  compacted?: boolean;
  /** 这次 compact 有没有真的折到东西。只在 POST compact 回包里有。 */
  did?: boolean;
};

export type ChatMessage = {
  /** "error" 只在前端：请求失败时的错误气泡，从不上行。
   *  "note" 是 compact 分隔条：旧气泡还在，标明上面几轮已折进摘要。 */
  role: "user" | "assistant" | "tool" | "error" | "note";
  text: string;
  ok?: boolean;
  /**
   * 点芯片发起时后端会带上芯片 id（sidecar >= v0.7.1）。
   * 有它就能把落盘的中文 label 换成当前语言，旧快照没有则回落 text。
   */
  chip?: string;
  /** sidecar >= v0.19.0：`note` 且 kind=compact 时前端用当前语言重绘文案。 */
  kind?: string;
  /** 本轮贴的图。thumb 只活在前端内存，落盘只有 mime。 */
  images?: { mime: string; thumb?: string }[];
  /** 错误气泡上挂「去设置」。 */
  goSettings?: boolean;
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
  /** 末 4 位掩码；短钥匙为空串。v0.18.0 起健康行/设置页只显示这个。 */
  key_tail?: string;
  thinking?: string;
};

/** GET /v1/health。旧 sidecar 没有 version —— 插件据此判定不能干活。 */
export type Health = {
  status: string;
  version?: string;
  llm: LlmStatus;
  /** 快模型那一槽。**形状和 llm 完全一样**（后端 fast_public_status 就是照它
   *  写的），所以不另立类型。旧 sidecar 没有这个键，故可选。 */
  fast?: LlmStatus;
};

/** 一槽配置的体检结论。`code` 空串 = 真的能用（不是「看起来填齐了」）。
 *  文案由 sidecar 给：`provider.*` 那张表是「节点这一下为什么失败」的
 *  唯一定义点，前端再抄一份就会和对话里的红字气泡说不一样的话。 */
export type PreflightSlot = {
  ok: boolean;
  code: string;
  message: string;
};

/** POST /v1/llm/preflight。fast 只在请求里带了 fast:true 时才有。 */
export type PreflightReport = {
  base: PreflightSlot;
  fast?: PreflightSlot;
};

export type NoteBinding = {
  handbook_id: string;
  session_id: string;
};

// ── 学习画像（v0.25.0）。镜像 pen/profile.py 的 report() / overview()。
// **插件不复算任何分数**：分、掌握概率、「怎么算的」全是服务端给的。──

export type ProfileArc = {
  start_idx: number;
  end_idx: number;
  turns: number;
  asking: number;
  closed: boolean;
  penalty: number;
};

export type ProfileLapse = { declare_idx: number; reopen_idx: number; minutes: number };

export type ProfileGap = { key: string; idx: number; quote: string; asked_at: string };

export type ProfileEvidence = {
  key: string;
  idx: number;
  type: string;
  quote: string;
  asked_at: string;
  verify: string | null;
  reject_right: boolean | null;
};

export type ProfileAxis = {
  id: string;
  name: string;
  definition: string;
  first_seen: string;
  /** 这轴一共几轮（含旧行）。 */
  n: number;
  /** 其中 v0.24.0 之前的旧行几轮——只进频率，不进分。 */
  n_legacy: number;
  /** 1–10；证据不足 3 轮是 null（未评）。 */
  score: number | null;
  /** 「这一分怎么来的」，按 Accept-Language 成文，逐条 setText。 */
  why: string[];
  /** BKT 掌握概率 0–1；没有观测是 null。与 score 并列显示，不平均。 */
  mastery: number | null;
  n_obs: number;
  arcs: ProfileArc[];
  lapses: ProfileLapse[];
  gaps: ProfileGap[];
  evidence: ProfileEvidence[];
};

/** GET /v1/handbooks/{id}/profile */
export type ProfileView = {
  handbook_id: string;
  title: string;
  n_turns: number;
  n_coded: number;
  n_legacy: number;
  n_uncoded: number;
  n_given_up: number;
  n_meta: number;
  axes: ProfileAxis[];
  coded_at: string;
  spend: TokenRow;
};

/** POST /v1/handbooks/{id}/profile/code —— 一次最多编 max_batches 批，插件循环到 remaining == 0。 */
export type ProfileCodeResult = {
  handbook_id: string;
  coded: number;
  n_coded: number;
  n_turns: number;
  remaining: number;
  uncoded_batches: number;
  spend: TokenRow;
  axes: { id: string; name: string; definition: string; aliases: string[]; first_seen: string }[];
};

export type ProfileAxisBrief = { id: string; name: string; score: number | null };
/** 书架里每条轴的计数：n 是证据轮数，asked 是「在问」的轮数（含旧行）。插件合并同标题登记时用它。 */
export type ProfileAxisCount = { id: string; name: string; score: number | null; n: number; asked: number };

export type ProfileBook = {
  handbook_id: string;
  title: string;
  original_path: string;
  n_turns: number;
  n_coded: number;
  n_axes: number;
  axes: ProfileAxisCount[];
  weakest: ProfileAxisBrief[];
  strongest: ProfileAxisBrief[];
  asked_most: { id: string; name: string; n: number }[];
  spend: TokenRow;
};

/** GET /v1/profiles?vault_root= —— 这个库里每本书一行；merged_by_title 指出同名的多次登记。 */
export type ProfileList = {
  vault_root: string;
  books: ProfileBook[];
  merged_by_title: Record<string, string[]>;
};
