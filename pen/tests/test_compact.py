from __future__ import annotations

import json
from pathlib import Path

import pytest

from pen.agent.permissions import READ_FIRST_MSG, read_first_block
from pen.compact import CompactPending, compact_session, should_auto_compact
from pen.tutor import FORCE_ANSWER
from pen.config import SELECTED_TEXT_CHARS, default_limits, merge_limits
from pen.session import PenSession
from pen.tutor import build_user_packet


def _sid() -> str:
    return "c" * 32


def _packet(path: Path, *, extra: str = "（无，按芯片意图行动）", sel: str = "为什么非要两个网络") -> str:
    return f"""[来源]
handbook_path: {path}
level: Level 3
beat: 第四拍
q_title: 决策①
kind: q
lines: 954-964

[工作目录里的其他教材]
- 《另一本》  path: {path.parent / "other.md"}
  大纲：开篇

[全书目录（不要整本背诵）]
- Level 3  L900  决策

[框选]
{sel}

[邻域]
{'N' * 200}

[意图]
chip = socratic
先别揭晓。

[用户补充]
{extra}

[已经抛过的追问（别再重复这些）]
（还没抛过）
"""


def _session(book: Path) -> PenSession:
    sess = PenSession(session_id=_sid(), handbook_id="demo")
    sess.book_title = "从零手写DQN"
    sess.lang = "zh"
    sess.last_anchor = {
        "path": str(book),
        "start_line": 954,
        "end_line": 964,
        "level": "Level 3",
        "beat": "第四拍",
        "q_title": "决策①",
        "selected_text": "为什么非要两个网络",
    }
    p1 = _packet(book)
    p2 = _packet(book, extra="不对，不是 target network 的问题")
    body = "\n".join(f"{i}\tLINE{i}" for i in range(10, 40))
    sess.messages = [
        {"role": "system", "content": "你是苏格拉底，正在带人读《从零手写DQN》。"},
        {"role": "user", "content": p1},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": str(book), "offset": 10, "limit": 30}),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": body},
        {
            "role": "assistant",
            "content": "那我问你：监督回归的标签是 ground truth 吧？可 Q_target 还是自己估的（L954-964）。",
        },
        {"role": "user", "content": p2},
        {"role": "assistant", "content": "好，那裂缝在决策①的第一层。"},
    ]
    sess.ui_messages = [
        {"role": "user", "text": "先别揭晓"},
        {"role": "assistant", "text": "监督回归的标签是自己估的？"},
        {"role": "user", "text": "不对，不是 target network 的问题"},
        {"role": "assistant", "text": "好，那裂缝在决策①的第一层。"},
    ]
    sess.last_chips = [{"text": "监督回归的标签是自己估的？"}]
    sess.read_ok_paths = [str(book.resolve())]
    return sess


def test_compact_keeps_required_slots(tmp_path: Path) -> None:
    book = tmp_path / "book.md"
    book.write_text("# x\n", encoding="utf-8")
    (tmp_path / "other.md").write_text("# y\n", encoding="utf-8")
    sess = _session(book)
    result = compact_session(sess, allow_paths=[book, tmp_path / "other.md"], original_path=book)
    assert result.did is True
    assert result.dropped_reads >= 1
    summary = str(sess.messages[1].get("content") or "")
    assert "<!--pen:compact-->" in summary
    assert "L954-964" in summary
    assert "决策①" in summary
    assert "从零手写DQN" in summary
    assert "监督回归" in summary
    assert "不对" in summary
    assert str(tmp_path / "other.md") in summary or "other.md" in summary
    assert sess.messages[-1]["content"].startswith("好，那裂缝")
    roles = [m.get("role") for m in sess.ui_messages]
    assert roles.count("user") == 2
    assert "note" in roles
    assert sess.compacted is True
    assert sess.read_ok_paths == []


def test_compact_drops_read_file_body_and_read_first_still_holds(tmp_path: Path) -> None:
    book = tmp_path / "book.md"
    book.write_text("# x\n", encoding="utf-8")
    sess = _session(book)
    compact_session(sess, allow_paths=[book], original_path=book)
    dumped = json.dumps(sess.messages, ensure_ascii=False)
    assert "LINE10" not in dumped
    assert "LINE20" not in dumped
    blocked = read_first_block("edit_file", book.resolve(), set())
    assert blocked == READ_FIRST_MSG
    still = read_first_block(
        "edit_file",
        book.resolve(),
        {Path(p).expanduser().resolve() for p in sess.read_ok_paths},
    )
    assert still == READ_FIRST_MSG


def test_selection_is_capped_in_first_packet(tmp_path: Path) -> None:
    from pen import libraries

    libraries.ensure_default()
    idx = libraries.load_index("swe-agent-v2")
    huge = "汉" * (SELECTED_TEXT_CHARS + 50)
    packet, anchor = build_user_packet(
        idx,
        Path(idx.original_path),
        selected_text=huge,
        start_line=1,
        end_line=2,
        chip="socratic",
        user_text="",
    )
    assert huge not in packet
    assert huge not in str(anchor["selected_text"])
    assert anchor["selection_capped"] is True
    assert "只留前" in packet


