from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import openai

from pen.agent import READ_FIRST_MSG, TOOLS, decide, dispatch, read_first_block, schemas
from pen.agent.tools_impl import handle_edit_file
from pen.config import LLMConfig
from pen.session import PenSession
from pen.tutor import resume_chat, stream_chat


def test_read_allow_edit_ask_unknown_deny() -> None:
    assert decide("read_file") == "allow"
    assert decide("edit_file") == "ask"
    assert decide("bash") == "deny"
    assert decide("write_file") == "deny"


def test_read_first_block_requires_earlier_read() -> None:
    book = Path("/tmp/note.md").resolve()
    assert read_first_block("read_file", book, set()) is None
    assert read_first_block("edit_file", book, set()) == READ_FIRST_MSG
    assert read_first_block("edit_file", book, {book}) is None
    other = Path("/tmp/other.md").resolve()
    assert read_first_block("edit_file", book, {other}) == READ_FIRST_MSG


def test_schemas_only_read_and_edit() -> None:
    names = [s["function"]["name"] for s in schemas()]
    assert names == ["read_file", "edit_file"]
    assert "write_file" not in TOOLS
    assert "bash" not in TOOLS
    read_desc = next(s["function"]["description"] for s in schemas() if s["function"]["name"] == "read_file")
    edit_desc = next(s["function"]["description"] for s in schemas() if s["function"]["name"] == "edit_file")
    assert "行号" in read_desc
    assert "N\\t原文" in read_desc
    assert "先成功 read_file" in edit_desc
    assert "行号" in edit_desc


