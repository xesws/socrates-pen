"""上下文超限：认出来、读出数字、把这一批工具结果退给模型。

分工照 `pen/vision.py`：这里只做**判定 + 给模型看的文案**，码表仍住在
`pen/tutor.py`（`provider_error_code` 是「节点这一下为什么失败」的唯一定义点），
给读者看的那句住在 `pen/i18n.py`。

为什么是**反应式**（撞了再退）而不是发枪前先量：基座的窗口我们不知道——
没有旋钮，也不该猜；快模型那一路有 `FastWindow` 是因为它的窗口是写死的
常量。节点的 400 是唯一可靠的信号。撞了之后做的事只有一件：把**这一枪之前
那整批工具结果**换成一段带数字的错误，让模型自己改成按行号区间读。
单次 `read_file` 被 `MAX_OUTPUT` 截到 5000 字符，真正撞窗口的从来是一批
（实测一枪 7～21 个 read_file）和一轮的累计，所以退的单位是「一批」。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from pen.compact import _is_force_answer, _line_span, _tool_calls_by_id
from pen.config import READ_LIMIT_DEFAULT
from pen.vision import content_text

# 报文里出现这些词就是「太长」。**只收明确说 token / 长度的**：`max_tokens`
# 是另一类错（输出预算填大了），`tokens per minute` 是限流，都不在这儿。
_OVERFLOW_WORDS = (
    "context length",
    "context_length",
    "context window",
    "maximum context",
    "prompt is too long",
    "input is too long",
    "too many tokens",
    "input token count",
    "prompt contains at least",
    "reduce the length of the messages",
    "exceeds the maximum number of tokens",
    "request too large",
)
# 命中这些的一律不是溢出，哪怕上面那张表也命中（Groq 的 413 限流报文里
# 就有 "Request too large"）。
_NOT_OVERFLOW = ("per minute", "per hour", "per day", "rate limit")
_OVERFLOW_CODES = frozenset({"context_length_exceeded"})

# 数字：「这一枪多少」和「上限多少」。顺序有讲究——Cerebras 那句
# "you requested 16384 output tokens and ... a total of at least 131073 tokens"
# 里 "you requested" 后面跟的是**输出预算**，所以 `total of at least` 排前面，
# 而 `you requested (\d+) tokens` 要求紧跟 tokens（中间夹 output 的不算）。
_GOT_RES = tuple(
    re.compile(p, re.I)
    for p in (
        r"total of at least (\d+)",
        r"resulted in (\d+)",
        r"you requested (\d+) tokens",
        r"input token count \((\d+)\)",
        r"prompt contains at least (\d+)",
        r"too long: (\d+)",
    )
)
_LIMIT_RES = tuple(
    re.compile(p, re.I)
    for p in (
        r"maximum context length is (\d+)",
        r"maximum number of tokens allowed \((\d+)\)",
        r">\s*(\d+) maximum",
        r"context (?:window|length) of (\d+)",
        r"limit(?:ed)? (?:to|of) (\d+)",
    )
)
# 比这小的数不是窗口，是报文里顺手带的别的数字（行号、状态码之类）。
_MIN_TOKENS = 1000

# 退回给模型的那段话的开头。**退过的不再退**靠它认；两种语言各一份。
OVERFLOW_MARK = "错误：上一枪被节点拒收"
OVERFLOW_MARK_EN = "Error: the endpoint rejected the last call"


def _wording(exc: BaseException) -> str:
    return " ".join(
        str(x).lower()
        for x in (exc, getattr(exc, "message", ""), getattr(exc, "body", ""))
        if x
    )


def _error_code(exc: BaseException) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("code") or "").lower()
    return ""


def looks_like_context_overflow(exc: BaseException) -> bool:
    """这个 400 / 413 是不是「上下文太长」。**只看报文，不看状态码以外的东西。**"""
    if getattr(exc, "status_code", None) not in (400, 413):
        return False
    bits = _wording(exc)
    if any(word in bits for word in _NOT_OVERFLOW):
        return False
    if _error_code(exc) in _OVERFLOW_CODES:
        return True
    return any(word in bits for word in _OVERFLOW_WORDS)


def _first_number(res: Sequence[re.Pattern[str]], text: str) -> int:
    for pat in res:
        m = pat.search(text)
        if m:
            n = int(m.group(1))
            return n if n >= _MIN_TOKENS else 0
    return 0


def overflow_numbers(exc: BaseException) -> tuple[int, int]:
    """(这一枪多少 token, 上限多少 token)。认不出的那一位给 0。"""
    text = _wording(exc)
    return _first_number(_GOT_RES, text), _first_number(_LIMIT_RES, text)


def is_overflow_stub(content: Any) -> bool:
    text = content_text(content)
    return text.startswith(OVERFLOW_MARK) or text.startswith(OVERFLOW_MARK_EN)


def overflow_stub_text(
    *,
    name: str,
    chars: int,
    span: tuple[int, int] | None,
    model: str,
    got: int,
    limit: int,
    lang: str,
    nth: int,
    cap: int,
    estimated: bool = False,
) -> str:
    """给模型看的那段话。**不含读者原话，不含节点原文，不含钥匙。**"""
    if lang == "en":
        got_s = f"about {got} tokens" if got > 0 else "a size the endpoint did not state"
        if estimated and got > 0:
            got_s += " (our estimate)"
        limit_s = f"{limit}" if limit > 0 else "not stated by the endpoint"
        span_s = f", lines {span[0]}–{span[1]}" if span else ""
        if name == "read_file":
            advice = (
                "Read in slices instead: call read_file on the same path with offset and "
                f"limit, at most {READ_LIMIT_DEFAULT} lines at a time, and only the passages "
                "the answer needs. What you already read earlier does not need re-reading."
            )
        elif name == "fetch":
            advice = "Do not fetch the whole page again; use a shorter page or answer from what you already have."
        else:
            advice = "Answer from what you already have."
        return (
            f"{OVERFLOW_MARK_EN} — context too long (this call was {got_s}; "
            f"{model}'s limit is {limit_s}). This {name} result ({chars} chars{span_s}) "
            f"has been dropped from the context. {advice} "
            f"Overflow {nth}/{cap} this turn; after that, answer from what you have."
        )
    got_s = f"约 {got} token" if got > 0 else "多少节点没说"
    if estimated and got > 0:
        got_s += "（估算）"
    limit_s = f"{limit}" if limit > 0 else "节点没说"
    span_s = f"，第 {span[0]}–{span[1]} 行" if span else ""
    if name == "read_file":
        advice = (
            "改成分段读：同一路径带 offset 和 limit，一次不超过 "
            f"{READ_LIMIT_DEFAULT} 行，只读回答要用的那几段；前面已经读到的不必重读。"
        )
    elif name == "fetch":
        advice = "别再取整页；换一个更短的页面，或用已经读到的内容作答。"
    else:
        advice = "用已经读到的内容作答。"
    return (
        f"{OVERFLOW_MARK}——上下文太长（这一枪{got_s}，{model} 的上限 {limit_s}）。"
        f"这条 {name} 的结果（{chars} 字符{span_s}）已经从上下文拿掉。{advice}"
        f"这是本轮第 {nth}/{cap} 次退回，用完就只能用手上的内容作答。"
    )


def stub_trailing_batch(
    messages: list[dict[str, Any]],
    *,
    model: str,
    got: int,
    limit: int,
    lang: str,
    nth: int,
    cap: int,
    estimated: bool = False,
) -> list[dict[str, Any]]:
    """把尾巴那一批工具结果**就地**换成退回文案，返回退掉了哪些。

    从尾巴往前：先跳过收口枪塞的那条假 user（`_is_force_answer`），再收连续
    的 `role == "tool"` 块。块里三种不退：已经退过的（开头是 OVERFLOW_MARK）、
    `edit_file` 的（那是写回记录，短，而且 `_eat_tool_result` 要靠它收
    writebacks）。尾巴不是 tool 块——第一枪就撞——返回空表，一个字不改。
    """
    end = len(messages)
    while end > 0 and messages[end - 1].get("role") == "user" and _is_force_answer(
        content_text(messages[end - 1].get("content"))
    ):
        end -= 1
    start = end
    while start > 0 and messages[start - 1].get("role") == "tool":
        start -= 1
    if start == end:
        return []
    calls = _tool_calls_by_id(messages[:start])
    out: list[dict[str, Any]] = []
    for m in messages[start:end]:
        content = content_text(m.get("content"))
        if is_overflow_stub(content):
            continue
        tid = str(m.get("tool_call_id") or "")
        meta = calls.get(tid) or {}
        name = str(meta.get("name") or "tool")
        if name == "edit_file":
            continue
        args = dict(meta.get("args") or {})
        span = _line_span(content) if name == "read_file" else None
        chars = len(content)
        m["content"] = overflow_stub_text(
            name=name,
            chars=chars,
            span=span,
            model=model,
            got=got,
            limit=limit,
            lang=lang,
            nth=nth,
            cap=cap,
            estimated=estimated,
        )
        out.append(
            {
                "tool_call_id": tid,
                "name": name,
                "path": str(args.get("path") or args.get("url") or ""),
                "chars": chars,
                "span": span,
            }
        )
    return out
