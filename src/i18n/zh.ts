/**
 * 简体中文词表——唯一允许写用户可见中文字面量的地方。
 *
 * 两条规约：
 *  1. **不要加 `as const`**。加了之后 `typeof zh` 全是字面量类型，
 *     将来的 `const en: Dict = {...}` 就永远无法满足（英文串 ≠ 中文字面量）。
 *  2. 静态文案写裸字符串，带参数的写箭头函数。函数式让 TS 原生检查参数的
 *     个数 / 顺序 / 类型，且中英文可以各自决定语序。
 *
 * `phases` 和 `chips` 的键是**后端下发的稳定 id**，不是随手起的名字：
 *   - `phases` ← pen/tutor.py 的 `{"type":"status","phase":...}`
 *   - `chips`  ← pen/session.py 的 `FIXED_CHIPS[].id`
 * 查表命中就本地化，没命中就照抄后端下发的文案（见 i18n/index.ts）。
 */

const NF = new Intl.NumberFormat("zh-CN");
const n = (v: number | undefined): string =>
  typeof v === "number" ? NF.format(v) : "?";

/**
 * 紧凑计数：12400 → 12.4k。状态行是 10px 等宽，窄侧栏 300px 下三格加上
 * 深挖提示用 NumberFormat 的「12,400」必然溢出。
 */
const k = (v: number | undefined): string => {
  if (typeof v !== "number" || !Number.isFinite(v)) return "?";
  const a = Math.abs(v);
  if (a < 1000) return String(Math.round(v));
  // 十万以上才丢小数：12.4k 有信息量，而 128.3k 在窄栏里太长。
  return `${(v / 1000).toFixed(a >= 100000 ? 0 : 1)}k`;
};


/**
 * 每个旋钮的「名字 / 一句人话说明」。和 settings.ts 的 LIMIT_SPEC 键一一对应，
 * 由 scripts/check-limits.mjs 守着不许缺。
 *
 * 三句必须诚实的话都在这儿：主对话那个不是硬上限、深挖存多了也只放 1 条、
 * 超时调大会让当轮看不见结果。
 */
const LIMIT_TEXT_ZH: Record<string, [string, string]> = {
  probe_max_per_window: ["每小时最多深挖几次", "跨会话累计。开一堆新会话也绕不过它。"],
  probe_every_n_rounds: [
    "两次深挖至少隔几轮",
    "填 0 就是每一轮实质回复都探（也是现在的行为）。",
  ],
  probe_keep_per_run: [
    "每次深挖最多存几条",
    "注意：每轮仍然只放出 1 条，多存的排队等下一轮，攒过 6 轮就丢。所以调大主要是在增大丢弃堆。",
  ],
  max_tokens_chat: [
    "每轮对话的 token 上限",
    "0 = 不限。这是工具循环的预算：到线之后苏格拉底还会用手上的材料出一次答案，那一枪不受限。所以实际花销会超出你填的数——超多少取决于这一轮已经翻了多少东西，不是一个固定比例。",
  ],
  max_tokens_probe: ["单次后台深挖的 token 上限", "0 = 不限。填得比一次深挖的开销还小，就一次都不探。"],
  max_tokens_cross_book: [
    "翻别的教材的 token 上限",
    "0 = 不限。它管的是「这一轮已经烧到多少就别再开新书」——别的书读进来之后每轮都要重发，代价是复利。",
  ],
  compact_chat_tokens: [
    "主对话自动折进摘要的窗口",
    "0 = 不自动折（命令面板仍可手动折）。到了就在下一轮开场把旧回合收成带行号的摘要，侧栏旧气泡还在。这不是花钱硬上限，也不是丢掉旧回合。",
  ],
  max_tool_rounds: ["苏格拉底一轮最多翻几次书", "翻本册不额外收费，跨教材另有下面两道闸。"],
  fast_context_tokens: [
    "Fast Mode 给快模型的上下文上限",
    "只在 Fast Mode 开着时生效。快模型的窗口是输入加输出合计 131072，扣掉输出后输入最多 114688；超了会被节点当场拒掉，所以这里默认留出余量。压不下去时这一轮自动退回基座模型，对话不会断。0 = 不压（不建议）。",
  ],
  cross_book_chars: ["一轮里翻别的教材的字符预算", "读当前这本不计，本职阅读一次都不受影响。"],
  cross_book_reads: ["一轮里翻别的教材的次数上限", "光封字节封不住：模型每次只读一行时，字节预算永远用不完。"],
  probe_max_per_session: ["每场对话最多深挖几次", "挡不住「开一堆新会话」，那靠上面的每小时配额。"],
  probe_pending_cap: ["手里攒够几条没抛就先停探", "省的不是频率，是浪费。"],
  probe_max_reads: ["一次深挖最多定向读几段正文", "读取由代码执行，不给模型自主循环的机会。"],
  probe_read_lines: ["每段最多读多少行", ""],
  probe_timeout_s: [
    "深挖单次调用超时（秒）",
    "调大之后深挖可能跑到你当轮看不见结果；题不会丢，会在下一轮搭便车抛出来。",
  ],
  probe_min_reply_chars: ["回复至少多少字才值得深挖", "太短的多半是一句反问，从那儿挖不出好问题。"],
  probe_concurrency: [
    "同时最多几个深挖在跑",
    "这是整台 sidecar 的数，不是每本书的。同时开着多个库时方向是反的：判据是「全局在飞数 < 你填的数」，所以把它调小反而更容易被另一个库的深挖占满位子。想省钱请调上面的每小时次数或 token 上限。",
  ],
};