def test_edit_file_unique_replace(tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n内容。\n\n尾\n", encoding="utf-8")
    ctx = {"original_path": book, "extra_roots": [tmp_path], "handbook_id": ""}
    out = handle_edit_file(
        {"path": str(book), "old_string": "内容。", "new_string": "真内容"},
        ctx,
    )
    assert out["ok"] is True
    assert out["line"] == 3
    assert "真内容" in book.read_text(encoding="utf-8")
    assert book.read_text(encoding="utf-8").count("内容。") == 0


def test_edit_file_relative_path_same_as_original(tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一一段\n\n尾\n", encoding="utf-8")
    ctx = {"original_path": book, "extra_roots": [tmp_path], "handbook_id": ""}
    out = handle_edit_file(
        {"path": "note.md", "old_string": "唯一一段", "new_string": "换成了"},
        ctx,
    )
    assert out["ok"] is True
    assert "换成了" in book.read_text(encoding="utf-8")
    assert "唯一一段" not in book.read_text(encoding="utf-8")


def test_edit_file_rejects_non_unique_and_missing(tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    book.write_text("aa\naa\n", encoding="utf-8")
    ctx = {"original_path": book, "extra_roots": [tmp_path], "handbook_id": ""}
    twice = handle_edit_file({"path": str(book), "old_string": "aa", "new_string": "bb"}, ctx)
    assert twice["ok"] is False
    miss = handle_edit_file({"path": str(book), "old_string": "nope", "new_string": "x"}, ctx)
    assert miss["ok"] is False
    empty = handle_edit_file({"path": str(book), "old_string": "", "new_string": "x"}, ctx)
    assert empty["ok"] is False
    space = handle_edit_file({"path": str(book), "old_string": "\n", "new_string": "x"}, ctx)
    assert space["ok"] is False
    whole = handle_edit_file(
        {"path": str(book), "old_string": "aa\naa\n", "new_string": "zz"},
        ctx,
    )
    assert whole["ok"] is False
    assert book.read_text(encoding="utf-8") == "aa\naa\n"


def test_edit_file_rejects_line_number_prefix(tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n内容。\n\n尾\n", encoding="utf-8")
    ctx = {"original_path": book, "extra_roots": [tmp_path], "handbook_id": ""}
    numbered = handle_edit_file(
        {"path": str(book), "old_string": "3\t内容。", "new_string": "真内容"},
        ctx,
    )
    assert numbered["ok"] is False
    assert "行号" in numbered["text"]
    assert "内容。" in book.read_text(encoding="utf-8")


def test_edit_file_rejects_overlapping_old_string(tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    book.write_text("# t\n\naaa\n\n尾\n", encoding="utf-8")
    ctx = {"original_path": book, "extra_roots": [tmp_path], "handbook_id": ""}
    out = handle_edit_file({"path": str(book), "old_string": "aa", "new_string": "Z"}, ctx)
    assert out["ok"] is False
    assert book.read_text(encoding="utf-8") == "# t\n\naaa\n\n尾\n"


def test_edit_file_rejects_other_path(tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    other = tmp_path / "other.md"
    book.write_text("a\n", encoding="utf-8")
    other.write_text("b\n", encoding="utf-8")
    ctx = {"original_path": book, "extra_roots": [tmp_path], "handbook_id": ""}
    out = dispatch(
        "edit_file",
        {"path": str(other), "old_string": "b", "new_string": "c"},
        ctx,
    )
    assert out["ok"] is False
    assert other.read_text(encoding="utf-8") == "b\n"
    rel = dispatch(
        "edit_file",
        {"path": "other.md", "old_string": "b", "new_string": "c"},
        ctx,
    )
    assert rel["ok"] is False
    assert other.read_text(encoding="utf-8") == "b\n"
    escaped = dispatch(
        "edit_file",
        {"path": "../other.md", "old_string": "b", "new_string": "c"},
        {"original_path": book, "extra_roots": [tmp_path], "handbook_id": ""},
    )
    assert escaped["ok"] is False


def test_dispatch_unknown() -> None:
    out = dispatch("bash", {"command": "ls"}, {"original_path": Path("."), "extra_roots": []})
    assert out["ok"] is False


def test_edit_takes_pre_edit_snapshot(tmp_path: Path, monkeypatch) -> None:
    from pen import config, snapshots

    lib = tmp_path / "libraries"
    lib.mkdir()
    monkeypatch.setattr(config, "LIBRARIES_DIR", lib)
    monkeypatch.setattr(snapshots, "LIBRARIES_DIR", lib)
    book = tmp_path / "note.md"
    book.write_text("# t\n\nhello unique\n\n尾\n", encoding="utf-8")
    ctx = {"original_path": book, "extra_roots": [tmp_path], "handbook_id": "hid"}
    out = handle_edit_file(
        {"path": str(book), "old_string": "hello unique", "new_string": "changed"},
        ctx,
    )
    assert out["ok"] is True
    snaps = list((lib / "hid" / "snapshots").glob("*.md"))
    assert len(snaps) == 1
    assert "hello unique" in snaps[0].read_text(encoding="utf-8")
    assert "changed" in book.read_text(encoding="utf-8")
    assert "hello unique" not in book.read_text(encoding="utf-8")


def _cfg() -> LLMConfig:
    return LLMConfig(
        base_url="https://api.deepseek.com",
        api_key="sk-test",
        model="deepseek-v4-flash",
        key_source="settings",
    )


class _Fn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _Tc:
    def __init__(self, cid: str, name: str, arguments: dict[str, Any]) -> None:
        self.id = cid
        self.function = _Fn(name, json.dumps(arguments, ensure_ascii=False))


class _Msg:
    def __init__(self, content: str | None = None, tool_calls: list[_Tc] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, exclude_none: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {"role": "assistant"}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in self.tool_calls
            ]
        return d


def stream_chunks(
    msg: _Msg,
    usage: Any = None,
    *,
    content_slice: int = 7,
    arg_slice: int = 5,
) -> list[Any]:
    """把一条完整回复切成分片，模拟真实供应商的流。

    切得**碎且不对齐**是故意的：正文按 7 字一片、工具参数按 5 字一片，
    id 和 name 只在第一片给。真实供应商就是这样，而拼错一个字节
    json.loads 就炸、整轮对话失败。对齐的假分片测不出这个。
    """
    out: list[Any] = []

    def d(**kw: Any) -> Any:
        base = {"content": None, "reasoning_content": None, "tool_calls": None}
        base.update(kw)
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(**base))], usage=None)

    # 先来一段推理内容：真实模型 1.3 秒就开始流它，正文要等十几秒
    out.append(d(reasoning_content="想一下…"))
    text = msg.content or ""
    for i in range(0, len(text), content_slice):
        out.append(d(content=text[i : i + content_slice]))
    for idx, tc in enumerate(msg.tool_calls or []):
        args = tc.function.arguments or ""
        out.append(
            d(tool_calls=[SimpleNamespace(
                index=idx, id=tc.id,
                function=SimpleNamespace(name=tc.function.name, arguments=args[:arg_slice]),
            )])
        )
        for i in range(arg_slice, len(args), arg_slice):
            out.append(
                d(tool_calls=[SimpleNamespace(
                    index=idx, id=None,
                    function=SimpleNamespace(name=None, arguments=args[i : i + arg_slice]),
                )])
            )
    # 末片只带 usage，没有 choices——include_usage 就是这个形状
    out.append(SimpleNamespace(choices=[], usage=usage or SimpleNamespace(
        prompt_tokens=8, completion_tokens=3)))
    return out


def _patch_script(monkeypatch, replies: list[_Msg]) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    queue = list(replies)

    class _Completions:
        def create(self, **kwargs: Any) -> Any:
            seen.append(kwargs)
            return iter(stream_chunks(queue.pop(0)))

    class _Client:
        def __init__(self, **_kwargs: Any) -> None:
            self.chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setattr(openai, "OpenAI", _Client)
    return seen


def test_read_file_then_answer_no_write(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    seen = _patch_script(
        monkeypatch,
        [
            _Msg(
                tool_calls=[
                    _Tc("c1", "read_file", {"path": str(book), "offset": 1, "limit": 20}),
                ]
            ),
            _Msg(content="看过了，这是邻域里的那一句。\n<!--pen:chips\n- 下一问\n-->"),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    assert any(e["type"] == "tool" and e["name"] == "read_file" and e["ok"] for e in events)
    assert any(e["type"] == "done" for e in events)
    assert not any(e["type"] == "approval" for e in events)
    assert book.read_text(encoding="utf-8") == "# 题\n\n唯一段。\n"
    assert seen[0].get("tools")


def test_edit_file_without_read_is_blocked(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    original = "# 题\n\n旧段。\n"
    book.write_text(original, encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(
                tool_calls=[
                    _Tc(
                        "c1",
                        "edit_file",
                        {"path": str(book), "old_string": "旧段。", "new_string": "新段。"},
                    )
                ]
            ),
            _Msg(content="好，我先去 read。\n<!--pen:chips\n- 下一问\n-->"),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    tools = [e for e in events if e["type"] == "tool" and e["name"] == "edit_file"]
    assert tools and tools[0]["ok"] is False
    assert "必须先成功 read_file" in str(tools[0]["preview"])
    assert not any(e["type"] == "approval" for e in events)
    assert sess.pending is None
    assert book.read_text(encoding="utf-8") == original
    assert any(e["type"] == "done" for e in events)


def test_read_round_then_edit_pauses(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    original = "# 题\n\n旧段。\n"
    book.write_text(original, encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(
                tool_calls=[
                    _Tc("c1", "read_file", {"path": str(book), "offset": 1, "limit": 20}),
                ]
            ),
            _Msg(
                tool_calls=[
                    _Tc(
                        "c2",
                        "edit_file",
                        {"path": str(book), "old_string": "旧段。", "new_string": "新段。"},
                    )
                ]
            ),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    assert any(e["type"] == "tool" and e["name"] == "read_file" and e["ok"] for e in events)
    approvals = [e for e in events if e["type"] == "approval"]
    assert len(approvals) == 1
    assert book.read_text(encoding="utf-8") == original
    assert sess.pending is not None
    assert any(str(book) in p or Path(p).name == "note.md" for p in sess.read_ok_paths)


def test_resume_allow_writes_then_finishes(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n旧段。\n", encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(
                tool_calls=[
                    _Tc("c1", "read_file", {"path": str(book), "offset": 1, "limit": 20}),
                ]
            ),
            _Msg(
                tool_calls=[
                    _Tc(
                        "c2",
                        "edit_file",
                        {"path": str(book), "old_string": "旧段。", "new_string": "新段。"},
                    )
                ]
            ),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    list(stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False))
    pid = sess.pending["id"]
    _patch_script(
        monkeypatch,
        [_Msg(content="已经按你批准写进去了。\n<!--pen:chips\n- 下一问\n-->")],
    )
    events = list(
        resume_chat(
            sess,
            book,
            allow=True,
            pending_id=pid,
            llm=_cfg(),
            extra_roots=[tmp_path],
            allow_env_fallback=False,
        )
    )
    assert "新段。" in book.read_text(encoding="utf-8")
    assert "旧段。" not in book.read_text(encoding="utf-8")
    assert any(e["type"] == "tool" and e["name"] == "edit_file" and e["ok"] for e in events)
    assert any(e["type"] == "done" for e in events)
    assert sess.pending is None


def test_resume_deny_does_not_write(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    original = "# 题\n\n旧段。\n"
    book.write_text(original, encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(
                tool_calls=[
                    _Tc("c1", "read_file", {"path": str(book), "offset": 1, "limit": 20}),
                ]
            ),
            _Msg(
                tool_calls=[
                    _Tc(
                        "c2",
                        "edit_file",
                        {"path": str(book), "old_string": "旧段。", "new_string": "新段。"},
                    )
                ]
            ),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    list(stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False))
    pid = sess.pending["id"]
    _patch_script(
        monkeypatch,
        [_Msg(content="好，原文没动。\n<!--pen:chips\n- 下一问\n-->")],
    )
    events = list(
        resume_chat(
            sess,
            book,
            allow=False,
            pending_id=pid,
            llm=_cfg(),
            extra_roots=[tmp_path],
            allow_env_fallback=False,
        )
    )
    assert book.read_text(encoding="utf-8") == original
    denied = [e for e in events if e["type"] == "tool" and e["name"] == "edit_file"]
    assert denied and denied[0]["ok"] is False
    assert any(e["type"] == "done" for e in events)


def test_failed_read_does_not_unlock_edit(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    original = "# 题\n\n旧段。\n"
    book.write_text(original, encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(tool_calls=[_Tc("c1", "read_file", {"path": "/etc/passwd"})]),
            _Msg(
                tool_calls=[
                    _Tc(
                        "c2",
                        "edit_file",
                        {"path": str(book), "old_string": "旧段。", "new_string": "新段。"},
                    )
                ]
            ),
            _Msg(content="读失败了，不能改。\n<!--pen:chips\n- 下一问\n-->"),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    assert any(e["type"] == "tool" and e["name"] == "read_file" and e["ok"] is False for e in events)
    edits = [e for e in events if e["type"] == "tool" and e["name"] == "edit_file"]
    assert edits and edits[0]["ok"] is False
    assert not any(e["type"] == "approval" for e in events)
    assert sess.pending is None
    assert book.read_text(encoding="utf-8") == original


def test_write_file_denied_then_continues(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    original = "keep\n"
    book.write_text(original, encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(tool_calls=[_Tc("c1", "write_file", {"path": str(book), "content": "hack"})]),
            _Msg(content="我没有 write_file。\n<!--pen:chips\n- 下一问\n-->"),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    tools = [e for e in events if e["type"] == "tool"]
    assert tools and tools[0]["ok"] is False
    assert book.read_text(encoding="utf-8") == original
    assert any(e["type"] == "done" for e in events)


def test_read_then_edit_in_one_batch_bounces_edit(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    original = "旧段。\n"
    book.write_text(original, encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(
                tool_calls=[
                    _Tc("c1", "read_file", {"path": str(book), "offset": 1, "limit": 10}),
                    _Tc(
                        "c2",
                        "edit_file",
                        {"path": str(book), "old_string": "旧段。", "new_string": "新段。"},
                    ),
                ]
            ),
            _Msg(content="看到读结果了，下一轮再 edit。\n<!--pen:chips\n- 下一问\n-->"),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    assert any(e["type"] == "tool" and e["name"] == "read_file" and e["ok"] for e in events)
    edits = [e for e in events if e["type"] == "tool" and e["name"] == "edit_file"]
    assert edits and edits[0]["ok"] is False
    assert not any(e["type"] == "approval" for e in events)
    assert sess.pending is None
    assert book.read_text(encoding="utf-8") == original
    assert any(e["type"] == "done" for e in events)


def test_bash_denied_in_stream(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    original = "# t\n\nkeep\n"
    book.write_text(original, encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(tool_calls=[_Tc("c1", "bash", {"command": "rm -rf /"})]),
            _Msg(content="没有 bash。\n<!--pen:chips\n- 下一问\n-->"),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    tools = [e for e in events if e["type"] == "tool"]
    assert tools and tools[0]["ok"] is False
    assert book.read_text(encoding="utf-8") == original
    assert any(e["type"] == "done" for e in events)


def test_second_edit_in_rest_asks_again(monkeypatch, tmp_path: Path) -> None:
    book = tmp_path / "note.md"
    original = "# t\n\n第一段。\n\n第二段。\n"
    book.write_text(original, encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(
                tool_calls=[
                    _Tc("c0", "read_file", {"path": str(book), "offset": 1, "limit": 20}),
                ]
            ),
            _Msg(
                tool_calls=[
                    _Tc(
                        "c1",
                        "edit_file",
                        {"path": str(book), "old_string": "第一段。", "new_string": "一改。"},
                    ),
                    _Tc(
                        "c2",
                        "edit_file",
                        {"path": str(book), "old_string": "第二段。", "new_string": "二改。"},
                    ),
                ]
            ),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    list(stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False))
    assert sess.pending is not None
    pid = sess.pending["id"]
    events = list(
        resume_chat(
            sess,
            book,
            allow=True,
            pending_id=pid,
            llm=_cfg(),
            extra_roots=[tmp_path],
            allow_env_fallback=False,
        )
    )
    assert "一改。" in book.read_text(encoding="utf-8")
    assert "第二段。" in book.read_text(encoding="utf-8")
    assert any(e["type"] == "approval" and e["name"] == "edit_file" for e in events)
    assert sess.pending is not None
    assert sess.pending["args"]["old_string"] == "第二段。"
    assert not any(e["type"] == "done" for e in events)


# ── v0.10.0 计量 ────────────────────────────────────────────────


def test_spend_event_fires_once_per_llm_call_and_only_grows(monkeypatch, tmp_path: Path) -> None:
    """实时计量的整条链：每打一枪就报一次，数字只增不减。

    读者要看的就是这个——翻书翻到一半时数字还在往上爬，那是失控循环
    唯一看得见的信号。
    """
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(tool_calls=[_Tc("c1", "read_file", {"path": str(book), "offset": 1, "limit": 20})]),
            _Msg(tool_calls=[_Tc("c2", "read_file", {"path": str(book), "offset": 1, "limit": 20})]),
            _Msg(content="看过了。" * 30),
        ],
    )
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    spends = [e for e in events if e["type"] == "spend"]
    # 假 client 每枪报 prompt_tokens=8 / completion_tokens=3
    # 上一行钉死了确切序列，再断言一次「有序」是空转的，不写。
    assert [s["turn"] for s in spends] == [11, 22, 33], "三枪，每枪 11，逐枪累加"
    assert sess.spend["chat"]["calls"] == 3
    assert sess.turn_spend["in_tokens"] == 24


def test_turn_spend_survives_the_approval_pause(monkeypatch, tmp_path: Path) -> None:
    """一轮从 /v1/chat 开始，到 /v1/chat/approve 那一枪结束，中间隔着两个
    HTTP 请求和一次落盘。turn_spend 必须跨过去——它在会话上而不是在
    _agent_loop 的闭包里，就是为了这个。"""
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n第二段。\n", encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(tool_calls=[_Tc("r1", "read_file", {"path": str(book), "offset": 1, "limit": 20})]),
            _Msg(tool_calls=[_Tc("e1", "edit_file", {"path": str(book), "old_string": "第二段。", "new_string": "改过。"})]),
        ],
    )
    sess = PenSession(session_id="p" * 32, handbook_id="demo")
    list(stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False))
    assert sess.pending is not None
    before = dict(sess.turn_spend)
    assert before["calls"] == 2

    _patch_script(monkeypatch, [_Msg(content="改完了。" * 30)])
    list(
        resume_chat(
            sess, book, allow=True, pending_id=sess.pending["id"],
            llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False,
        )
    )
    assert sess.turn_spend["calls"] == 3, "续跑那一枪要加在同一轮上，不是从头数"
    assert sess.turn_spend["in_tokens"] > before["in_tokens"]


def test_usage_and_spend_are_two_different_things(monkeypatch, tmp_path: Path) -> None:
    """done.usage 是「最后一枪」的快照（此刻窗口占多大），
    spend 是累加器（一共花了多少）。同一轮里这两个数必须不同，
    否则说明有人把它们合并了。"""
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(tool_calls=[_Tc("c1", "read_file", {"path": str(book), "offset": 1, "limit": 20})]),
            _Msg(content="看过了。" * 30),
        ],
    )
    sess = PenSession(session_id="u" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False)
    )
    done = next(e for e in events if e["type"] == "done")
    assert done["usage"]["prompt_tokens"] == 8, "最后一枪的窗口占用"
    assert sess.spend["chat"]["in_tokens"] == 16, "两枪累加"


def test_one_turn_gets_one_cross_book_budget_even_across_an_approval(monkeypatch, tmp_path: Path) -> None:
    """审批把一轮劈成两个 HTTP 请求，两边各建一个 ctx。计数器活在 ctx 里、
    不落盘，于是「翻几本书 → 提一次编辑 → 被拒 → 再翻几本」可以循环。
    模型自己就能触发暂停，不需要读者配合——所以这不是理论上的洞。

    盯的是被改的那两行本身：暂停时冻进 pending、续跑时种回 ctx。
    走完整脚本去测会对「模型还要几轮才收口」过敏，那是另一件事。
    """
    from pen import tutor

    cur = tmp_path / "cur.md"
    cur.write_text("# 当前这本\n\n第二段。\n", encoding="utf-8")
    other = tmp_path / "other.md"
    other.write_text("\n".join(f"别的书第 {i} 行" for i in range(1, 200)), encoding="utf-8")

    _patch_script(
        monkeypatch,
        [
            # 先翻一次别的书（吃掉跨书额度）
            _Msg(tool_calls=[_Tc("r1", "read_file",
                                 {"path": str(other), "offset": 1, "limit": 5})]),
            # 再读一次当前这本——edit_file 有 read-first 硬闸，不先读就提编辑会被挡
            _Msg(tool_calls=[_Tc("r2", "read_file",
                                 {"path": str(cur), "offset": 1, "limit": 20})]),
            _Msg(tool_calls=[_Tc("e1", "edit_file",
                                 {"path": str(cur), "old_string": "第二段。",
                                  "new_string": "改过。"})]),
        ],
    )
    sess = PenSession(session_id="b" * 32, handbook_id="demo")
    list(stream_chat(sess, cur, "packet", llm=_cfg(), extra_roots=[tmp_path],
                     allow_env_fallback=False))
    assert sess.pending is not None, "模型该提出编辑并暂停"
    spent = int(sess.pending.get("cross_book_chars") or 0)
    assert sess.pending.get("cross_book_reads") == 1, "暂停时要把用掉的次数冻进去"
    assert spent > 0, "字符数也要冻进去"

    # 续跑那半轮：ctx 必须**继承**这两个数，而不是从 0 重来
    seen: dict = {}
    real = tutor._tool_ctx

    def spy(session, original_path, extra_roots, limits=None):
        ctx = real(session, original_path, extra_roots, limits)
        seen["ctx"] = ctx
        return ctx

    monkeypatch.setattr(tutor, "_tool_ctx", spy)
    _patch_script(monkeypatch, [_Msg(content="只读到一段，剩下的没看。" * 8)])
    list(resume_chat(sess, cur, allow=False, pending_id=sess.pending["id"],
                     llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False))
    assert seen["ctx"]["cross_book_reads"] == 1, "一轮 = 一份预算，审批不该让它翻倍"
    assert seen["ctx"]["cross_book_chars"] == spent


def test_tool_rounds_are_deliberately_not_carried_across_approval() -> None:
    """同类泄漏，但**故意不修**——别当漏网之鱼修掉。

    跨书预算是「这一轮总共能花多少钱」，审批不该让它翻倍；
    轮数是「别让一次不受打断的循环跑飞」，而读者点那一下就是真实的断路器。
    跟着清零的话，暂停前用满轮数的会话在批准之后第 0 轮就被收口枪顶住，
    读者看到的是「批准完苏格拉底答得莫名其妙地敷衍」。
    """
    import inspect

    from pen import tutor

    src = inspect.getsource(tutor.resume_chat)
    assert "cross_book_chars" in src, "跨书预算要继承"
    assert "轮数" in src, "为什么不继承轮数，理由必须写在代码里"


# ── v0.10.6 三个 token 上限 ────────────────────────────────────


def _capped_script(
    monkeypatch, book: Path, tool_rounds: int = 2, per_shot: tuple[int, int] = (8, 3)
) -> list[dict[str, Any]]:
    """一个更忠实的假 client：**不给 tools 时一定回文本**。

    用固定队列会让这批测试对「第几枪收口」过敏——收口那一枪如果恰好领到
    队列里的工具调用消息，content 就是空的，读者拿到的是 error 而不是答案。
    真实模型在不带 tools 的那一枪永远吐正文，照着这个来。
    """
    seen: list[dict[str, Any]] = []
    left = [tool_rounds]

    class _Completions:
        def create(self, **kwargs: Any) -> Any:
            seen.append(kwargs)
            if "tools" in kwargs and left[0] > 0:
                left[0] -= 1
                msg = _Msg(tool_calls=[
                    _Tc(f"c{left[0]}", "read_file",
                        {"path": str(book), "offset": 1, "limit": 20})
                ])
            else:
                msg = _Msg(content="看过了，讲一段。" * 20)
            return iter(stream_chunks(msg, SimpleNamespace(
                prompt_tokens=per_shot[0], completion_tokens=per_shot[1])))

    monkeypatch.setattr(
        openai, "OpenAI",
        lambda **_kw: SimpleNamespace(chat=SimpleNamespace(completions=_Completions())),
    )
    return seen


def test_turn_cap_zero_is_byte_for_byte_identical(monkeypatch, tmp_path: Path) -> None:
    """默认 0 = 不限时，**发给供应商的字节必须完全一样**。

    只断言「可见事件相同」是不够的——那证明不了我们没多打一枪、没改 prompt。
    这里比的是 create() 收到的 kwargs 序列。
    """
    from dataclasses import replace

    from pen.config import default_limits

    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")

    def run(limits) -> tuple[list, list]:
        seen = _capped_script(monkeypatch, book)
        sess = PenSession(session_id="z" * 32, handbook_id="demo")
        evs = list(stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path],
                               allow_env_fallback=False, limits=limits))
        return seen, evs

    seen_a, ev_a = run(None)
    seen_b, ev_b = run(replace(default_limits(), max_tokens_chat=0))
    assert seen_a == seen_b, "发给供应商的 kwargs 序列必须逐字节相同"
    assert ev_a == ev_b, "可见事件也必须一样"
    assert len(seen_a) == 3, "两轮工具 + 一枪收尾，一枪不多一枪不少"


def test_turn_cap_breaks_to_a_real_answer_not_an_error(monkeypatch, tmp_path: Path) -> None:
    """撞线之后读者必须拿到**一个真答案**。

    直接返回的话，此刻 messages 末尾是一条纯 tool_calls 消息、content 是空的
    ——读者花了钱一个字没拿到。硬报错则会诱发模型换个参数重试，反而更贵。
    """
    from dataclasses import replace

    from pen.config import default_limits
    from pen.tutor import FORCE_ANSWER, FORCE_ANSWER_BUDGET

    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    # 备足 5 轮工具，看它会不会在第 2 轮之后自己停下来
    seen = _capped_script(monkeypatch, book, tool_rounds=5)
    sess = PenSession(session_id="y" * 32, handbook_id="demo")
    # 假 client 每枪 prompt=8/completion=3；cap=20 时第三轮顶部撞线
    evs = list(stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path],
                           allow_env_fallback=False,
                           limits=replace(default_limits(), max_tokens_chat=20)))
    assert evs[-1]["type"] == "done", "要正常收场，不是 error"
    assert not any(e["type"] == "error" for e in evs)
    texts = [str(m.get("content") or "") for m in sess.messages if m.get("role") == "user"]
    assert any(FORCE_ANSWER_BUDGET in t for t in texts), "收口话术要说是预算到线"
    assert not any(FORCE_ANSWER in t for t in texts), (
        "不能说「工具次数用完了」——次数根本没用完，那是对模型撒谎"
    )
    assert "tools" not in seen[-1], "收口那一枪不带 tools，否则它还会接着翻"
    with_tools = [k for k in seen if "tools" in k]
    assert len(with_tools) == 2, (
        f"脚本备了 5 轮工具、轮数上限是 100，却该被预算停在 2 轮，实际 {len(with_tools)}"
    )


def test_turn_cap_reserves_headroom_for_the_closing_shot(monkeypatch, tmp_path: Path) -> None:
    """卡在线上和留余量会停在**不同的轮次**。这条钉住留余量那一半。

    不留余量的话上限根本不是上限：累计花销是二次增长的，而收口枪的大小
    只和 messages 有多长有关，和上限之间没有任何关系。
    """
    from dataclasses import replace

    from pen.config import default_limits

    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    # 每枪 11 token（8+3），余量取上一枪的 prompt_tokens=8。
    # cap=30：卡线要到 33 才停（第 3 轮后）；留余量在 22+8=30 就停（第 2 轮后）。
    seen = _capped_script(monkeypatch, book)
    sess = PenSession(session_id="h" * 32, handbook_id="demo")
    list(stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path],
                     allow_env_fallback=False,
                     limits=replace(default_limits(), max_tokens_chat=30)))
    with_tools = [k for k in seen if "tools" in k]
    assert len(with_tools) == 2, (
        f"留余量该在第 2 枪之后收口，实际带 tools 打了 {len(with_tools)} 枪"
        "（等于 3 就说明余量没生效）"
    )


def test_turn_cap_never_blocks_the_very_first_shot(monkeypatch, tmp_path: Path) -> None:
    """填错一个小数字，最坏是退化成单轮直答，不是把插件变砖。

    公式自带这个性质：第 0 轮 turn_spend 和 prompt_tokens 都是 0，
    `0 + 0 >= cap` 对任何 cap > 0 都是 False。不用写特例。
    """
    from dataclasses import replace

    from pen.config import default_limits

    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    seen = _capped_script(monkeypatch, book)
    sess = PenSession(session_id="i" * 32, handbook_id="demo")
    evs = list(stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path],
                           allow_env_fallback=False,
                           limits=replace(default_limits(), max_tokens_chat=1)))
    assert len(seen) >= 1, "cap=1 也必须打得出第一枪"
    assert evs[-1]["type"] == "done", "而且要有答案，不是 error"
    assert "tools" in seen[0] and "tools" not in seen[-1]


def test_cross_book_token_gate_is_a_third_gate_not_a_replacement(tmp_path: Path) -> None:
    """token 闸回答的是前两道回答不了的问题：「这一轮已经烧到 X 了，别再开新书」。

    它只能是后置的——读的时候根本不知道那段文本值多少 token。所以字符闸和
    次数闸都不能被它替换掉。
    """
    from dataclasses import replace

    from pen.agent.tools_impl import handle_read_file
    from pen.config import default_limits

    cur = tmp_path / "cur.md"
    cur.write_text("# 当前这本\n", encoding="utf-8")
    other = tmp_path / "other.md"
    other.write_text("\n".join(f"别的书第 {i} 行" for i in range(1, 200)), encoding="utf-8")

    base = {"original_path": cur, "extra_roots": [tmp_path]}
    lim = replace(default_limits(), max_tokens_cross_book=5000)

    # 本轮还没烧多少 → 照常读
    ok_ctx = {**base, "limits": lim, "turn_tokens": 100}
    assert "预算快到线" not in handle_read_file(
        {"path": str(other), "offset": 1, "limit": 5}, ok_ctx)["text"]

    # 本轮已经烧过头 → 拦住，而字符和次数都远没到线
    hot_ctx = {**base, "limits": lim, "turn_tokens": 9000}
    got = handle_read_file({"path": str(other), "offset": 1, "limit": 5}, hot_ctx)
    assert got["ok"] is True, "超预算不能报错"
    assert "预算快到线" in got["text"]
    assert hot_ctx.get("cross_book_chars") is None, "被第三道闸拦住时不该计入字符预算"
    # 两道闸给模型**两句不同的话**，看 trace 就知道是哪道触发的
    assert "额度用完" not in got["text"]


def test_cross_book_token_gate_is_off_by_default(tmp_path: Path) -> None:
    """cap=0 时 over() 恒为 False，前两道闸一个字节都不变。"""
    from pen.agent.tools_impl import handle_read_file

    cur = tmp_path / "cur.md"
    cur.write_text("# 当前这本\n", encoding="utf-8")
    other = tmp_path / "other.md"
    other.write_text("\n".join(f"别的书第 {i} 行" for i in range(1, 200)), encoding="utf-8")
    ctx = {"original_path": cur, "extra_roots": [tmp_path], "turn_tokens": 10**9}
    got = handle_read_file({"path": str(other), "offset": 1, "limit": 5}, ctx)
    assert "预算快到线" not in got["text"], "默认不限时，烧再多也不该被这道闸拦"
    assert ctx["cross_book_reads"] == 1


# ── v0.10.8 上限的三个真 bug ───────────────────────────────────


def test_turn_cap_resets_every_turn(monkeypatch, tmp_path: Path) -> None:
    """**这是 v0.10.6 漏掉的那条。**

    turn_spend 只累加不清零的话，名为「每轮上限」的旋钮实际是「整场一次性
    预算」：用完之后每一轮都直接进收口枪，苏格拉底**永远不再翻手册**，而且
    turn_spend 落盘，重启 sidecar 也救不回来。

    v0.10.6 的五条上限测试**全部只跑一轮**——凡是「每轮重置」型的状态，
    那个测试形状按定义看不见。
    """
    from dataclasses import replace

    from pen.config import default_limits

    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    sess = PenSession(session_id="t" * 32, handbook_id="demo")
    # 每枪 1100，cap 5000 → 一轮里第 4 枪顶部撞线。**每枪的量必须真能撞到
    # cap**，否则这条测试是空转的（第一版就是：每枪 11 token 撞不到 5000，
    # 把重置删掉照样绿）。
    lim = replace(default_limits(), max_tokens_chat=5000)
    rounds = []
    for i in range(4):
        seen = _capped_script(monkeypatch, book, tool_rounds=9, per_shot=(1000, 100))
        list(stream_chat(sess, book, f"第 {i} 轮", llm=_cfg(), extra_roots=[tmp_path],
                         allow_env_fallback=False, limits=lim))
        rounds.append(len([k for k in seen if "tools" in k]))
    assert rounds[0] > 0, "第一轮就该能翻书"
    assert all(r == rounds[0] for r in rounds), (
        f"每一轮的翻书枪数该一样（预算每轮重置），实际 {rounds}"
        "——全 0 就说明 turn_spend 没清零，苏格拉底从第二轮起再也不翻书了"
    )


def test_the_reset_does_not_leak_into_the_approval_pause(monkeypatch, tmp_path: Path) -> None:
    """清零只能在 stream_chat。放进 _agent_loop 或 resume_chat 就等于
    审批让预算翻倍——v0.10.3 刚修过反向的同一件事。"""
    cur = tmp_path / "cur.md"
    cur.write_text("# 当前这本\n\n第二段。\n", encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(tool_calls=[_Tc("r1", "read_file", {"path": str(cur), "offset": 1, "limit": 20})]),
            _Msg(tool_calls=[_Tc("e1", "edit_file", {"path": str(cur),
                                                     "old_string": "第二段。",
                                                     "new_string": "改过。"})]),
        ],
    )
    sess = PenSession(session_id="k" * 32, handbook_id="demo")
    list(stream_chat(sess, cur, "packet", llm=_cfg(), extra_roots=[tmp_path],
                     allow_env_fallback=False))
    before = dict(sess.turn_spend)
    assert sess.pending is not None
    _patch_script(monkeypatch, [_Msg(content="改完了。" * 30)])
    list(resume_chat(sess, cur, allow=True, pending_id=sess.pending["id"],
                     llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False))
    assert sess.turn_spend["calls"] > before["calls"], "续跑那一枪要加在同一轮上"
    assert sess.turn_spend["in_tokens"] > before["in_tokens"], "不能被清零重来"


def test_the_batch_is_not_executed_once_the_budget_is_gone(monkeypatch, tmp_path: Path) -> None:
    """模型常常一枪吐一整批（实测 7 个 read_file，也见过 21 个）。
    整批执行完再收口，收口枪面对的是多了整批正文的 messages——
    实测超出从约 1.5 倍变成 2.18 倍。撞线就不执行这一批。"""
    from dataclasses import replace

    from pen.config import default_limits

    book = tmp_path / "note.md"
    book.write_text("# 题\n\n" + "很长的一段正文。" * 200 + "\n", encoding="utf-8")
    reads: list[str] = []
    real_dispatch = None

    class _Completions:
        def __init__(self) -> None:
            self.n = 0

        def create(self, **kwargs: Any) -> Any:
            self.n += 1
            if "tools" in kwargs:
                # 一枪吐 5 个 read_file
                tcs = [
                    _Tc(f"c{i}", "read_file", {"path": str(book), "offset": 1, "limit": 100})
                    for i in range(5)
                ]
                msg = _Msg(tool_calls=tcs)
            else:
                msg = _Msg(content="用手上的东西答一段。" * 20)
            return iter(stream_chunks(msg, SimpleNamespace(
                prompt_tokens=4000, completion_tokens=500)))

    monkeypatch.setattr(
        openai, "OpenAI",
        lambda **_kw: SimpleNamespace(chat=SimpleNamespace(completions=_Completions())),
    )
    sess = PenSession(session_id="q" * 32, handbook_id="demo")
    evs = list(stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path],
                           allow_env_fallback=False,
                           limits=replace(default_limits(), max_tokens_chat=6000)))
    assert evs[-1]["type"] == "done", "仍要给读者一个真答案"
    tool_evs = [e for e in evs if e["type"] == "tool"]
    assert not tool_evs, f"撞线之后这一批不该执行，却跑了 {len(tool_evs)} 个"
    # 但协议必须合法：每个 tool_call 都要有配对的 tool 结果
    ids = {
        tc["id"]
        for m in sess.messages
        if m.get("role") == "assistant"
        for tc in (m.get("tool_calls") or [])
    }
    answered = {m.get("tool_call_id") for m in sess.messages if m.get("role") == "tool"}
    assert ids and ids <= answered, (
        "每个 tool_call 都必须有配对的 tool 结果，少一条供应商直接 400"
    )
    assert any(
        "没有执行" in str(m.get("content") or "")
        for m in sess.messages
        if m.get("role") == "tool"
    ), "合成结果要告诉模型为什么没读到"


