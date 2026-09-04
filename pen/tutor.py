"""对话脑：L2 外环 + registry 工具。edit_file 须人批；无 bash / write_file。fetch 自动过。"""

from __future__ import annotations

import json
import re
import uuid
from types import SimpleNamespace
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from pen.i18n import msg
from pen import meter as metermod
from pen import providers
from pen.config import (
    FAST_WINDOW,
    OVERFLOW_RETRIES,
    LLMConfig,
    REPO_ROOT,
    RuntimeLimits,
    default_limits,
    resolve_llm,
)
from pen.chips import CustomChipSpec, chip_intent, custom_intent
from pen.compact import CompactPending, allow_paths_for, compact_session
from pen.compaction import est_tokens, fast_budget, strategy_for
from pen.overflow import overflow_numbers, stub_trailing_batch
from pen.index import HandbookIndex, neighborhood
from pen.agent.permissions import decide, read_first_block
from pen.agent.registry import dispatch, schemas
from pen.agent.tools_impl import limits_of
from pen.sandbox import resolve_read_target
from pen.questions import clean_candidates
from pen import reasoning as reasoningmod
from pen.vision import has_image_parts, strip_images
from pen.session import (
    PROMPT_EXAMPLE_LINES,
    FIXED_CHIPS,
    PenSession,
    apply_session_lang,
)

# MAX_TOOL_ROUNDS 搬到了 config.py，**这里不留别名**：留一个
# `MAX_TOOL_ROUNDS = config.MAX_TOOL_ROUNDS` 就是第二个定义点，
# 下次有人改这个而不是那个，两边就分家了。读取一律走 limits_of(ctx)。
# 全书目录注进 packet 的字符预算。实测那本 13083 行的手册全量 87 条只要 3647 字符。
TOC_CHARS = 4500
ASKED_CHARS = 700
# 书架：MAX_FILES=8 本，with_paths 是**两行一组**（书名+path / 大纲）。
# 1500 是按旧的单行格式估的，双行下 8 本长标题 + iCloud 那种深路径就会被
# 静默砍掉最后几本——读者的书架上凭空少两本书，还看不出为什么。
# 按 8 本 × 2 行 × 200 字符的最坏情况给足。
SHELF_CHARS = 3200
# 预算到线时的收口话术。**不能复用 FORCE_ANSWER**——那句说的是「工具次数
# 用完了」，而次数根本没用完，是钱到线了，对模型撒谎。措辞对齐跨书那道闸
# 的现成先例，让两道闸给模型的心智模型一致。
# 芯片块的起手记号。收分片时要拿它切，收尾时 parse_dynamic_chips 也认它。
CHIPS_MARKER = "<!--pen:chips"
FORCE_ANSWER_BUDGET = {
    "zh": (
        "这一轮的翻阅额度用完了。用邻域和你已经读到的内容直接回答读者，"
        "并且说清楚你只读了哪几段、还有哪些没看。不要再调用任何工具，也不要凭书名猜。"
    ),
    "en": (
        "The reading budget for this turn is used up. Answer the reader from the "
        "neighborhood and what you already read, and say which passages you saw and "
        "which you did not. Do not call any more tools, and do not guess from the title."
    ),
}
FORCE_ANSWER = {
    "zh": (
        "工具次数用完了。根据邻域和你已经读到的内容，直接用自然语言回答读者。"
        "不要再调用任何工具。"
    ),
    "en": (
        "The tool-call budget is used up. Answer the reader in plain language from the "
        "neighborhood and what you already read. Do not call any more tools."
    ),
}


def _force_answer(capped: bool, lang: str) -> str:
    table = FORCE_ANSWER_BUDGET if capped else FORCE_ANSWER
    return table["en"] if lang == "en" else table["zh"]


def read_roots(extra_roots: Sequence[Path] | None) -> list[Path]:
    """stream_chat 交给 read_file 的根：仓库根**加上**本手册自己的 allow_root。

    算书架可见性时必须调这个函数，不能在 app.py 里再拼一遍——v0.8.7 那个
    「书架印出苏格拉底读不到的路径」就是两处各算一遍漂移出来的，修完又漂到反面：
    书架传的是 extra_roots_for(hid)（不含 REPO_ROOT），于是仓库根里的教材
    苏格拉底读得到、书架却不列。「列得出」和「读得到」必须由同一个函数保证。

    注意 probe 那条线的语义**不一样**（`job.extra_roots or [REPO_ROOT]`——
    extra 非空时不含 REPO_ROOT），它有自己的 probe._reading_roots()。
    """
    return [REPO_ROOT, *(extra_roots or [])]


def _budget_lines(lines: Sequence[str], budget: int) -> str:
    """按字符预算收行，不按条数——书变厚时截的是尾巴，不是整段失控。"""
    out: list[str] = []
    used = 0
    for line in lines:
        used += len(line) + 1
        if used > budget:
            break
        out.append(line)
    return "\n".join(out)


def _place(
    idx: HandbookIndex,
    start_line: int,
    end_line: int,
    original_path: Path,
    fallback: dict[str, Any] | None,
) -> tuple[int, int, str]:
    """**start_line=0 的解释只在这里。**

    阅读视图下读者划到的是渲染后的字，插件对不回原文行号时发 0。以前它发 1——
    1 是一个真实的行号，sidecar 老老实实把那一轮记在「封面」，实测一场 65 轮的
    对话有 36 轮这样记丢，磁带那 11 轮全在里面。

    同一本书里上一处锚点还在就沿用它，标 sticky（读者多半还在原地追问）；
    没有就老实记在第 1 行，标 none。三个值都进 anchor，分析时按可信度用。
    """
    if start_line >= 1:
        return start_line, end_line, "exact"
    prev = fallback if isinstance(fallback, dict) else {}
    try:
        a = int(prev.get("start_line") or 0)
        b = int(prev.get("end_line") or a)
    except (TypeError, ValueError):
        a = b = 0
    if str(prev.get("path") or "") == str(original_path) and 1 <= a <= max(idx.n_lines, 1):
        return a, max(a, b), "sticky"
    return 1, 1, "none"


