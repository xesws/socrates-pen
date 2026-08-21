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