def test_the_loop_top_check_only_bites_on_a_resumed_turn(monkeypatch, tmp_path: Path) -> None:
    """循环顶部那道判在一次 stream_chat 之内是**冗余**的：工具执行既不改
    turn_spend 也不改 usage，所以本轮顶部和上一轮批次前的输入完全一样。

    它真正起作用的只有审批续跑——那时 turn_spend 从暂停前带过来，而 usage
    刚重置成 0。所以它不留余量是想清楚的，不是漏了。
    """
    from dataclasses import replace

    from pen.config import default_limits

    cur = tmp_path / "cur.md"
    cur.write_text("# 当前这本\n\n第二段。\n", encoding="utf-8")
    _capped_script(monkeypatch, cur, tool_rounds=1, per_shot=(3000, 200))
    sess = PenSession(session_id="L" * 32, handbook_id="demo")
    # 先花掉一大笔，然后手工造一个 pending，模拟「审批暂停在预算已经超了之后」
    list(stream_chat(sess, cur, "packet", llm=_cfg(), extra_roots=[tmp_path],
                     allow_env_fallback=False))
    assert sess.turn_spend["in_tokens"] > 0
    sess.pending = {
        "id": "pid-resume", "name": "edit_file", "args": {},
        "tool_call_id": "tc-x", "rest": [],
        "original_path": str(cur.expanduser().resolve()),
        "cross_book_chars": 0, "cross_book_reads": 0,
    }
    sess.messages.append(
        {"role": "assistant",
         "tool_calls": [{"id": "tc-x", "type": "function",
                         "function": {"name": "edit_file", "arguments": "{}"}}]}
    )
    spent_before = sess.turn_spend["in_tokens"] + sess.turn_spend["out_tokens"]

    seen = _capped_script(monkeypatch, cur, tool_rounds=5, per_shot=(3000, 200))
    list(resume_chat(sess, cur, allow=False, pending_id="pid-resume",
                     llm=_cfg(), extra_roots=[tmp_path], allow_env_fallback=False,
                     limits=replace(default_limits(), max_tokens_chat=spent_before)))
    assert not [k for k in seen if "tools" in k], (
        "续跑时预算已经超了，顶部那道判就该拦住，一枪带 tools 的都不该打"
    )
    assert seen, "但仍要打收口那一枪，给读者一个答案"
    assert "tools" not in seen[-1]