export const zh = {
  // ── 品牌 / 视图元数据 ──
  appName: "苏格拉底",
  viewTitle: "苏格拉底",

  // ── 命令 / ribbon（Obsidian 会自动加「Socrates: 」前缀，这里别再写一遍）──
  ribbonTooltip: "打开苏格拉底",
  cmdAskSelection: "用当前选区提问",
  cmdOpenPanel: "打开面板",
  cmdCompactSession: "把这场对话折进摘要",

  // ── 底座按钮 ──
  btnUseSelection: "用当前选区",
  btnAsk: "问",
  askPlaceholder: "自己问一句…",
  askPlaceholderVision: "自己问一句，或粘贴图片…",
  tipUseSelection: "在笔记里划一段，再点这里交给苏格拉底",

  // ── 品牌条工具按钮 ──
  tipNewSession: "另起一场，丢掉这场的模型记忆和选区",
  tipCompact: "把更早的回合折进摘要。侧栏旧气泡还在，下次请求不再带全文。",
  compactMarker: "上面几轮已经折进摘要",
  kickerCompact: "摘要",
  tipFastOff: "Fast Mode 关着。开了之后，只问不改的那些轮次交给快模型答。",
  tipFastOn: "Fast Mode 开着：只读的轮次走快模型。要改原文时自动换回基座模型。",
  tipFastTrimmed: (steps: string): string =>
    `Fast Mode 开着。这一轮上下文超了快模型的窗口，已经压过：${steps}。会话本身没有被折。`,
  kickerRoute: "换模型",
  noteRouteEdit: "这一轮要动原文，已经换回基座模型重答。上面那半句是快模型说的，不算数。",
  noteRouteTooBig: "这一轮的上下文压不进快模型的窗口，已经换回基座模型重答。",
  noteRouteNoKey: "Fast Mode 开着，但快模型还没有 API Key，这一轮走的是基座模型。到设置里补上就生效。",
  noteRouteHostGap: "Fast Mode 开着，但本机存的那把快模型钥匙是给另一个站点的，这一轮走的是基座模型。到设置里，在现在这个 Fast Base URL 上重新存一次钥匙就好。",
  noticeFastNoKey: "Fast Mode 已打开，但还没填快模型的 API Key，所以暂时还是走基座模型。到设置里补上就生效。",
  noticeFastKeyHostMismatch: (keyHost: string, urlHost: string): string =>
    `快模型的站点换了。本机存的那把钥匙还是给 ${keyHost} 的，不会拿到 ${urlHost} 上用，所以每轮都会退回基座模型。请在 ${urlHost} 上重新存一次 API Key。`,
  noticeCompactEmpty: "还没有可折的对话",
  noticeCompactPending: "有一次编辑在等你审批，先点允许或拒绝，再折摘要。",
  noticeCompactBusy: "这场对话还在跑，先等它结束。",
  noticeCompactOk: "旧回合已经折进摘要。侧栏气泡还在。",
  tipUndoEmpty: "还没有可回退的版本。允许一次编辑后才会亮。",
  tipUndo: (count: number): string => `整篇笔记回到上一版（还能退 ${count} 次）`,
  tipRedoEmpty: "没有可重做的版本",
  tipRedo: (count: number): string => `把刚才撤销的写回回来（还能重做 ${count} 次）`,

  // ── 健康行 ──
  healthUnprobed: "sidecar 未探测",
  healthOkKey: (tail: string, model: string): string =>
    `sidecar 正常 · 钥匙已存本机${tail ? ` …${tail}` : ""} · ${model}`,
  healthOkFallback: (source: string, model: string): string =>
    `sidecar 正常 · 开发回退 ${source} · ${model}`,
  healthNoKey: "sidecar 在，请到设置 → Socrates 填写 API Key",
  healthDown: "连不上 sidecar",
  healthStale: "旧服务占着端口，先到设置里停止再启动升级",

  // ── 错误气泡（v0.18.0：请求失败不再伪装成流式） ──
  errNoKey: "还没配模型钥匙。到设置 → Socrates 填 API Key 后再问。",
  errNoVision:
    "这个模型没开图像理解。到设置 → Socrates 打开「图像理解」。若节点没有视觉，开了也会被拒。",
  errVisionTooBig: "图片太大（每张最多 2MB，最多 4 张）。",
  errVisionTooMany: "一次最多贴 4 张图。",
  errVisionBadType: "只收 png / jpeg / webp / gif。",
  bubbleGoSettings: "去设置",

  // ── 错误 ──
  errUnreachable: (detail: string): string =>
    `连不上 sidecar（CORS / 没启动 / 端口不对）：${detail}`,
  errNoSelection: "没读到选区。在笔记里划一段再点「用当前选区」。",
  // v0.12.4：会话按时间清理之后，读者手里那个 sid 可能已经不在了。
  // 不给这两句的话，读者的失败模式是「输入框能打字，一发就报错，
  // 不知道该干什么」——再点还是同一个死 sid。
  // 下面三条都不写死原因。「过了保留期」只是最常见的一种——读者手删过
  // `.pen/`、换了 sidecar、盘满写不进去，走的都是同一条 404。断言一个自己
  // 并不知道的因由，读者照着它去排查就白费一趟。
  noticeSessionArchived: "这场对话在 sidecar 上已经找不到了（多半是过了保留期被清理）。已经给你开了新的一场，刚才那句已经重新问出去了。",
  // 换会话是**不可逆**的（历史从面板上消失、笔记已重绑到新 sid）。重发失败
  // 也必须通报——不然读者只看到一条原始报错，历史没了却一个字的解释都没有。
  noticeSessionArchivedResendFailed: "这场对话在 sidecar 上已经找不到了（多半是过了保留期被清理）。已经给你开了新的一场，但刚才那句没能重新问出去——原因在下面那条错误里。",
  noticeSessionRenewed: "这篇笔记上次那场对话在 sidecar 上已经找不到了（多半是过了保留期被清理）。已经给你开了新的一场。",
  bubbleSessionRenewed:
    "这场对话的上一次记录已被清理（过保留期），这里是新开的一场。原笔记上的旧对话若还在保留期内，重新划一段即可找回。",
  noticeNoteRestored: (note: string): string => `已切回《${note}》的对话。要接着问，先在笔记里划一段。`,
  errSessionGone: (note: string): string =>
    `《${note}》上次那场对话已被清理，需要重新划一段开新的一场。`,
  // 括号里原本是「（sidecar 连不上？）」——又一句在猜因由。现在 reviveSession
  // 会把服务端那句真话留在 this.err 里，这条只在**确实不知道**为什么时才出现。
  errSessionArchivedHard: "这场对话在 sidecar 上已经找不到了，而且新会话也没开起来。",
  errApprovalArchived: "这次编辑所在的会话在 sidecar 上已经找不到了。原文没有被改动。已经给你开了新的一场，重新问一次就行。",
  // 审批那条路上最要紧的一句是「原文没有被改动」，`errSessionArchivedHard`
  // 里没有它。读者刚点完「同意写回」就看到会话找不到，此刻他唯一想知道的
  // 就是笔记被改了没有——不能因为新会话也没开起来就把这句省掉。
  errApprovalArchivedHard: "这次编辑所在的会话在 sidecar 上已经找不到了，新会话也没开起来。原文没有被改动。",
  // 追在服务端那句准确因由**后面**的一句。审批路上「原文没有被改动」不能
  // 因为让位给准确因由就整句消失——读者刚点完「同意写回」。
  // 不要写「原文没有被改动」：这句多半追在服务端那句「**原文**找不到了」
  // 后面，同一个词指同一个东西、两句表面上互相打架（找不到的东西怎么会
  // 没被改动），而这句存在的意义恰恰是让读者一眼放心。改说「你的笔记」。
  errApprovalUntouched: "（这次写回没有执行，你的笔记没有被改动。）",

  // ── 用量与花销 ──
  // 前两格是**此刻窗口占用**（诊断用），第三格才是**累计花销**。
  // 两个口径不一样，别合并。
  usage: (ctx: number | undefined, out: number | undefined): string =>
    `上下文 ${k(ctx)} · 回复 ${k(out)}`,
  spendTurn: (tok: number | undefined): string => `本轮 ${k(tok)}`,
  spendSession: (tok: number | undefined): string => `本会话 ${k(tok)}`,
  spendTipTotal: (tok: number | undefined): string => `本会话共 ${k(tok)} token`,
  spendTipRow: (label: string, inTok: number | undefined, outTok: number | undefined): string =>
    `${label}　输入 ${k(inTok)} / 输出 ${k(outTok)}`,
  spendTipCached: (tok: number | undefined): string => `其中缓存命中 ${k(tok)}（便宜很多）`,
  spendKindChat: "主对话",
  spendKindProbe: "深挖",
  spendKindFold: "写回",
  // 不折算成钱是拍板过的：第三方端点单价不可靠、缓存命中差十倍、
  // SDK 重试那次的用量根本拿不到。乘出来的钱是假精度。
  spendTipNote: "只数 token，不折算成钱",

  // ── 对话气泡 ──
  kickerYou: "你",
  kickerPen: "苏格拉底",
  kickerReadTool: "翻手册",
  kickerEditTool: "改原文",
  kickerFetchTool: "取网页",
  toolOk: "成功",
  toolDenied: "拒绝",
  noPath: "（无路径）",
  streamPlaceholder: "…",
  emptyHint: "在笔记里划一段（实时预览或阅读模式都行），再点「用当前选区」。",

  // ── 启动 Logo ──
  splashTagline: "苏格拉底学习法",
  splashSubline: "Socrates-agent",

  // ── 状态行。键对齐 pen/tutor.py 的 ev.phase ──
  phases: {
    thinking: "苏格拉底在想…",
    writing: "在写…",
    reading: "在翻手册…",
    tool: "在动手…",
    selection_capped: "划选太长，第一包只带了开头，其余按需再读",
  } as Record<string, string>,
  // 推理阶段的活体计数。读者看的不是这个数本身，是**它在跳**——
  // 实测正文要等 14.5 秒才开始，那段时间以前屏幕上什么都没有。
  thinkTick: (chars: number): string => `苏格拉底在想… ${k(chars)} 字`,
  statusEditing: "在改原文…",
  statusDeclined: "已拒绝，让苏格拉底收尾…",
  statusAwaitApproval: "等你批准这次编辑",
  statusRollingBack: "在回到上一版…",
  statusRedoing: "在重做…",

  // ── 审批面板 ──
  approvalTitle: "审批这次编辑",
  approvalTarget: (tool: string, path: string): string => `${tool} → ${path}`,
  approvalCurrentHandbook: "当前手册",
  approvalTruncated: (n: number): string => `… 还有 ${n} 个字符没显示`,
  approvalWarn: "模型自己选要换的那一小段。点允许才会改这篇笔记。",
  approvalOldLabel: "--- 原文 ---",
  approvalNewLabel: "+++ 换成 +++",
  btnApprove: "允许这次编辑",
  btnReject: "拒绝",

  // ── Notice ──
  noticeUnreachable: "连不上 sidecar，先看面板上的错误信息",
  noticeRegisterFirst: "先框选并登记当前笔记",
  // `reviveSession()` 顶上那条早退专用。**不要**图省事复用上面那句
  // `noticeRegisterFirst`：那是给 `new Notice()` 写的短祈使句，
  //   - 塞进 `this.err` 之后读者只被要求做个动作，「这场对话没了」整句缺席；
  //   - 它不以「。」收句，而 `errApprovalUntouched` 是按「前面已经收句」
  //     设计的追加物，拼出来那个括号会像在修饰「登记笔记」这个动作
  //     （英文那边更糟：整句从头到尾不终止）；
  //   - 键名和去处也对不上（v0.12.6 ⑤ 立的规矩）。
  // 所以这里要一条为这个位置写的完整句：既说发生了什么，也说该做什么。
  errSessionGoneNoHandbook:
    // 「还没有**已登记的**笔记」而不是「还没有登记**过**笔记」：后者是对
    // **历史**的断言，而代码知道的只有**此刻** `handbookId` 是空的。
    // 这条分支存在的全部理由恰恰是「将来有人加了『重开面板恢复上次会话』」
    // ——到那时读者明明框选过、只是 handbookId 没跟着恢复，他看到
    // 「还没登记过」的第一反应是「我明明登记过」。英文那边 `yet` 本来就
    // 只描述状态，不带历史断言。
    "这场对话在 sidecar 上已经找不到了，而面板上还没有已登记的笔记，没法给你开新的一场。请先框选一次。",
  noticeResolveApproval: "先批准或拒绝这次编辑",
  noticeUseSelectionFirst: "先点「用当前选区」",
  noticeRolledBack: "已回到上一版",
  noticeRedone: "已重做",

  // ── confirm ──
  confirmNewSession: "新开会话会丢掉当前这场的模型记忆和选区。确定？",
  confirmRollback: "整篇笔记将回到上一版；这之后你手改的也会没。确定？",

  // ── 写进对话流的系统消息 ──
  msgRolledBack: "已回到上一版。",
  msgRedone: "已重做刚才撤销的写入。",

  // ── main.ts ──
  errNoRightLeaf: "没有可用的右侧叶子",
  errViewNotMounted: "苏格拉底视图未挂上",
  errNeedDesktopVault: "需要桌面库（FileSystemAdapter）",
  noticeSidecarDown:
    "本机服务还没起来。打开设置 → Socrates，看最上面那一块的状态。",
  noticeKeySaved: "钥匙已存入本机 sidecar（~/.socrates-pen/llm.json），不进这个库。",
  noticeKeySaveFailed: (detail: string): string =>
    `没存进去（${detail}）。钥匙未落任何文件。`,
  noticeKeySaveOldSidecar:
    "没存进去：占着端口的是旧服务，收不了新钥匙。先点停止再点启动升级。钥匙还在输入框里，未落任何文件。",
  noticeKeyCleared: "钥匙已从本机 sidecar 清除。",
  noticeKeyMigrated:
    "API Key 已从 data.json 迁到本机 ~/.socrates-pen/llm.json，不再随 Sync / iCloud / git 走。若这个库曾被同步或提交过，建议去服务商轮换这把钥匙。",
  noticeSidecarTooOld:
    "本机服务还是旧版，收不了新钥匙。到设置 → Socrates 点停止再点启动升级——钥匙随后自动迁入，迁好之前仍留在 data.json 里。",
  noticeSidecarAlready: "本机服务已经在跑。",
  noticeSidecarStale: "旧服务占着端口，先点停止再点启动升级。",
  noticeSidecarAlreadyStopped: "本机服务本来就没在跑。",
  noticeSidecarStoppedOwned: "已停止本机服务。",
  noticeSidecarStoppedLeftover: (command: string): string =>
    command && command !== "?"
      ? `已停止占用端口的旧服务（${command}）。`
      : "已停止占用端口的旧服务。",
  noticeSidecarStoppedShared: (command: string): string =>
    command && command !== "?"
      ? `已停止占用端口的本机服务（${command}，别的库或上次留下的）。`
      : "已停止占用端口的本机服务（别的库或上次留下的）。",
  noticeSidecarStoppedOther: (command: string): string =>
    command && command !== "?"
      ? `已停止占用端口的其他进程（${command}）。`
      : "已停止占用端口的其他进程。",
  noticeSidecarStopFailed: "没停掉。看上面那一行状态。",
  noticeKeyMigrateTimeout:
    "45 秒内没把 API Key 迁进本机服务（它可能还在安装）。钥匙仍留在 data.json 里，本机服务就绪后会自动再迁一次；急着用就到设置里手动贴一次。",
  noticeKeyHostMismatch: (keyHost: string, urlHost: string): string =>
    `节点换了。本机那把钥匙还是给 ${keyHost} 的，不会拿到 ${urlHost} 上用。请贴一份 ${urlHost} 的 API Key。`,
  noticeSessionSwitched: (note: string): string =>
    `已切换到《${note}》的会话。原笔记的对话仍在原笔记上——再划它就能回来。`,
  noticeRightOpened: "Socrates 面板已在右侧栏打开。",

  // ── 芯片。键对齐后端 pen/session.py 的 FIXED_CHIPS[].id ──
  chips: {
    socratic: { label: "先别揭晓，问我一个问题", hint: "" },
    explain_zero: { label: "当我零基础，讲清楚再给两个例子", hint: "" },
    examples: { label: "只举例子", hint: "" },
    search: { label: "查相关论文 / 算法出处", hint: "P2 才开放，现在不会假装搜过" },
    writeback: { label: "把刚才的解答写进手册原文", hint: "先有一轮实质解答" },
  } as Record<string, { label: string; hint: string }>,

  // ── 设置页 ──
  setLangName: "语言 / Language",
  setLangDesc: "默认跟随 Obsidian 的界面语言。",
  setLangAuto: "自动（跟随 Obsidian）",
  setSidecarSvc: "本机服务",
  setSidecarSvcDesc:
    "插件会在你家目录的 ~/.socrates-pen 里建一个隔离的 Python 环境，装上脑子，并在本机 127.0.0.1 拉起。需要系统已安装 Python 3.11+。第一次可能要一两分钟（要下载依赖）。",
  setSidecarStart: "启动",
  setSidecarStop: "停止",
  setSidecarStopping: "正在停止…",
  setSidecarAutoName: "打开 Obsidian 时自动启动",
  setSidecarAutoDesc: "默认开。关掉就只在你点启动时才拉起。",
  setSidecarPythonName: "Python 路径",
  setSidecarPythonDesc: "留空则自动找 python3 / python / py。找不到时请先从 python.org 安装 3.11 或更高。",
  setSidecarPhaseIdle: "未运行",
  setSidecarPhaseChecking: "在检查…",
  setSidecarPhaseInstalling: "在安装本机服务（第一次会久一点）…",
  setSidecarPhaseStarting: "在启动…",
  setSidecarPhaseRunning: "运行中",
  setSidecarPhaseStopping: "正在停止…",
  setSidecarPhaseStopped: "已停止",
  setSidecarPhaseStale: (ver: string): string =>
    ver
      ? `旧服务占着端口（${ver}），先停止再启动升级`
      : "旧服务占着端口，先停止再启动升级",
  setSidecarErrNoPython:
    "PATH 上没找到 Python 3.11+，新建本机环境需要它。请先安装，或在下面填解释器的绝对路径（例如 /opt/homebrew/bin/python3）。",
  setSidecarErrNotLoopback: "插件只能在 127.0.0.1 / localhost 上拉起服务。Sidecar URL 改回 loopback。",
  setSidecarErrBadUrl: "Sidecar URL 不是合法地址。",
  setSidecarErrInstall: "安装失败。需要能访问 GitHub 和 PyPI。",
  setSidecarErrSpawn: "进程没能拉起来。",
  setSidecarErrHealth: "进程起来了，但健康检查一直没过。",
  setSidecarErrStop: "停止失败。",
  setSidecarErrStopNoPid: "停止失败：找不到占用端口的进程。",
  setSidecarErrOther: (code: string): string => `启动失败（${code}）`,
  setIntro1:
    "钥匙和节点填在下面。本机服务由插件自己拉起，不用开终端。当前库的路径会在框选时自动带给服务。",
  setIntro2:
    "API Key 只存本机 sidecar 家目录（~/.socrates-pen/llm.json，权限 0600），不写进这个库——Sync / iCloud / git 带不走它。",
  setApiKeyDesc:
    "只写：粘贴后点保存（回车或失焦也可），存入本机 sidecar，不落 vault。服务没就绪或还是旧版时保存是灰的。留空忽略；要清除用右侧按钮。",
  setKeySave: "保存",
  setKeyStatusSaved: (source: string, tail: string): string =>
    `已保存${source ? `（来源 ${source}）` : ""}${tail ? `，尾号 …${tail}` : ""}。`,
  setKeyStatusNone: "尚未保存钥匙。",
  setKeyStatusUnreachable: "sidecar 未运行，钥匙状态未知。",
  setKeyClear: "清除密钥",
  setKeepAliveName: "退出后保持本机服务运行",
  setKeepAliveDesc:
    "本机服务是你机器上的一个 Python 进程，多个库共用同一只。默认开启：退出 Obsidian 后它继续在，下次秒开。关掉则退出时停掉由本插件拉起的那只——别的库正在用时请别关。",
  setBaseUrlDesc: "Chat Completions 兼容地址，不要带尾斜杠。",
  setModelName: "模型名",
  setModelDesc: "节点上的 model 字符串，例如 deepseek-v4-flash 或 gpt-4.1-mini。",
  setVisionName: "图像理解",
  setVisionDesc:
    "打开后可在对话框粘贴或拖入图片。DeepSeek 文本模型请关；GLM / Qwen 等多模态再开。关着时贴图会直接报错，不会把图发出去。",
  setThinkingDesc: "off 最低，high 是该节点顶档。不支持思考的节点请保持 off。",
  // ── Fast Mode（v0.22.0）──
  setSecFast: "Fast Mode",
  setFastDesc:
    "只问不改的轮次交给一个更快的模型答，要动原文时自动换回上面那个基座模型。" +
    "开关在侧栏右上角那枚闪电。这里填快模型的节点、型号和钥匙；不填也不报错，只是开关点了不生效。" +
    "快模型的上下文窗口小得多，开着时会自动压缩上下文——压缩只影响发给它的那一份，你的会话不会被折。",
  setFastBaseUrlDesc: "快模型的 Chat Completions 兼容地址，不要带尾斜杠。",
  setFastModelName: "快模型名",
  setFastModelDesc: "快模型节点上的 model 字符串。",
  setFastKeyName: "快模型 API Key",
  setFastKeyDesc:
    "和上面那把一样只写不读，存进本机 sidecar，不落这个库。快模型通常在另一台主机上，所以必须单独填一把——基座那把不会被挪用过去。",
  setFastKeyStatusNone: "尚未保存快模型钥匙。",
  noticeFastKeySaved: "快模型钥匙已存入本机 sidecar，不进这个库。",
  noticeFastKeyCleared: "快模型钥匙已清除。",
  setThinkingOff: "off（默认）",
  deepQuotaSpent: "深挖已用满",
  setDeepName: "后台深挖",
  setDeepDesc: "苏格拉底答完之后，后台再花一次调用去想一个跨关的问题，想到了才冒出来。关掉就只留即时的那两条。",
  tipDeepPrefix: "◆ ",
  // ── 自定义泡泡（v0.21.0）──
  setSecChips: "自定义泡泡",
  setChipsDesc:
    "侧栏那排按钮，每一枚底下都是一段发给 AI 的指令。这里可以加你自己的：" +
    "写清楚你要它做什么、按什么体例写，点一下就照着做。" +
    "勾了「会改写原文」的那些，动笔前一律先弹审批面板，你点了允许笔记才会变。",
  setChipsEmpty: "还没有自定义泡泡。从下面的模板挑一个开始，改成你自己的体例。",
  setChipsFull: (max: number): string => `最多 ${max} 枚。再加就只是在给侧栏堆滚动条了。`,
  setChipNewFrom: "从模板新建",
  setChipNewFromDesc: "挑一个起手模板复制过来，再照你自己那本书的体例改。",
  setChipPresetBlank: "空白",
  setChipNewBtn: "新建",
  setChipLabelName: "按钮上的字",
  setChipLabelDesc: "留空就用下面指令的第一行。这个名字不翻译，两种界面语言下都显示你写的原文。",
  setChipHintName: "悬停提示",
  setChipHintDesc: "可留空。鼠标停在按钮上时显示，用来提醒你这枚是干什么的。",
  setChipPromptName: "指令",
  setChipPromptDesc:
    "点这枚泡泡时发给 AI 的话。把「要写进哪一节」「按什么格式」都写明白，" +
    "越具体它越照做。这段是必填的——空的泡泡和自由提问没有区别。",
  setChipPromptPlaceholder: "就我划中的这一段，……",
  setChipWritebackName: "会改写原文",
  setChipWritebackDesc:
    "开着：告诉 AI 这一轮要动笔记，它会先读原文再提改动，你审批后才落盘。" +
    "关着：只在对话里回答。注意这个开关管的是给 AI 的交代，不是磁盘的锁——" +
    "真正拦住写盘的是审批面板，它一直都在。",
  setChipEnabledName: "在侧栏显示",
  setChipEnabledDesc: "关掉就先收起来，不删。半成品可以停在这儿。",
  setChipDraftNote: "还没写指令，所以侧栏那排先不显示它。写完就出现。",
  setChipDelete: "删除这枚泡泡",
  setChipDeleteBtn: "删除",
  setChipDeleteConfirm: "再点一次确认删除",
  setChipUnnamed: "（还没起名）",
  setChipChars: (n2: number, max: number): string => `${n2} / ${max} 字`,
  // ── 设置页分区与旋钮（v0.10.7）──
  setSecUsage: "花销",
  setUsageLoading: "正在读账…",
  setUsageDown: "连不上 sidecar，读不到账。",
  // v0.12.4 起会话按时间清理，这个数是**现存会话**的合计，会往下掉——
  // 不写明的话读者会以为账丢了。
  setUsageNote:
    "从 v0.10.0 起算。状态行上那个数是「这一场」，这里是「一共」。只数 token，不折算成钱。只统计还在保留期内的会话（空会话留 1 天、聊过的留 7 天），所以这个数会随着旧会话被清理而下降。",
  setUsageTotal: (tok: number, sessions: number): string =>
    `一共 ${n(tok)} token，来自 ${n(sessions)} 场对话`,
  setUsageBreak: (chat: number, probe: number, fold: number): string =>
    `主对话 ${n(chat)} · 深挖 ${n(probe)} · 写回 ${n(fold)}`,
  setUsageCached: (tok: number): string => `其中缓存命中 ${n(tok)}（便宜很多）`,
  setUsageEmpty: "还没有记到账。这个数从升级到 v0.10.0 之后的对话开始算。",
  setSecCommon: "常用",
  setSecAdvanced: "高级（花钱和速度的闸，不确定就别动）",
  setAdvancedNote:
    "下面这些默认值是实测调出来的。改之前先想清楚要省的是什么——" +
    "多数时候该动的是上面那三个 token 上限，不是这里。",
  setDefaultHint: (v: number): string => ` 默认 ${v}。`,
  limitName: (k: string): string => LIMIT_TEXT_ZH[k]?.[0] ?? k,
  limitDesc: (k: string): string => LIMIT_TEXT_ZH[k]?.[1] ?? "",

  setSidecarDesc:
    "本机服务的地址。一般不用改。插件只会往 loopback 上拉起进程。",
};

export type Dict = typeof zh;
