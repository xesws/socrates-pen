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

const TYPE_EN: Record<string, string> = {
  ASK: "asking",
  VERIFY: "self-check",
  DEMAND: "demand",
  REJECT: "pushback",
  GAP: "blind spot",
  DECLARE: "declared understood",
  META: "bookkeeping",
};
const VERIFY_EN: Record<string, string> = {
  confirmed: "confirmed",
  corrected: "corrected",
  unclear: "unjudged",
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
    "Note: only one is released per round — the rest queue up and are dropped after 6 rounds. Raising this mostly grows the discard pile.",
  ],
  max_tokens_chat: [
    "Token cap per turn",
    "0 = no cap. This budgets the tool loop: once it's hit, the tutor still writes one answer from what it already has, and that shot is not capped. Actual spend therefore runs over the number you set — by how much depends on how much was read this turn, not by any fixed ratio.",
  ],
  max_tokens_probe: ["Token cap per background dig", "0 = no cap. Set it below the cost of one dig and no dig runs at all."],
  max_tokens_cross_book: [
    "Token cap for reading other textbooks",
    "0 = no cap. It answers «this turn has burned X already, don't open another book» — other books get resent every round, so the cost compounds.",
  ],
  compact_chat_tokens: [
    "Auto-fold the main chat into a summary at this window size",
    "0 = don't auto-fold (the command palette can still fold by hand). When the last prompt hits this many tokens, the next turn starts by folding older rounds into a summary with line anchors; the sidebar bubbles stay. This is not a spend cap, and it does not drop old turns.",
  ],
  max_tool_rounds: ["Tool calls per answer", "Reading this handbook is free; other textbooks have the two gates below."],
  fast_context_tokens: [
    "Context cap for the fast model",
    "Only applies while Fast Mode is on. The fast model's window is 131072 for input and output combined, leaving at most 114688 for input; going over is rejected outright by the provider, so this default keeps some headroom. If it still will not fit, that turn falls back to the base model and the conversation carries on. 0 = no compression (not recommended).",
  ],
  cross_book_chars: ["Character budget for other textbooks", "Reading this handbook doesn't count — normal reading is never affected."],
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
    "Per sidecar, not per book. With several vaults open the knob points the wrong way: the test is «digs in flight globally < your number», so turning it down makes it easier for another vault to crowd you out. To spend less, use the hourly count or the token caps above.",
  ],
};