# ── v0.11.1 写回那条线的三处 ────────────────────────────────────


def test_narration_alongside_a_tool_call_reaches_the_reader(monkeypatch, tmp_path: Path) -> None:
    """模型常常边说边动手：一条消息里既有正文又有 tool_calls。

    以前这里只看 tool_calls，那段正文一个字都到不了读者眼前——实测落盘会话
    里 25 条带工具调用的回复有 14 条同时带正文，包括「收到，批准了，这轮直接
    动手」这种。读者于是只看到审批弹窗凭空冒出来，觉得前后顺序错乱。
    """
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(
                content="我先读一下文件，看准行号再动手。",
                tool_calls=[_Tc("c1", "read_file", {"path": str(book), "offset": 1, "limit": 20})],
            ),
            _Msg(content="读完了，就这样。" * 20),
        ],
    )
    sess = PenSession(session_id="n" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path],
                    allow_env_fallback=False)
    )
    said = "".join(str(e.get("text") or "") for e in events if e["type"] == "token")
    assert "我先读一下文件" in said, "边说边做的那段话不能被吞掉"
    assert "读完了" in said, "收尾那段也要在"
    # 但它不能污染 last_assistant——那是写回和深挖的输入
    assert "我先读一下文件" not in (sess.last_assistant or ""), (
        "中途那句不能进 last_assistant，否则写回会拿它当解答"
    )


