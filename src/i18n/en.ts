import type { Dict } from "./zh";

const NF = new Intl.NumberFormat("en-US");
const n = (v: number | undefined): string =>
  typeof v === "number" ? NF.format(v) : "?";

const k = (v: number | undefined): string => {
  if (typeof v !== "number" || !Number.isFinite(v)) return "?";
  const a = Math.abs(v);
  if (a < 1000) return String(Math.round(v));
  // 十万以上才丢小数：12.4k 有信息量，而 128.3k 在窄栏里太长。
  return `${(v / 1000).toFixed(a >= 100000 ? 0 : 1)}k`;
};

/**
 * 这一行的 `: Dict` 就是全部的强制力：
 *   缺键   -> TS2741 Property 'x' is missing
 *   多键   -> TS2353 excess property
 *   参数错 -> TS2322 签名不匹配
 * 函数参数由 Dict 上下文推断，不用手写注解（noImplicitAny 也不会报）。
 *
 * 语气对齐中文表：像坐在旁边的人在说话，口语，短句，不要客服腔。
 */

const LIMIT_TEXT_EN: Record<string, [string, string]> = {
  probe_max_per_window: ["Deep digs per hour", "Counted across sessions — opening new ones won't get around it."],
  probe_every_n_rounds: ["Rounds between deep digs", "0 means dig on every substantive reply (today's behaviour)."],
  probe_keep_per_run: [
    "Questions kept per dig",
    "Note: **only one is released per round** — the rest queue up and are dropped after 6 rounds. Raising this mostly grows the discard pile.",
  ],
  max_tokens_chat: [
    "Token cap per turn",
    "0 = no cap. This budgets the **tool loop**: once it's hit, the tutor still writes one answer from what it already has, and that shot is not capped. Actual spend therefore runs over the number you set — by how much depends on how much was read this turn, not by any fixed ratio.",
  ],
  max_tokens_probe: ["Token cap per background dig", "0 = no cap. Set it below the cost of one dig and no dig runs at all."],
  max_tokens_cross_book: [
    "Token cap for reading other textbooks",
    "0 = no cap. It answers «this turn has burned X already, don't open another book» — other books get resent every round, so the cost compounds.",
  ],
  max_tool_rounds: ["Tool calls per answer", "Reading this handbook is free; other textbooks have the two gates below."],
  cross_book_chars: ["Character budget for other textbooks", "Reading **this** handbook doesn't count — normal reading is never affected."],
  cross_book_reads: ["Read count for other textbooks", "A byte budget alone can't cap this: reading one line at a time never exhausts it."],
  probe_max_per_session: ["Deep digs per conversation", "Doesn't stop «open lots of new sessions» — the hourly quota does."],
  probe_pending_cap: ["Stop digging once this many are queued", "This saves waste, not frequency."],
  probe_max_reads: ["Passages a dig may read", "Reads are executed by code — the model never gets a loop of its own."],
  probe_read_lines: ["Lines per passage", ""],
  probe_timeout_s: [
    "Timeout per dig call (seconds)",
    "Raise it and a dig may outlast the turn you're on; questions aren't lost, they ride along on the next round.",
  ],
  probe_min_reply_chars: ["Minimum reply length to dig", "Short replies are usually a single counter-question — nothing to dig into."],
  probe_concurrency: [
    "Concurrent digs",
    "Per sidecar, not per book. **With several vaults open the knob points the wrong way**: the test is «digs in flight globally < your number», so turning it down makes it easier for another vault to crowd you out. To spend less, use the hourly count or the token caps above.",
  ],
};

