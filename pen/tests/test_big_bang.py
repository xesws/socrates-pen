"""Concurrency invariants exercised with real threads and real temporary notes."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest
from fastapi.testclient import TestClient

from pen import filecoord, runs, snapshots
from pen.agent.tools_impl import handle_edit_file, handle_read_file
from pen.app import app
from pen.session import PenSession, STORE
from pen.tutor import _remember_read, _tool_ctx, stream_chat, resume_chat
from pen.tests.test_agent import _Msg, _Tc, _cfg, _patch_script


def context(path, sid="a", hid=""):
    return {"original_path": path, "extra_roots": [path.parent], "handbook_id": hid,
            "session_id": sid, "read_versions": {str(path.resolve()): filecoord.revision(filecoord.read_text(path))}}


def test_two_stale_proposals_only_one_commits(tmp_path):
    note = tmp_path / "book.md"
    initial = "# Note\nalpha\nbeta\nend\n"
    note.write_text(initial)
    contexts = [context(note), context(note)]
    barrier = threading.Barrier(2)

    def edit(i):
        barrier.wait(timeout=3)
        return handle_edit_file({"path": str(note), "old_string": ["alpha", "beta"][i],
                                 "new_string": ["ALPHA", "BETA"][i]}, contexts[i])
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(edit, [0, 1]))
    assert sum(r["ok"] for r in results) == 1
    assert next(r for r in results if not r["ok"])["code"] == "FILE_CHANGED"
    assert note.read_text() in (initial.replace("alpha", "ALPHA"), initial.replace("beta", "BETA"))
    assert not list(tmp_path.glob("*.pen-tmp"))
    assert not filecoord._entries


def test_same_region_stale_edit_is_also_file_changed(tmp_path):
    note = tmp_path / "book.md"
    note.write_text("# Note\nalpha\nend\n")
    ctx = context(note)
    assert handle_edit_file({"old_string": "alpha", "new_string": "ALPHA"}, ctx)["ok"]
    result = handle_edit_file({"old_string": "alpha", "new_string": "other"}, ctx)
    assert result["code"] == "FILE_CHANGED"
    assert "other" not in note.read_text()


def test_pagination_does_not_silently_mix_versions(tmp_path):
    note = tmp_path / "book.md"
    note.write_text("one\ntwo\nthree\n")
    sess = PenSession("s", "h")
    ctx = _tool_ctx(sess, note, [tmp_path])
    first = handle_read_file({"path": str(note), "limit": 1}, ctx)
    _remember_read(sess, ctx, first)
    note.write_text("inserted\none\ntwo\nthree\n")
    stale = handle_read_file({"path": str(note), "offset": 2, "limit": 1}, ctx)
    _remember_read(sess, ctx, stale)
    assert stale["code"] == "FILE_CHANGED"
    assert not sess.read_versions and not sess.read_ok_paths
    fresh = handle_read_file({"path": str(note), "offset": 1}, ctx)
    _remember_read(sess, ctx, fresh)
    assert fresh["ok"] and fresh["text"].startswith("1\tinserted")
    assert first["revision"] != fresh["revision"]


def test_approval_freezes_basis_across_restart_and_requires_new_approval(tmp_path, monkeypatch):
    note = tmp_path / "book.md"
    note.write_text("# Note\nalpha\nend\n")
    _patch_script(monkeypatch, [
        _Msg(tool_calls=[_Tc("read", "read_file", {"path": str(note)})]),
        _Msg(tool_calls=[_Tc("edit", "edit_file", {"path": str(note), "old_string": "alpha", "new_string": "ALPHA"})]),
    ])
    sess = PenSession("s", "h")
    list(stream_chat(sess, note, "packet", llm=_cfg(), extra_roots=[tmp_path]))
    old_id = sess.pending["id"]
    sess = PenSession.from_dict(sess.to_dict())
    note.write_text(note.read_text() + "another agent's change\n")
    _patch_script(monkeypatch, [
        _Msg(tool_calls=[_Tc("read2", "read_file", {"path": str(note)})]),
        _Msg(tool_calls=[_Tc("edit2", "edit_file", {"path": str(note), "old_string": "alpha", "new_string": "ALPHA"})]),
    ])
    events = list(resume_chat(sess, note, allow=True, pending_id=old_id, llm=_cfg(), extra_roots=[tmp_path]))
    assert any(e.get("code") == "FILE_CHANGED" for e in events)
    assert "ALPHA" not in note.read_text()
    assert sess.pending and sess.pending["id"] != old_id
    assert sess.pending["basis_revision"] == filecoord.revision(filecoord.read_text(note))


def test_legacy_pending_cannot_borrow_a_newer_read(tmp_path, monkeypatch):
    note = tmp_path / "book.md"
    note.write_text("# Note\nalpha\nend\n")
    sess = PenSession("s", "h", read_versions=context(note)["read_versions"])
    sess.pending = {"id": "old", "name": "edit_file", "tool_call_id": "e",
                    "args": {"path": str(note), "old_string": "alpha", "new_string": "ALPHA"}}
    _patch_script(monkeypatch, [_Msg(content="Please read again.")])
    events = list(resume_chat(sess, note, allow=True, pending_id="old", llm=_cfg(), extra_roots=[tmp_path]))
    assert any(e.get("code") == "FILE_CHANGED" for e in events)
    assert "ALPHA" not in note.read_text()


def test_symlink_alias_joins_transaction_and_other_file_does_not(tmp_path):
    note = tmp_path / "book.md"
    note.write_text("note")
    alias = tmp_path / "link.md"
    alias.symlink_to(note)
    other = tmp_path / "other.md"
    other.write_text("other")
    started, acquired = threading.Event(), threading.Event()

    def waiter():
        started.set()
        with filecoord.file_lock(alias):
            acquired.set()

    with ThreadPoolExecutor(2) as pool:
        with filecoord.file_lock(note):
            pending = pool.submit(waiter)
            assert started.wait(2)
            independent = pool.submit(lambda: handle_edit_file({"old_string": "missing", "new_string": "x"}, context(other)))
            assert independent.result(timeout=2)["ok"] is False
            assert not acquired.is_set()
            filecoord.atomic_write(note, "new note")
        pending.result(timeout=2)
    assert acquired.is_set() and not filecoord._entries


def test_undo_then_old_proposal_cannot_overwrite_restored_note(tmp_path):
    note = tmp_path / "book.md"
    note.write_text("# Note\nalpha\nend\n")
    ctx = context(note, hid="h")
    assert handle_edit_file({"old_string": "alpha", "new_string": "ALPHA"}, ctx)["ok"]
    stale = context(note)
    snapshots.undo("h", note)
    result = handle_edit_file({"old_string": "end", "new_string": "END"}, stale)
    assert result["code"] == "FILE_CHANGED"
    assert note.read_text() == "# Note\nalpha\nend\n"


def test_cancel_is_run_specific_and_can_arrive_before_start():
    runs.cancel("s", "early")
    run = runs.begin("s", "early")
    with pytest.raises(runs.Cancelled):
        run.check()
    runs.finish(run)
    next_run = runs.begin("s", "next")
    assert not runs.cancel("s", "early")
    next_run.check()
    assert runs.cancel("s", "next")
    with pytest.raises(runs.Cancelled):
        next_run.check()
    runs.finish(next_run)


def test_cancelled_run_cannot_edit(tmp_path):
    note = tmp_path / "book.md"
    note.write_text("# Note\nalpha\nend\n")
    ctx = context(note)
    run = runs.begin("s", "r")
    ctx["run"] = run
    run.cancel()
    try:
        with pytest.raises(runs.Cancelled):
            handle_edit_file({"old_string": "alpha", "new_string": "ALPHA"}, ctx)
        assert "ALPHA" not in note.read_text()
    finally:
        runs.finish(run)


def test_cancel_pending_settles_whole_tool_batch_without_a_model():
    sess = STORE.create("h")
    sess.messages.append({"role": "assistant", "tool_calls": [
        {"id": "e1", "function": {"name": "edit_file", "arguments": "{}"}},
        {"id": "e2", "function": {"name": "edit_file", "arguments": "{}"}},
    ]})
    sess.pending = {"id": "p", "tool_call_id": "e1"}
    STORE.save(sess)
    with TestClient(app) as client:
        bad = client.post(f"/v1/sessions/{sess.session_id}/cancel", json={"pending_id": "wrong"})
        assert bad.status_code == 200 and sess.pending
        stopped = client.post(f"/v1/sessions/{sess.session_id}/cancel", json={"pending_id": "p"})
    assert stopped.status_code == 200 and sess.pending is None
    assert {m["tool_call_id"] for m in sess.messages if m["role"] == "tool"} == {"e1", "e2"}
    assert STORE.try_lock(sess) is not None
    STORE.lock_for(sess.session_id).release()


def test_pending_cancel_can_win_before_approval_run_is_registered():
    assert not runs.cancel_pending("sid", "p-before-start")
    run = runs.begin("sid", "approve-run", "p-before-start")
    with pytest.raises(runs.Cancelled):
        run.check()
    runs.finish(run)


def test_stop_http_releases_session_and_does_not_cancel_another_agent(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def fake_stream(sess, path, packet, **kwargs):
        yield {"type": "token", "text": "partial reply"}
        if sess.session_id == first:
            entered.set()
            assert release.wait(3)
            sess.active_run.check()
        else:
            sess.last_assistant = "other agent finished"
            yield {"type": "done", "has_substantive": True}

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    with TestClient(app) as client:
        first = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        second = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        def body(sid, rid):
            return {"session_id": sid, "run_id": rid, "selected_text": "shell 和 Bash",
                    "start_line": 1, "end_line": 1, "chip": "free", "user_text": "explain", "deep": False}
        with ThreadPoolExecutor(1) as pool:
            first_request = pool.submit(client.post, "/v1/chat", json=body(first, "first-run"))
            assert entered.wait(3)
            other = client.post("/v1/chat", json=body(second, "second-run"))
            assert '"type": "done"' in other.text
            cancelled = client.post(f"/v1/sessions/{first}/cancel", json={"run_id": "first-run"})
            assert cancelled.json()["cancelling"]
            release.set()
            result = first_request.result(timeout=3)
        assert '"type": "cancelled"' in result.text
        assert '"run_id": "first-run"' in result.text
        assert STORE.get(first).pending is None
        assert "partial reply" in STORE.get(first).last_assistant
        assert "other agent finished" == STORE.get(second).last_assistant
        acquired = STORE.try_lock(STORE.get(first))
        assert acquired is not None
        acquired[0].release()


def test_disconnect_before_generator_starts_releases_session_lock():
    import asyncio
    from pen.app import _run_response
    sess = STORE.create("h")
    acquired = STORE.try_lock(sess)
    run = runs.begin(sess.session_id, "never-started")
    sess.active_run = run
    def empty():
        yield
    response = _run_response(empty(), run, sess, acquired[0])
    asyncio.run(response.background())
    assert run.finished and sess.active_run is None
    acquired = STORE.try_lock(sess)
    assert acquired is not None
    acquired[0].release()


def test_same_batch_reread_cannot_upgrade_an_edit_basis(tmp_path):
    from pen.tutor import _run_tool_batch
    note = tmp_path / "book.md"
    note.write_text("# Note\nalpha\nend\n")
    sess = PenSession("s", "h")
    ctx = _tool_ctx(sess, note, [tmp_path])
    _remember_read(sess, ctx, handle_read_file({"path": str(note)}, ctx))
    note.write_text(note.read_text() + "new context\n")
    events = list(_run_tool_batch(sess, ctx, [
        _Tc("changed", "read_file", {"path": str(note)}),
        _Tc("fresh", "read_file", {"path": str(note)}),
        _Tc("blind", "edit_file", {"path": str(note), "old_string": "alpha", "new_string": "ALPHA"}),
    ]))
    assert not sess.pending
    assert not any(e["type"] == "approval" for e in events)
    assert events[-1].get("code") == "FILE_CHANGED"
    assert "ALPHA" not in note.read_text()