def test_midturn_narration_never_leaks_the_chips_block(monkeypatch, tmp_path: Path) -> None:
    """芯片块是收尾才该出现的东西，中途漏出来就是一段生注释。"""
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(
                content="先读文件。\n<!--pen:chips\n- 漏出来的追问？\n-->",
                tool_calls=[_Tc("c1", "read_file", {"path": str(book), "offset": 1, "limit": 20})],
            ),
            _Msg(content="读完了，就这样。" * 20),
        ],
    )
    sess = PenSession(session_id="c" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path],
                    allow_env_fallback=False)
    )
    said = "".join(str(e.get("text") or "") for e in events if e["type"] == "token")
    assert "先读文件。" in said
    assert "pen:chips" not in said and "漏出来的追问" not in said


def test_read_then_edit_in_one_turn_is_allowed(monkeypatch, tmp_path: Path) -> None:
    """要求从来只是「先 read_file，拿到返回再 edit」——不是「下一轮再单独调用」。

    读完之后**同一轮**接着 edit 必须走得通，否则读者要多说一句话、多花一轮钱。
    """
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n第二段。\n", encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(tool_calls=[_Tc("r1", "read_file", {"path": str(book), "offset": 1, "limit": 20})]),
            _Msg(tool_calls=[_Tc("e1", "edit_file", {"path": str(book),
                                                     "old_string": "第二段。",
                                                     "new_string": "改过。"})]),
        ],
    )
    sess = PenSession(session_id="o" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path],
                    allow_env_fallback=False)
    )
    assert any(e["type"] == "approval" for e in events), (
        "同一轮里读完接着改，应该直接弹审批，而不是停下来等读者"
    )
    reads = [e for e in events if e["type"] == "tool" and e["name"] == "read_file"]
    assert reads and reads[0]["ok"], "读那一步要成功"