export const en: Dict = {
  appName: "Socrates",
  viewTitle: "Socrates",

  ribbonTooltip: "Open Socrates",
  cmdAskSelection: "Ask about the current selection",
  cmdOpenPanel: "Open the panel",

  btnUseSelection: "Use selection",
  btnAsk: "Ask",
  askPlaceholder: "Ask something…",
  tipUseSelection: "Highlight a passage in your note, then hand it over here",

  tipNewSession: "Start over — drops this session's memory and selection",
  tipUndoEmpty: "Nothing to roll back yet. Approve an edit first.",
  tipUndo: (count) => `Roll the whole note back one version (${count} left)`,
  tipRedoEmpty: "Nothing to redo",
  tipRedo: (count) => `Put back what you just undid (${count} left)`,

  healthUnprobed: "sidecar not checked yet",
  healthOkSettings: (model) => `sidecar ok · from settings · ${model}`,
  healthOkFallback: (source, model) => `sidecar ok · dev fallback ${source} · ${model}`,
  healthNoKey: "sidecar is up — add your API key under Settings → Socrates",
  healthDown: "can't reach sidecar",

  errUnreachable: (detail) =>
    `Can't reach the sidecar (CORS / not running / wrong port): ${detail}`,
  errNoSelection:
    "Didn't catch a selection. Highlight a passage in the note, then hit “Use selection”.",
  // None of these name a cause. Ageing out is only the most common one — a
  // hand-deleted `.pen/`, a different sidecar, a full disk all land on the
  // same 404, and sending the reader chasing a cause we don't know is worse
  // than saying plainly what happened.
  noticeSessionArchived:
    "This conversation is gone from the sidecar (most likely cleaned up past its retention window). A fresh one is open and your last question was re-sent.",
  noticeSessionArchivedResendFailed:
    "This conversation is gone from the sidecar (most likely cleaned up past its retention window). A fresh one is open, but your last question could not be re-sent — the error below says why.",
  noticeSessionRenewed:
    "This note's previous conversation is gone from the sidecar (most likely cleaned up past its retention window). A fresh one is open.",
  errSessionArchivedHard:
    "This conversation is gone from the sidecar, and a fresh one couldn't be opened either.",
  errApprovalArchived:
    "The conversation this edit belongs to is gone from the sidecar. The note was not modified. A fresh conversation is open; just ask again.",
  errApprovalArchivedHard:
    "The conversation this edit belongs to is gone from the sidecar, and a fresh one couldn't be opened either. The note was not modified.",
  errApprovalUntouched: " (The write-back never ran — your note was not modified.)",

  usage: (ctx, out) => `context ${k(ctx)} · reply ${k(out)}`,
  spendTurn: (tok) => `turn ${k(tok)}`,
  spendSession: (tok) => `session ${k(tok)}`,
  spendTipTotal: (tok) => `${k(tok)} tokens this session`,
  spendTipRow: (label, inTok, outTok) => `${label}  in ${k(inTok)} / out ${k(outTok)}`,
  spendTipCached: (tok) => `${k(tok)} of that was cache hits (much cheaper)`,
  spendKindChat: "Tutor",
  spendKindProbe: "Deep dig",
  spendKindFold: "Write-back",
  spendTipNote: "Tokens only — not converted to money",

  kickerYou: "You",
  kickerPen: "Socrates",
  kickerReadTool: "reading",
  kickerEditTool: "editing",
  toolOk: "ok",
  toolDenied: "blocked",
  noPath: "(no path)",
  streamPlaceholder: "…",
  emptyHint:
    "Highlight a passage in a note — Live Preview or Reading view, either works — then hit “Use selection”.",

  splashTagline: "The Socratic Method",
  splashSubline: "Socrates-agent",

  phases: {
    thinking: "Thinking it over…",
    writing: "Writing…",
    reading: "Flipping through the manual…",
    tool: "Working on it…",
  },
  thinkTick: (chars) => `Socrates is thinking… ${k(chars)} chars`,
  statusEditing: "Editing the note…",
  statusDeclined: "Declined — letting it wrap up…",
  statusAwaitApproval: "Waiting for you to approve this edit",
  statusRollingBack: "Rolling back…",
  statusRedoing: "Redoing…",

  approvalTitle: "Approve this edit",
  approvalTarget: (tool, path) => `${tool} → ${path}`,
  approvalCurrentHandbook: "current manual",
  approvalTruncated: (n) => `… ${n} more characters not shown`,
  approvalWarn:
    "The model picked this snippet itself. Nothing touches your note until you allow it.",
  approvalOldLabel: "--- before ---",
  approvalNewLabel: "+++ after +++",
  btnApprove: "Allow this edit",
  btnReject: "Reject",

  noticeUnreachable: "Can't reach the sidecar — check the error up in the panel",
  noticeRegisterFirst: "Pick a passage first so this note gets registered",
  errSessionGoneNoHandbook:
    "This conversation is gone from the sidecar, and no note is registered in the panel yet, so a fresh one can't be opened. Pick a passage first.",
  noticeResolveApproval: "Allow or reject the pending edit first",
  noticeUseSelectionFirst: "Hit “Use selection” first",
  noticeRolledBack: "Rolled back one version",
  noticeRedone: "Redone",

  confirmNewSession:
    "A new session drops what the model remembers from this one, and the current selection. Go ahead?",
  confirmRollback:
    "The whole note goes back one version — anything you edited by hand after that goes too. Sure?",

  msgRolledBack: "Rolled back one version.",
  msgRedone: "Put back the edit you undid.",

  errNoRightLeaf: "No pane available in the right sidebar",
  errViewNotMounted: "The Socrates view didn't mount",
  errNeedDesktopVault: "Needs a desktop vault (FileSystemAdapter)",
  noticeSidecarDown:
    "The sidecar isn't running. Start it in a terminal: python -m pen — the model goes under Settings → Socrates",

  chips: {
    socratic: { label: "Don't tell me yet — ask me something", hint: "" },
    explain_zero: { label: "Assume I know nothing, then give me two examples", hint: "" },
    examples: { label: "Just show me examples", hint: "" },
    search: {
      label: "Find the paper / where this came from",
      hint: "Lands in P2. It won't pretend it searched.",
    },
    writeback: {
      label: "Write that answer back into the manual",
      hint: "Needs one real answer first",
    },
  },

  setLangName: "语言 / Language",
  setLangDesc: "Follows Obsidian's interface language by default.",
  setLangAuto: "Auto (follow Obsidian)",
  setIntro1:
    "Key and endpoint go here — no environment variables needed. This vault's path is handed to the local sidecar the moment you pick a passage.",
  setIntro2:
    "The API key lives in this vault, at .obsidian/plugins/socrates-pen/data.json. If the whole vault goes into Sync / iCloud / git, the key rides along.",
  setApiKeyDesc:
    "Key for your OpenAI-compatible endpoint. Masked here, never shown in the status line.",
  setBaseUrlDesc: "A Chat Completions-compatible address. No trailing slash.",
  setModelName: "Model",
  setModelDesc:
    "The model string your endpoint expects, e.g. deepseek-v4-flash or gpt-4.1-mini.",
  setThinkingDesc:
    "off is the safe bet. Turn it up only for reasoning models — endpoints that don't support it may hand you a 400.",
  setThinkingOff: "off (default)",
  deepQuotaSpent: "deep dives used up",
  setDeepName: "Dig deeper in the background",
  setDeepDesc: "After each answer, spend one more call looking for a question that reaches across chapters. It only appears if one turns up. Turn this off to keep just the two instant ones.",
  tipDeepPrefix: "\u25c6 ",
  setSecUsage: "Spend",
  setUsageLoading: "Reading the ledger…",
  setUsageDown: "Can't reach the sidecar, no ledger to read.",
  setUsageNote:
    "Counted from v0.10.0 onward. The number in the status bar is «this session»; this one is «all of it». Tokens only — not converted to money. Only sessions still inside the retention window are counted (empty ones last 1 day, ones you actually talked in last 7), so this number goes down as old sessions are swept.",
  setUsageTotal: (tok, sessions) => `${n(tok)} tokens across ${n(sessions)} conversations`,
  setUsageBreak: (chat, probe, fold) =>
    `tutor ${n(chat)} · deep dig ${n(probe)} · write-back ${n(fold)}`,
  setUsageCached: (tok) => `${n(tok)} of that was cache hits (much cheaper)`,
  setUsageEmpty: "Nothing on the ledger yet. Counting starts with conversations after the v0.10.0 upgrade.",
  setSecCommon: "Basics",
  setSecAdvanced: "Advanced (cost and speed gates — leave them alone if unsure)",
  setAdvancedNote:
    "These defaults were tuned against real runs. Before changing one, be clear about what you're saving — " +
    "most of the time the knob you want is one of the three token caps above, not these.",
  setDefaultHint: (v) => `Default ${v}.`,
  limitName: (k) => LIMIT_TEXT_EN[k]?.[0] ?? k,
  limitDesc: (k) => LIMIT_TEXT_EN[k]?.[1] ?? "",

  setSidecarDesc:
    "Where the local pen listens. You rarely need to touch this. Start it with: python -m pen --host 127.0.0.1 --port 8765",
};