def build_user_packet(
    idx: HandbookIndex,
    original_path: Path,
    *,
    selected_text: str,
    start_line: int,
    end_line: int,
    chip: str,
    user_text: str,
    asked: Sequence[str] = (),
    intent_extra: str = "",
    # 读者自定义的芯片。给了它就**整段取代** CHIP_INTENT 查表——
    # 自定义芯片的意图就是读者写的那段话，没有别的来源。
    custom: CustomChipSpec | None = None,
    shelf: str = "",
    lang: str = "zh",
    compact_fed: bool = False,
    # 上一轮的锚点。插件定位不到划选时发 start_line=0，这时沿用它。
    fallback: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    from pen.compact import cap_selected_text, compact_fed_packet_suffix

    start_line, end_line, located = _place(idx, start_line, end_line, original_path, fallback)
    section = idx.locate(start_line)
    capped_sel, selection_capped = cap_selected_text(
        selected_text, lang=lang, start_line=start_line, end_line=end_line
    )
    en = lang == "en"
    if compact_fed:
        # 目录 / 邻域 / 书架已经在滚动摘要里。再整段重喂就是把折过的东西喂回去。
        mid = compact_fed_packet_suffix(lang)
    else:
        toc_lines = []
        for t in idx.toc:
            if t.beat is None:
                toc_lines.append(f"- {t.level}  L{t.start_line}  {t.heading}")
            else:
                toc_lines.append(f"  - {t.level} / {t.beat}  L{t.start_line}")
        # 按字符预算截，不按条数。以前是 toc_lines[:80]，而这本手册有 87 条——
        # 被砍掉的正好是尾部的 Capstone 和附录，跨关的问题就是这么问不出来的。
        toc = _budget_lines(toc_lines, TOC_CHARS)
        nb = neighborhood(original_path, section, (start_line, end_line))
        # 只有一本书时 shelf 是空串，整段消失——不写「（无）」。
        # 写「（无）」会让模型以为我们替它确认过没有别的书；整段不在时，
        # 它答「另一本我没读到」是对的，那本来就是实情。
        # 预算截完可能一行不剩（首行就超预算）。段头还在、条目却是空的，等于向模型
        # 断言「有别的教材」然后一本都不给——它只能凭空编。宁可整段不出现。
        shelf_rows = _budget_lines(shelf.splitlines(), SHELF_CHARS).strip()
        if shelf_rows:
            if en:
                shelf_block = (
                    "[Other handbooks in the workspace]\n"
                    "(Titles skimmed from the first 400 lines of each book, not the body. "
                    "To say what a book teaches, read_file its path first. If you cannot read it, "
                    "or this turn's budget is gone, say you have not read it — guessing from the "
                    "title is the same as pretending you searched.)\n"
                    f"{shelf_rows}\n\n"
                )
            else:
                shelf_block = (
                    "[工作目录里的其他教材]\n"
                    "（下面只是每本前 400 行扒出来的标题，不是正文。要说它讲了什么，\n"
                    "  先用 read_file 按 path 读，读了再说。读不到、或者本轮额度用完了，\n"
                    "  就照实说「那本我没读到」——按标题猜内容，和假装搜过是一回事。）\n"
                    f"{shelf_rows}\n\n"
                )
        else:
            shelf_block = ""
        if en:
            mid = (
                f"{shelf_block}[Table of contents (do not recite the book)]\n{toc}\n\n"
                f"[Selection]\n{capped_sel}\n\n"
                f"[Neighborhood]\n{nb}\n\n"
            )
        else:
            mid = (
                f"{shelf_block}[全书目录（不要整本背诵）]\n{toc}\n\n"
                f"[框选]\n{capped_sel}\n\n"
                f"[邻域]\n{nb}\n\n"
            )
    intent = custom_intent(custom, lang) if custom else chip_intent(chip, lang)
    none_user = (
        "(none — follow the chip)"
        if en
        else "（无，按芯片意图行动）"
    )
    none_asked = "(none yet)" if en else "（还没抛过）"
    if compact_fed:
        if en:
            mid = f"{mid}[Selection]\n{capped_sel}\n\n"
        else:
            mid = f"{mid}[框选]\n{capped_sel}\n\n"
    if en:
        packet = f"""[Source]
handbook_path: {original_path}
level: {section.level}
beat: {section.beat or "—"}
q_title: {section.q_title or "—"}
kind: {section.kind}
lines: {start_line}-{end_line}

{mid}[Intent]
chip = {chip}
{intent}
{intent_extra}

[Reader's note]
{user_text or none_user}

[Follow-ups already asked (do not repeat these)]
{_budget_lines([f"- {a}" for a in asked], ASKED_CHARS) or none_asked}
"""
    else:
        packet = f"""[来源]
handbook_path: {original_path}
level: {section.level}
beat: {section.beat or "—"}
q_title: {section.q_title or "—"}
kind: {section.kind}
lines: {start_line}-{end_line}

{mid}[意图]
chip = {chip}
{intent}
{intent_extra}

[用户补充]
{user_text or none_user}

[已经抛过的追问（别再重复这些）]
{_budget_lines([f"- {a}" for a in asked], ASKED_CHARS) or none_asked}
"""
    anchor = {
        "path": str(original_path),
        "level": section.level,
        "beat": section.beat,
        "q_title": section.q_title,
        "kind": section.kind,
        "start_line": start_line,
        "end_line": end_line,
        "selected_text": capped_sel,
        "selection_capped": selection_capped,
        "selected_chars": len(selected_text or ""),
        # exact / sticky / none。分析时按可信度用：sticky 是「大概还在上一处」。
        "located": located,
    }
    return packet, anchor


def parse_dynamic_chips(
    reply: str,
    *,
    user_text: str = "",
    asked: Sequence[str] = (),
) -> tuple[str, list[dict[str, Any]]]:
    """剥掉回复末尾的 <!--pen:chips--> 块，把里面的行清洗成可下发的芯片。

    清洗不是可选的：模型会把 prompt 里的示范文字原样抄出来（历史上「下一问 1」
    真的变成过按钮），也会把读者刚问过的那句话换个说法再吐回来。
    """
    marker = "<!--pen:chips"
    if marker not in reply:
        return reply.strip(), []
    head, rest = reply.split(marker, 1)
    end = rest.find("-->")
    block = rest[:end] if end >= 0 else rest
    visible = head.strip()
    chips = clean_candidates(
        block.splitlines(),
        example_lines=PROMPT_EXAMPLE_LINES,
        fixed_labels=[str(c["label"]) for c in FIXED_CHIPS],
        user_text=user_text,
        asked=asked,
        limit=2,
        kind="quick",
    )
    return visible, chips


def usage_snapshot(prompt: int, completion: int) -> dict[str, int]:
    """最后一次 LLM 调用的用量。context_tokens = 此刻喂进去的上下文。"""
    p = int(prompt or 0)
    c = int(completion or 0)
    return {"context_tokens": p, "completion_tokens": c, "prompt_tokens": p}


class ProviderError(RuntimeError):
    """供应商调用失败。message 已是给用户看的中文，不含 key。

    `code` 是 `provider_error_code` 给的稳定码；只有 `PROVIDER_TOO_LONG` 那一格
    带 `got` / `limit`（这一枪多少 token、模型上限多少），`_agent_loop` 靠它们
    决定「退回这一批重打」还是报错。`estimated` 说 `got` 是我们估的，
    不是节点说的——退回给模型的那句话要如实标出来。
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        got: int = 0,
        limit: int = 0,
        estimated: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.got = got
        self.limit = limit
        self.estimated = estimated


# 「节点这一下为什么失败」——**这张表是唯一定义点**。对话里的红字气泡、
# 设置页的体检、状态栏那颗点，三处共用它；分家就会出现「同一个 404 在气泡里
# 叫『意料外的错误』，在体检里叫『没这个模型』」。
#
# 码本身进 SSE 和 /v1/llm/preflight 的响应，让前端能按码决定颜色和落点，
# 而不是去 match 一句会被翻译的话。
PROVIDER_OK = ""
PROVIDER_BAD_KEY = "bad-key"
PROVIDER_NO_MODEL = "no-model"
PROVIDER_NO_VISION = "no-vision"
PROVIDER_REJECTED = "rejected"
PROVIDER_UNREACHABLE = "unreachable"
PROVIDER_UNEXPECTED = "unexpected"
# 上下文太长。对话里**可重试**：_agent_loop 会退回这一批工具结果再打一枪，
# 判定住在 pen/overflow.py。
PROVIDER_TOO_LONG = "too-long"
# 下面四条**只有体检会给**（pen/preflight.py 的减配梯子）。对话里撞上时，
# 节点回的仍是一个笼统的 400，只有把同一枪减掉一样再打一次才分得出是哪一样。
PROVIDER_NO_TOOLS = "no-tools"
PROVIDER_NO_STREAM = "no-stream"
PROVIDER_NO_USAGE = "no-usage"
PROVIDER_NO_THINKING = "no-thinking"

_CODE_MSG = {
    PROVIDER_BAD_KEY: "provider.bad_key",
    PROVIDER_NO_MODEL: "provider.no_model",
    PROVIDER_NO_VISION: "provider.bad_vision",
    PROVIDER_REJECTED: "provider.rejected",
    PROVIDER_UNREACHABLE: "provider.unreachable",
    PROVIDER_UNEXPECTED: "provider.unexpected",
    PROVIDER_TOO_LONG: "provider.too_long",
    PROVIDER_NO_TOOLS: "provider.no_tools",
    PROVIDER_NO_STREAM: "provider.no_stream",
    PROVIDER_NO_USAGE: "provider.no_usage",
    PROVIDER_NO_THINKING: "provider.no_thinking",
}


def provider_error_code(exc: BaseException, *, sent_image: bool) -> str:
    """异常 → 稳定的码。**不碰 i18n**，所以测试和前端都能拿它当锚点。

    `sent_image` 是必填的，没有默认值。**这一枪里到底有没有图，只有调用方
    知道，而它决定了一整类结论能不能成立**：`looks_like_vision_reject` 是个
    文本猜测（在报文里找 "image_url" / "vision" 这些词），没发图的时候它猜中
    也是错的——节点不可能在拒绝一张不存在的图。

    v0.22.2 就是这么翻的车：读者把「图像理解」关了，体检没发图，节点因为
    别的原因回了 400，报文里恰好有个匹配词，于是状态栏一口咬定「节点拒收了
    图片，把图像理解关掉」——而他已经关了。
    """
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return PROVIDER_BAD_KEY
    if status == 404:
        # 读者截图里那条「意料外的错误（NotFoundError）」就是它。404 在
        # OpenAI 兼容节点上几乎只有一个意思：这个节点没有这个型号。
        return PROVIDER_NO_MODEL
    if status in (400, 413):
        from pen.overflow import looks_like_context_overflow
        from pen.vision import looks_like_vision_reject

        # 溢出排在视觉之前：溢出报文写明了 token，视觉那条是在猜。
        if looks_like_context_overflow(exc):
            return PROVIDER_TOO_LONG
        if status == 413:
            return PROVIDER_UNEXPECTED
        if sent_image and looks_like_vision_reject(exc):
            return PROVIDER_NO_VISION
        return PROVIDER_REJECTED
    from openai import APIConnectionError, APITimeoutError

    if isinstance(exc, (APIConnectionError, APITimeoutError, OSError, TimeoutError)):
        return PROVIDER_UNREACHABLE
    return PROVIDER_UNEXPECTED


# 钥匙长什么样。转述节点原话之前先把它抹掉——报文里几乎不会带 key，
# 但「几乎」不是理由：这句话会被显示在状态栏上，也会进读者的截图。
_KEYISH = re.compile(r"\b(?:sk|ck|gsk|xai|pk)[-_][A-Za-z0-9_\-]{8,}", re.I)


def provider_detail(exc: BaseException, cap: int = 200) -> str:
    """节点自己那句话。**分不出类的时候，转述它远胜过我们再猜一次。**

    v0.22.2 之前分不出类就回一句「请核对 Base URL、model 和 API Key」——
    三样都核对过的读者看到这句话，等于什么也没得到。
    """
    raw = ""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            raw = str(err.get("message") or "")
        elif isinstance(err, str):
            raw = err
        raw = raw or str(body.get("message") or "")
    raw = (raw or str(getattr(exc, "message", "")) or str(exc)).strip()
    raw = _KEYISH.sub("[key]", " ".join(raw.split()))
    return raw[: cap - 1] + "…" if len(raw) > cap else raw


def provider_message_for(code: str, lang: str = "zh", **fmt: Any) -> str:
    """码 → 给用户看的一句话。不带 key，不贴原始报文。"""
    return msg(_CODE_MSG.get(code, "provider.unexpected"), lang, **fmt)


def provider_error_message(
    exc: BaseException, lang: str = "zh", model: str = "", *, sent_image: bool = False
) -> str:
    """OpenAI / 网络异常 → 给用户看的一句话。**实现只有上面那几份。**"""
    return provider_message_for(
        provider_error_code(exc, sent_image=sent_image),
        lang,
        kind=type(exc).__name__,
        model=model or "?",
        detail=provider_detail(exc),
    )


def _finish_text(
    session: PenSession,
    raw: str,
    usage: dict[str, int],
    *,
    user_text: str = "",
    streamed: bool = False,
) -> Iterator[dict[str, Any]]:
    """收尾：剥芯片块、落 last_assistant、发 done。

    streamed=True 表示正文**已经在收分片的时候吐过了**，这里不能再吐一遍——
    前端是 `acc += text` 累加的，重吐一次读者会看到两份。
    """
    # 上一轮抛过的别再抛一遍
    asked = [str(c.get("text") or "") for c in session.last_chips]
    visible, dyn = parse_dynamic_chips(raw, user_text=user_text, asked=asked)
    session.last_assistant = visible
    session.last_chips = dyn
    if len(visible) > 80:
        session.has_substantive = True
    if not streamed:
        yield {"type": "status", "phase": "writing", "text": "在写…"}
        for i in range(0, len(visible), 48):
            yield {"type": "token", "text": visible[i : i + 48]}
    yield {
        "type": "done",
        "usage": usage,
        # dynamic_chips 保持 list[str]：web/src/pen/PenPanel.tsx 还在吃这个形状。
        # 富格式走新键 dyn_chips，前端 ?? 回落。
        "dynamic_chips": [c["text"] for c in dyn],
        "dyn_chips": dyn,
        "has_substantive": session.has_substantive,
    }


def thinking_wire(model: str, level: str, provider: str = "") -> dict[str, Any]:
    """转发。**规则住在 pen/providers.py**，这里一个字都不判。

    在 v0.23.0 之前，这里是一串按型号名分叉的 `if`，认不出的一律走 DeepSeek
    那一支——那正是 Google 那个 `Unknown name "thinking"` 400 的成因。
    """
    return providers.thinking_wire(model, level, provider)


def thinking_on(model: str, level: str, provider: str = "") -> bool:
    """同上：转发到那张表的同一格，不在这儿再判一次型号名。"""
    return providers.thinking_on(model, level, provider)


def flatten_unsigned(
    messages: list[dict[str, Any]], model: str, provider: str = ""
) -> list[dict[str, Any]]:
    """同上：**规则住在 pen/providers.py**，这里一个字都不判。"""
    return providers.flatten_unsigned(messages, model, provider)


def llm_create_kwargs(
    cfg: LLMConfig,
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """组 chat.completions.create 参数。thinking 档按型号收口，不原样上线。

    **这里是「主对话那一枪长什么样」的唯一定义点**，体检也调它。所以历史的
    修补也放在这儿：别处补一遍就是第二个定义点，而这个仓踩过三次。
    """
    kwargs: dict[str, Any] = {
        "model": cfg.model,
        # 这一家发不出去的工具往返，压平成文本。不要求签名的家原样返回同一个
        # 列表，一个字节都不动。
        "messages": flatten_unsigned(messages, cfg.model, cfg.provider),
        # 主对话线走真流式：读者要等 14.5 秒才看到第一个正文字，那段时间里
        # 屏幕上什么都没有。推理内容 1.3 秒就开始流，拿它当「没卡住」的信号。
        # include_usage 让末片带回 usage——不加就没账可记。
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        kwargs["tools"] = tools
    kwargs.update(thinking_wire(cfg.model, cfg.thinking, cfg.provider))
    return kwargs


class _StreamedMessage:
    """流式拼出来的一条 assistant 消息。

    形状要和非流式那个 message 对得上：下游只用 `.content` / `.tool_calls`
    和 `.model_dump()`，所以只实现这三样。tool_calls 里每个元素也要能被
    `_run_tool_batch` 当对象用（`.id` / `.function.name` / `.function.arguments`）。
    """

    __slots__ = ("content", "tool_calls", "reasoning")

    def __init__(
        self, content: str, calls: list[dict[str, Any]], reasoning: str = ""
    ) -> None:
        self.content = content or None
        self.tool_calls = [_StreamedCall(c) for c in calls] or None
        self.reasoning = reasoning or ""

    def model_dump(self, exclude_none: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {"role": "assistant"}
        if self.content:
            out["content"] = self.content
        if self.tool_calls:
            out["tool_calls"] = [c.raw for c in self.tool_calls]
        # **产出它的那一枪要把它带回去。** 有些节点在 thinking 模式下硬性要求
        # 回传这个字段，缺了就 400（见 pen/reasoning.py 开头）。以前这里只数
        # 字数、不留正文，于是任何「调了一次工具、下一枪接着说」的对话都会
        # 在第二枪吃 400——不是 Fast Mode 的病，Fast Mode 只是让它必然发生。
        if self.reasoning:
            out[reasoningmod.FIELD] = self.reasoning
        return out


class _StreamedCall:
    __slots__ = ("raw", "id", "type", "function")

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.id = str(raw.get("id") or "")
        self.type = "function"
        fn = raw.get("function") or {}
        self.function = SimpleNamespace(
            name=str(fn.get("name") or ""),
            arguments=str(fn.get("arguments") or ""),
        )


class _ToolCallDraft:
    """把流式分片拼回一个完整的 tool_call。

    `delta.tool_calls[i].function.arguments` 是**一片一片来的**，`id` 和 `name`
    通常只在第一片出现，之后的片只带 index 和 arguments 的碎片。拼错一个字节
    json.loads 就炸，整轮对话失败——所以这块单独测。

    除了这三样，分片上还可能有**我们不认识、但产出它的那个节点要求原样带
    回**的字段。v0.23.1 撞上的是 Gemini 3：它在每个 tool_call 上挂一个
    `extra_content.google.thought_signature`，下一枪回传时缺了它就是
    `Function call is missing a thought_signature in functionCall part`
    ——也就是说**任何一次工具调用之后，这轮对话就再也发不出去了**。

    所以这里不认字段名，只认来路：**分片上带的，原样留住、原样带回。**
    写死 `extra_content` 一个键的话，下一家换个名字我们就再修一次——
    这已经是同一个病的第三次发作了（v0.22.4 是推理正文，见 model_dump）。
    """

    __slots__ = ("id", "name", "args", "extra")

    # 这四个我们自己拆开处理，别的一律照抄。
    _OURS = ("index", "id", "type", "function")

    def __init__(self) -> None:
        self.id = ""
        self.name = ""
        self.args: list[str] = []
        self.extra: dict[str, Any] = {}

    def eat(self, piece: Any) -> None:
        if getattr(piece, "id", None):
            self.id = str(piece.id)
        fn = getattr(piece, "function", None)
        if fn is not None:
            if getattr(fn, "name", None):
                self.name = str(fn.name)
            frag = getattr(fn, "arguments", None)
            if frag:
                self.args.append(str(frag))
        dump = getattr(piece, "model_dump", None)
        if callable(dump):
            for k, v in dump(exclude_none=True).items():
                # 只收非空值：空片盖掉前面那片带来的签名就白留了。
                if k not in self._OURS and v:
                    self.extra[k] = v

    def done(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": "".join(self.args)},
            **self.extra,
        }


def assemble_tool_calls(drafts: dict[int, _ToolCallDraft]) -> list[dict[str, Any]]:
    """按 index 升序还原。供应商不保证分片有序，所以这里排一次。"""
    return [drafts[i].done() for i in sorted(drafts)]


def _tool_ctx(
    session: PenSession,
    original_path: Path,
    extra_roots: list[Path],
    limits: RuntimeLimits | None = None,
) -> dict[str, Any]:
    return {
        "original_path": original_path,
        "extra_roots": extra_roots,
        # 只为把读者原话透到 _finish_text 去做复读过滤，工具链路不读它
        "user_text": "",
        "handbook_id": session.handbook_id,
        "read_ok": {Path(p).expanduser().resolve() for p in session.read_ok_paths},
        # 本次请求认下来的上限。**必须在这里放，不能在两个调用点各拼一遍**
        # ——read_roots() 那段注释讲的就是这个教训。
        # tools_impl.limits_of() 是唯一的取值口。
        "limits": limits or default_limits(),
    }


def _remember_read(session: PenSession, ctx: dict[str, Any], out: dict[str, Any]) -> None:
    if not out.get("ok") or not out.get("resolved"):
        return
    got = Path(str(out["resolved"])).expanduser().resolve()
    ctx.setdefault("read_ok", set()).add(got)
    known = {Path(p).expanduser().resolve() for p in session.read_ok_paths}
    known.add(got)
    session.read_ok_paths = [str(p) for p in known]


def stream_chat(
    session: PenSession,
    original_path: Path,
    user_packet: str,
    llm: LLMConfig | None = None,
    extra_roots: list[Path] | None = None,
    allow_env_fallback: bool = True,
    lang: str = "zh",
    user_text: str = "",
    limits: RuntimeLimits | None = None,
    images: list[dict[str, str]] | None = None,
    route: str = "base",
    fast_llm: LLMConfig | None = None,
) -> Iterator[dict[str, Any]]:
    """`llm` 恒是**基座**那份，`fast_llm` 是快模型那份，`route` 说这一轮从哪个起步。

    两份都往下传而不是只传选中的那一份：`_agent_loop` 里那道 edit_file 绊线
    要在半路换回基座，手里必须有基座的 cfg。
    """
    # 请求换了主机却没带 key 时 merge_llm 会返回 None；不能再 or resolve_llm() 把 .env 钥匙挪用过去。
    cfg = llm if llm is not None else (resolve_llm() if allow_env_fallback else None)
    if cfg is None:
        yield {
            "type": "error",
            "message": msg("llm.missing_config", lang),
        }
        return

    from pen.vision import apply_vision_clause, user_message_content

    pics = list(images or [])
    if pics and not cfg.vision:
        yield {"type": "error", "message": msg("vision.disabled", lang)}
        return

    apply_session_lang(session, lang)
    if session.messages and session.messages[0].get("role") == "system":
        session.messages[0]["content"] = apply_vision_clause(
            str(session.messages[0].get("content") or ""),
            enabled=cfg.vision,
            lang=lang,
        )
    # 上一轮的思考正文没人要了。**「必须回传」那条契约只约束当前这条工具链**，
    # 而整段历史每一枪都重发——不忘掉就是按轮次翻倍地烧钱。就地写回是对的：
    # 这些字节除了上线没有别的用途（落盘也不写）。
    session.messages = reasoningmod.forget(session.messages)
    session.messages.append(
        {"role": "user", "content": user_message_content(user_packet, pics)}
    )
    # 新的一轮，本轮账清零。**只能在这里清，绝不能放进 _agent_loop 或
    # resume_chat**——那是同一轮的后半段，清了就等于审批让预算翻倍，
    # 而 v0.10.3 刚修过反向的同一件事。
    #
    # 不清的后果实测过：max_tokens_chat 会从「每轮上限」退化成「整场一次性
    # 预算」，用完之后每一轮都直接进收口枪，苏格拉底**永远不再翻手册**；
    # turn_spend 还落盘，重启 sidecar 也救不回来，只能新开会话。
    session.turn_spend = metermod.blank()

    ctx = _tool_ctx(session, original_path, read_roots(extra_roots), limits)
    ctx["user_text"] = user_text
    # 快模型没配好（fast_llm 是 None）就当没开过 Fast Mode。这一格在 app.py
    # 的 route_for 里已经判过一次（no-fast-key），这里是第二道保险：
    # stream_chat 也被 test 直接调，不该假设调用方一定先跑过路由。
    start_fast = route == "fast" and fast_llm is not None
    yield from _agent_loop(
        session, ctx, fast_llm if start_fast else cfg, lang, base_cfg=cfg, fast=start_fast
    )


def _needs_human(reply: Any) -> bool:
    """这一批里有没有需要人点头的工具。**快模型的绊线判据。**

    判据借 `permissions.decide()`，不在这儿再列一遍工具名。「哪些工具会动
    磁盘」是那个模块的职责，两处分家的话，将来多一个写类工具就会出现
    「审批面板弹了、但快模型已经把它执行完了」。
    """
    for tc in getattr(reply, "tool_calls", None) or []:
        name = str(getattr(getattr(tc, "function", None), "name", "") or "")
        if decide(name) == "ask":
            return True
    return False


def _agent_loop(
    session: PenSession,
    ctx: dict[str, Any],
    cfg: LLMConfig,
    lang: str = "zh",
    *,
    base_cfg: LLMConfig | None = None,
    fast: bool = False,
) -> Iterator[dict[str, Any]]:
    """`cfg` 是这一轮起步用的配置，`fast` 说它是不是快模型那份。

    `base_cfg` 是**永远可用的那份**（基座）。快轮半路要写盘时靠它换回去。
    """
    from openai import OpenAI, OpenAIError

    # cfg / client 都不是常量：快轮撞上 edit_file 时会当场换成基座重绑。
    # _create 是闭包，读的就是这两个名字，重新赋值下一枪就生效。
    client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=120.0)
    tools = schemas()
    # usage 是「最后一枪」的快照（此刻窗口占多大），meter 是累加器（一共花了多少）。
    # 两件事，键名刻意不同，永远不合并——见 pen/meter.py 开头和
    # test_tutor.test_usage_snapshot_is_last_call_not_a_sum。
    usage = usage_snapshot(0, 0)
    meter = metermod.Meter(kind=metermod.KIND_CHAT)
    # 这一枪实际要发的 messages。**None = 就用 session.messages 原表。**
    # 只有快轮的窗口闸会往里放东西，放的是压缩后的**副本**；session 一个字
    # 都不改（理由见 pen/compaction.py 开头：compact_session 会把
    # session.compacted 置真，而全仓没有任何地方把它改回去）。
    shot: list[dict[str, Any]] | None = None

    def _create(*, with_tools: bool) -> Iterator[dict[str, Any]]:
        """打一枪，边收边报。**用 `yield from` 调它，返回值就是那条消息。**

        写成生成器是为了让 _agent_loop 的结构不用动：它本来就是生成器，
        而流式要在收分片的过程中往外报事件。

        三样东西分开走：
          reasoning_content → 只计数（1633 个分片，正文里显示是灾难），
                              报出去让状态行跳数字——这是「没卡住」的信号
          content           → 直接转 token 事件，**这才是真吐字**
          tool_calls        → 按 index 拼回完整 JSON，见 _ToolCallDraft
        """
        # 「图像理解」关着就**当场摘图**——这是那个开关唯一真正生效的地方。
        #
        # stream_chat 入口那道闸只拦「这一轮新贴的图」。可 session.messages 是
        # 常驻内存的：上一轮贴的 image_url 段会跟着每一枪原样重发，于是读者
        # 关掉开关之后节点照旧收到图、照旧拒收，看起来就是开关坏了（v0.22.2
        # 的读者报告）。摘在这儿而不是入口，是因为**这里是唯一的发枪点**，
        # 而且 cfg 会在 Fast Mode 换模型时重绑——快模型没视觉、基座有视觉时，
        # 同一段历史该发什么，只有这一枪自己知道。
        wire = session.messages if shot is None else shot
        sent = wire if cfg.vision else strip_images(wire)
        # **问真发出去的那份，不问 cfg.vision。** 开着视觉但这场压根没贴过图
        # 时，节点回的 400 一样不可能是拒图。
        with_pics = any(has_image_parts(m.get("content")) for m in sent)
        kwargs = llm_create_kwargs(
            cfg,
            messages=sent,
            tools=tools if with_tools else None,
        )
        parts: list[str] = []
        # 已经吐出去多少字。芯片块是给界面剥的，**一个字都不能吐给读者**——
        # 而它是一片一片来的，marker 本身可能被切碎，所以尾巴留 len(marker)-1
        # 不吐，等下一片来了再说；流完再冲一次。
        emitted = 0
        drafts: dict[int, _ToolCallDraft] = {}
        got_usage: Any = None
        think_chars = 0
        think_text: list[str] = []
        try:
            stream = client.chat.completions.create(**kwargs)
            for chunk in stream:
                if getattr(chunk, "usage", None):
                    got_usage = chunk.usage
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                if delta is None:
                    continue
                # 两个字段名都认：DeepSeek / GLM 走 reasoning_content，
                # celeris 走 reasoning。少认一个不影响正确性，但状态行的
                # 「在想…」会恒为 0——而那正是「没卡住」的唯一信号。
                think = getattr(delta, "reasoning_content", None) or getattr(
                    delta, "reasoning", None
                )
                if think:
                    think_chars += len(str(think))
                    # 封顶累加：思考正文实测上千个分片，而整段历史每一枪都
                    # 重发。不封顶就是按轮次翻倍地烧钱。
                    if len(think_text) < reasoningmod.MAX_CHARS:
                        think_text.append(str(think))
                    yield {"type": "think", "chars": think_chars}
                said = getattr(delta, "content", None)
                if said:
                    parts.append(str(said))
                    buf = "".join(parts)
                    cut = buf.find(CHIPS_MARKER)
                    safe = cut if cut >= 0 else max(0, len(buf) - len(CHIPS_MARKER) + 1)
                    if safe > emitted:
                        yield {"type": "token", "text": buf[emitted:safe]}
                        emitted = safe
                for piece in getattr(delta, "tool_calls", None) or []:
                    idx = int(getattr(piece, "index", 0) or 0)
                    drafts.setdefault(idx, _ToolCallDraft()).eat(piece)
        except (OpenAIError, OSError, TimeoutError) as exc:
            code = provider_error_code(exc, sent_image=with_pics)
            got = limit = 0
            estimated = False
            if code == PROVIDER_TOO_LONG:
                # 节点说了多少就用多少；没说就用我们的窗口估算，并且标明是估的。
                # 上限：快模型的窗口是写死的常量；基座的我们不知道，留 0。
                got, limit = overflow_numbers(exc)
                if not got:
                    got, estimated = est_tokens(sent), True
                if not limit and fast:
                    limit = FAST_WINDOW
            raise ProviderError(
                provider_message_for(
                    code,
                    lang,
                    kind=type(exc).__name__,
                    model=cfg.model,
                    detail=provider_detail(exc),
                ),
                code=code,
                got=got,
                limit=limit,
                estimated=estimated,
            ) from exc
        # 冲掉尾巴：没有芯片块时上面那个保守切法会压着最后十几个字不吐。
        buf = "".join(parts)
        cut = buf.find(CHIPS_MARKER)
        tail_end = cut if cut >= 0 else len(buf)
        if tail_end > emitted:
            yield {"type": "token", "text": buf[emitted:tail_end]}
        if got_usage:
            usage.update(
                usage_snapshot(
                    getattr(got_usage, "prompt_tokens", 0) or 0,
                    getattr(got_usage, "completion_tokens", 0) or 0,
                )
            )
            session.last_context_tokens = int(usage["prompt_tokens"] or 0)
        # 记账在 if 外面：usage 缺失时这一枪照样是花过钱的，calls 该 +1。
        # read_usage(None) 会安静地回零，不会炸。
        row = meter.add(got_usage)
        session.turn_spend = metermod.merge(session.turn_spend, row)
        session.spend[metermod.KIND_CHAT] = metermod.merge(
            session.spend.get(metermod.KIND_CHAT), row
        )
        return _StreamedMessage(
            "".join(parts),
            assemble_tool_calls(drafts),
            # **只有这一枪自己在思考模式里才带回正文。** 关着思考的节点收到
            # 这个字段没有好处，而个别节点对没要过的字段会挑剔。
            reasoningmod.clip("".join(think_text))
            if thinking_on(cfg.model, cfg.thinking, cfg.provider)
            else "",
        )

    def _spend_ev() -> dict[str, Any]:
        # chat 下发**整行**而不是一个总数：前端的悬停明细要分「输入 / 输出 /
        # 缓存命中」，只给总数的话流式期间那份明细会是错的。
        return {
            "type": "spend",
            "turn": metermod.total(session.turn_spend),
            "chat": dict(session.spend.get(metermod.KIND_CHAT) or metermod.blank()),
        }

    def _to_base(why: str) -> dict[str, Any]:
        """把这一轮从快模型换回基座。**返回要 yield 的 route 事件。**

        **不回滚任何消息。** `session.messages` 是供应商中立的，换个 client
        接着发就行；快模型已经读到的正文和 `ctx["read_ok"]` 全部留着，基座的
        edit_file 因此不用重读就能过 `read_first_block` 那道硬闸。代价只有
        浪费掉的那一枪。
        """
        nonlocal cfg, client, fast, shot
        cfg = base_cfg or cfg
        client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=120.0)
        fast = False
        # 窗口闸压的那份副本作废：基座是 1M 窗口，发全量。
        shot = None
        return {"type": "route", "to": "base", "why": why}

    # 已经报给读者的压缩档位。每一枪都会重算一次，档位没变就别再报一遍。
    told: list[str] = []
    # 摘要里允许指名道姓的文件。**一轮只算一次**：allow_paths_for 要翻书架
    # 索引（磁盘 I/O），而窗口闸是每一枪都跑的，max_tool_rounds 默认 100。
    allowed_names: list[Path] | None = None

    def _fit() -> Iterator[dict[str, Any]]:
        """快轮的窗口闸：这一枪发出去之前，先确认它塞得进 131072。

        **和旁边那道 `metermod.over` 花销闸不同源，不能互相替代。** 那道量的是
        「这一轮花了多少钱」，默认 0（关着），撞线只是收口；这道量的是「这一枪
        有多大」，由供应商在**请求时**硬拒。而 `_agent_loop` 每轮把整段 messages
        重发，一次 read_file 最多加 5000 字符且**永不裁剪**，
        `max_tool_rounds` 默认 100——基座 1M 窗口从没暴露过这件事，
        快模型没这道闸会在对话中途吃 400。
        """
        nonlocal shot, told, allowed_names
        if not fast:
            shot = None
            return
        if allowed_names is None:
            allowed_names = allow_paths_for(ctx["original_path"])
        plan = strategy_for("fast").plan(
            session,
            budget=fast_budget(limits_of(ctx)),
            allow_paths=allowed_names,
            original_path=ctx["original_path"],
        )
        if not plan.fits:
            # 压到底还是超。**不硬发**——把上下文压成废墟去迁就窗口，
            # 不如让 1M 窗口的基座跑。这也正是「保守」。
            yield _to_base("context-too-big")
            return
        shot = plan.messages if plan.changed else None
        if plan.steps != told:
            told = list(plan.steps)
            if plan.steps:
                # **不叫 compacted。** 那个名字已经被 app.py:992 占了，
                # 意思是「会话真的被折了」（破坏性、不可逆，前端会往日志里
                # 插一条永久提示）。快轮压的是副本，会话一个字没动——
                # 共用一个事件名就是对读者说谎。
                yield {
                    "type": "trimmed",
                    "steps": list(plan.steps),
                    "est": plan.est_tokens,
                }

    # 这一轮已经因为「上下文太长」退过几次。封顶 OVERFLOW_RETRIES，循环必然终止。
    overflows = 0

    def _shrink(exc: ProviderError) -> Iterator[dict[str, Any]]:
        """撞窗口之后把上下文弄小一点。**用 `yield from` 调，返回值 = 能不能重打。**

        三级，从最便宜、最不伤历史的开始：
          1  退这一批   尾巴那批工具结果换成带数字的错误（pen/overflow.py），
                        模型下一枪自己改成按行号区间读。这是读者要的那个
                        「可重试错误」。
          2  换基座     快模型且没东西可退：交给 1M 窗口的基座（现有事件）。
          3  真折一次   基座且没东西可退（第一枪就撞）：历史本身太大了，
                        `compact_session` 走破坏性那条路——和轮与轮之间的
                        自动折是同一件事，只是发生在一轮中间。
          -  都不行     False，调用方如实报错。
        """
        nonlocal overflows, allowed_names
        overflows += 1
        if overflows > OVERFLOW_RETRIES:
            return False
        stubbed = stub_trailing_batch(
            session.messages,
            model=cfg.model,
            got=exc.got,
            limit=exc.limit,
            lang=lang,
            nth=overflows,
            cap=OVERFLOW_RETRIES,
            estimated=exc.estimated,
        )
        if stubbed:
            # 节点拒了那一枪，模型**没见过**这些原文。撤销它们的 read-first 资格，
            # 不然退回之后它照样能 edit_file、直接进审批（二审 #1）。
            original = Path(ctx["original_path"])
            gone: set[Path] = set()
            for item in stubbed:
                if item["name"] != "read_file":
                    continue
                tried = resolve_read_target(original, str(item["path"] or original))
                gone.add(Path(tried).expanduser().resolve())
            if gone:
                ctx["read_ok"] = {p for p in (ctx.get("read_ok") or set()) if p not in gone}
                session.read_ok_paths = [
                    p for p in session.read_ok_paths if Path(p).expanduser().resolve() not in gone
                ]
            n = len(stubbed)
            yield {
                "type": "status",
                "phase": "overflow",
                "text": (
                    f"Context overflowed; returning {n} tool results and retrying…"
                    if lang == "en"
                    else f"上下文超限，退回 {n} 条工具结果重试…"
                ),
            }
            return True
        if fast:
            yield _to_base("context-too-big")
            return True
        if allowed_names is None:
            allowed_names = allow_paths_for(ctx["original_path"])
        # 先用纯预览问一句「折了会变小吗」。`fold_messages` 对一份已经只剩
        # [system, 包] 的历史照样折出一份摘要（did=True），但一个 token 都省不下，
        # 白打一枪不说，还把 session.compacted 置真——那是不可逆的。
        before = est_tokens(session.messages)
        preview = strategy_for("base").plan(
            session,
            budget=fast_budget(limits_of(ctx)),
            allow_paths=allowed_names,
            original_path=ctx["original_path"],
        )
        if not preview.changed or preview.est_tokens >= before:
            return False
        try:
            folded = compact_session(
                session, allow_paths=allowed_names, original_path=ctx["original_path"]
            )
        except CompactPending:
            return False
        if not folded.did:
            return False
        # 折叠说明写的就是「旧读不算数」：edit_file 之前得再 read_file 一次。
        ctx["read_ok"] = set()
        yield {"type": "compacted", "dropped_reads": folded.dropped_reads}
        return True

    capped = False
    for _step in range(limits_of(ctx).max_tool_rounds):
        # 进这一枪之前判一次。**这里不留余量，是想清楚的，不是漏了**：
        # 下面那道「执行批次之前」的判用的是同一组输入（工具执行既不改
        # turn_spend 也不改 usage），所以本轮 k 的顶部和上一轮 k-1 的批次前
        # 完全等价——余量写在这里是一个永远不会先触发的死表达式。
        #
        # 它真正起作用的只有一种情况：**审批续跑**。那时 turn_spend 是从
        # 暂停前带过来的，而 usage 刚重置成 0（没有上一枪可估），
        # 判据就该是裸的「已经花超了就别再开工具枪」。
        #
        # stream_chat 的第 0 轮 turn_spend 是 0，`0 >= cap` 对任何 cap > 0
        # 都是 False——**上限永远不会让第一枪打不出去**，填错一个小数字最坏
        # 是退化成单轮直答，不是把插件变砖。
        if metermod.over(metermod.total(session.turn_spend), limits_of(ctx).max_tokens_chat):
            capped = True
            break
        while True:
            yield from _fit()
            yield {"type": "status", "phase": "thinking", "text": "苏格拉底在想…"}
            try:
                # 不要叫 msg：会遮蔽模块级的 i18n msg()，下面那条空正文文案就调不到了
                reply = yield from _create(with_tools=True)
                break
            except ProviderError as exc:
                # 上下文太长是**可重试**的：弄小一点再打同一枪。**不占轮数**：失败的
                # 那一枪模型什么都没做，占了的话 max_tool_rounds 小时它还没机会
                # 分段读就被推进禁用工具的收口枪（二审 #2）。终止靠 _shrink 的封顶。
                if exc.code == PROVIDER_TOO_LONG and (yield from _shrink(exc)):
                    continue
                yield {"type": "error", "message": str(exc)}
                return
        if fast and _needs_human(reply):
            # 快轮张口要动磁盘。**dispatch 之前就拦下**——它物理上到不了
            # handle_edit_file，点「允许」之前磁盘一个字节都不会动。
            #
            # 这条 assistant 消息**不 append**：带 tool_calls 的消息必须有
            # 配对的 tool 结果，不 append 就没有这个配对义务，历史天然保持
            # 合法，一行回滚都不用写。钱照报，这一枪是真花过的。
            yield _spend_ev()
            yield _to_base("wants-edit")
            continue
        session.messages.append(reply.model_dump(exclude_none=True))
        # 刚花完钱就报。前端只拿它刷状态行那一个文本节点，成本可以忽略。
        yield _spend_ev()
        if not reply.tool_calls:
            raw = (reply.content or "").strip()
            if not raw:
                yield {
                    "type": "error",
                    "message": msg("llm.empty_reply", lang, model=cfg.model, base_url=cfg.base_url),
                }
                return
            yield from _finish_text(
                session, raw, usage, user_text=str(ctx.get("user_text") or ""), streamed=True
            )
            return
        # 执行这一批之前再判一次。循环顶部那道判在**这一枪之前**，此刻它的
        # 花销已经落袋——而模型常常一枪吐一整批（实测一次 7 个 read_file，
        # 也见过 21 个）。整批执行完再收口的话，收口枪面对的是多了整批正文的
        # messages，实测超出从约 1.5 倍变成 2.18 倍。撞线就不执行这一批。
        if metermod.over(
            metermod.total(session.turn_spend),
            limits_of(ctx).max_tokens_chat,
            headroom=usage["prompt_tokens"],
        ):
            # **协议要求每个 tool_call 都有配对的 tool 结果，少一条供应商直接
            # 400。** 所以不执行也要给这一批每个调用补一条合成结果——顺带把
            # 「为什么没读到」告诉模型，它收口时才说得清自己没看哪几段。
            for tc in reply.tool_calls:
                session.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": "本轮的翻阅额度用完了，这次没有执行。",
                    }
                )
            capped = True
            break
        # v0.11.1 曾在这里手工把 reply.content 吐出去（非流式时它会被吞掉）。
        # 改真流式之后，那段话在收分片的当口就已经吐过了——再吐一遍读者看到两份。
        yield {"type": "status", "phase": "reading", "text": "在翻手册…"}
        # 跨书那第三道闸要看「这一轮已经烧了多少」，每轮刷新一次。
        ctx["turn_tokens"] = metermod.total(session.turn_spend)
        paused = yield from _run_tool_batch(session, ctx, list(reply.tool_calls))
        if paused:
            return

    session.messages.append(
        {"role": "user", "content": _force_answer(capped, lang)}
    )
    # 收口枪是**整轮里 messages 最长的一枪**（工具正文全在里面了），
    # 最该撞窗口的就是它。闸放在 append 之后，量的才是真正要发的那份。
    # 撞了同样退一批重打：stub_trailing_batch 会跳过刚 append 的那条假 user。
    while True:
        yield from _fit()
        yield {"type": "status", "phase": "thinking", "text": "苏格拉底在想…"}
        try:
            reply = yield from _create(with_tools=False)
            break
        except ProviderError as exc:
            if exc.code == PROVIDER_TOO_LONG and (yield from _shrink(exc)):
                continue
            yield {"type": "error", "message": str(exc)}
            return
    session.messages.append(reply.model_dump(exclude_none=True))
    yield _spend_ev()
    raw = (reply.content or "").strip()
    if not raw:
        yield {
            "type": "error",
            "message": msg("loop.exhausted", lang),
        }
        return
    yield from _finish_text(
        session, raw, usage, user_text=str(ctx.get("user_text") or ""), streamed=True
    )


def _tool_event(name: str, out: dict[str, Any]) -> dict[str, Any]:
    ev: dict[str, Any] = {
        "type": "tool",
        "name": name,
        "detail": str(out.get("detail") or ""),
        "resolved": str(out.get("resolved") or ""),
        "ok": bool(out.get("ok")),
        "preview": str(out.get("text") or "")[:200],
    }
    if out.get("line"):
        ev["line"] = int(out["line"])
    return ev


def _run_one(
    session: PenSession,
    ctx: dict[str, Any],
    *,
    name: str,
    args: dict[str, Any],
    tool_call_id: str,
) -> dict[str, Any]:
    out = dispatch(name, args, ctx)
    if name == "read_file":
        _remember_read(session, ctx, out)
    session.messages.append(
        {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": str(out.get("text") or ""),
        }
    )
    return _tool_event(name, out)


def _run_tool_batch(
    session: PenSession,
    ctx: dict[str, Any],
    calls: list[Any],
) -> Iterator[dict[str, Any]]:
    """执行到第一个需要审批的工具则暂停。yields events；return True 表示已 pause。"""
    paused = False
    read_before = {Path(p).expanduser().resolve() for p in (ctx.get("read_ok") or [])}
    for i, tc in enumerate(calls):
        name = tc.function.name
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError as exc:
            result = f"错误：工具参数不是合法 JSON：{exc}"
            yield {"type": "tool", "name": name, "detail": "", "ok": False, "preview": result[:200]}
            session.messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )
            continue
        verdict = decide(name)
        if verdict == "deny":
            result = f"错误：未知或禁用的工具 {name}"
            yield {"type": "tool", "name": name, "detail": "", "ok": False, "preview": result[:200]}
            session.messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result}
            )
            continue
        if verdict == "ask":
            original = Path(ctx["original_path"])
            raw = str(args.get("path") or "").strip() or str(original)
            tried = resolve_read_target(original, raw)
            blocked = read_first_block(name, tried, read_before)
            if blocked:
                yield {
                    "type": "tool",
                    "name": name,
                    "detail": raw,
                    "ok": False,
                    "preview": blocked[:200],
                }
                session.messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": blocked}
                )
                continue
            rest = []
            for later in calls[i + 1 :]:
                rest.append(
                    {
                        "id": later.id,
                        "name": later.function.name,
                        "arguments": later.function.arguments or "{}",
                    }
                )
            session.pending = {
                "id": uuid.uuid4().hex,
                "name": name,
                "args": args,
                "tool_call_id": tc.id,
                "rest": rest,
                "original_path": str(Path(ctx["original_path"]).expanduser().resolve()),
                # 跨书预算必须跨过这次暂停活下来（v0.10.3）。审批把一轮劈成两个
                # HTTP 请求，两边各建一个 ctx，而计数器就活在 ctx 里、不落盘——
                # 于是「翻 8 本书 → 提一次编辑 → 被拒 → 再翻 8 本」可以循环，
                # 而且模型自己就能触发暂停，不需要读者配合。
                # session.pending 本来就落盘，sidecar 重启也不丢。
                "cross_book_chars": int(ctx.get("cross_book_chars") or 0),
                "cross_book_reads": int(ctx.get("cross_book_reads") or 0),
            }
            yield {
                "type": "approval",
                "pending_id": session.pending["id"],
                "name": name,
                "args": args,
            }
            paused = True
            break
        yield {"type": "status", "phase": "tool", "text": f"在用 {name}…"}
        yield _run_one(session, ctx, name=name, args=args, tool_call_id=tc.id)
    return paused


def resume_chat(
    session: PenSession,
    original_path: Path,
    *,
    allow: bool,
    pending_id: str,
    llm: LLMConfig | None = None,
    extra_roots: list[Path] | None = None,
    allow_env_fallback: bool = True,
    lang: str = "zh",
    limits: RuntimeLimits | None = None,
) -> Iterator[dict[str, Any]]:
    apply_session_lang(session, lang)
    pending = session.pending
    if not pending or pending.get("id") != pending_id:
        yield {"type": "error", "message": msg("approval.expired", lang)}
        return
    cfg = llm if llm is not None else (resolve_llm() if allow_env_fallback else None)
    if cfg is None:
        yield {
            "type": "error",
            "message": msg("llm.missing_config_short", lang),
        }
        return
    frozen = str(pending.get("original_path") or "").strip()
    if frozen:
        frozen_p = Path(frozen).expanduser().resolve()
        if frozen_p != original_path.expanduser().resolve():
            tcid_bad = str(pending.get("tool_call_id") or "")
            result = msg("approval.path_changed", lang)
            session.messages.append(
                {"role": "tool", "tool_call_id": tcid_bad, "content": result}
            )
            session.pending = None
            yield {"type": "error", "message": result}
            return
    # 走 read_roots()，别在这里再拼一遍：v0.8.8 修的就是「两处各算一遍必然漂移」，
    # 自己留第二处说不过去。今天两者等价，改的是「下一次漂移的入口」。
    extra_roots = read_roots(extra_roots)
    ctx = _tool_ctx(session, original_path, extra_roots, limits)
    # 一轮 = 一份跨书预算。老会话的 pending 里没有这两个键 → 0 → 与改造前
    # 完全一致，不需要迁移。
    ctx["cross_book_chars"] = int(pending.get("cross_book_chars") or 0)
    ctx["cross_book_reads"] = int(pending.get("cross_book_reads") or 0)
    # 跨书那第三道闸读的是 turn_tokens。不种回去的话，续跑里那批 rest 调用
    # 看到的是 None → over(0, cap) 恒假 → 整道闸在审批之后失效一整批。
    # 它和上面两行是同一处语义的两半：那两行管「续多少」，这一行管「续什么」。
    ctx["turn_tokens"] = metermod.total(session.turn_spend)
    # 翻书轮数**故意不跟着恢复**，别当漏网之鱼修掉：
    # 跨书预算是「这一轮总共能花多少钱」，审批不该让它翻倍；轮数是「别让一次
    # 不受打断的循环跑飞」，而读者点那一下就是真实的断路器。跟着清零的话，
    # 暂停前用满轮数的会话在批准之后第 0 轮就被收口枪顶住，读者看到的是
    # 「批准完苏格拉底答得莫名其妙地敷衍」。
    name = str(pending.get("name") or "")
    args = dict(pending.get("args") or {})
    tcid = str(pending.get("tool_call_id") or "")
    run_it = bool(allow) and decide(name) == "ask"
    if run_it:
        yield {"type": "status", "phase": "writing", "text": "在改原文…"}
        try:
            yield _run_one(session, ctx, name=name, args=args, tool_call_id=tcid)
        except Exception as exc:
            result = f"错误：编辑失败：{type(exc).__name__}"
            session.messages.append(
                {"role": "tool", "tool_call_id": tcid, "content": result}
            )
            yield {
                "type": "tool",
                "name": name,
                "detail": str(args.get("path") or ""),
                "ok": False,
                "preview": result,
            }
    else:
        if allow:
            result = f"错误：不能执行未审批的工具 {name}"
        else:
            # 说「读者」不说角色名：拒绝这次编辑的是人，不是模型自己。
            # 原文写的是「苏格拉底不允许」——那等于让模型以为是它自己否掉的。
            result = "读者不允许这次编辑，原文没动。"
        yield {
            "type": "tool",
            "name": name,
            "detail": str(args.get("path") or ""),
            "ok": False,
            "preview": result,
        }
        session.messages.append(
            {"role": "tool", "tool_call_id": tcid, "content": result}
        )
    session.pending = None
    rest = list(pending.get("rest") or [])
    if rest:
        class _Fn:
            def __init__(self, n: str, a: str) -> None:
                self.name = n
                self.arguments = a

        class _Tc:
            def __init__(self, rec: dict[str, Any]) -> None:
                self.id = rec.get("id") or ""
                self.function = _Fn(str(rec.get("name") or ""), str(rec.get("arguments") or "{}"))

        paused = yield from _run_tool_batch(session, ctx, [_Tc(r) for r in rest])
        if paused:
            return
    yield from _agent_loop(session, ctx, cfg, lang)


def propose_fold_md(
    session: PenSession,
    llm: LLMConfig | None = None,
    allow_env_fallback: bool = True,
    lang: str = "zh",
) -> str:
    cfg = llm if llm is not None else (resolve_llm() if allow_env_fallback else None)
    if cfg is None:
        raise RuntimeError(msg("llm.missing_config_fold", lang))
    from openai import OpenAI, OpenAIError

    client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=120.0)
    prompt = f"""把下面苏格拉底刚讲的内容收成一个可插入手册的 Meta Instance。
只输出一个 <details> 块，不要 TL;DR/(a)(b)(c)，不要 〔回读〕。
<summary> 用「🔍 实例 N：一句话看点」（N 可先写 1，后端会改号）。
<details> 后空行，</summary> 后空行，</details> 前空行。
深度至少有一段 ```text 伪代码；有概念关系就加 ```mermaid。
教学代码函数要有 -> 返回类型。终端输出若不是实测，标「示意」。

解答正文：
{session.last_assistant}
"""
    try:
        resp = client.chat.completions.create(
            **llm_create_kwargs(
                cfg,
                messages=[
                    {"role": "system", "content": "你只输出一个合法的 <details> Markdown 块。"},
                    {"role": "user", "content": prompt},
                ],
            )
        )
    except (OpenAIError, OSError, TimeoutError) as exc:
        # 折叠块那一枪是纯文本，永远不带图。
        raise ProviderError(
            provider_error_message(exc, lang, cfg.model, sent_image=False)
        ) from exc
    # 写回这一枪也要记账。它落在会话上（请求线程独占，没有 probe 那条红线）。
    session.spend[metermod.KIND_FOLD] = metermod.merge(
        session.spend.get(metermod.KIND_FOLD),
        metermod.read_usage(getattr(resp, "usage", None)),
    )
    return (resp.choices[0].message.content or "").strip()
