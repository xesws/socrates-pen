from __future__ import annotations

from pathlib import Path

import pytest

from pen import config
from pen.session import PenSession, SessionStore, load_session, save_session


@pytest.fixture()
def pen_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "PEN_DIR", tmp_path)
    return tmp_path


def test_create_then_reload_from_disk(pen_home: Path) -> None:
    store = SessionStore()
    sess = store.create("swe-agent-v2")
    sess.ui_messages.append({"role": "user", "text": "先别揭晓，问我一个问题"})
    sess.ui_messages.append({"role": "assistant", "text": "你觉得 shell 是一类还是一个？"})
    sess.last_anchor = {
        "start_line": 695,
        "end_line": 695,
        "selected_text": "chmod +x hello.sh",
        "kind": "q",
        "level": "Level 0",
    }
    sess.has_substantive = True
    sess.last_assistant = "你觉得 shell 是一类还是一个？"
    store.save(sess)

    disk = pen_home / "sessions" / f"{sess.session_id}.json"
    assert disk.is_file()

    other = SessionStore()
    loaded = other.get(sess.session_id)
    assert loaded.handbook_id == "swe-agent-v2"
    assert loaded.has_substantive is True
    assert loaded.ui_messages[0]["text"].startswith("先别揭晓")
    assert loaded.last_anchor is not None
    assert loaded.last_anchor["start_line"] == 695
    assert any(m.get("role") == "system" for m in loaded.messages)


def test_missing_session_raises(pen_home: Path) -> None:
    store = SessionStore()
    with pytest.raises(KeyError):
        store.get("deadbeefdeadbeefdeadbeefdeadbeef")


def test_pending_roundtrip(pen_home: Path) -> None:
    store = SessionStore()
    sess = store.create("swe-agent-v2")
    sess.pending = {
        "id": "abc123",
        "name": "edit_file",
        "args": {"path": "/tmp/n.md", "old_string": "a", "new_string": "b"},
        "tool_call_id": "c1",
        "rest": [],
    }
    store.save(sess)
    public = sess.to_public()
    assert public["pending"]["pending_id"] == "abc123"
    assert public["pending"]["args"]["old_string"] == "a"

    other = SessionStore()
    loaded = other.get(sess.session_id)
    assert loaded.pending is not None
    assert loaded.pending["id"] == "abc123"


def test_read_ok_paths_roundtrip(pen_home: Path) -> None:
    store = SessionStore()
    sess = store.create("swe-agent-v2")
    sess.read_ok_paths = ["/tmp/note.md"]
    store.save(sess)
    loaded = SessionStore().get(sess.session_id)
    assert loaded.read_ok_paths == ["/tmp/note.md"]


def test_corrupt_json_is_missing(pen_home: Path) -> None:
    sess = PenSession(session_id="ab" * 16, handbook_id="swe-agent-v2")
    dest = save_session(sess)
    dest.write_text("{not-json", encoding="utf-8")
    assert load_session(sess.session_id) is None


def test_turns_and_last_chips_roundtrip() -> None:
    """v0.8.0 新增的两个字段要能落盘、能从旧快照回落。"""
    from pen.session import PenSession

    sess = PenSession(session_id="t-turns", handbook_id="h")
    sess.turns = 3
    sess.last_chips = [{"id": "q0", "kind": "quick", "text": "为什么这里选 A？"}]
    back = PenSession.from_dict(sess.to_dict())
    assert back.turns == 3
    assert back.last_chips == sess.last_chips
    assert back.to_public()["dyn_chips"] == sess.last_chips


def test_old_snapshot_without_new_fields_still_loads() -> None:
    from pen.session import PenSession

    sess = PenSession(session_id="t-old", handbook_id="h")
    data = {k: v for k, v in sess.to_dict().items() if k not in ("turns", "last_chips")}
    back = PenSession.from_dict(data)
    assert back.turns == 0 and back.last_chips == []


# --- v0.15.0：教材和 prompt 解耦 ------------------------------------------


def test_book_title_lands_in_the_system_message() -> None:
    """建场时的书名要注进 messages[0]，而且不能再提 SWE Agent。"""
    from pen.session import PenSession

    sess = PenSession(
        session_id="t-book", handbook_id="h", book_title="从零手写 DQN · 强化学习通关手册"
    )
    first = sess.messages[0]["content"]
    assert first.startswith("你是苏格拉底")
    assert "《从零手写 DQN · 强化学习通关手册》" in first
    assert "SWE" not in first, "写死的那本书必须已经不在了"
    assert "实习生" not in first


def test_no_book_title_stays_anonymous_not_empty_quotes() -> None:
    """没书名时说「一本通关手册」，不能留一对空的《》。"""
    from pen.session import PenSession

    first = PenSession(session_id="t-anon", handbook_id="h").messages[0]["content"]
    assert "一本通关手册" in first
    assert "《》" not in first
    assert "SWE" not in first


def test_examples_chip_no_longer_names_swe_only_files() -> None:
    """examples 芯片曾点名 scan.sh / dispatch，那是只有那一本书才有的东西。"""
    from pen.session import SYSTEM_PROMPT

    for name in ("scan.sh", "messages.append", "dispatch", "approve…"):
        assert name not in SYSTEM_PROMPT, name
    assert "第七拍里真出现过的东西" in SYSTEM_PROMPT


def test_book_phrase_strips_extension_and_existing_quotes() -> None:
    """书名取不到 H1 时退回文件名，那条路上不能露出 `.md`。"""
    from pen.session import _book_phrase

    assert _book_phrase("从零手写DQN.md") == "一本叫《从零手写DQN》的通关手册"
    assert _book_phrase("《从零手写DQN》") == "一本叫《从零手写DQN》的通关手册"
    assert _book_phrase("  ") == "一本通关手册"
    assert _book_phrase(".md") == "一本通关手册"


def test_english_keeps_the_appended_instruction_with_a_book_title() -> None:
    from pen.session import system_prompt

    zh = system_prompt("zh", book_title="Deep RL")
    en = system_prompt("en", book_title="Deep RL")
    assert en.startswith(zh)
    assert "Reply in English" in en and "Reply in English" not in zh
    assert "《Deep RL》" in zh


def test_book_title_does_not_touch_the_persisted_schema() -> None:
    """book_title 只在建场那一刻有用，不落盘——旧会话的 messages[0] 原样不动。"""
    from pen.session import PenSession

    sess = PenSession(session_id="t-nostore", handbook_id="h", book_title="某本书")
    data = sess.to_dict()
    assert "book_title" not in data

    frozen = "你是苏格拉底，坐在读者旁边，正在带人读一本手搓 SWE Agent 的通关手册。"
    old = {**data, "messages": [{"role": "system", "content": frozen}]}
    back = PenSession.from_dict(old)
    assert back.messages[0]["content"] == frozen, "老会话的第一条不许被重写"
    assert back.book_title == ""


def test_store_create_passes_the_title_through(pen_home: Path) -> None:
    store = SessionStore()
    sess = store.create("h", lang="zh", book_title="从零手写 DQN")
    assert "《从零手写 DQN》" in sess.messages[0]["content"]
    # 落盘再读回来，第一条还是那句（因为它就存在 messages 里）
    back = load_session(sess.session_id)
    assert back is not None
    assert "《从零手写 DQN》" in back.messages[0]["content"]
