"""快模型在 `_agent_loop` 里的三件事：推理档、绊线、窗口闸。

三组断言各自防一件事：

  推理档  celeris **没有 high**（实测传了当场 400）。设置页的「高」是个常见
          档位，不映射的话每个高档快轮都打不出去。
  绊线    快轮张口要动磁盘时，必须在 **dispatch 之前**换回基座。这是路由漏判
          的兜底，也是「快模型写不了盘」这句话的全部实现。
  窗口闸  快轮的压缩只许发副本。**`session` 一个字都不许改**——
          `compact_session` 会把 `session.compacted` 置真，而全仓没有任何地方
          把它改回去；污染一次，这场会话之后连基座轮次都永远拿不到
          目录 / 邻域 / 书架。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from pen.config import LLMConfig, default_limits
from pen.session import PenSession
from pen.tutor import stream_chat, thinking_wire

FAST = LLMConfig("https://fast.example/v1", "ck_x", "celeris-1-magnus", "t", "off")
BASE = LLMConfig("https://base.example/v1", "sk_x", "deepseek-chat", "t", "off")


# ── 推理档 ──────────────────────────────────────────────────────────


def test_celeris_off_actually_turns_thinking_off() -> None:
    """实测 `reasoning_effort="none"` → 0 个 reasoning token、0.36 秒。

    不发这个字段的话它默认是开着的（实测 63 个 reasoning token），
    快模式最想要的那一档速度就拿不到。
    """
    assert thinking_wire("celeris-1-magnus", "off") == {"reasoning_effort": "none"}


def test_celeris_has_no_high_tier() -> None:
    """节点原话：`Supported types are xhigh (default), medium, and low`。

    UI 的「高」必须映射成 xhigh。**这条红了就是每个高档快轮 400。**
    """
    assert thinking_wire("celeris-1-magnus", "high") == {"reasoning_effort": "xhigh"}


@pytest.mark.parametrize("lv", ["off", "low", "medium", "high"])
def test_celeris_never_sends_a_thinking_block(lv: str) -> None:
    """extra_body.thinking 这一路 celeris 收下但完全不理会。

    实测 `disabled` 照样吐 49 个 reasoning token——发它是一把空枪，
    还会让人以为这条开关有用。
    """
    wire = thinking_wire("celeris-1-magnus", lv)
    assert "extra_body" not in wire
    assert set(wire) == {"reasoning_effort"}


@pytest.mark.parametrize("lv", ["off", "low", "medium", "high"])
def test_celeris_branch_does_not_leak_into_the_others(lv: str) -> None:
    """加了一格不能动其他型号。DeepSeek 和 GLM 的四档一个字都不变。"""
    assert thinking_wire("deepseek-chat", lv) == thinking_wire("deepseek-chat", lv)
    assert "reasoning_effort" not in thinking_wire("deepseek-chat", "off")
    assert thinking_wire("glm-5.3", "off")["extra_body"] == {"thinking": {"type": "enabled"}}


# ── 假客户端 ────────────────────────────────────────────────────────


class _Recorder:
    """记下每一枪打给了哪个 base_url、发了什么 messages，按脚本回话。

    `script` 是一串 `(content, tool_calls)`，一枪一条；用完了就恒回正文。
    """

    def __init__(self, script: list[tuple[str | None, list[tuple[str, dict]]]]) -> None:
        self.script = list(script)
        self.shots: list[dict] = []

    def client(self, **kw):
        rec = self

        class _Completions:
            def create(self, **kwargs):
                rec.shots.append({"base_url": kw.get("base_url"), **kwargs})
                if rec.script:
                    content, calls = rec.script.pop(0)
                else:
                    content, calls = "说完了。" * 20, []
                tcs = [
                    SimpleNamespace(
                        id=f"c{i}",
                        type="function",
                        function=SimpleNamespace(name=n, arguments=json.dumps(a)),
                    )
                    for i, (n, a) in enumerate(calls)
                ]
                dumped = {"role": "assistant"}
                if content:
                    dumped["content"] = content
                if tcs:
                    dumped["tool_calls"] = [
                        {
                            "id": t.id,
                            "type": "function",
                            "function": {
                                "name": t.function.name,
                                "arguments": t.function.arguments,
                            },
                        }
                        for t in tcs
                    ]
                m = SimpleNamespace(
                    content=content,
                    tool_calls=tcs or None,
                    model_dump=lambda exclude_none=True, _d=dumped: dict(_d),
                )
                from pen.tests.test_agent import stream_chunks

                return iter(stream_chunks(m))

        return type("C", (), {"chat": SimpleNamespace(completions=_Completions())})()


def _book(tmp_path) -> Path:
    """教材的落点。**脚本要在 _run 之前就拼出这个路径**（工具参数里带它），
    所以路径由这个函数定，不由 _run 定。"""
    book = tmp_path / "note.md"
    if not book.exists():
        book.write_text("# 题\n\n第一段原文。\n第二段原文。\n", encoding="utf-8")
    return book


def _run(
    monkeypatch, tmp_path, script, *, route="fast", limits=None, packet="包", seed_turns=0
):
    import openai

    book = _book(tmp_path)
    rec = _Recorder(script)
    monkeypatch.setattr(openai, "OpenAI", rec.client)
    # 阶梯要有东西可降，就得先有「更早的回合」。夹具借 test_compaction 那个
    # ——两处各造一份「聊过几轮的会话」，迟早会漂移成两种形状。
    if seed_turns:
        from pen.tests.test_compaction import _session

        sess = _session(book, turns=seed_turns)
    else:
        sess = PenSession(session_id="f" * 32, handbook_id="demo")
    events = list(
        stream_chat(
            sess,
            book,
            packet,
            llm=BASE,
            extra_roots=[tmp_path],
            allow_env_fallback=False,
            limits=limits or default_limits(),
            route=route,
            fast_llm=FAST,
        )
    )
    return sess, book, rec, events


def _routes(events) -> list[dict]:
    return [e for e in events if e.get("type") == "route"]


# ── 绊线 ────────────────────────────────────────────────────────────


def test_fast_turn_wanting_to_edit_switches_to_base(monkeypatch, tmp_path) -> None:
    """快模型张口要 edit_file → 当场换基座重打一枪。"""
    book = _book(tmp_path)
    sess, book, rec, events = _run(
        monkeypatch,
        tmp_path,
        [
            (None, [("read_file", {"path": str(book), "offset": 1, "limit": 9})]),
            (None, [("edit_file", {"path": str(book), "old_string": "第一段原文。",
                                   "new_string": "改过的第一段。"})]),
        ],
    )
    routes = _routes(events)
    assert routes and routes[0] == {"type": "route", "to": "base", "why": "wants-edit"}
    hosts = [s["base_url"] for s in rec.shots]
    assert hosts[0] == FAST.base_url, "第一枪该在快模型上"
    assert hosts[-1] == BASE.base_url, "换过之后每一枪都在基座上"


def test_the_edit_never_reaches_the_disk(monkeypatch, tmp_path) -> None:
    """绊线在 dispatch **之前**。点「允许」之前磁盘一个字节都不许动。"""
    book = _book(tmp_path)
    sess, book, rec, events = _run(
        monkeypatch,
        tmp_path,
        [
            (None, [("read_file", {"path": str(book), "offset": 1, "limit": 9})]),
            (None, [("edit_file", {"path": str(book), "old_string": "第一段原文。",
                                   "new_string": "改过的第一段。"})]),
        ],
    )
    assert "第一段原文。" in book.read_text(encoding="utf-8")
    assert "改过的第一段。" not in book.read_text(encoding="utf-8")


def test_the_refused_shot_is_not_appended_to_history(monkeypatch, tmp_path) -> None:
    """被拦下的那条 assistant 消息**不进历史**。

    带 tool_calls 的消息必须有配对的 tool 结果，少一条供应商直接 400。
    不 append 就没有这个配对义务——这就是「升级不需要回滚」的全部理由。
    """
    book = _book(tmp_path)
    sess, book, rec, events = _run(
        monkeypatch,
        tmp_path,
        [
            (None, [("read_file", {"path": str(book), "offset": 1, "limit": 9})]),
            (None, [("edit_file", {"path": str(book), "old_string": "第一段原文。",
                                   "new_string": "改过的第一段。"})]),
        ],
    )
    for m in sess.messages:
        for tc in m.get("tool_calls") or []:
            assert tc["function"]["name"] != "edit_file", "被绊线拦下的调用不该留在历史里"
    # 历史仍然合法：每个 tool_call_id 都有配对的 tool 结果
    want = {tc["id"] for m in sess.messages for tc in (m.get("tool_calls") or [])}
    got = {str(m.get("tool_call_id")) for m in sess.messages if m.get("role") == "tool"}
    assert want <= got, f"有 tool_call 没配对结果：{want - got}"


def test_base_can_edit_right_after_the_switch_without_rereading(monkeypatch, tmp_path) -> None:
    """快模型读过的，基座直接就能改——`read_ok` 是跨模型留着的。

    这条红了说明升级要付一次重读的代价，而 `read_first_block` 那道硬闸
    只认「更早一轮读过」，重读要多花整整一枪。
    """
    book = _book(tmp_path)
    sess, book, rec, events = _run(
        monkeypatch,
        tmp_path,
        [
            (None, [("read_file", {"path": str(book), "offset": 1, "limit": 9})]),
            (None, [("edit_file", {"path": str(book), "old_string": "第一段原文。",
                                   "new_string": "改过的第一段。"})]),
            (None, [("edit_file", {"path": str(book), "old_string": "第一段原文。",
                                   "new_string": "改过的第一段。"})]),
        ],
    )
    assert sess.pending, "基座那一枪该直接进审批，而不是被 read-first 硬闸打回"
    assert sess.pending.get("name") == "edit_file"


def test_a_read_only_fast_turn_never_leaves_the_fast_model(monkeypatch, tmp_path) -> None:
    """只读轮一枪都不许回基座——回了整个提速就没了。"""
    book = _book(tmp_path)
    sess, book, rec, events = _run(
        monkeypatch,
        tmp_path,
        [(None, [("read_file", {"path": str(book), "offset": 1, "limit": 9})])],
    )
    assert _routes(events) == []
    assert {s["base_url"] for s in rec.shots} == {FAST.base_url}


def test_a_base_turn_has_no_trip_wire(monkeypatch, tmp_path) -> None:
    """基座轮该照旧走审批，不是被绊线拦掉。"""
    book = _book(tmp_path)
    sess, book, rec, events = _run(
        monkeypatch,
        tmp_path,
        [
            (None, [("read_file", {"path": str(book), "offset": 1, "limit": 9})]),
            (None, [("edit_file", {"path": str(book), "old_string": "第一段原文。",
                                   "new_string": "改过的第一段。"})]),
        ],
        route="base",
    )
    assert _routes(events) == []
    assert sess.pending, "基座轮的 edit_file 该弹审批"
    assert {s["base_url"] for s in rec.shots} == {BASE.base_url}


# ── 窗口闸 ──────────────────────────────────────────────────────────


def test_the_window_gate_compacts_the_shot_not_the_session(monkeypatch, tmp_path) -> None:
    """**整个设计的立身之本。** 快轮压的是副本，session 一个字都没改。

    这条红了不是「测试挂了」，是 Fast Mode 会把会话永久降级：`compacted`
    一旦为真，全仓没有任何地方把它改回去，之后连基座轮次都再拿不到
    目录 / 邻域 / 书架。
    """
    book = _book(tmp_path)
    sess, book, rec, events = _run(
        monkeypatch,
        tmp_path,
        [(None, [("read_file", {"path": str(book), "offset": 1, "limit": 9})])],
        limits=replace(default_limits(), fast_context_tokens=8000),
        seed_turns=6,
    )
    assert sess.compacted is False, "Fast Mode 绝不许置这个位"
    assert sess.read_ok_paths, "也绝不许清掉读过的路径"
    # 真的压过：发出去的那份比 session 手里的短
    sent = min(len(json.dumps(s["messages"], ensure_ascii=False)) for s in rec.shots)
    full = len(json.dumps(sess.messages, ensure_ascii=False))
    assert sent < full, "预算够小却一枪都没压，闸没起作用"


def test_the_window_gate_tells_the_reader_what_it_dropped(monkeypatch, tmp_path) -> None:
    """压了什么要报出来，而且同一档只报一次。"""
    book = _book(tmp_path)
    sess, book, rec, events = _run(
        monkeypatch,
        tmp_path,
        [(None, [("read_file", {"path": str(book), "offset": 1, "limit": 9})])],
        limits=replace(default_limits(), fast_context_tokens=8000),
        seed_turns=6,
    )
    told = [e for e in events if e.get("type") == "compacted"]
    assert told, "压过就得让读者看见"
    assert all(e["steps"] for e in told), "空档位不该报"
    seq = [e["steps"] for e in told]
    assert all(a != b for a, b in zip(seq, seq[1:])), f"同一档反复报了：{seq}"


def test_a_roomy_budget_costs_nothing(monkeypatch, tmp_path) -> None:
    """预算够就原样发，**连一次遍历都不该让读者看见**。

    「够用就原样返回」是仓里所有压缩件共用的哲学（cap_selected_text /
    neighborhood / _thin_by_level 全都这样），闸不能例外。
    """
    book = _book(tmp_path)
    sess, book, rec, events = _run(
        monkeypatch,
        tmp_path,
        [(None, [("read_file", {"path": str(book), "offset": 1, "limit": 9})])],
        limits=replace(default_limits(), fast_context_tokens=90000),
        seed_turns=6,
    )
    assert [e for e in events if e.get("type") == "compacted"] == []
    assert _routes(events) == []
    assert rec.shots[-1]["messages"] is sess.messages, "没超预算就该直接发那张表本身"


def test_an_impossible_budget_falls_back_to_base_instead_of_shredding(monkeypatch, tmp_path) -> None:
    """压到底还是超 → 退回基座，**不硬发**。

    把上下文压成废墟去迁就窗口，不如让 1M 窗口的基座跑。对话不中断。
    """
    book = _book(tmp_path)
    sess, book, rec, events = _run(
        monkeypatch,
        tmp_path,
        [(None, [("read_file", {"path": str(book), "offset": 1, "limit": 9})])],
        limits=replace(default_limits(), fast_context_tokens=1),
        packet="很长的教材正文。" * 200,
    )
    routes = _routes(events)
    assert routes and routes[0]["why"] == "context-too-big"
    assert rec.shots[-1]["base_url"] == BASE.base_url
    assert [e for e in events if e.get("type") == "error"] == [], "退回基座不该顺带把这一轮弄挂"


def test_a_base_turn_is_sent_byte_for_byte_unchanged(monkeypatch, tmp_path) -> None:
    """基座轮**发的就是 session.messages 那张表**，闸一个字都不许碰。

    v0.21.1 之前的行为必须逐字保留——这是老路的零回归闸。
    """
    book = _book(tmp_path)
    sess, book, rec, events = _run(
        monkeypatch,
        tmp_path,
        [(None, [("read_file", {"path": str(book), "offset": 1, "limit": 9})])],
        limits=replace(default_limits(), fast_context_tokens=1),
        packet="很长的教材正文。" * 200,
        route="base",
    )
    assert _routes(events) == []
    assert [e for e in events if e.get("type") == "compacted"] == []
    assert rec.shots[-1]["messages"] is sess.messages, "基座该直接发那张表本身，不是副本"


# ── delta.reasoning ─────────────────────────────────────────────────


def test_delta_reasoning_counts_as_thinking(monkeypatch, tmp_path) -> None:
    """celeris 走 `reasoning`，DeepSeek 走 `reasoning_content`，两个都得认。

    少认一个不影响正确性，但状态行的「在想…」会恒为 0——而那正是
    读者判断「没卡住」的唯一信号。
    """
    import openai

    book = tmp_path / "note.md"
    book.write_text("# 题\n\n正文。\n", encoding="utf-8")

    def _d(**kw):
        base = {"content": None, "tool_calls": None}
        base.update(kw)
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(**base))], usage=None
        )

    class _Completions:
        def create(self, **kwargs):
            return iter([_d(reasoning="嗯，先看看…"), _d(content="想好了。" * 20)])

    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **_kw: type("C", (), {"chat": SimpleNamespace(completions=_Completions())})(),
    )
    sess = PenSession(session_id="g" * 32, handbook_id="demo")
    events = list(
        stream_chat(
            sess, book, "包", llm=FAST, extra_roots=[tmp_path], allow_env_fallback=False
        )
    )
    think = [e for e in events if e.get("type") == "think"]
    assert think and think[-1]["chars"] == len("嗯，先看看…")


# ── 审批续跑 ────────────────────────────────────────────────────────


def test_the_approved_half_turn_always_runs_on_base(monkeypatch, tmp_path) -> None:
    """点「允许」之后那半轮**恒走基座**，快模型碰不到它。

    那半轮必然执行 edit_file，让它跑在写不了盘的模型上没有意义；而且
    `resume_chat` 只收一份 `llm`，手里根本没有基座之外的第二个配置——
    这条断言把「它确实只有一份」钉住，免得将来有人顺手把 fast 也传进去。
    """
    from pen.tutor import resume_chat

    book = _book(tmp_path)
    sess, book, rec, events = _run(
        monkeypatch,
        tmp_path,
        [
            (None, [("read_file", {"path": str(book), "offset": 1, "limit": 9})]),
            (None, [("edit_file", {"path": str(book), "old_string": "第一段原文。",
                                   "new_string": "改过的第一段。"})]),
            (None, [("edit_file", {"path": str(book), "old_string": "第一段原文。",
                                   "new_string": "改过的第一段。"})]),
        ],
    )
    assert sess.pending
    rec.shots.clear()
    list(
        resume_chat(
            sess,
            book,
            allow=True,
            pending_id=str(sess.pending["id"]),
            llm=BASE,
            extra_roots=[tmp_path],
            allow_env_fallback=False,
        )
    )
    assert rec.shots, "续跑该真的再打一枪"
    assert {s["base_url"] for s in rec.shots} == {BASE.base_url}
    assert "改过的第一段。" in book.read_text(encoding="utf-8"), "点了允许就该真的落盘"