def test_the_prompts_no_longer_say_next_round() -> None:
    """「下一轮再单独 edit_file」是我们自己写进去的一句错话——约束只是
    「拿到 read 的返回之后再改」。模型逐字照做，回「下一轮我就动手」就停了。"""
    from pen.agent.registry import schemas
    from pen.session import SYSTEM_PROMPT

    assert "下一轮" not in SYSTEM_PROMPT
    edit = next(t for t in schemas() if t["function"]["name"] == "edit_file")
    assert "下一轮" not in edit["function"]["description"]
    # 但「不能同批」这条得留着：同批发出时模型还没看到原文，old_string 只能靠猜
    assert "同一批" in edit["function"]["description"]


def test_same_batch_read_and_edit_is_still_blocked(monkeypatch, tmp_path: Path) -> None:
    """放宽的是「不用等下一轮」，不是「可以同批」。同批发出时模型还没看到
    原文，old_string 只能靠猜。"""
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n第二段。\n", encoding="utf-8")
    _patch_script(
        monkeypatch,
        [
            _Msg(tool_calls=[
                _Tc("r1", "read_file", {"path": str(book), "offset": 1, "limit": 20}),
                _Tc("e1", "edit_file", {"path": str(book), "old_string": "第二段。",
                                        "new_string": "改过。"}),
            ]),
            _Msg(content="那我分两步来。" * 20),
        ],
    )
    sess = PenSession(session_id="b" * 32, handbook_id="demo")
    events = list(
        stream_chat(sess, book, "packet", llm=_cfg(), extra_roots=[tmp_path],
                    allow_env_fallback=False)
    )
    assert not any(e["type"] == "approval" for e in events), "同批里的 edit 该被挡下"
    assert "第二段。" in book.read_text(encoding="utf-8"), "原文不能被动"


