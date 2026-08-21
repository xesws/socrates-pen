from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from pen.config import LLMConfig
from pen.session import PenSession
from pen.tutor import (
    ProviderError,
    llm_create_kwargs,
    propose_fold_md,
    provider_error_message,
    stream_chat,
    usage_snapshot,
)


def _cfg() -> LLMConfig:
    return LLMConfig(
        base_url="https://api.deepseek.com",
        api_key="sk-secret-do-not-leak",
        model="deepseek-v4-flash",
        key_source="settings",
    )


def _status_exc(cls: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    req = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    return cls("boom", response=httpx.Response(status, request=req), body=None)


def _patch_openai_boom(monkeypatch, exc: Exception) -> None:
    """openai.OpenAI 换成假客户端：create 必抛 exc。exc 是 openai 的异常实例。"""

    class _BoomCompletions:
        def create(self, **_kwargs: Any) -> Any:
            raise exc

    class _BoomClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.chat = SimpleNamespace(completions=_BoomCompletions())

    monkeypatch.setattr(openai, "OpenAI", _BoomClient)


def test_usage_snapshot_is_last_call_not_a_sum() -> None:
    first = usage_snapshot(100, 20)
    second = usage_snapshot(250, 40)
    assert first == {
        "context_tokens": 100,
        "completion_tokens": 20,
        "prompt_tokens": 100,
    }
    assert second["context_tokens"] == 250
    assert second["completion_tokens"] == 40
    assert second["prompt_tokens"] == 250
    merged = {**first, **second}
    assert merged["context_tokens"] == 250
    assert merged["context_tokens"] != first["context_tokens"] + second["context_tokens"]


def test_llm_create_kwargs_thinking_off_vs_high() -> None:
    off = LLMConfig(
        base_url="https://api.deepseek.com",
        api_key="sk",
        model="deepseek-v4-flash",
        key_source="settings",
        thinking="off",
    )
    kw_off = llm_create_kwargs(off, messages=[{"role": "user", "content": "hi"}])
    assert "reasoning_effort" not in kw_off
    assert "extra_body" not in kw_off
    assert kw_off["model"] == "deepseek-v4-flash"
    high = LLMConfig(
        base_url="https://api.deepseek.com",
        api_key="sk",
        model="deepseek-v4-flash",
        key_source="settings",
        thinking="high",
    )
    kw_high = llm_create_kwargs(high, messages=[], tools=[{"type": "function"}])
    assert kw_high["reasoning_effort"] == "high"
    assert kw_high["extra_body"] == {"thinking": {"type": "enabled"}}
    assert kw_high["tools"] == [{"type": "function"}]


def test_stream_chat_error_points_to_settings(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("pen.tutor.resolve_llm", lambda *a, **k: None)
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    book = tmp_path / "book.md"
    book.write_text("# x\n", encoding="utf-8")
    events = list(stream_chat(sess, book, "packet"))
    assert events[0]["type"] == "error"
    assert "设置 → Socrates" in events[0]["message"]
    assert "环境变量" not in events[0]["message"]


def test_provider_error_message_maps_common_failures() -> None:
    auth = provider_error_message(_status_exc(openai.AuthenticationError, 401))
    assert "设置" in auth and "API Key" in auth
    denied = provider_error_message(_status_exc(openai.PermissionDeniedError, 403))
    assert "设置" in denied and "API Key" in denied
    bad = provider_error_message(_status_exc(openai.BadRequestError, 400))
    assert "Thinking" in bad and "off" in bad
    req = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    conn = provider_error_message(openai.APIConnectionError(request=req))
    assert "Base URL" in conn
    timeout = provider_error_message(openai.APITimeoutError(request=req))
    assert "Base URL" in timeout
    assert "Base URL" in provider_error_message(OSError(" refused"))
    assert "Base URL" in provider_error_message(TimeoutError())
    other = provider_error_message(_status_exc(openai.RateLimitError, 429))
    assert "RateLimitError" in other
    assert "sk-secret-do-not-leak" not in other


def test_stream_chat_auth_error_yields_error_event(monkeypatch, tmp_path: Path) -> None:
    _patch_openai_boom(monkeypatch, _status_exc(openai.AuthenticationError, 401))
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    book = tmp_path / "book.md"
    book.write_text("# x\n", encoding="utf-8")
    events = list(stream_chat(sess, book, "packet", llm=_cfg()))
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert "设置" in errors[0]["message"] and "API Key" in errors[0]["message"]
    assert "sk-secret-do-not-leak" not in errors[0]["message"]


def test_stream_chat_thinking_rejected_points_to_off(monkeypatch, tmp_path: Path) -> None:
    _patch_openai_boom(monkeypatch, _status_exc(openai.BadRequestError, 400))
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    book = tmp_path / "book.md"
    book.write_text("# x\n", encoding="utf-8")
    events = list(stream_chat(sess, book, "packet", llm=_cfg()))
    errors = [e for e in events if e["type"] == "error"]
    assert len(errors) == 1
    assert "Thinking" in errors[0]["message"]


def test_propose_fold_md_provider_error_raises_runtime_error(monkeypatch) -> None:
    _patch_openai_boom(monkeypatch, _status_exc(openai.AuthenticationError, 401))
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    sess.last_assistant = "讲了一段。"
    with pytest.raises(ProviderError, match="API Key") as excinfo:
        propose_fold_md(sess, llm=_cfg())
    assert isinstance(excinfo.value, RuntimeError)
    assert "sk-secret-do-not-leak" not in str(excinfo.value)


# ── v0.8.0：动态芯片的清洗 ──────────────────────────────────────────


def test_parse_dynamic_chips_returns_rich_dicts() -> None:
    from pen.tutor import parse_dynamic_chips

    reply = (
        "正文。\n\n<!--pen:chips\n"
        "- 七块积木里 messages 为什么不算文件？数据流上它凭什么独立一格？\n"
        "-->"
    )
    visible, chips = parse_dynamic_chips(reply)
    assert visible == "正文。"
    assert chips == [
        {
            "id": "q0",
            "kind": "quick",
            "text": "七块积木里 messages 为什么不算文件？数据流上它凭什么独立一格？",
        }
    ]


def test_parse_dynamic_chips_drops_placeholders_and_navigation() -> None:
    from pen.tutor import parse_dynamic_chips

    reply = (
        "答。\n<!--pen:chips\n"
        "- 下一问 1\n"
        "- 下一问 2\n"
        "- ...\n"
        "- 带我读一下本书玩法说明\n"
        "- 那步数上限设多少合适？任务没跑完就被熔断了怎么办？\n"
        "-->"
    )
    _visible, chips = parse_dynamic_chips(reply)
    assert [c["text"] for c in chips] == ["那步数上限设多少合适？任务没跑完就被熔断了怎么办？"]


def test_parse_dynamic_chips_caps_at_two() -> None:
    from pen.tutor import parse_dynamic_chips

    # 用真正不同的五条：只差一个数字的问题会被相互去重合并掉，
    # 那样测的就不是 limit 而是去重了。
    lines = "\n".join(
        "- " + q
        for q in (
            "为什么审批闸门要单独算一件，不能塞进工具里？",
            "那步数上限设多少合适？任务没跑完就被熔断了怎么办？",
            "白名单排在危险检测前面，危险命令会不会被静默放行？",
            "工具输出被截断之后，实习生看不到完整结果，会不会瞎猜？",
            "为什么第一个参考实现偏偏是 mini-swe-agent，而不是 LangChain？",
        )
    )
    _visible, chips = parse_dynamic_chips(f"答。\n<!--pen:chips\n{lines}\n-->")
    assert len(chips) == 2


def test_parse_dynamic_chips_without_block_is_untouched() -> None:
    from pen.tutor import parse_dynamic_chips

    visible, chips = parse_dynamic_chips("就是一段普通回复。")
    assert visible == "就是一段普通回复。"
    assert chips == []


def test_finish_text_emits_both_chip_shapes() -> None:
    """dynamic_chips 保持 list[str]（web/ 那个前端还在吃它），富格式走 dyn_chips。"""
    from pen.session import PenSession
    from pen.tutor import _finish_text

    sess = PenSession(session_id="s1", handbook_id="h1")
    raw = "答案很长" * 30 + "\n<!--pen:chips\n- 那步数上限设多少合适？没跑完被熔断怎么办？\n-->"
    done = [ev for ev in _finish_text(sess, raw, {"prompt_tokens": 1}) if ev["type"] == "done"][0]
    assert done["dynamic_chips"] == ["那步数上限设多少合适？没跑完被熔断怎么办？"]
    assert done["dyn_chips"][0]["kind"] == "quick"
    assert sess.last_chips == done["dyn_chips"]
    assert sess.has_substantive is True


def test_build_user_packet_keeps_whole_toc_and_lists_asked() -> None:
    """toc 以前是 [:80]，那本手册有 87 条——砍掉的正好是 Capstone 和附录。"""
    from pathlib import Path

    from pen import libraries
    from pen.tutor import build_user_packet

    # 自己把默认手册建进本测试的临时 .pen。此前这四条是靠「别的测试先跑过
    # 一遍 lifespan，把 swe-agent-v2 写进真实 .pen」才绿的——干净 checkout 上会红。
    libraries.ensure_default()
    idx = libraries.load_index("swe-agent-v2")
    packet, _anchor = build_user_packet(
        idx,
        Path(idx.original_path),
        selected_text="x",
        start_line=544,
        end_line=545,
        chip="socratic",
        user_text="",
        asked=["上一轮抛过的那个问题？"],
    )
    toc_seg = packet.split("[全书目录（不要整本背诵）]")[1].split("[框选]")[0]
    assert len([l for l in toc_seg.strip().splitlines() if l.strip()]) == len(idx.toc)
    assert "附录" in toc_seg
    assert "上一轮抛过的那个问题？" in packet


def test_packet_omits_the_shelf_block_when_there_is_only_one_book() -> None:
    """写「（无）」会让模型以为我们替它确认过没有别的书。整段不在时，
    它答「另一本我没读到」是对的——那本来就是实情。"""
    from pathlib import Path

    from pen import libraries
    from pen.tutor import build_user_packet

    # 自己把默认手册建进本测试的临时 .pen。此前这四条是靠「别的测试先跑过
    # 一遍 lifespan，把 swe-agent-v2 写进真实 .pen」才绿的——干净 checkout 上会红。
    libraries.ensure_default()
    idx = libraries.load_index("swe-agent-v2")
    packet, _ = build_user_packet(
        idx, Path(idx.original_path), selected_text="x",
        start_line=544, end_line=545, chip="free", user_text="",
    )
    assert "[工作目录里的其他教材]" not in packet


def test_packet_carries_the_shelf_with_paths_so_the_tutor_can_read_file() -> None:
    """v0.8.1 把跨教材整个挂在 probe 上，实时这条线一个字都没有。
    苏格拉底手里有 read_file、沙箱也放行，却不知道有那本书、更不知道路径。"""
    from pathlib import Path

    from pen import libraries
    from pen.tutor import build_user_packet

    # 自己把默认手册建进本测试的临时 .pen。此前这四条是靠「别的测试先跑过
    # 一遍 lifespan，把 swe-agent-v2 写进真实 .pen」才绿的——干净 checkout 上会红。
    libraries.ensure_default()
    idx = libraries.load_index("swe-agent-v2")
    shelf = "- 《另一本》  path: /tmp/vault/other.md\n  大纲：开篇 / 第一章"
    packet, _ = build_user_packet(
        idx, Path(idx.original_path), selected_text="x",
        start_line=544, end_line=545, chip="free",
        user_text="另一本讲什么", shelf=shelf,
    )
    assert "[工作目录里的其他教材]" in packet
    assert "/tmp/vault/other.md" in packet, "光给书名，苏格拉底只会去猜文件名"
    assert "read_file" in packet, "得明说怎么读，否则它照着大纲吹"
    # 书架排在目录之前：先说手上这本、库里还有哪些，再展开当前这本的目录。
    # 插在目录和框选之间会污染 test_build_user_packet_keeps_whole_toc 的切片。
    assert packet.index("[工作目录里的其他教材]") < packet.index("[全书目录（不要整本背诵）]")


def test_packet_drops_the_shelf_block_when_the_budget_eats_every_row() -> None:
    """预算截完一行不剩时，段头还在、条目是空的——等于向模型断言「有别的教材」
    然后一本都不给，它只能凭空编。宁可整段不出现。"""
    from pathlib import Path

    from pen import libraries, tutor
    from pen.tutor import build_user_packet

    # 自己把默认手册建进本测试的临时 .pen。此前这四条是靠「别的测试先跑过
    # 一遍 lifespan，把 swe-agent-v2 写进真实 .pen」才绿的——干净 checkout 上会红。
    libraries.ensure_default()
    idx = libraries.load_index("swe-agent-v2")
    huge = "- 《" + "长" * 4000 + "》  path: /x.md"
    packet, _ = build_user_packet(
        idx, Path(idx.original_path), selected_text="x",
        start_line=544, end_line=545, chip="free", user_text="", shelf=huge,
    )
    assert len(huge) > tutor.SHELF_CHARS
    assert "[工作目录里的其他教材]" not in packet


def test_cross_book_budget_never_touches_reading_the_current_handbook(tmp_path) -> None:
    """预算只管别的书。写回要先 read_file 看原文、翻本册别的 Level 都是本职，
    一次都不该受影响——回归断言：连读 30 次当前手册照常返回正文。"""
    from pen.agent.tools_impl import handle_read_file

    cur = tmp_path / "cur.md"
    cur.write_text("\n".join(f"当前手册第 {i} 行" for i in range(1, 400)), encoding="utf-8")
    ctx = {"original_path": cur, "extra_roots": [tmp_path]}
    for i in range(30):
        got = handle_read_file({"path": str(cur), "offset": 1, "limit": 200}, ctx)
        assert got["ok"] and "当前手册第 1 行" in got["text"], f"第 {i} 次就被预算挡了"
    assert ctx.get("cross_book_chars") is None, "读当前手册不该计入跨书预算"


def test_cross_book_budget_stops_the_翻书_loop_without_erroring(tmp_path) -> None:
    """v0.8.7 把书架接上之后，实测一句「另一本讲什么」触发 21 次 read_file、
    46912 字符，全部进 session.messages 并落盘，之后每一轮都重发。
    超预算要让它收敛，不能报错——报错模型会换个 offset 再试，那正是要止住的循环。"""
    from pen.agent.tools_impl import handle_read_file
    from pen.config import CROSS_BOOK_CHARS

    cur = tmp_path / "cur.md"
    cur.write_text("# 当前这本\n", encoding="utf-8")
    other = tmp_path / "other.md"
    other.write_text("\n".join(f"别的书第 {i} 行，这一行写得挺长的好把预算吃掉" for i in range(1, 4000)), encoding="utf-8")
    ctx = {"original_path": cur, "extra_roots": [tmp_path]}

    stopped_at = None
    for i in range(1, 40):
        got = handle_read_file({"path": str(other), "offset": 1, "limit": 400}, ctx)
        assert got["ok"] is True, "超预算不能报错——报错它会换个 offset 再试"
        if "额度用完" in got["text"]:
            stopped_at = i
            break
    assert stopped_at is not None, "翻了 39 次还没停"
    assert ctx["cross_book_chars"] >= CROSS_BOOK_CHARS
    # 必须是**字节**闸停的，不是次数闸兜住的——否则拿掉字节闸这条测试照样绿。
    from pen.config import CROSS_BOOK_READS

    assert ctx["cross_book_reads"] < CROSS_BOOK_READS, (
        f"次数先触顶了（{ctx['cross_book_reads']}），这条测不到字节闸"
    )
    # 单次 read_file 已被 MAX_OUTPUT 截到 5000，所以约等于 5 次满窗
    assert 2 <= stopped_at <= 12, f"预算档位不对：第 {stopped_at} 次才停"
    assert "没读到" in got["text"] or "只读了哪几段" in got["text"], "得告诉它怎么诚实收场"


def test_case_typo_in_handbook_path_is_not_charged_as_cross_book(tmp_path) -> None:
    """APFS 默认大小写不敏感：模型把 handbook_path 的大小写抄错，读到的是**同一个
    文件**，但 resolve() 不规范化大小写，字符串比较会判成跨书、白吃预算。
    比 inode 不比字符串。"""
    from pen.agent.tools_impl import handle_read_file

    book = tmp_path / "Handbook.md"
    book.write_text("# 当前这本\n", encoding="utf-8")
    typo = tmp_path / "handbook.md"
    if not typo.exists():  # 大小写敏感的文件系统上这条不适用
        import pytest

        pytest.skip("文件系统区分大小写，撞不到这个坑")

    ctx = {"original_path": book, "extra_roots": [tmp_path]}
    got = handle_read_file({"path": str(typo)}, ctx)
    assert got["ok"] and "当前这本" in got["text"]
    assert ctx.get("cross_book_chars") is None, "读的是同一个文件，不该计跨书预算"

    # 对照：真的别的文件要计
    other = tmp_path / "other.md"
    other.write_text("# 别的书\n", encoding="utf-8")
    handle_read_file({"path": str(other)}, ctx)
    assert ctx.get("cross_book_chars")


def test_cross_book_budget_also_caps_the_number_of_reads(tmp_path) -> None:
    """光封字节封不住轮数：模型每次只读一行，字节预算永远用不完，
    而翻书轮数上限（默认 100）一次不少，每轮 prompt 还要把整段 messages 重发一遍。"""
    from pen.agent.tools_impl import handle_read_file
    from pen.config import CROSS_BOOK_CHARS, CROSS_BOOK_READS

    cur = tmp_path / "cur.md"
    cur.write_text("# 当前这本\n", encoding="utf-8")
    other = tmp_path / "other.md"
    other.write_text("\n".join(f"第 {i} 行" for i in range(1, 500)), encoding="utf-8")
    ctx = {"original_path": cur, "extra_roots": [tmp_path]}

    stopped = None
    for i in range(1, 40):
        got = handle_read_file({"path": str(other), "offset": i, "limit": 1}, ctx)
        if "额度用完" in got["text"]:
            stopped = i
            break
    assert stopped == CROSS_BOOK_READS + 1, f"次数闸没生效，第 {stopped} 次才停"
    assert ctx["cross_book_chars"] < CROSS_BOOK_CHARS, "前提：每次只读一行，字节预算根本用不完"


# ── v0.10.1 闸值从 ctx 来 ───────────────────────────────────────


def test_cross_book_limit_comes_from_ctx_not_the_module(tmp_path) -> None:
    """闸值必须是**这一次请求**认下来的那个，不是进程默认。

    sidecar 是多会话共享进程：读死模块常量 = A 库的设置串到 B 库的会话上，
    而且设置页改完要重启才认。
    """
    from dataclasses import replace

    from pen.agent.tools_impl import handle_read_file
    from pen.config import default_limits

    cur = tmp_path / "cur.md"
    cur.write_text("# 当前这本\n", encoding="utf-8")
    other = tmp_path / "other.md"
    other.write_text("\n".join(f"别的书第 {i} 行" for i in range(1, 200)), encoding="utf-8")

    ctx = {
        "original_path": cur,
        "extra_roots": [tmp_path],
        "limits": replace(default_limits(), cross_book_reads=1),
    }
    first = handle_read_file({"path": str(other), "offset": 1, "limit": 5}, ctx)
    assert "额度用完" not in first["text"], "第一次该放过去"
    second = handle_read_file({"path": str(other), "offset": 6, "limit": 5}, ctx)
    assert second["ok"] is True, "超了也不报错"
    assert "额度用完" in second["text"], "把上限调成 1 之后第二次就该收敛"


def test_ctx_without_limits_falls_back_to_config_not_to_zero(tmp_path) -> None:
    """回落 0 会让所有手工拼 ctx 的调用方从「第 9 次停」静默变成「第 1 次停」。
    那种回归看起来像模型突然变笨了，没人会怀疑到闸上。"""
    from pen.agent.tools_impl import handle_read_file, limits_of
    from pen.config import default_limits

    assert limits_of({}) == default_limits()
    assert limits_of({"limits": "垃圾"}) == default_limits()

    cur = tmp_path / "cur.md"
    cur.write_text("# 当前这本\n", encoding="utf-8")
    other = tmp_path / "other.md"
    other.write_text("\n".join(f"别的书第 {i} 行" for i in range(1, 200)), encoding="utf-8")
    ctx = {"original_path": cur, "extra_roots": [tmp_path]}  # 没有 limits 键
    got = handle_read_file({"path": str(other), "offset": 1, "limit": 5}, ctx)
    assert "额度用完" not in got["text"], "没给 limits 时第一次必须照常读得到"


def test_max_tool_rounds_has_exactly_one_definition() -> None:
    """搬家之后 tutor 不留别名。留一个 `MAX_TOOL_ROUNDS = config.MAX_TOOL_ROUNDS`
    就是第二个定义点，下次有人改这个而不是那个，两边就分家了——
    本仓「两处各算一遍必然漂移」踩过三次。"""
    from pen import tutor

    assert not hasattr(tutor, "MAX_TOOL_ROUNDS"), "tutor 里不该再有这个名字"


def test_tool_rounds_limit_is_honoured(monkeypatch, tmp_path) -> None:
    """把轮数调小，工具循环就该早收口——而且收口那一枪**不带 tools**。"""
    from dataclasses import replace

    import openai
    from pen.config import LLMConfig, default_limits
    from pen.session import PenSession
    from pen.tutor import stream_chat

    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    seen: list[dict] = []

    class _Completions:
        def create(self, **kwargs):
            seen.append(kwargs)
            from types import SimpleNamespace

            if "tools" in kwargs:
                tc = SimpleNamespace(
                    id="c1", type="function",
                    function=SimpleNamespace(
                        name="read_file",
                        arguments=json.dumps({"path": str(book), "offset": 1, "limit": 5}),
                    ),
                )
                m = SimpleNamespace(
                    content=None, tool_calls=[tc],
                    model_dump=lambda exclude_none=True: {
                        "role": "assistant",
                        "tool_calls": [{"id": "c1", "type": "function",
                                        "function": {"name": "read_file",
                                                     "arguments": tc.function.arguments}}],
                    },
                )
            else:
                m = SimpleNamespace(
                    content="收口了。" * 30, tool_calls=None,
                    model_dump=lambda exclude_none=True: {"role": "assistant",
                                                          "content": "收口了。" * 30},
                )
            from pen.tests.test_agent import stream_chunks

            return iter(stream_chunks(m))

    monkeypatch.setattr(
        openai, "OpenAI",
        lambda **_kw: type("C", (), {"chat": SimpleNamespace(completions=_Completions())})(),
    )
    sess = PenSession(session_id="r" * 32, handbook_id="demo")
    list(stream_chat(
        sess, book, "packet",
        llm=LLMConfig("http://x", "sk", "m", "t", "off"),
        extra_roots=[tmp_path], allow_env_fallback=False,
        limits=replace(default_limits(), max_tool_rounds=3),
    ))
    with_tools = [k for k in seen if "tools" in k]
    assert len(with_tools) == 3, f"轮数上限设成 3，带 tools 的调用应恰好 3 次，实际 {len(with_tools)}"
    assert "tools" not in seen[-1], "收口那一枪不能带 tools，否则它还会接着翻"