export const en: Dict = {
  appName: "Socrates",
  viewTitle: "Socrates",

  ribbonTooltip: "Open Socrates",
  cmdAskSelection: "Ask about the current selection",
  cmdOpenPanel: "Open the panel",
  cmdCompactSession: "Compact this session",

  btnUseSelection: "Use selection",
  btnAsk: "Ask",
  askPlaceholder: "Ask something…",
  askPlaceholderVision: "Ask something, or paste an image…",
  tipUseSelection: "Highlight a passage in your note, then hand it over here",

  tipNewSession: "Start over — drops this session's memory and selection",
  tipCompact: "Fold earlier turns into a summary. Sidebar bubbles stay; the next request will not resend the full text.",
  compactMarker: "Earlier turns folded into the summary",
  kickerCompact: "Summary",
  tipFastOff: "Fast Mode is off. Turn it on and read-only turns get answered by the fast model.",
  tipFastOn: "Fast Mode is on: read-only turns run on the fast model. Turns that rewrite your note switch back to the base model.",
  tipFastTrimmed: (steps: string): string =>
    `Fast Mode is on. This turn's context did not fit the fast model's window, so it was trimmed: ${steps}. Your session itself was not folded.`,
  kickerRoute: "Model switch",
  noteRouteEdit: "This turn wants to rewrite your note, so it was handed back to the base model. Ignore the half-sentence above — that was the fast model.",
  noteRouteTooBig: "This turn's context would not fit the fast model's window, so it was handed back to the base model.",
  noteRouteNoKey: "Fast Mode is on, but the fast model has no API key yet, so this turn ran on the base model. Add one in settings and it starts working.",
  noteRouteHostGap: "Fast Mode is on, but the fast key stored on this machine belongs to a different endpoint, so this turn ran on the base model. Open settings and save the API key again for the Fast Base URL you have now.",
  noticeFastNoKey: "Fast Mode is on, but there is no API key for the fast model yet, so turns still run on the base model. Add one in settings.",
  noticeFastKeyHostMismatch: (keyHost, urlHost) =>
    `The fast endpoint changed. The key stored on this machine is still for ${keyHost} and will not be sent to ${urlHost}, so every turn falls back to the base model. Save an API key for ${urlHost}.`,
  noticeCompactEmpty: "Nothing to fold yet",
  noticeCompactPending: "Allow or reject the pending edit first, then compact.",
  noticeCompactBusy: "This conversation is still running — let it finish first.",
  noticeCompactOk: "Earlier turns are in the summary. Sidebar bubbles stay.",
  tipUndoEmpty: "Nothing to roll back yet. Approve an edit first.",
  tipUndo: (count) => `Roll the whole note back one version (${count} left)`,
  tipRedoEmpty: "Nothing to redo",
  tipRedo: (count) => `Put back what you just undid (${count} left)`,

  healthUnprobed: "sidecar not checked yet",
  healthOkKey: (tail, model) =>
    `sidecar ok · key stored locally${tail ? ` …${tail}` : ""} · ${model}`,
  healthOkFallback: (source, model) => `sidecar ok · dev fallback ${source} · ${model}`,
  healthNoKey: "sidecar is up — add your API key under Settings → Socrates",
  healthDown: "can't reach sidecar",
  healthStale: "Old service holding the port. Stop then Start in settings to upgrade.",

  // ── Error bubbles (v0.18.0: a failed request no longer fakes streaming) ──
  errNoKey: "No model key yet. Add your API key under Settings → Socrates, then ask again.",
  errNoVision:
    "Image understanding is off for this model. Turn it on under Settings → Socrates. If the endpoint has no vision, it will still reject the image.",
  errVisionTooBig: "Image too large (2MB each, up to 4 images).",
  errVisionTooMany: "At most 4 images per turn.",
  errVisionBadType: "Only png / jpeg / webp / gif.",
  bubbleGoSettings: "Open settings",

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
  bubbleSessionRenewed:
    "This conversation's previous record was cleaned up past its retention window — this is a freshly opened one. If the old thread on the original note is still within retention, select a passage there to bring it back.",
  noticeNoteRestored: (note) =>
    `Switched back to the conversation for “${note}”. To keep asking, highlight a passage in the note first.`,
  errSessionGone: (note) =>
    `The previous conversation for “${note}” was cleaned up. Highlight a passage to start a fresh one.`,
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
  kickerFetchTool: "fetch",
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
    selection_capped: "Selection too long — first packet has the start; the rest is read on demand",
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
    "The local service isn't up. Open Settings → Socrates and check the status at the top.",
  noticeKeySaved:
    "Key saved to the local sidecar (~/.socrates-pen/llm.json). It never enters this vault.",
  noticeKeySaveFailed: (detail) =>
    `Couldn't save (${detail}). The key was written nowhere.`,
  noticeKeySaveOldSidecar:
    "Couldn't save: an old service is holding the port and can't take the new key. Hit Stop then Start to upgrade. The key is still in the box and was written nowhere.",
  noticeKeyCleared: "Key removed from the local sidecar.",
  noticeKeyMigrated:
    "Your API key moved from data.json to ~/.socrates-pen/llm.json on this machine — Sync / iCloud / git no longer carry it. If this vault was ever synced or committed, rotate that key at your provider.",
  noticeSidecarTooOld:
    "The local service is an old version and can't take the new key. Under Settings → Socrates, hit Stop then Start to upgrade — the key migrates right after, and until then it stays in data.json.",
  noticeSidecarAlready: "The local service is already running.",
  noticeSidecarStale: "An old service is holding the port. Hit Stop then Start to upgrade.",
  noticeSidecarAlreadyStopped: "The local service wasn't running.",
  noticeSidecarStoppedOwned: "Stopped the local service.",
  noticeSidecarStoppedLeftover: (command) =>
    command && command !== "?"
      ? `Stopped the old service holding the port (${command}).`
      : "Stopped the old service holding the port.",
  noticeSidecarStoppedShared: (command) =>
    command && command !== "?"
      ? `Stopped the local service holding the port (${command} — another vault or left over from last time).`
      : "Stopped the local service holding the port (another vault or left over from last time).",
  noticeSidecarStoppedOther: (command) =>
    command && command !== "?"
      ? `Stopped another process holding the port (${command}).`
      : "Stopped another process holding the port.",
  noticeSidecarStopFailed: "Couldn't stop it. See the status line above.",
  noticeKeyMigrateTimeout:
    "Couldn't migrate your API key into the local service within 45s (it may still be installing). The key stays in data.json and migrates automatically once the service is ready; to use it right now, paste it once in settings.",
  noticeKeyHostMismatch: (keyHost, urlHost) =>
    `The endpoint changed. The key on this machine is still for ${keyHost} and will not be sent to ${urlHost}. Paste a key for ${urlHost}.`,
  noticeSessionSwitched: (note) =>
    `Switched to the conversation for “${note}”. The previous note's conversation stays with that note — select in it again to come back.`,
  noticeRightOpened: "The Socrates panel is open in the right sidebar.",

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
  setSidecarSvc: "Local service",
  setSidecarSvcDesc:
    "The plugin creates an isolated Python environment under ~/.socrates-pen, installs the tutor, and binds it to 127.0.0.1. You need Python 3.11+ on this machine. The first run can take a minute (downloads dependencies).",
  setSidecarStart: "Start",
  setSidecarStop: "Stop",
  setSidecarStopping: "Stopping…",
  setSidecarAutoName: "Start when Obsidian opens",
  setSidecarAutoDesc: "On by default. Turn it off to start only from this page.",
  setSidecarPythonName: "Python path",
  setSidecarPythonDesc: "Leave empty to auto-detect python3 / python / py. If none is found, install 3.11+ from python.org.",
  setSidecarPhaseIdle: "Not running",
  setSidecarPhaseChecking: "Checking…",
  setSidecarPhaseInstalling: "Installing the local service (first time is slower)…",
  setSidecarPhaseStarting: "Starting…",
  setSidecarPhaseRunning: "Running",
  setSidecarPhaseStopping: "Stopping…",
  setSidecarPhaseStopped: "Stopped",
  setSidecarPhaseStale: (ver) =>
    ver
      ? `Old service holding the port (${ver}). Stop then Start to upgrade`
      : "Old service holding the port. Stop then Start to upgrade",
  setSidecarErrNoPython:
    "No Python 3.11+ on PATH — it's needed to create the local environment. Install it, or set an absolute interpreter path below (e.g. /opt/homebrew/bin/python3).",
  setSidecarErrNotLoopback: "The plugin will only start the service on 127.0.0.1 / localhost. Put a loopback Sidecar URL back.",
  setSidecarErrBadUrl: "Sidecar URL isn't a valid address.",
  setSidecarErrInstall: "Install failed. This machine needs access to GitHub and PyPI.",
  setSidecarErrSpawn: "The process didn't start.",
  setSidecarErrHealth: "The process started, but health checks never passed.",
  setSidecarErrStop: "Couldn't stop it.",
  setSidecarErrStopNoPid: "Couldn't stop it: no process found on that port.",
  setSidecarErrOther: (code: string): string => `Couldn't start (${code})`,
  setIntro1:
    "Key and endpoint go below. The plugin starts the local service — no terminal. This vault's path is handed over the moment you pick a passage.",
  setIntro2:
    "The API key lives only on this machine, in the sidecar home (~/.socrates-pen/llm.json, mode 0600) — never inside this vault, so Sync / iCloud / git can't carry it away.",
  setApiKeyDesc:
    "Write-only: paste, then Save (Enter or click away still work). Stored on the local sidecar, never in the vault. Save is disabled until the service is up and current. Empty input is ignored; use the button to clear.",
  setKeySave: "Save",
  setKeyStatusSaved: (source, tail) =>
    `Saved${source ? ` (source: ${source})` : ""}${tail ? `, tail …${tail}` : ""}.`,
  setKeyStatusNone: "No key saved yet.",
  setCheckRunning: "Checking with the endpoint…",
  setCheckOk: "Verified against the endpoint.",
  setKeyStatusUnreachable: "Sidecar not running — key status unknown.",
  setKeyClear: "Clear key",
  setKeepAliveName: "Keep local service running after exit",
  setKeepAliveDesc:
    "The local service is a Python process on your machine, shared by all vaults. On by default: it survives quitting Obsidian, so reopening is instant. Turn off to stop the one this plugin spawned when it unloads — don't if another vault is using it.",
  setBaseUrlDesc: "A Chat Completions-compatible address. No trailing slash.",
  setProviderName: "Provider",
  setFastProviderName: "Fast model provider",
  setProviderDesc:
    "Pick one and the thinking level is sent the way that provider expects. Auto reads the model name and falls back to a generic form. Picking one also prefills the Base URL, without overwriting an address you typed yourself.",
  setProviderAuto: "Auto (from the model name)",
  setProviderGeneric: "Generic OpenAI-compatible",
  providerHint: {
    auto: "Read from the model name: deepseek / gemini / glm / kimi / gpt- and so on pick that provider. Anything else uses the generic form.",
    celeris: "https://inference.celeris.ai/celeris-1-magnus/v1 · models like celeris-1-magnus · it has no high tier, so high is sent as xhigh.",
    google: "https://generativelanguage.googleapis.com/v1beta/openai/ · models like gemini-3-pro · Gemini 3 cannot turn thinking off, so off drops to the lowest tier.",
    deepseek: "https://api.deepseek.com · models like deepseek-v4-flash · it thinks at full effort when no level is given, so off means explicitly off.",
    glm: "https://open.bigmodel.cn/api/paas/v4 · models like glm-5.3 · GLM-5.3 cannot turn thinking off, so off drops to the lowest tier.",
    kimi: "https://api.moonshot.ai/v1 · models like kimi-k3 or kimi-k2.6 · K3 cannot turn thinking off, and its wire format differs entirely from K2.",
    meta: "https://api.meta.ai/v1 · models like muse-spark-1.3 · it always reasons, so off drops to the lowest tier.",
    openai: "https://api.openai.com/v1 · models like gpt-5 or o3-mini · the gpt-4 family are not reasoning models, so no reasoning field is sent at all.",
    openrouter: "https://openrouter.ai/api/v1 · model names carry a vendor prefix, e.g. google/gemini-3-pro · auto-detection is easiest to get wrong here, so pick explicitly.",
    generic: "Any OpenAI-compatible endpoint. Sends only the common reasoning_effort and never a vendor-specific form — the safest choice for an endpoint you don't know well.",
  },
  setModelName: "Model",
  setModelDesc:
    "The model string your endpoint expects, e.g. deepseek-v4-flash or gpt-4.1-mini.",
  setVisionName: "Image understanding",
  setVisionDesc:
    "When on, you can paste or drop images in the chat box. Leave it off for text-only models like DeepSeek. Turn it on for multimodal GLM / Qwen. Pasting while off errors immediately — the image is not sent.",
  setSecFast: "Fast Mode",
  setFastDesc:
    "Turns that only ask get answered by a faster model; turns that rewrite your note switch back to the base model above. " +
    "The toggle is the lightning bolt at the top right of the sidebar. Fill in the fast model's endpoint, name and key here; leaving them empty is not an error, the toggle just will not do anything. " +
    "The fast model has a much smaller context window, so Fast Mode compresses context automatically — that only affects the copy sent to it, your session is never folded.",
  setFastBaseUrlDesc: "Chat Completions compatible endpoint for the fast model, no trailing slash.",
  setFastModelName: "Fast model name",
  setFastModelDesc: "The model string on the fast endpoint.",
  setFastKeyName: "Fast model API key",
  setFastKeyDesc:
    "Write-only like the one above: stored in the local sidecar, never in this vault. The fast model usually lives on a different host, so it needs its own key — the base key is never reused for it.",
  setFastKeyStatusNone: "No fast model key saved yet.",
  noticeFastKeySaved: "Fast model key saved to the local sidecar, not to this vault.",
  noticeFastKeyCleared: "Fast model key cleared.",
  setThinkingDesc:
    "off is the lowest; high is the top tier for that endpoint. Keep off if the model doesn't reason.",
  setThinkingOff: "off (default)",
  deepQuotaSpent: "deep dives used up",
  setDeepName: "Dig deeper in the background",
  setDeepDesc: "After each answer, spend one more call looking for a question that reaches across chapters. It only appears if one turns up. Turn this off to keep just the two instant ones.",
  tipDeepPrefix: "\u25c6 ",
  // ── 自定义泡泡（v0.21.0）──
  setSecChips: "Your own buttons",
  setChipsDesc:
    "Every button in that row on the side panel is a piece of instruction sent to the AI. " +
    "You can add your own: say what you want done and in what format, then click it. " +
    "Anything marked as rewriting the note always opens the approval panel first — " +
    "the note only changes once you allow it.",
  setChipsEmpty: "No buttons of your own yet. Start from one of the templates below and rework it to match your book.",
  setChipsFull: (max: number): string => `${max} is the limit. More than that just adds a scrollbar to the side panel.`,
  setChipNewFrom: "New from template",
  setChipNewFromDesc: "Copy a starting template, then rework it to match how your own book is laid out.",
  setChipPresetBlank: "Blank",
  setChipNewBtn: "Add",
  setChipLabelName: "Button text",
  setChipLabelDesc: "Leave empty to use the first line of the instruction below. This name is never translated — both interface languages show exactly what you typed.",
  setChipHintName: "Tooltip",
  setChipHintDesc: "Optional. Shown when you hover the button, to remind you what it does.",
  setChipPromptName: "Instruction",
  setChipPromptDesc:
    "What gets sent to the AI when you click this button. Spell out which section to write into " +
    "and what format to follow — the more specific, the more closely it follows. " +
    "This one is required: an empty button is no different from just asking freely.",
  setChipPromptPlaceholder: "Take the passage I selected and ...",
  setChipWritebackName: "Rewrites the note",
  setChipWritebackDesc:
    "On: tells the AI this turn edits the note, so it reads the passage first, proposes a change, " +
    "and only writes once you approve. Off: it answers in the conversation only. " +
    "Note this switch sets expectations for the AI, it is not a lock on the file — " +
    "what actually stops a write is the approval panel, which is always there.",
  setChipEnabledName: "Show in the side panel",
  setChipEnabledDesc: "Turn off to tuck it away without deleting it. Half-finished ones can wait here.",
  setChipDraftNote: "No instruction yet, so it stays out of the side panel. It appears once you write one.",
  setChipDelete: "Delete this button",
  setChipDeleteBtn: "Delete",
  setChipDeleteConfirm: "Click again to confirm",
  setChipUnnamed: "(unnamed)",
  setChipChars: (n2: number, max: number): string => `${n2} / ${max} characters`,
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
  setDefaultHint: (v) => ` Default ${v}.`,
  limitName: (k) => LIMIT_TEXT_EN[k]?.[0] ?? k,
  limitDesc: (k) => LIMIT_TEXT_EN[k]?.[1] ?? "",

  // ── v0.25.0 learner profile panel (ReportView) ──
  viewTitleReport: "Learner profile",
  cmdOpenReport: "Open learner profile",
  tipReport: "Learner profile: where you are strong and where you get stuck in this book",
  reportLoading: "Loading the profile…",
  reportNoFile: "Open a Markdown note to see its learner profile. The books in this vault are listed below.",
  reportNotRegistered: "This note has not been asked about in Socrates yet. Select a passage in the panel and ask first; the profile needs material.",
  reportNoTurns: "No conversation on this book yet.",
  btnAnalyze: "Analyze this book",
  btnResume: "Resume analysis",
  btnRecompute: "Recompute",
  btnRecomputeSure: "Recompute from scratch? Billed per turn",
  btnCancel: "Cancel",
  btnStop: "Stop",
  reportUnrated: "unrated",
  reportNoMastery: "—",
  reportColAxis: "Skill",
  reportColScore: "Score",
  reportColMastery: "Mastery",
  reportColN: "Evidence",
  reportWhyTitle: "How this score was built",
  reportGapsTitle: "Self-declared blind spots",
  reportEvidenceTitle: "Evidence",
  reportVaultTitle: "Books in this vault",
  reportVaultEmpty: "No registered books in this vault yet.",
  reportVaultDown: "The shelf is unavailable: the sidecar did not answer.",
  reportColBook: "Book",
  reportColTurns: "Turns",
  reportColAxes: "Axes",
  reportColWeakest: "Weakest",
  reportColAskedMost: "Asked most",
  reportNoKeyHint: "No model key yet, so only the existing profile is shown. Add an API key under Settings → Socrates, then come back to analyze.",
  reportNotAnalyzed: (turns) =>
    `This book has ${n(turns)} turns and has not been analyzed. Analysis sends every turn to the main model for coding and is billed per turn.`,
  reportProgress: (coded, total, tokens) =>
    `Coded ${n(coded)} / ${n(total)} turns · ${k(tokens)} tokens this run`,
  reportTurns: (total, coded, meta) =>
    `${n(total)} turns · ${n(coded)} coded · ${n(meta)} of them are bookkeeping`,
  reportDegraded: (remaining) =>
    `${n(remaining)} turns are still uncoded. Add a model key and reopen this page to fill them in.`,
  reportLegacy: (count) =>
    `${n(count)} turns predate v0.24.0: they count toward frequencies only, never toward scores.`,
  reportGivenUp: (count) =>
    `${n(count)} turns got no valid coding after three attempts and were given up.`,
  reportTurnRef: (idx) => `turn ${idx}`,
  reportMerged: (count) => `${count} registrations merged`,
  reportRadarLabel: (count) => `Radar chart of ${count} skill axes`,
  reportMastery: (pct, obs) => `Mastery probability ${pct}, from ${obs} judged observations`,
  reportScoreTip: (score) => `Rule score ${score} / 10`,
  reportEvidenceCount: (count, legacy) =>
    legacy > 0 ? `${count} turns (${legacy} old)` : `${count} turns`,
  reportCodedAt: (stamp) => `Last coded ${stamp}`,
  reportSpend: (tokens) => `Profile total ${k(tokens)} tokens`,
  reportAxisScore: (name, score) => `${name} ${score}`,
  reportAxisN: (name, count) => `${name} ×${count}`,
  reportType: (typ) => TYPE_EN[typ] ?? typ,
  reportVerify: (outcome) => VERIFY_EN[outcome] ?? "",
  reportRejectRight: "and was right",
  reportRejectWrong: "and was wrong",
  errReportFailed: (detail) => `Could not load the profile: ${detail}`,
  errReportUnreachable: (detail) => `Cannot reach the sidecar, so no profile: ${detail}`,
  errReportStalled: "Coding stopped making progress: the sidecar coded no new turns three times in a row. Reopen this page to retry.",

  setSidecarDesc:
    "Where the local service listens. You rarely need to touch this. The plugin only spawns on loopback.",
};