# ── v0.12.0 真流式 ─────────────────────────────────────────────


def test_tool_call_fragments_reassemble_into_valid_json() -> None:
    """`delta.tool_calls[i].function.arguments` 是一片一片来的，id 和 name
    只在第一片出现。**拼错一个字节 json.loads 就炸，整轮对话失败。**
    分片还不保证按 index 有序。
    """
    from pen.tutor import _ToolCallDraft, assemble_tool_calls

    frags = [
        (1, SimpleNamespace(id="b", function=SimpleNamespace(name="edit_file", arguments=""))),
        (0, SimpleNamespace(id="a", function=SimpleNamespace(name="read_file", arguments='{"pa'))),
        (0, SimpleNamespace(id=None, function=SimpleNamespace(name=None, arguments='th": "x.'))),
        (1, SimpleNamespace(id=None, function=SimpleNamespace(name=None, arguments='{"old":"y"}'))),
        (0, SimpleNamespace(id=None, function=SimpleNamespace(name=None, arguments='md"}'))),
    ]
    drafts: dict[int, Any] = {}
    for i, f in frags:
        drafts.setdefault(i, _ToolCallDraft()).eat(f)
    got = assemble_tool_calls(drafts)
    assert [c["id"] for c in got] == ["a", "b"], "要按 index 升序还原，不是按到达顺序"
    assert json.loads(got[0]["function"]["arguments"]) == {"path": "x.md"}
    assert json.loads(got[1]["function"]["arguments"]) == {"old": "y"}
    assert got[0]["function"]["name"] == "read_file"


