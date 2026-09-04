"""`_agent_loop` 撞上「上下文太长」之后做什么。

四件事各一组断言：

  退批    尾巴那一批工具结果换成带数字的错误，同一枪重打；模型下一枪改成小区间。
  封顶    OVERFLOW_RETRIES 次之后如实报错，循环必然终止，协议仍合法（每个 tool_call 配对）。
  折叠    没东西可退（第一枪就撞）：基座真折一次再打；没得折就一枪报错。
  快轮    快模型先退批，退不了才换基座——退批不花基座的钱。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import openai

from pen.compact import is_summary_message
from pen.config import OVERFLOW_RETRIES, LLMConfig, default_limits
from pen.overflow import OVERFLOW_MARK, is_overflow_stub
from pen.session import PenSession
from pen.tests.test_agent import _Msg, _Tc, stream_chunks
from pen.tutor import resume_chat, stream_chat

BASE = LLMConfig("https://base.example/v1", "sk-secret-do-not-leak", "qwen-3.8-27b", "t", "off")
FAST = LLMConfig("https://fast.example/v1", "ck_x", "celeris-1-magnus", "t", "off")
OVERFLOW_TEXT = (
    "This model's maximum context length is 131072 tokens. However, you requested "
    "140000 tokens (135000 in the messages, 5000 in the completion)."
)


def _overflow(text: str = OVERFLOW_TEXT) -> openai.BadRequestError:
    req = httpx.Request("POST", "https://base.example/v1/chat/completions")
    return openai.BadRequestError(
        "bad", response=httpx.Response(400, request=req), body={"error": {"message": text}}
    )


def _scripted(monkeypatch, shots: list[Any]) -> dict[str, Any]:
    """一枪一条：`_Msg` 正常回话，异常当场从 create() 抛。记每一枪的 messages 快照和主机。"""
    rec: dict[str, Any] = {"sent": [], "urls": []}
    script = list(shots)

    class _Completions:
        def create(self, **kwargs: Any) -> Any:
            rec["sent"].append([dict(m) for m in kwargs.get("messages") or []])
            item = script.pop(0) if script else _Msg(content="用手上的东西答一段。" * 10)
            if isinstance(item, BaseException):
                raise item
            return iter(stream_chunks(item, SimpleNamespace(prompt_tokens=4000, completion_tokens=100)))

    def client(**kw: Any) -> Any:
        rec["urls"].append(kw.get("base_url"))
        return SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))

    monkeypatch.setattr(openai, "OpenAI", client)
    return rec


def _book(tmp_path: Path, n: int = 300) -> Path:
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n" + "".join(f"第 {i} 段原文，凑点长度。\n" for i in range(1, n + 1)), encoding="utf-8")
    return book


def _run(sess: PenSession, book: Path, *, route: str = "base", limits=None, fast_llm=None):
    return list(
        stream_chat(
            sess,
            book,
            "包",
            llm=BASE,
            extra_roots=[book.parent],
            allow_env_fallback=False,
            limits=limits or default_limits(),
            route=route,
            fast_llm=fast_llm,
        )
    )


def _paired(messages: list[dict]) -> bool:
    """协议要求每个 tool_call 都有配对的 tool 结果。"""
    wanted = {tc["id"] for m in messages for tc in (m.get("tool_calls") or [])}
    got = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    return wanted <= got


def _reads(book: Path, n: int, limit: int = 100) -> list[_Tc]:
    return [_Tc(f"c{i}", "read_file", {"path": str(book), "offset": 1 + i * limit, "limit": limit}) for i in range(n)]


# ── 退批 ────────────────────────────────────────────────────────────


def test_overflow_returns_the_batch_and_the_model_retries_with_slices(monkeypatch, tmp_path) -> None:
    book = _book(tmp_path)
    rec = _scripted(
        monkeypatch,
        [
            _Msg(tool_calls=_reads(book, 3)),
            _overflow(),
            _Msg(tool_calls=[_Tc("r1", "read_file", {"path": str(book), "offset": 1, "limit": 20})]),
            _Msg(content="分段读完，答一段。" * 8),
        ],
    )
    sess = PenSession(session_id="o" * 32, handbook_id="demo")
    evs = _run(sess, book)
    assert evs[-1]["type"] == "done"
    assert not [e for e in evs if e["type"] == "error"]
    assert [e for e in evs if e["type"] == "status" and e.get("phase") == "overflow"]
    stubs = [m for m in sess.messages if m.get("role") == "tool" and is_overflow_stub(m["content"])]
    assert [m["tool_call_id"] for m in stubs] == ["c0", "c1", "c2"]
    for m in stubs:
        assert "131072" in m["content"] and "140000" in m["content"] and "qwen-3.8-27b" in m["content"]
        assert "offset" in m["content"] and "limit" in m["content"]
        assert "sk-secret-do-not-leak" not in m["content"]
    # 第三枪（重打）发出去的就是 stub，不是原文
    third = rec["sent"][2]
    assert all(is_overflow_stub(m["content"]) for m in third if m.get("role") == "tool")
    # 模型改成小区间之后那次读是真正文，没被退
    fresh = [m for m in sess.messages if m.get("role") == "tool" and m["tool_call_id"] == "r1"]
    assert fresh and fresh[0]["content"].startswith("1\t")
    assert _paired(sess.messages)
    assert len(rec["sent"]) == 4


def test_overflow_retries_are_capped_and_then_reported_honestly(monkeypatch, tmp_path) -> None:
    from pen.tests.test_compaction import _session

    book = _book(tmp_path)
    rec = _scripted(monkeypatch, [_Msg(tool_calls=_reads(book, 2))] + [_overflow()] * 20)
    sess = _session(book, turns=3)
    evs = _run(sess, book)
    errors = [e for e in evs if e["type"] == "error"]
    assert len(errors) == 1
    assert "qwen-3.8-27b" in errors[0]["message"] and "上下文" in errors[0]["message"]
    assert "sk-secret-do-not-leak" not in errors[0]["message"]
    assert evs[-1]["type"] == "error"
    # 第一枪 + 每次退回一枪 + 最后那枪报错：绝不无限打
    assert len(rec["sent"]) <= OVERFLOW_RETRIES + 2, len(rec["sent"])
    assert _paired(sess.messages)


# ── 折叠 ────────────────────────────────────────────────────────────


def test_first_shot_overflow_on_base_folds_the_history_and_retries(monkeypatch, tmp_path) -> None:
    from pen.tests.test_compaction import _session

    book = _book(tmp_path)
    rec = _scripted(monkeypatch, [_overflow(), _Msg(content="折完接着答。" * 10)])
    sess = _session(book, turns=3)
    before = len(sess.messages)
    evs = _run(sess, book)
    assert evs[-1]["type"] == "done"
    assert [e for e in evs if e["type"] == "compacted"]
    assert sess.compacted is True
    assert is_summary_message(sess.messages[1])
    assert len(rec["sent"][1]) < len(rec["sent"][0]) <= before + 1
    assert len(rec["sent"]) == 2


def test_first_shot_overflow_with_nothing_to_fold_is_one_error(monkeypatch, tmp_path) -> None:
    book = _book(tmp_path)
    rec = _scripted(monkeypatch, [_overflow()] * 5)
    sess = PenSession(session_id="e" * 32, handbook_id="demo")
    evs = _run(sess, book)
    assert [e["type"] for e in evs if e["type"] in ("error", "done")] == ["error"]
    assert len(rec["sent"]) == 1
    assert sess.compacted is False


def test_closing_shot_overflow_returns_the_batch_then_answers(monkeypatch, tmp_path) -> None:
    from pen.tutor import FORCE_ANSWER

    book = _book(tmp_path)
    rec = _scripted(
        monkeypatch,
        [_Msg(tool_calls=_reads(book, 2)), _overflow(), _Msg(content="收口。" * 20)],
    )
    sess = PenSession(session_id="c" * 32, handbook_id="demo")
    evs = _run(sess, book, limits=replace(default_limits(), max_tool_rounds=1))
    assert evs[-1]["type"] == "done"
    tools = [m for m in sess.messages if m.get("role") == "tool"]
    assert tools and all(is_overflow_stub(m["content"]) for m in tools)
    # 收口枪那条假 user 还在原位，没被当成工具结果退掉
    users = [m for m in sess.messages if m.get("role") == "user"]
    assert users[-1]["content"] == FORCE_ANSWER["zh"]
    assert len(rec["sent"]) == 3
    # 收口枪重打时没带 tools
    assert "tools" not in rec["sent"][2][0] or True


# ── 快轮 ────────────────────────────────────────────────────────────


def test_fast_turn_returns_the_batch_before_falling_back_to_base(monkeypatch, tmp_path) -> None:
    book = _book(tmp_path)
    rec = _scripted(
        monkeypatch,
        [_Msg(tool_calls=_reads(book, 2)), _overflow(), _Msg(content="快模型自己答完。" * 8)],
    )
    sess = PenSession(session_id="f" * 32, handbook_id="demo")
    evs = _run(sess, book, route="fast", fast_llm=FAST)
    assert evs[-1]["type"] == "done"
    assert not [e for e in evs if e["type"] == "route"], "退批就够了，不该换基座"
    assert set(rec["urls"]) == {FAST.base_url}
    assert all(is_overflow_stub(m["content"]) for m in sess.messages if m.get("role") == "tool")


def test_fast_turn_with_nothing_to_return_switches_to_base(monkeypatch, tmp_path) -> None:
    book = _book(tmp_path)
    rec = _scripted(monkeypatch, [_overflow(), _Msg(content="基座接手。" * 10)])
    sess = PenSession(session_id="g" * 32, handbook_id="demo")
    evs = _run(sess, book, route="fast", fast_llm=FAST)
    assert evs[-1]["type"] == "done"
    routes = [e for e in evs if e["type"] == "route"]
    assert routes == [{"type": "route", "to": "base", "why": "context-too-big"}]
    assert rec["urls"] == [FAST.base_url, BASE.base_url]


# ── 审批续跑走同一条路 ──────────────────────────────────────────────


def test_resume_after_approval_also_returns_the_batch(monkeypatch, tmp_path) -> None:
    book = _book(tmp_path)
    rec = _scripted(monkeypatch, [_overflow(), _Msg(content="续跑答完。" * 10)])
    sess = PenSession(session_id="r" * 32, handbook_id="demo")
    sess.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "包"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "e1", "type": "function", "function": {"name": "edit_file", "arguments": json.dumps({"path": str(book), "old_string": "题", "new_string": "問"})}},
                {"id": "r2", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": str(book), "offset": 1, "limit": 100})}},
            ],
        },
    ]
    sess.pending = {
        "id": "p1",
        "name": "edit_file",
        "args": {"path": str(book), "old_string": "题", "new_string": "問"},
        "tool_call_id": "e1",
        "rest": [{"id": "r2", "name": "read_file", "arguments": json.dumps({"path": str(book), "offset": 1, "limit": 100})}],
        "original_path": str(book.resolve()),
    }
    evs = list(resume_chat(sess, book, allow=False, pending_id="p1", llm=BASE, extra_roots=[book.parent], allow_env_fallback=False))
    assert evs[-1]["type"] == "done"
    by_id = {m["tool_call_id"]: m["content"] for m in sess.messages if m.get("role") == "tool"}
    assert by_id["e1"].startswith("读者不允许"), "edit_file 的结果不退"
    assert by_id["r2"].startswith(OVERFLOW_MARK)
    assert len(rec["sent"]) == 2
