"""sidecar 的用户可见文案中英表。

只收**会显示到插件界面上的**文案：HTTPException 的 detail、SSE 的 error.message。
给模型看的人设在 `session.system_prompt`，按同一份 Accept-Language 选中文或英文模板。

语言从请求的 Accept-Language 头来——比给每个 Pydantic 模型加 lang 字段省事，
而且 GET 路由（没有 body）也能覆盖。
"""

from __future__ import annotations

DEFAULT_LANG = "zh"


def norm_lang(raw: str | None) -> str:
    """Accept-Language -> zh | en。认不出来一律回落中文。"""
    if not raw:
        return DEFAULT_LANG
    head = raw.split(",")[0].strip().lower()
    return "zh" if head.startswith("zh") else "en"


MESSAGES: dict[str, dict[str, str]] = {
    # ── 供应商调用失败。用户最常撞到的四条 ──
    "provider.bad_key": {
        "zh": "节点不收这把钥匙。请到设置 → Socrates 检查 API Key。",
        "en": "The endpoint rejected this key. Check your API key under Settings → Socrates.",
    },
    # 分不出类的 400。**转述节点自己那句话**，别再猜一遍——三样都核对过的
    # 读者，看到「请核对 Base URL、model 和 API Key」等于什么也没得到。
    "provider.rejected": {
        "zh": "节点拒绝了这次请求：{detail}",
        "en": "The endpoint rejected this request: {detail}",
    },
    "provider.bad_vision": {
        "zh": "节点拒收了图片。这个模型多半没有视觉，把设置里的「图像理解」关掉，或换一个多模态模型。",
        "en": "The endpoint rejected the image. This model likely has no vision — turn off Image understanding in settings, or switch to a multimodal model.",
    },
    "vision.disabled": {
        "zh": "这个模型没开图像理解。到设置 → Socrates 打开「图像理解」。若节点没有视觉，开了也会被拒。",
        "en": "Image understanding is off for this model. Turn it on under Settings → Socrates. If the endpoint has no vision, it will still reject the image.",
    },
    "vision.too_big": {
        "zh": "图片太大（每张最多 2MB，最多 4 张）。",
        "en": "Image too large (2MB each, up to 4 images).",
    },
    "vision.too_many": {
        "zh": "一次最多贴 4 张图。",
        "en": "At most 4 images per turn.",
    },
    "vision.bad_type": {
        "zh": "只收 png / jpeg / webp / gif。",
        "en": "Only png / jpeg / webp / gif.",
    },
    "provider.no_model": {
        "zh": "这个节点上没有 {model} 这个模型（404）。核对设置里的 model 字符串，"
        "或换一个这个节点真有的型号。",
        "en": "This endpoint has no model called {model} (404). Check the model name in "
        "settings, or pick one this endpoint actually serves.",
    },
    "provider.unreachable": {
        "zh": "连不上节点。检查设置里的 Base URL 有没有填对。",
        "en": "Can't reach the endpoint. Check the Base URL in settings.",
    },
    # ── 体检的减配梯子才给得出的四条。占位符只能用 {kind}/{model}/{detail}
    # （app._slot_report 那句是写死的三个参数，用别的名字就是一个 500）。
    "provider.no_tools": {
        "zh": "这个节点不支持工具调用，而读手册、改原文、取网页都要靠它。"
        "换一个支持 function calling 的型号。节点原话：{detail}",
        "en": "This endpoint does not support tool calling, which is how Socrates "
        "reads your handbook, edits it, and fetches pages. Pick a model that "
        "supports function calling. The endpoint said: {detail}",
    },
    "provider.no_stream": {
        "zh": "这个节点不支持流式输出，回答没法边写边显示。换一个支持流式的型号。"
        "节点原话：{detail}",
        "en": "This endpoint does not support streaming, so answers can't appear as "
        "they are written. Pick a model that streams. The endpoint said: {detail}",
    },
    "provider.no_usage": {
        "zh": "这个节点不认 stream_options，花销记不了账。换一个型号，"
        "或换一个兼容度更高的节点。节点原话：{detail}",
        "en": "This endpoint rejects stream_options, so token usage can't be "
        "recorded. Pick another model or a more compatible endpoint. "
        "The endpoint said: {detail}",
    },
    "provider.no_thinking": {
        "zh": "这个节点不认我们发的推理档写法。到设置里把「模型厂商」选成"
        "对应的那一家；不确定是哪一家就选「通用 OpenAI 兼容」。节点原话：{detail}",
        "en": "This endpoint does not accept the reasoning-effort dialect we sent. "
        "Pick the matching provider in settings, or choose the generic "
        "OpenAI-compatible option if you are not sure which one it is. "
        "The endpoint said: {detail}",
    },
    # 上下文太长。**措辞中性**：这句话体检、画像编码、折叠块生成都会用，
    # 只有主对话那条路真的退过批、折过历史，不能替别的路径宣称做过什么（二审 #7）。
    # 占位符只能用 {kind}/{model}/{detail}（app._slot_report 写死的三个名）。
    "provider.too_long": {
        "zh": "上下文超过了 {model} 的窗口（节点原话：{detail}）。让发出去的东西少一点："
        "主对话里分段 read_file、折叠一次历史或把设置里的自动折叠阈值调低；"
        "或换一个窗口更大的模型。",
        "en": "The context exceeds {model}'s window (the endpoint said: {detail}). Send less: "
        "in the chat, read_file in slices, fold the history once or lower the auto-fold "
        "threshold in settings; or switch to a model with a larger window.",
    },
    "provider.unexpected": {
        "zh": "节点返回了意料外的错误（{kind}）。稍后再试，或检查设置里的配置。",
        "en": "The endpoint returned an unexpected error ({kind}). Try again later, or check your settings.",
    },
    # ── 缺配置 ──
    "llm.missing_config": {
        "zh": (
            "找不到模型配置。请到 Obsidian 设置 → Socrates 填写 API Key，"
            "或给本机 sidecar 留一份开发用 .env（DEEPSEEK_API_KEY / OPENAI_API_KEY）。"
        ),
        "en": (
            "No model configured. Add an API key under Settings → Socrates, "
            "or leave a dev .env next to the sidecar (DEEPSEEK_API_KEY / OPENAI_API_KEY)."
        ),
    },
    "llm.missing_config_short": {
        "zh": "找不到模型配置。请到设置 → Socrates 填写 API Key。",
        "en": "No model configured. Add an API key under Settings → Socrates.",
    },
    # 体检给出的**判词**。设置页失焦时那条 Notice 是另一句（它填得出两个
    # 主机名），两句话服务两个时刻；判定本身只有 config.fast_llm_status 一份。
    "llm.host_mismatch": {
        "zh": "本机存的那把钥匙是给另一个站点的，不会发到现在这个节点上。请在当前 Base URL 上重新存一次 API Key。",
        "en": "The key stored on this machine belongs to a different endpoint and will not be sent to this one. Save the API key again for the Base URL you have now.",
    },
    "llm.empty_key": {
        "zh": "钥匙是空的。粘贴一份再保存。",
        "en": "The key is empty. Paste one before saving.",
    },
    "llm.missing_config_fold": {
        "zh": "找不到模型配置，无法生成折叠块。请到设置 → Socrates 填写 API Key。",
        "en": "No model configured, so the fold block can't be generated. Add an API key under Settings → Socrates.",
    },
    "llm.empty_reply": {
        "zh": "模型 {model} 返回了空正文（已接上 {base_url}）。",
        "en": "Model {model} returned an empty body (connected to {base_url}).",
    },
    # ── 会话 / 手册 ──
    "handbook.unknown": {"zh": "未知手册 {handbook_id}", "en": "Unknown manual {handbook_id}"},
    "session.unknown": {"zh": "未知会话", "en": "Unknown session"},
    "session.busy": {
        "zh": "这场对话还在跑，先等它结束。",
        "en": "This conversation is still running — let it finish first.",
    },
    # ── 人批 ──
    "approval.pending": {
        "zh": "有一次编辑在等你审批，先点允许或拒绝。",
        "en": "An edit is waiting for your approval — allow or reject it first.",
    },
    "approval.none": {
        "zh": "没有待审批的编辑，或已经过期",
        "en": "No edit is pending approval, or it has expired",
    },
    "approval.expired": {
        "zh": "没有待审批的编辑，或已经过期。",
        "en": "No edit is pending approval, or it has expired.",
    },
    "approval.path_changed": {
        "zh": "错误：手册路径已经变了，这次编辑作废。请重新框选。",
        "en": "Error: the manual's path changed, so this edit is void. Pick the passage again.",
    },
    # ── 写回 ──
    "writeback.no_answer": {"zh": "还没有可写回的解答", "en": "No answer to write back yet"},
    "writeback.no_anchor": {"zh": "缺少框选锚点", "en": "Missing the selection anchor"},
    "writeback.stale": {
        "zh": "笔记在选定位置之后改过了，请再选一次写入位置",
        "en": "The note changed after that spot was chosen. Pick the insert position again.",
    },
    # ── 上游模块抛出、经 str(exc) 冒到 detail 的热路径几条 ──
    "handbook.file_missing": {
        "zh": "原文找不到了：{path}。笔记可能被改名或移走了，请重新框选一次。",
        "en": "The source note is gone: {path}. It was probably renamed or moved — pick the passage again.",
    },
    "handbook.not_found": {
        "zh": "找不到教材：{path}",
        "en": "Manual not found: {path}",
    },
    "handbook.bad_id": {"zh": "非法 handbook_id：{got}", "en": "Invalid handbook_id: {got}"},
    "sandbox.not_markdown": {
        "zh": "只接受 Markdown 教材（.md / .markdown）：{got}",
        "en": "Only Markdown manuals are accepted (.md / .markdown): {got}",
    },
    "sandbox.outside_roots": {
        "zh": "教材不在允许的根内：{got}",
        "en": "That manual is outside the allowed roots: {got}",
    },
    "sandbox.protected": {"zh": "拒绝受保护路径：{got}", "en": "Refused a protected path: {got}"},
    "sandbox.vault_root_is_fs_root": {
        "zh": "vault_root 不能是文件系统根",
        "en": "vault_root can't be the filesystem root",
    },
    "sandbox.vault_root_not_dir": {
        "zh": "vault_root 不是目录：{got}",
        "en": "vault_root is not a directory: {got}",
    },
    # ── 学习画像。why.* 是「这一分怎么来的」，参数只有数字和轮号，绝不带读者原话 ──
    "profile.vault_root_required": {
        "zh": "缺 vault_root：画像的书架按库根找书。",
        "en": "vault_root is required: the shelf lists the books registered from this vault.",
    },
    "profile.why.unrated": {
        "zh": "证据只有 {n} 轮，不足 3 轮，未评",
        "en": "Only {n} turns of evidence, fewer than 3: unrated",
    },
    "profile.why.base": {"zh": "起评 {base}", "en": "Starts at {base}"},
    "profile.why.arc_closed": {
        "zh": "追问弧线 第{start}→{end}轮（{turns} 轮，{asking} 轮在问，有收口）：−{p}",
        "en": "Follow-up arc, turns {start}→{end} ({turns} turns, {asking} asking, closed): −{p}",
    },
    "profile.why.arc_open": {
        "zh": "追问弧线 第{start}→{end}轮（{turns} 轮，{asking} 轮在问，未收口）：−{p}",
        "en": "Follow-up arc, turns {start}→{end} ({turns} turns, {asking} asking, never closed): −{p}",
    },
    "profile.why.lapse": {
        "zh": "第{a}轮说懂了，第{b}轮又回来（{min} 分钟后）：−{p}",
        "en": "Declared understood at turn {a}, came back at turn {b} ({min} min later): −{p}",
    },
    "profile.why.lapse_cancelled": {
        "zh": "第{a}轮说懂了、第{b}轮回来，但第{c}轮求证被确认——算核对，不算遗忘",
        "en": "Declared at turn {a}, came back at turn {b}, but turn {c} was confirmed — a check, not a lapse",
    },
    "profile.why.gap": {"zh": "自陈盲区 ×{n}：−{p}", "en": "Self-declared blind spot ×{n}: −{p}"},
    "profile.why.corrected": {"zh": "求证被纠正 ×{n}：−{p}", "en": "Self-check corrected ×{n}: −{p}"},
    "profile.why.confirmed": {"zh": "求证被确认 ×{n}：+{p}", "en": "Self-check confirmed ×{n}: +{p}"},
    "profile.why.reject": {"zh": "顶回导师且对 ×{n}：+{p}", "en": "Pushed back and was right ×{n}: +{p}"},
    "profile.why.adopted": {
        "zh": "自己提出的模型被导师采纳：+{p}",
        "en": "Own model adopted by the tutor: +{p}",
    },
    "profile.why.hold": {
        "zh": "说懂了之后没再回来 ×{n}：+{p}",
        "en": "Declared understood and never came back ×{n}: +{p}",
    },
    "profile.why.arc_cap": {
        "zh": "追问弧线合计 −{raw}，封顶 −{cap}",
        "en": "Arcs total −{raw}, capped at −{cap}",
    },
    "profile.store_failed": {
        "zh": "画像缓存写不进去：{path}。看看目录权限和磁盘空间。",
        "en": "Could not write the profile cache: {path}. Check the directory permissions and free disk space.",
    },
    "profile.why.cap": {
        "zh": "证据只有 {n} 轮，不足 6 轮，封顶 {cap}",
        "en": "Only {n} turns of evidence, fewer than 6: capped at {cap}",
    },
    "index.line_out_of_range": {
        "zh": "行号越界：{line}（全书 {n_lines} 行）",
        "en": "Line out of range: {line} (the manual has {n_lines} lines)",
    },
    "snapshot.none_to_undo": {
        "zh": "没有可回退的快照：{handbook_id}",
        "en": "No snapshot to roll back to: {handbook_id}",
    },
    "snapshot.none_to_redo": {
        "zh": "没有可重做的快照：{handbook_id}",
        "en": "No snapshot to redo: {handbook_id}",
    },
    "proposal.unknown": {
        "zh": "提议不存在或已过期",
        "en": "That proposal doesn't exist or has expired",
    },
    "proposal.wrong_session": {
        "zh": "提议不属于这个会话",
        "en": "That proposal belongs to a different session",
    },
    # ── 循环 / 兜底 ──
    "loop.exhausted": {
        "zh": "翻了几页还没收工。请把问题问得更具体一点，或再点一次芯片。",
        "en": "Flipped through a few pages without landing it. Ask something more specific, or hit a chip again.",
    },
    "chat.unexpected": {
        "zh": "对话中途出了意外错误，请重试。",
        "en": "Something went wrong mid-conversation. Please retry.",
    },
    "approve.unexpected": {
        "zh": "审批后续出错，请重试。",
        "en": "Something went wrong after the approval. Please retry.",
    },
}


def localized(exc: BaseException, lang: str | None = DEFAULT_LANG) -> str:
    """把上游异常转成用户语言。

    异常带 `i18n_key` 就查表，否则回落 `str(exc)`（中文原文）。这样上游模块
    可以一条一条地补 key，没补的照旧工作，不需要一次性改完所有抛出点。
    """
    key = getattr(exc, "i18n_key", None)
    if not key:
        return str(exc)
    args = getattr(exc, "i18n_args", None) or {}
    return msg(str(key), lang, **args)


def msg(key: str, lang: str | None = DEFAULT_LANG, **kw: object) -> str:
    """查表成文。key 不认识时原样返回 key，方便开发期立刻看出漏登记。"""
    table = MESSAGES.get(key)
    if table is None:
        return key
    text = table.get(norm_lang(lang)) or table[DEFAULT_LANG]
    return text.format(**kw) if kw else text