def test_compact_fed_packet_omits_toc(tmp_path: Path) -> None:
    from pen import libraries

    libraries.ensure_default()
    idx = libraries.load_index("swe-agent-v2")
    packet, _ = build_user_packet(
        idx,
        Path(idx.original_path),
        selected_text="x",
        start_line=1,
        end_line=1,
        chip="socratic",
        user_text="",
        compact_fed=True,
    )
    assert "[全书目录（不要整本背诵）]" not in packet
    assert "[邻域]" not in packet
    assert "见滚动摘要" in packet or "地图已在滚动摘要里" in packet


def test_pending_refuses_compact(tmp_path: Path) -> None:
    book = tmp_path / "book.md"
    book.write_text("# x\n", encoding="utf-8")
    sess = _session(book)
    before = json.dumps(sess.messages, ensure_ascii=False)
    sess.pending = {"id": "p1", "name": "edit_file", "args": {"old_string": "a", "new_string": "b"}}
    with pytest.raises(CompactPending):
        compact_session(sess, allow_paths=[book], original_path=book)
    assert json.dumps(sess.messages, ensure_ascii=False) == before


def test_sibling_vault_note_does_not_enter_summary(tmp_path: Path) -> None:
    book = tmp_path / "book.md"
    diary = tmp_path / "diary.md"
    book.write_text("# x\n", encoding="utf-8")
    diary.write_text("# private\n", encoding="utf-8")
    sess = _session(book)
    sess.messages[1]["content"] += f"\n- 《日记》  path: {diary}\n"
    compact_session(sess, allow_paths=[book], original_path=book)
    summary = str(sess.messages[1].get("content") or "")
    assert "diary.md" not in summary


def test_outside_sandbox_path_does_not_enter_summary(tmp_path: Path) -> None:
    book = tmp_path / "book.md"
    book.write_text("# x\n", encoding="utf-8")
    sess = _session(book)
    secret = "/etc/passwd"
    sess.messages[1]["content"] += f"\n- 《机密》  path: {secret}\n"
    compact_session(sess, allow_paths=[book], original_path=book)
    summary = str(sess.messages[1].get("content") or "")
    assert "/etc/passwd" not in summary


def test_should_auto_compact_respects_zero_and_pending(tmp_path: Path) -> None:
    book = tmp_path / "book.md"
    book.write_text("# x\n", encoding="utf-8")
    sess = _session(book)
    sess.last_context_tokens = 80_000
    off = merge_limits({"compact_chat_tokens": 0})
    assert should_auto_compact(sess, off) is False
    on = default_limits()
    assert on.compact_chat_tokens == 32000
    assert should_auto_compact(sess, on) is True
    sess.pending = {"id": "x"}
    assert should_auto_compact(sess, on) is False


def test_force_answer_user_is_not_last_turn(tmp_path: Path) -> None:
    book = tmp_path / "book.md"
    book.write_text("# x\n", encoding="utf-8")
    sess = _session(book)
    sess.messages.append({"role": "user", "content": FORCE_ANSWER["zh"]})
    sess.messages.append({"role": "assistant", "content": "那就先说到这儿。"})
    compact_session(sess, allow_paths=[book], original_path=book)
    assert sess.messages[0]["role"] == "system"
    assert "<!--pen:compact-->" in str(sess.messages[1].get("content"))
    assert sess.messages[2]["role"] == "user"
    assert "决策①" in str(sess.messages[2].get("content"))
    assert "<!--pen:compact-->" not in str(sess.messages[2].get("content"))


def test_shelf_path_with_spaces_survives(tmp_path: Path) -> None:
    book = tmp_path / "book.md"
    spaced = tmp_path / "Mobile Documents" / "other.md"
    spaced.parent.mkdir()
    book.write_text("# x\n", encoding="utf-8")
    spaced.write_text("# y\n", encoding="utf-8")
    sess = _session(book)
    sess.messages[1]["content"] = _packet(book).replace(str(book.parent / "other.md"), str(spaced))
    compact_session(sess, allow_paths=[book, spaced], original_path=book)
    summary = str(sess.messages[1].get("content") or "")
    assert "Mobile Documents" in summary
    assert str(spaced) in summary
    # 再折一次，带空格的 dropped / shelf 还在
    compact_session(sess, allow_paths=[book, spaced], original_path=book)
    summary2 = str(sess.messages[1].get("content") or "")
    assert "Mobile Documents" in summary2


def test_compact_resets_last_context_tokens(tmp_path: Path) -> None:
    book = tmp_path / "book.md"
    book.write_text("# x\n", encoding="utf-8")
    sess = _session(book)
    sess.last_context_tokens = 80_000
    compact_session(sess, allow_paths=[book], original_path=book)
    assert sess.last_context_tokens == 0
    assert should_auto_compact(sess, default_limits()) is False


def test_old_snapshot_without_compact_fields_still_loads() -> None:
    sess = PenSession(session_id=_sid(), handbook_id="demo")
    data = sess.to_dict()
    data.pop("compacted", None)
    data.pop("last_context_tokens", None)
    loaded = PenSession.from_dict(data)
    assert loaded.compacted is False
    assert loaded.last_context_tokens == 0


def test_compact_note_carries_a_clock() -> None:
    """折叠那一刻也要有钟：回看时才知道哪些气泡是折叠前的。"""
    from datetime import datetime

    from pen.compact import _upsert_note
    from pen.session import PenSession

    sess = PenSession(session_id="s-clock", handbook_id="hb")
    sess.ui_messages = [{"role": "user", "text": "hi"}]
    _upsert_note(sess)
    note = sess.ui_messages[-1]
    assert note["role"] == "note"
    assert datetime.fromisoformat(note["ts"]).utcoffset() is not None
