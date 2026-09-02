"""思考正文的去留。

## 病灶（v0.22.4 读者报告）

节点原话：`The reasoning_content in the thinking mode must be passed back to
the API.` 而 `_StreamedMessage` 一直只数思考的字数、把正文扔掉，于是任何
「模型调了一次工具、下一枪接着说」的对话都在第二枪吃 400。

**这不是 Fast Mode 的病。** 纯基座、thinking 开着、翻一次手册，一模一样；
Fast Mode 只是让它必然发生（快模型总是先读、再交班）。它直到 v0.22.3 才
看得见——在那之前这条 400 被渲染成「请核对 Base URL、model 和 API Key」。

## 这份闸看的是「真发出去的那一枪」

不是看 session、不是看返回值。历史里有没有那个字段无所谓，**节点收到没有**
才是契约本身。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pen import reasoning as reasoningmod
from pen.config import LLMConfig
from pen.session import PenSession
from pen.tutor import stream_chat, thinking_on

FAST = LLMConfig("https://fast.example/v1", "ck_x", "celeris-1-magnus", "t", "high")


def _shots(monkeypatch, tmp_path, *, thinking: str, calls_tool: bool, **kw) -> list[dict]:
    """跑一轮，返回每一枪真发给节点的 kwargs。"""
    import openai
    from pen.tests.test_fast_loop import _Recorder, _book

    book = _book(tmp_path)
    script: list = []
    if calls_tool:
        script.append((None, [("read_file", {"path": str(book)})]))
    script.append(("答完了。" * 20, []))
    rec = _Recorder(script)
    monkeypatch.setattr(openai, "OpenAI", rec.client)
    sess = PenSession(session_id="r" * 32, handbook_id="demo")
    cfg = LLMConfig("http://x/v1", "sk", "deepseek-v4", "t", thinking)
    list(
        stream_chat(
            sess, book, "包", llm=cfg, extra_roots=[tmp_path], allow_env_fallback=False, **kw
        )
    )
    return rec.shots


def _echoed(shot: dict) -> list[int]:
    return [
        i
        for i, m in enumerate(shot["messages"])
        if m.get("role") == "assistant" and m.get(reasoningmod.FIELD)
    ]


def test_a_tool_call_turn_carries_its_reasoning_back(monkeypatch, tmp_path) -> None:
    """**这条红了就是读者报的那个 400。**

    第一枪调了工具，第二枪必须把那条 assistant 消息的思考正文一起带回去。
    """
    shots = _shots(monkeypatch, tmp_path, thinking="high", calls_tool=True)
    assert len(shots) >= 2
    assert _echoed(shots[1]), "第二枪没把思考正文带回去"


def test_thinking_off_sends_no_reasoning_at_all(monkeypatch, tmp_path) -> None:
    """反向闸：关着思考的节点没要过这个字段，别塞。"""
    shots = _shots(monkeypatch, tmp_path, thinking="off", calls_tool=True)
    assert all(not _echoed(s) for s in shots)


def test_the_reader_still_never_sees_the_thinking_text(monkeypatch, tmp_path) -> None:
    """留正文是为了回传，**不是为了显示**。1633 个分片吐进气泡是灾难。"""
    import openai
    from pen.tests.test_fast_loop import _Recorder, _book

    book = _book(tmp_path)
    rec = _Recorder([("答完了。" * 20, [])])
    monkeypatch.setattr(openai, "OpenAI", rec.client)
    sess = PenSession(session_id="r" * 32, handbook_id="demo")
    cfg = LLMConfig("http://x/v1", "sk", "deepseek-v4", "t", "high")
    evs = list(
        stream_chat(sess, book, "包", llm=cfg, extra_roots=[tmp_path], allow_env_fallback=False)
    )
    assert all(e["type"] != "token" or "想" not in e.get("text", "") for e in evs)
    # think 事件只报字数，不报正文
    assert all(set(e) <= {"type", "chars"} for e in evs if e["type"] == "think")


# ── 封顶与遗忘 ──────────────────────────────────────────────────────


def test_it_is_capped() -> None:
    """实测上千个分片，而整段历史每一枪都重发。不封顶就是按轮次翻倍烧钱。"""
    assert len(reasoningmod.clip("想" * 50_000)) == reasoningmod.MAX_CHARS


def test_a_new_user_turn_forgets_the_old_thinking(monkeypatch, tmp_path) -> None:
    """「必须回传」那条契约只约束当前这条工具链，上一轮的推理没人要。"""
    import openai
    from pen.tests.test_fast_loop import _Recorder, _book

    book = _book(tmp_path)
    rec = _Recorder([("第一轮答完。" * 10, []), ("第二轮答完。" * 10, [])])
    monkeypatch.setattr(openai, "OpenAI", rec.client)
    sess = PenSession(session_id="r" * 32, handbook_id="demo")
    cfg = LLMConfig("http://x/v1", "sk", "deepseek-v4", "t", "high")
    for _ in range(2):
        list(
            stream_chat(
                sess, book, "包", llm=cfg, extra_roots=[tmp_path], allow_env_fallback=False
            )
        )
    # 第二轮那一枪里，上一轮的 assistant 不该再带着思考正文
    assert not _echoed(rec.shots[-1])


def test_forget_is_free_when_there_is_nothing_to_forget() -> None:
    msgs = [{"role": "user", "content": "x"}]
    assert reasoningmod.forget(msgs) is msgs


def test_it_never_reaches_the_disk() -> None:
    """会话文件里塞几万字推理，只会让每次读盘变慢。"""
    sess = PenSession(session_id="r" * 32, handbook_id="demo")
    sess.messages.append({"role": "assistant", "content": "答", "reasoning_content": "想了很久"})
    assert "想了很久" not in json.dumps(sess.to_dict(), ensure_ascii=False)
    assert "想了很久" in str(sess.messages[-1])  # 内存里还在，下一枪要用


def test_the_window_estimate_counts_it() -> None:
    """它是**真上线的字节**，快模型那道硬窗口闸量的正是「这一枪有多大」。"""
    from pen.compact import message_chars

    plain = [{"role": "assistant", "content": "答"}]
    thick = [{"role": "assistant", "content": "答", "reasoning_content": "想" * 100}]
    assert message_chars(thick) - message_chars(plain) == 100


# ── thinking_on 是从 thinking_wire 推导的，不是第二张表 ──────────────


@pytest.mark.parametrize(
    "model,level,want",
    [
        ("deepseek-v4", "off", False),
        ("deepseek-v4", "high", True),
        # GLM-5.3 关不掉思考：UI 的 off 映射成 enabled + 最低档。**这一格正是
        # 另写一张表必然会写错的那一格。**
        ("glm-5.3", "off", True),
        ("glm-5.3", "high", True),
        ("celeris-1-magnus", "off", False),
        ("celeris-1-magnus", "high", True),
    ],
)
def test_thinking_on_follows_the_wire(model: str, level: str, want: bool) -> None:
    assert thinking_on(model, level) is want