def test_reasoning_is_reported_as_a_count_not_as_text(monkeypatch, tmp_path: Path) -> None:
    """读者要的是「没卡住」，不是「让我读它的草稿」。推理内容实测 1633 个分片，
    塞进窄侧栏是灾难——只报字数，让状态行跳数字。"""
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    _patch_script(monkeypatch, [_Msg(content="答一段。" * 30)])
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    events = list(stream_chat(sess, book, "packet", llm=_cfg(),
                              extra_roots=[tmp_path], allow_env_fallback=False))
    thinks = [e for e in events if e["type"] == "think"]
    assert thinks, "推理阶段要报出来，否则那十几秒还是一片空白"
    assert all(isinstance(e["chars"], int) for e in thinks)
    said = "".join(str(e.get("text") or "") for e in events if e["type"] == "token")
    assert "想一下" not in said, "推理内容不能混进正文"


def test_content_streams_once_not_twice(monkeypatch, tmp_path: Path) -> None:
    """前端是 `acc += text` 累加的。收分片时吐过一遍、收尾再吐一遍，
    读者会看到两份。"""
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    _patch_script(monkeypatch, [_Msg(content="独一无二的这句话。" + "补" * 80)])
    sess = PenSession(session_id="d" * 32, handbook_id="demo")
    events = list(stream_chat(sess, book, "packet", llm=_cfg(),
                              extra_roots=[tmp_path], allow_env_fallback=False))
    said = "".join(str(e.get("text") or "") for e in events if e["type"] == "token")
    assert said.count("独一无二的这句话。") == 1, f"吐了 {said.count('独一无二的这句话。')} 遍"
    assert said.strip() == (sess.last_assistant or "").strip(), "吐出去的和落盘的要是同一份"


def test_the_chips_block_never_reaches_the_reader(monkeypatch, tmp_path: Path) -> None:
    """芯片块是给界面剥的。流式下它也是一片一片来的，marker 本身会被切碎——
    保守切法必须挡住它，否则读者看到一段生注释。"""
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    _patch_script(
        monkeypatch,
        # 题面要够长：clean_candidates 的中文下限是 8 字，太短会被当噪音滤掉，
        # 那样 dyn_chips 是空的，测的就不是「芯片有没有被挡住」了。
        [_Msg(content="正文到此为止。" * 20
              + "\n<!--pen:chips\n- 那这块和前面第三拍讲的是同一件事吗？\n"
                "- 为什么这里选白名单不选沙箱，代价是什么？\n-->")],
    )
    sess = PenSession(session_id="p" * 32, handbook_id="demo")
    events = list(stream_chat(sess, book, "packet", llm=_cfg(),
                              extra_roots=[tmp_path], allow_env_fallback=False))
    said = "".join(str(e.get("text") or "") for e in events if e["type"] == "token")
    assert "pen:chips" not in said and "第三拍讲的是同一件事" not in said, f"漏出来了：{said[-60:]!r}"
    assert "正文到此为止。" in said, "正文不能被切法误伤"
    done = next(e for e in events if e["type"] == "done")
    assert [c["text"] for c in done["dyn_chips"]], "芯片本身还要正常解析出来"


def test_the_whole_tail_is_flushed_when_there_is_no_chips_block(monkeypatch, tmp_path: Path) -> None:
    """保守切法会压着最后十几个字不吐，等下一片来了再说。没有下一片时
    必须冲掉——否则每条回复都缺一截尾巴。"""
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    tail = "这是最后一句话，一个字都不能少。"
    _patch_script(monkeypatch, [_Msg(content="开头。" * 30 + tail)])
    sess = PenSession(session_id="t" * 32, handbook_id="demo")
    events = list(stream_chat(sess, book, "packet", llm=_cfg(),
                              extra_roots=[tmp_path], allow_env_fallback=False))
    said = "".join(str(e.get("text") or "") for e in events if e["type"] == "token")
    assert said.endswith(tail), f"尾巴被吞了：{said[-40:]!r}"


def test_usage_comes_from_the_final_chunk(monkeypatch, tmp_path: Path) -> None:
    """include_usage 让末片带回 usage，那一片没有 choices。不认这个形状就没账可记。"""
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    _patch_script(monkeypatch, [_Msg(content="答一段。" * 30)])
    sess = PenSession(session_id="u" * 32, handbook_id="demo")
    events = list(stream_chat(sess, book, "packet", llm=_cfg(),
                              extra_roots=[tmp_path], allow_env_fallback=False))
    done = next(e for e in events if e["type"] == "done")
    assert done["usage"]["prompt_tokens"] == 8
    assert sess.spend["chat"]["in_tokens"] == 8, "流式下也要记账"


def test_streaming_is_actually_requested(monkeypatch, tmp_path: Path) -> None:
    """别改回 stream=False——那样读者又要对着空屏幕干等十几秒。"""
    book = tmp_path / "note.md"
    book.write_text("# 题\n\n唯一段。\n", encoding="utf-8")
    seen = _patch_script(monkeypatch, [_Msg(content="答一段。" * 30)])
    sess = PenSession(session_id="v" * 32, handbook_id="demo")
    list(stream_chat(sess, book, "packet", llm=_cfg(),
                     extra_roots=[tmp_path], allow_env_fallback=False))
    assert seen[0]["stream"] is True
    assert seen[0]["stream_options"] == {"include_usage": True}
