from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import openai
import pytest
from fastapi.testclient import TestClient

from pen import config, gitops, libraries, snapshots
from pen.app import SEARCH_REPLY, app
from pen import config
from pen.config import REPO_ROOT

# 不写 `from pen.config import DEFAULT_HANDBOOK`：那是 import 时冻住的绑定，
# conftest 的 `_default_handbook_fixture` patch 的是 config 上的属性，够不着它。
# 每处都走 `config.DEFAULT_HANDBOOK`，取的才是 patch 之后的那本。
from pen.session import STORE

FIXTURE = Path(__file__).parent / "fixtures" / "mini_handbook.md"
FOLD = """<details>

<summary>🔍 实例 1：苏格拉底补的例子</summary>

```text
伪代码：shell 是一类，Bash 是一个
```

</details>
"""


def _isolate_pen(tmp_path: Path, monkeypatch) -> Path:
    lib = tmp_path / "libraries"
    lib.mkdir()
    monkeypatch.setattr(config, "PEN_DIR", tmp_path)
    monkeypatch.setattr(config, "LIBRARIES_DIR", lib)
    monkeypatch.setattr(libraries, "LIBRARIES_DIR", lib)
    monkeypatch.setattr(snapshots, "LIBRARIES_DIR", lib)
    return lib


def test_health_and_locate_q1() -> None:
    with TestClient(app) as client:
        assert client.get("/v1/health").json()["status"] == "ok"
        books = client.get("/v1/handbooks").json()["handbooks"]
        assert any(b["handbook_id"] == "swe-agent-v2" for b in books)
        text = config.DEFAULT_HANDBOOK.read_text(encoding="utf-8").splitlines()
        line = next(i for i, ln in enumerate(text, 1) if ln.startswith("**Q1. shell 和 Bash"))
        loc = client.get(f"/v1/handbooks/swe-agent-v2/locate?line={line}").json()
        assert loc["level"] == "Level 0"
        assert loc["q_title"] == "**Q1. shell 和 Bash 是什么关系？**"
        assert loc["kind"] == "q"


def test_session_get_and_resume() -> None:
    with TestClient(app) as client:
        created = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()
        sid = created["session_id"]
        got = client.get(f"/v1/sessions/{sid}")
        assert got.status_code == 200
        assert got.json()["session_id"] == sid
        assert got.json()["ui_messages"] == []
        resumed = client.post(
            "/v1/sessions",
            json={"handbook_id": "swe-agent-v2", "session_id": sid},
        ).json()
        assert resumed["session_id"] == sid
        missing = client.get("/v1/sessions/deadbeefdeadbeefdeadbeefdeadbeef")
        assert missing.status_code == 404


def test_create_without_id_mints_fresh_and_keeps_old() -> None:
    with TestClient(app) as client:
        first = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()
        second = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()
        assert first["session_id"] != second["session_id"]
        assert second["ui_messages"] == []
        assert second["last_anchor"] is None
        assert second["has_substantive"] is False
        old = client.get(f"/v1/sessions/{first['session_id']}")
        assert old.status_code == 200
        assert old.json()["session_id"] == first["session_id"]


def test_search_is_friendly_sse_and_skips_trajectory(tmp_path, monkeypatch) -> None:
    from pen import trajectory as trajmod

    monkeypatch.setattr(trajmod.config, "PEN_DIR", tmp_path)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        before = (tmp_path / "trajectories" / "swe-agent-v2.jsonl")
        before_n = before.read_text(encoding="utf-8").count("\n") if before.is_file() else 0
        resp = client.post(
            "/v1/chat",
            json={
                "session_id": sid,
                "selected_text": "查论文",
                "start_line": 695,
                "end_line": 695,
                "chip": "search",
                "user_text": "",
            },
        )
        assert resp.status_code == 200
        assert "P2" in resp.text
        assert SEARCH_REPLY[:8] in resp.text
        after_n = before.read_text(encoding="utf-8").count("\n") if before.is_file() else 0
        assert after_n == before_n
        sess = STORE.get(sid)
        assert sess.ui_messages == []


def test_import_vault_root_without_env(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.delenv("PEN_ALLOW_ROOTS", raising=False)
    book = tmp_path / "mini.md"
    book.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    with TestClient(app) as client:
        denied = client.post(
            "/v1/handbooks/import",
            json={"original_path": str(book), "handbook_id": "mini-vault"},
        )
        assert denied.status_code == 400
        assert "允许的根" in denied.json()["detail"]
        rooted = client.post(
            "/v1/handbooks/import",
            json={
                "original_path": str(book),
                "handbook_id": "mini-vault",
                "vault_root": str(tmp_path),
            },
        )
        assert rooted.status_code == 200
        assert rooted.json()["handbook_id"] == "mini-vault"
        assert rooted.json()["allow_root"] == str(tmp_path.resolve())
        text = client.get("/v1/handbooks/mini-vault/content")
        assert text.status_code == 200
        assert "Q1. shell" in text.json()["text"]
        slash = client.post(
            "/v1/handbooks/import",
            json={
                "original_path": str(book),
                "handbook_id": "slash",
                "vault_root": "/",
            },
        )
        assert slash.status_code == 400
        assert "文件系统根" in slash.json()["detail"]


def test_apply_uses_stored_allow_root(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.delenv("PEN_ALLOW_ROOTS", raising=False)
    book = tmp_path / "mini.md"
    book.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    q1 = next(
        i
        for i, ln in enumerate(book.read_text(encoding="utf-8").splitlines(), 1)
        if ln.startswith("**Q1. shell")
    )
    monkeypatch.setattr(
        "pen.app.propose_fold_md",
        lambda _sess, llm=None, allow_env_fallback=True, lang="zh": FOLD,
    )
    with TestClient(app) as client:
        imported = client.post(
            "/v1/handbooks/import",
            json={
                "original_path": str(book),
                "handbook_id": "mini-stored",
                "vault_root": str(tmp_path),
            },
        )
        assert imported.status_code == 200
        sid = client.post("/v1/sessions", json={"handbook_id": "mini-stored"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.last_assistant = "x" * 90
        sess.last_anchor = {
            "start_line": q1,
            "end_line": q1,
            "selected_text": "shell",
            "kind": "q",
            "level": "Level 0",
            "q_title": "**Q1. shell 和 Bash 是什么关系？**",
        }
        STORE.save(sess)
        proposed = client.post("/v1/writeback/propose", json={"session_id": sid})
        assert proposed.status_code == 200
        body = proposed.json()
        assert isinstance(body["insert_after_line"], int)
        assert body["insert_after_line"] >= 1
        assert body["instance_n"] >= 1
        applied = client.post(
            "/v1/writeback/apply",
            json={
                "session_id": sid,
                "proposal_id": proposed.json()["proposal_id"],
                "commit": False,
            },
        )
        assert applied.status_code == 200
        assert "苏格拉底补的例子" in book.read_text(encoding="utf-8")


def test_retarget_after_line_and_outline(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.delenv("PEN_ALLOW_ROOTS", raising=False)
    book = tmp_path / "plain.md"
    book.write_text("# 随便\n\nkeep-me\n", encoding="utf-8")
    monkeypatch.setattr(
        "pen.app.propose_fold_md",
        lambda _sess, llm=None, allow_env_fallback=True, lang="zh": FOLD,
    )
    with TestClient(app) as client:
        imported = client.post(
            "/v1/handbooks/import",
            json={
                "original_path": str(book),
                "handbook_id": "plain-note",
                "vault_root": str(tmp_path),
            },
        )
        assert imported.status_code == 200
        ol = client.get("/v1/handbooks/plain-note/outline")
        assert ol.status_code == 200
        assert ol.json()["headings"][0]["text"] == "随便"
        sid = client.post("/v1/sessions", json={"handbook_id": "plain-note"}).json()[
            "session_id"
        ]
        sess = STORE.get(sid)
        sess.last_assistant = "x" * 90
        sess.last_anchor = {
            "start_line": 1,
            "end_line": 1,
            "selected_text": "随便",
            "kind": "other",
            "level": "封面",
            "q_title": None,
        }
        STORE.save(sess)
        proposed = client.post("/v1/writeback/propose", json={"session_id": sid})
        assert proposed.status_code == 200
        pid = proposed.json()["proposal_id"]
        moved = client.post(
            "/v1/writeback/retarget",
            json={"proposal_id": pid, "kind": "after_line", "after_line": 3},
        )
        assert moved.status_code == 200
        assert moved.json()["insert_after_line"] == 3
        assert "where" in moved.json()
        bad = client.post(
            "/v1/writeback/retarget",
            json={"proposal_id": pid, "kind": "after_line", "after_line": 99},
        )
        assert bad.status_code == 400
        applied = client.post(
            "/v1/writeback/apply",
            json={"session_id": sid, "proposal_id": pid, "commit": False},
        )
        assert applied.status_code == 200
        text = book.read_text(encoding="utf-8")
        assert text.index("keep-me") < text.index("苏格拉底补的例子")


def test_chat_forwards_settings_overrides(monkeypatch) -> None:
    from pen.config import LLMConfig

    seen: dict = {}

    def fake_merge(**kw):
        seen["merge"] = kw
        return LLMConfig(
            base_url=kw.get("base_url") or "https://example.invalid/v1",
            api_key=kw.get("api_key") or "sk-test",
            model=kw.get("model") or "demo-model",
            key_source="settings",
            thinking=kw.get("thinking") or "off",
        )

    def fake_stream(sess, path, packet, llm=None, extra_roots=None, allow_env_fallback=True, lang="zh", **_kw):
        seen["llm"] = llm
        yield {
            "type": "done",
            "usage": {"context_tokens": 1, "completion_tokens": 1, "prompt_tokens": 1},
            "dynamic_chips": [],
            "has_substantive": False,
        }

    monkeypatch.setattr("pen.app.merge_llm", fake_merge)
    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    text = config.DEFAULT_HANDBOOK.read_text(encoding="utf-8").splitlines()
    line = next(i for i, ln in enumerate(text, 1) if ln.startswith("**Q1. shell 和 Bash"))
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        resp = client.post(
            "/v1/chat",
            json={
                "session_id": sid,
                "selected_text": "shell 和 Bash",
                "start_line": line,
                "end_line": line,
                "chip": "socratic",
                "user_text": "",
                "api_key": "sk-from-page",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4.1-mini",
                "thinking": "medium",
            },
        )
        assert resp.status_code == 200
    assert seen["merge"]["api_key"] == "sk-from-page"
    assert seen["merge"]["base_url"] == "https://api.openai.com/v1"
    assert seen["merge"]["model"] == "gpt-4.1-mini"
    assert seen["merge"]["thinking"] == "medium"
    assert seen["llm"] is not None
    assert seen["llm"].api_key == "sk-from-page"
    assert seen["llm"].thinking == "medium"


def test_chat_request_base_url_disables_env_fallback(monkeypatch) -> None:
    seen: dict = {}
    monkeypatch.setattr("pen.app.merge_llm", lambda **kw: None)

    def fake_stream(sess, path, packet, llm=None, extra_roots=None, allow_env_fallback=True, lang="zh", **_kw):
        seen["llm"] = llm
        seen["allow_env_fallback"] = allow_env_fallback
        yield {
            "type": "error",
            "message": "找不到模型配置。请到设置 → Socrates 填写 API Key。",
        }

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    text = config.DEFAULT_HANDBOOK.read_text(encoding="utf-8").splitlines()
    line = next(i for i, ln in enumerate(text, 1) if ln.startswith("**Q1. shell 和 Bash"))
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        resp = client.post(
            "/v1/chat",
            json={
                "session_id": sid,
                "selected_text": "shell 和 Bash",
                "start_line": line,
                "end_line": line,
                "chip": "socratic",
                "user_text": "",
                "base_url": "https://api.openai.com/v1",
            },
        )
        assert resp.status_code == 200
        assert "API Key" in resp.text
    assert seen["llm"] is None
    assert seen["allow_env_fallback"] is False


def test_chat_forwards_stored_allow_root(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.delenv("PEN_ALLOW_ROOTS", raising=False)
    book = tmp_path / "mini.md"
    book.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    q1 = next(
        i
        for i, ln in enumerate(book.read_text(encoding="utf-8").splitlines(), 1)
        if ln.startswith("**Q1. shell")
    )
    seen: dict = {}

    def fake_stream(sess, path, packet, llm=None, extra_roots=None, allow_env_fallback=True, lang="zh", **_kw):
        seen["extra_roots"] = extra_roots
        yield {
            "type": "done",
            "usage": {"context_tokens": 1, "completion_tokens": 1, "prompt_tokens": 1},
            "dynamic_chips": [],
            "has_substantive": False,
        }

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    with TestClient(app) as client:
        imported = client.post(
            "/v1/handbooks/import",
            json={
                "original_path": str(book),
                "handbook_id": "mini-root",
                "vault_root": str(tmp_path),
            },
        )
        assert imported.status_code == 200
        sid = client.post("/v1/sessions", json={"handbook_id": "mini-root"}).json()["session_id"]
        resp = client.post(
            "/v1/chat",
            json={
                "session_id": sid,
                "selected_text": "shell 和 Bash",
                "start_line": q1,
                "end_line": q1,
                "chip": "socratic",
                "user_text": "",
            },
        )
        assert resp.status_code == 200
    roots = [Path(r).expanduser().resolve() for r in seen["extra_roots"]]
    assert tmp_path.resolve() in roots


def test_chat_stream_raise_yields_error_and_records_not_ok(tmp_path: Path, monkeypatch) -> None:
    from pen.tutor import ProviderError

    _isolate_pen(tmp_path, monkeypatch)

    def boom_stream(sess, path, packet, llm=None, extra_roots=None, allow_env_fallback=True, lang="zh", **_kw):
        raise ProviderError("节点不收这把钥匙。请到设置 → Socrates 检查 API Key。")
        yield  # 只是为了让本函数成为生成器：第一次 next 才抛

    monkeypatch.setattr("pen.app.stream_chat", boom_stream)
    text = config.DEFAULT_HANDBOOK.read_text(encoding="utf-8").splitlines()
    line = next(i for i, ln in enumerate(text, 1) if ln.startswith("**Q1. shell 和 Bash"))
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        resp = client.post(
            "/v1/chat",
            json={
                "session_id": sid,
                "selected_text": "shell 和 Bash",
                "start_line": line,
                "end_line": line,
                "chip": "socratic",
                "user_text": "",
            },
        )
        assert resp.status_code == 200
        assert '"type": "error"' in resp.text
        assert "API Key" in resp.text
    turns = [
        json.loads(raw)
        for raw in (tmp_path / "trajectories" / "swe-agent-v2.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if raw.strip()
    ]
    assert turns[-1]["ok"] is False


def test_propose_provider_error_becomes_400(tmp_path: Path, monkeypatch) -> None:
    import httpx

    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.delenv("PEN_ALLOW_ROOTS", raising=False)
    book = tmp_path / "mini.md"
    book.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    q1 = next(
        i
        for i, ln in enumerate(book.read_text(encoding="utf-8").splitlines(), 1)
        if ln.startswith("**Q1. shell")
    )
    req = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    auth_exc = openai.AuthenticationError(
        "bad key", response=httpx.Response(401, request=req), body=None
    )

    class _BoomCompletions:
        def create(self, **_kwargs: Any) -> Any:
            raise auth_exc

    class _BoomClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.chat = SimpleNamespace(completions=_BoomCompletions())

    monkeypatch.setattr(openai, "OpenAI", _BoomClient)
    with TestClient(app) as client:
        imported = client.post(
            "/v1/handbooks/import",
            json={
                "original_path": str(book),
                "handbook_id": "mini-propose",
                "vault_root": str(tmp_path),
            },
        )
        assert imported.status_code == 200
        sid = client.post("/v1/sessions", json={"handbook_id": "mini-propose"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.last_assistant = "x" * 90
        sess.last_anchor = {
            "start_line": q1,
            "end_line": q1,
            "selected_text": "shell",
            "kind": "q",
            "level": "Level 0",
            "q_title": "**Q1. shell 和 Bash 是什么关系？**",
        }
        STORE.save(sess)
        proposed = client.post(
            "/v1/writeback/propose",
            json={"session_id": sid, "api_key": "sk-from-page"},
        )
        assert proposed.status_code == 400
        assert "设置" in proposed.json()["detail"]
        assert "API Key" in proposed.json()["detail"]
        assert "sk-from-page" not in proposed.json()["detail"]


def test_propose_releases_the_lock_even_when_the_tail_blows_up(
    tmp_path: Path, monkeypatch
) -> None:
    """propose 的尾段炸了，锁必须还是要放。

    v0.12.6 之前，从 `path.read_text()` 到最后那次 `_save_and_unlock` 之间
    没有任何异常保护。笔记在这中间被移走、被同步工具换成非 UTF-8，
    `lock.release()` 就不执行——这一场对读者是**永久 409「这场对话还在跑」**，
    重启 sidecar 之前解不开；v0.12.5 的「永不淘汰持锁会话」还让它永久
    占住一个内存槽，对 MAX_LIVE_SESSIONS 完全豁免。

    断言写成「再打一次不是 409」而不是去摸 `STORE._locks`：卡死的读者
    看到的就是那个 409，测的应该是他看到的东西。
    """
    from pen import app as appmod, insert as insertmod

    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.delenv("PEN_ALLOW_ROOTS", raising=False)
    book = tmp_path / "mini.md"
    book.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    q1 = next(
        i
        for i, ln in enumerate(book.read_text(encoding="utf-8").splitlines(), 1)
        if ln.startswith("**Q1. shell")
    )
    # 绕开 LLM：这条测的是锁的收尾，不是模型
    monkeypatch.setattr(appmod, "propose_fold_md", lambda *a, **k: "> 折叠正文\n")

    def _boom(*_a: Any, **_k: Any) -> str:
        raise UnicodeDecodeError("utf-8", b"", 0, 1, "笔记被换成了非 UTF-8")

    monkeypatch.setattr(insertmod, "render_new_text", _boom)

    # 不让 TestClient 把服务端异常原样抛回来——读者那边看到的是 500，
    # 而这条测的正是「500 之后锁还在不在」。
    with TestClient(app, raise_server_exceptions=False) as client:
        client.post(
            "/v1/handbooks/import",
            json={
                "original_path": str(book),
                "handbook_id": "mini-lock",
                "vault_root": str(tmp_path),
            },
        )
        sid = client.post("/v1/sessions", json={"handbook_id": "mini-lock"}).json()[
            "session_id"
        ]
        sess = STORE.get(sid)
        sess.last_assistant = "x" * 90
        sess.last_anchor = {
            "start_line": q1,
            "end_line": q1,
            "selected_text": "shell",
            "kind": "q",
            "level": "Level 0",
            "q_title": "**Q1. shell 和 Bash 是什么关系？**",
        }
        STORE.save(sess)
        body = {"session_id": sid}
        first = client.post("/v1/writeback/propose", json=body)
        assert first.status_code == 500, "尾段真炸了才算测到东西"
        again = client.post("/v1/writeback/propose", json=body)
        assert again.status_code != 409, "锁漏了：这一场对读者永久卡在「还在跑」"
        assert again.status_code == 500, "该是同一个炸法，不是别的"


def test_import_rejects_arbitrary_and_unsafe_ids(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    outsider = tmp_path / "secret.md"
    outsider.write_text("# 外面\n", encoding="utf-8")
    with TestClient(app) as client:
        denied = client.post(
            "/v1/handbooks/import",
            json={"original_path": str(outsider), "handbook_id": "evil"},
        )
        assert denied.status_code == 400
        assert "允许的根" in denied.json()["detail"]
        py = client.post(
            "/v1/handbooks/import",
            json={"original_path": str(REPO_ROOT / "pen" / "app.py"), "handbook_id": "py"},
        )
        assert py.status_code == 400
        assert "Markdown" in py.json()["detail"]
        bad_id = client.post(
            "/v1/handbooks/import",
            json={"original_path": str(config.DEFAULT_HANDBOOK), "handbook_id": "../escape"},
        )
        assert bad_id.status_code == 400
        assert "handbook_id" in bad_id.json()["detail"]
        ok = client.post(
            "/v1/handbooks/import",
            json={"original_path": str(config.DEFAULT_HANDBOOK), "handbook_id": "swe-agent-v2"},
        )
        assert ok.status_code == 200
        assert ok.json()["handbook_id"] == "swe-agent-v2"
    assert not (tmp_path / "libraries" / "evil" / "meta.json").is_file()
    assert not (tmp_path / "libraries" / "py" / "meta.json").is_file()


def test_import_allows_extra_root(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.setenv("PEN_ALLOW_ROOTS", str(tmp_path))
    book = tmp_path / "mini.md"
    book.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    with TestClient(app) as client:
        r = client.post(
            "/v1/handbooks/import",
            json={"original_path": str(book), "handbook_id": "mini-v011"},
        )
        assert r.status_code == 200
        assert r.json()["handbook_id"] == "mini-v011"
        text = client.get("/v1/handbooks/mini-v011/content")
        assert text.status_code == 200
        assert "Q1. shell" in text.json()["text"]


def test_apply_commit_failure_consumes_proposal(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.setenv("PEN_ALLOW_ROOTS", str(tmp_path))
    book = tmp_path / "mini.md"
    book.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    q1 = next(i for i, ln in enumerate(book.read_text(encoding="utf-8").splitlines(), 1) if ln.startswith("**Q1. shell"))
    monkeypatch.setattr(
        "pen.app.propose_fold_md",
        lambda _sess, llm=None, allow_env_fallback=True, lang="zh": FOLD,
    )

    def boom(_path, _msg):
        raise gitops.GitError("gpg failed")

    monkeypatch.setattr(gitops, "commit_original", boom)
    with TestClient(app) as client:
        imported = client.post(
            "/v1/handbooks/import",
            json={"original_path": str(book), "handbook_id": "mini-v011"},
        )
        assert imported.status_code == 200
        sid = client.post("/v1/sessions", json={"handbook_id": "mini-v011"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.last_assistant = "x" * 90
        sess.last_anchor = {
            "start_line": q1,
            "end_line": q1,
            "selected_text": "shell",
            "kind": "q",
            "level": "Level 0",
            "q_title": "**Q1. shell 和 Bash 是什么关系？**",
        }
        STORE.save(sess)
        proposed = client.post("/v1/writeback/propose", json={"session_id": sid})
        assert proposed.status_code == 200
        pid = proposed.json()["proposal_id"]
        before = book.read_text(encoding="utf-8")
        applied = client.post(
            "/v1/writeback/apply",
            json={"session_id": sid, "proposal_id": pid, "commit": True},
        )
        assert applied.status_code == 200
        body = applied.json()
        assert body["ok"] is True
        assert body["commit"] is None
        assert "gpg" in (body.get("commit_error") or "")
        mid = book.read_text(encoding="utf-8")
        assert len(mid) > len(before)
        assert "苏格拉底补的例子" in mid
        again = client.post(
            "/v1/writeback/apply",
            json={"session_id": sid, "proposal_id": pid, "commit": True},
        )
        assert again.status_code == 404
        assert book.read_text(encoding="utf-8") == mid


def _q1_line() -> int:
    text = config.DEFAULT_HANDBOOK.read_text(encoding="utf-8").splitlines()
    return next(i for i, ln in enumerate(text, 1) if ln.startswith("**Q1. shell 和 Bash"))


def test_chat_blocks_when_pending() -> None:
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.pending = {
            "id": "pend1",
            "name": "edit_file",
            "args": {"path": "x.md", "old_string": "a", "new_string": "b"},
            "tool_call_id": "c1",
            "rest": [],
        }
        STORE.save(sess)
        public = client.get(f"/v1/sessions/{sid}").json()
        assert public["pending"]["pending_id"] == "pend1"
        resp = client.post(
            "/v1/chat",
            json={
                "session_id": sid,
                "selected_text": "shell",
                "start_line": _q1_line(),
                "end_line": _q1_line(),
                "chip": "socratic",
                "user_text": "",
            },
        )
        assert resp.status_code == 400
        assert "审批" in resp.json()["detail"]


def test_approve_wrong_id_and_missing_session() -> None:
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        missing = client.post(
            "/v1/chat/approve",
            json={"session_id": sid, "pending_id": "nope", "allow": True},
        )
        assert missing.status_code == 400
        ghost = client.post(
            "/v1/chat/approve",
            json={
                "session_id": "deadbeefdeadbeefdeadbeefdeadbeef",
                "pending_id": "x",
                "allow": True,
            },
        )
        assert ghost.status_code == 404


def test_approve_allow_runs_resume(monkeypatch) -> None:
    seen: dict = {}

    def fake_resume(sess, path, *, allow, pending_id, llm=None, extra_roots=None, allow_env_fallback=True, lang="zh", **_kw):
        seen["allow"] = allow
        seen["pending_id"] = pending_id
        seen["path"] = path
        yield {
            "type": "tool",
            "name": "edit_file",
            "ok": True,
            "resolved": str(path),
            "detail": "",
            "preview": "已编辑",
            "line": 3,
        }
        yield {
            "type": "done",
            "usage": {"context_tokens": 1, "completion_tokens": 1, "prompt_tokens": 1},
            "dynamic_chips": [],
            "has_substantive": True,
        }

    monkeypatch.setattr("pen.app.resume_chat", fake_resume)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.pending = {
            "id": "pend2",
            "name": "edit_file",
            "args": {"old_string": "a", "new_string": "b"},
            "tool_call_id": "c1",
            "rest": [],
        }
        STORE.save(sess)
        resp = client.post(
            "/v1/chat/approve",
            json={"session_id": sid, "pending_id": "pend2", "allow": True},
        )
        assert resp.status_code == 200
        assert "edit_file" in resp.text
        assert '"type": "done"' in resp.text
    assert seen["allow"] is True
    assert seen["pending_id"] == "pend2"


def test_chat_409_when_session_busy() -> None:
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        lock = STORE.lock_for(sid)
        assert lock.acquire(blocking=False)
        try:
            resp = client.post(
                "/v1/chat",
                json={
                    "session_id": sid,
                    "selected_text": "shell",
                    "start_line": _q1_line(),
                    "end_line": _q1_line(),
                    "chip": "socratic",
                    "user_text": "",
                },
            )
            assert resp.status_code == 409
        finally:
            lock.release()


def test_snapshot_status_undo_redo_api(tmp_path: Path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    monkeypatch.setenv("PEN_ALLOW_ROOTS", str(tmp_path))
    book = tmp_path / "mini.md"
    book.write_text("# t\n\nA\n", encoding="utf-8")
    with TestClient(app) as client:
        imported = client.post(
            "/v1/handbooks/import",
            json={
                "original_path": str(book),
                "handbook_id": "mini-snap",
                "vault_root": str(tmp_path),
            },
        )
        assert imported.status_code == 200
        hid = imported.json()["handbook_id"]
        empty = client.get(f"/v1/handbooks/{hid}/snapshots").json()
        assert empty["undo_n"] == 0
        assert empty["can_undo"] is False
        snapshots.take_snapshot(hid, book, "pre-edit")
        book.write_text("# t\n\nB\n", encoding="utf-8")
        st = client.get(f"/v1/handbooks/{hid}/snapshots").json()
        assert st["can_undo"] is True
        rolled = client.post("/v1/writeback/rollback", json={"handbook_id": hid})
        assert rolled.status_code == 200
        assert book.read_text(encoding="utf-8") == "# t\n\nA\n"
        assert rolled.json()["can_redo"] is True
        redone = client.post("/v1/writeback/redo", json={"handbook_id": hid})
        assert redone.status_code == 200
        assert book.read_text(encoding="utf-8") == "# t\n\nB\n"
        again = client.post("/v1/writeback/rollback", json={"handbook_id": hid})
        assert again.status_code == 200
        assert book.read_text(encoding="utf-8") == "# t\n\nA\n"
        missing = client.post("/v1/writeback/rollback", json={"handbook_id": hid})
        assert missing.status_code == 400



# ── v0.8.1：深挖收件箱 ──────────────────────────────────────


def test_deep_inbox_unknown_session_is_404() -> None:
    with TestClient(app) as client:
        assert client.get("/v1/sessions/deadbeefdeadbeefdeadbeefdeadbeef/deep").status_code == 404


def test_deep_inbox_starts_empty_and_reports_no_runner() -> None:
    """sidecar 刚起来时返回 running: []，语义明确「没有在跑的，停轮询」——
    这正是选会话为键而不是 probe 为键的理由之一。"""
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        got = client.get(f"/v1/sessions/{sid}/deep").json()
        assert got["items"] == [] and got["running"] == []
        assert got["budget"]["max"] > 0


def test_deep_inbox_does_not_take_the_session_lock() -> None:
    """那把锁在 /v1/chat 整个请求期间被持有。轮询去抢会把读者的下一次
    提问顶成 409——所以这个端点在会话被锁着时也必须照常 200。"""
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        lock = STORE.lock_for(sid)
        assert lock.acquire(blocking=False)
        try:
            assert client.get(f"/v1/sessions/{sid}/deep").status_code == 200
        finally:
            lock.release()


def test_deep_inbox_surfaces_a_ripe_question(tmp_path, monkeypatch) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    from pen import diagnose, probe_store
    from pen.probe_store import DeepQuestion

    monkeypatch.setattr(probe_store, "probes_dir", lambda: tmp_path / "probes")
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.last_anchor = {"level": "Level 0", "kind": "q", "q_title": "**Q1. 甲？**"}
        sess.turns = 1
        STORE.save(sess)
        pid = probe_store.try_claim(sid, "swe-agent-v2", 1)
        probe_store.add_questions(
            sid,
            pid,
            [
                DeepQuestion(
                    id="d1",
                    text="白名单排在危险检测前面，危险命令会不会被静默放行？",
                    why="读者刚碰到权限",
                    timing="now",
                    atom=diagnose.atom_key(sess.last_anchor),
                    born_round=1,
                )
            ],
        )
        got = client.get(f"/v1/sessions/{sid}/deep?since=0").json()
        assert len(got["items"]) == 1
        assert got["items"][0]["kind"] == "deep"
        assert got["items"][0]["why"]
        assert got["cursor"] > 0


def test_get_session_restores_deep_questions(tmp_path, monkeypatch) -> None:
    """关掉侧栏再打开，已经花钱挖出来、也给读者看过的深题必须还在。

    深题不进 PenSession（后台线程碰它会和请求线程抢 to_dict() 快照），
    所以恢复只能在 app 层现拼。这条断言就是为了守住那次拼接——
    最初的实现漏了它，深题关一次侧栏就永久丢失。
    """
    _isolate_pen(tmp_path, monkeypatch)
    from pen import diagnose, probe_store
    from pen.probe_store import DeepQuestion

    monkeypatch.setattr(probe_store, "probes_dir", lambda: tmp_path / "probes")
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.last_anchor = {"level": "Level 0", "kind": "q", "q_title": "**Q1. 甲？**"}
        sess.turns = 1
        sess.last_chips = [{"id": "q0", "kind": "quick", "text": "实时层那条？"}]
        STORE.save(sess)
        pid = probe_store.try_claim(sid, "swe-agent-v2", 1)
        probe_store.add_questions(
            sid,
            pid,
            [
                DeepQuestion(
                    id="d1", text="白名单排在危险检测前面，危险命令会不会被静默放行？",
                    why="跨关", timing="now",
                    atom=diagnose.atom_key(sess.last_anchor), born_round=1,
                )
            ],
        )
        # 先抛给读者看过
        assert client.get(f"/v1/sessions/{sid}/deep?since=0").json()["items"]

        restored = client.get(f"/v1/sessions/{sid}").json()["dyn_chips"]
        kinds = [c["kind"] for c in restored]
        assert "deep" in kinds, f"深题没恢复：{restored}"
        assert kinds[0] == "deep", "深题应排在实时层前面"
        assert any(c["kind"] == "quick" for c in restored), "实时层那条不该被挤掉"


def test_pending_deep_questions_are_not_restored_early(tmp_path, monkeypatch) -> None:
    """还没成熟、没抛给读者看过的，不能因为重开侧栏就提前冒出来。"""
    _isolate_pen(tmp_path, monkeypatch)
    from pen import probe_store
    from pen.probe_store import DeepQuestion

    monkeypatch.setattr(probe_store, "probes_dir", lambda: tmp_path / "probes")
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        pid = probe_store.try_claim(sid, "swe-agent-v2", 1)
        probe_store.add_questions(
            sid, pid,
            [DeepQuestion(id="d9", text="挂在很后面那一关的问题？", timing="later",
                          target="Level 6", born_round=1)],
        )
        assert client.get(f"/v1/sessions/{sid}").json()["dyn_chips"] == []


def test_failed_spawn_gives_the_claim_back(tmp_path, monkeypatch) -> None:
    """占了坑却没起成线程，坑必须立刻还回去。

    不还的话要等五分钟孤儿回收，期间这个会话一次都探不了，而正在轮询的
    前端会对着一个永远不会完成的幽灵白等满 90 秒。
    """
    _isolate_pen(tmp_path, monkeypatch)
    from pen import probe as probemod, probe_store
    from pen.app import _maybe_probe
    from pen.config import LLMConfig
    from pen.session import PenSession

    monkeypatch.setattr(probe_store, "probes_dir", lambda: tmp_path / "probes")
    monkeypatch.setattr(probemod, "spawn", lambda job, pid: (_ for _ in ()).throw(RuntimeError("no thread")))

    sess = PenSession(session_id="spawnfail", handbook_id="swe-agent-v2")
    sess.last_assistant = "回答够长以判为实质。" * 12
    sess.turns = 1
    body = SimpleNamespace(
        chip="socratic", user_text="", base_url="",
        merged=lambda: LLMConfig("http://x", "sk", "m", "t", "off"),
    )
    got = _maybe_probe(sess, body, {"level": "Level 0"}, config.DEFAULT_HANDBOOK, "zh")
    assert got is False
    assert probe_store.load("spawnfail").running == [], "坑没还回去"


def test_the_probe_job_learns_which_book_it_is_reading(tmp_path, monkeypatch) -> None:
    """v0.15.7 的透传闸。**走完整条 HTTP**，不是直接调 `_maybe_probe`——

    真正会漏的是调用点：`_maybe_probe` 的 `book_title` 有默认值（不设必填是为了
    不动十七处测试构造点），所以 app 里忘了传参不会当场炸，只会静默退回
    「深挖不知道自己在读哪本书」——正是 v0.15.7 要治的那个病本身。
    """
    _isolate_pen(tmp_path, monkeypatch)
    from pen import libraries, probe as probemod

    seen: dict = {}
    monkeypatch.setattr(probemod, "spawn", lambda job, pid: seen.update(job=job))

    def fake_stream(sess, path, packet, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **_kw):
        sess.last_assistant = "讲了一大段。" * 30
        sess.has_substantive = True
        yield {"type": "done", "usage": {"context_tokens": 1, "completion_tokens": 1,
                                         "prompt_tokens": 1},
               "dynamic_chips": [], "has_substantive": True}

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.turns = 1
        # 会话是从内存里拿的，book_title 建场时就有了——**把它清掉**，
        # 逼这条路只能走 meta。落盘恢复回来的会话本来就是这个样子
        # （book_title 不落盘），而深挖恰恰最常发生在读者聊了几轮之后。
        sess.book_title = ""
        resp = client.post("/v1/chat", json=_chat_body(
            sid, _q1_line(), base_url="http://x", api_key="sk", model="m"))
        assert resp.status_code == 200
        title = libraries.get("swe-agent-v2").title

    job = seen.get("job")
    assert job is not None, "深挖压根没起，这条测试就没在测东西"
    assert job.book_title == title, "app 没把书名接上去"
    assert probemod.build_user_message(job).startswith("[你在带读哪本书]")


def test_the_openapi_version_is_not_a_second_hand_copied_literal() -> None:
    """v0.15.11。`FastAPI(version=...)` 曾经写死成 `"0.12.13"`，而
    `pen/__init__.py` / `manifest.json` / `package.json` / `pyproject.toml`
    四家早就是 `0.13.1`——落下了后面两个发布没人发现，因为**全仓没有任何代码读它**
    （唯一出口是 `/openapi.json` 的 `info.version`）。没人读的常量必然过期，
    所以这条测试就是那个读它的人。
    """
    from pen import __version__

    assert app.version == __version__, "别在 app.py 里抄第二份版本号字面量"


def test_all_four_version_sources_agree() -> None:
    """`pen/__init__.py` / `manifest.json` / `package.json` / `pyproject.toml`
    必须同版：公开仓里 sidecar 和插件是**同一个 GitHub Release tag**，
    而 Obsidian 要求 tag 逐字等于 `manifest.json` 的 `version`。

    v0.15.11 建这道闸时只盯了 `manifest.json` 一家。审查随即指出：
    `package.json` 和 `pyproject.toml` 仍然没人盯，下次发版漏改哪一家都不会变红——
    **正是 v0.15.11 自己写下的那条规律**（没人读的常量必然过期）。所以这里一次盯全。

    `versions.json` 也一并检：Obsidian 要求发布的版本必须在这张
    version → minAppVersion 表里，漏登记会让插件市场那边直接拒。

    用 `pytest.skip` 而不是裸 `return`，因为「静默不跑」和「跑过了」长得一模一样。
    **skip 的措辞只陈述事实，不下判断**：实验室仓的插件在 `obsidian/` 下、
    根目录确实没有这些文件，但那边 `obsidian/manifest.json` 现在是 `0.12.13`
    ——落后 `pen/__init__.py` 两个发布（这个插件总共只发过 `0.12.13` /
    `0.13.0` / `0.13.1` 三个版本，`versions.json` 的 key 就是完整清单）。跳过的地方恰好就是漂了的地方，
    这件事该被人看见，不该被一句「那边跳过是对的」盖住。
    """
    import json

    from pen import __version__, config

    root = config.REPO_ROOT
    if not (root / "manifest.json").is_file():
        pytest.skip(
            f"{root} 下没有 manifest.json——这里只检查了 pen/__init__.py "
            f"（{__version__}）。若这是实验室仓，插件在 obsidian/ 下，不在这道闸里。"
        )

    for name in ("manifest.json", "package.json"):
        got = json.loads((root / name).read_text(encoding="utf-8"))["version"]
        assert got == __version__, f"{name} 是 {got}，pen/__init__.py 是 {__version__}"

    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    line = next(ln for ln in pyproject.splitlines() if ln.startswith("version"))
    assert line.split("=")[1].strip().strip('"') == __version__, f"pyproject.toml：{line}"

    versions = json.loads((root / "versions.json").read_text(encoding="utf-8"))
    assert __version__ in versions, (
        f"versions.json 里没有 {__version__}——Obsidian 要求发布的版本登记在这张表里"
    )


# ── v0.10.0 计量 ────────────────────────────────────────────────


def test_done_event_carries_the_merged_session_spend(monkeypatch, tmp_path) -> None:
    """done 是自愈通道：轮询漏掉的、面板关着那段时间发生的深挖花销，
    下一轮 done 一定会补齐。所以它必须是**合并后**的三格。"""
    from pen import probe_store
    from pen.meter import Meter

    _isolate_pen(tmp_path, monkeypatch)

    def fake_stream(sess, path, packet, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **_kw):
        sess.spend["chat"] = {"calls": 2, "in_tokens": 900, "out_tokens": 40,
                              "cached_tokens": 0, "reasoning_tokens": 0}
        yield {
            "type": "done",
            "usage": {"context_tokens": 1, "completion_tokens": 1, "prompt_tokens": 1},
            "dynamic_chips": [],
            "has_substantive": False,
        }

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    monkeypatch.setattr("pen.app.probemod.spawn", lambda job, pid: None)
    text = config.DEFAULT_HANDBOOK.read_text(encoding="utf-8").splitlines()
    line = next(i for i, ln in enumerate(text, 1) if ln.startswith("**Q1. shell 和 Bash"))
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        # 先往账本里塞一笔深挖花销，模拟「面板关着的时候后台花了钱」
        pid = probe_store.try_claim(sid, "swe-agent-v2", 0)
        m = Meter()
        m.add({"prompt_tokens": 6000, "completion_tokens": 300})
        probe_store.release(sid, pid, spend=m.to_dict())

        resp = client.post("/v1/chat", json={
            "session_id": sid, "selected_text": "shell 和 Bash",
            "start_line": line, "end_line": line, "chip": "socratic", "user_text": "",
        })
        done = next(
            json.loads(ln[6:])
            for ln in resp.text.splitlines()
            if ln.startswith("data: ") and json.loads(ln[6:]).get("type") == "done"
        )
    assert done["spend"]["chat"]["in_tokens"] == 900
    assert done["spend"]["probe"]["in_tokens"] == 6000, "深挖那格从账本合进来"
    assert done["spend"]["fold"]["in_tokens"] == 0


def test_search_chip_done_carries_every_usage_key(tmp_path, monkeypatch) -> None:
    """search 分支不调 LLM 也发 done。context_tokens 一直缺着，
    前端靠 ?? prompt_tokens 才没露馅。"""
    _isolate_pen(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        resp = client.post("/v1/chat", json={
            "session_id": sid, "selected_text": "x", "start_line": 1, "end_line": 1,
            "chip": "search", "user_text": "",
        })
        done = next(
            json.loads(ln[6:])
            for ln in resp.text.splitlines()
            if ln.startswith("data: ") and json.loads(ln[6:]).get("type") == "done"
        )
    assert set(done["usage"]) == {"context_tokens", "prompt_tokens", "completion_tokens"}
    assert done["spend"]["chat"]["in_tokens"] == 0, "这一轮没花钱，但会话累计不该被清零"


def test_deep_inbox_never_takes_the_session_lock(tmp_path, monkeypatch) -> None:
    """那把锁在 /v1/chat 整个请求期间被持有。轮询端点去抢，就会把读者
    下一次提问顶成 409。这条测试今天之前是缺的——而往 inbox() 里加字段
    正是最容易有人顺手在端点里 STORE.get() 拿点别的东西的时刻。"""
    _isolate_pen(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        lock = STORE.lock_for(sid)
        assert lock.acquire(blocking=False)
        try:
            got = client.get(f"/v1/sessions/{sid}/deep?since=0")
        finally:
            lock.release()
    assert got.status_code == 200, "持锁期间也必须能查"
    assert "spend" in got.json()


def test_session_get_restores_spend_after_reopening_the_panel(tmp_path, monkeypatch) -> None:
    """关掉侧栏再打开，第三格要从这里恢复而不是归零。"""
    from pen import probe_store
    from pen.meter import Meter

    _isolate_pen(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        pid = probe_store.try_claim(sid, "swe-agent-v2", 0)
        m = Meter()
        m.add({"prompt_tokens": 1234, "completion_tokens": 56})
        probe_store.release(sid, pid, spend=m.to_dict())
        got = client.get(f"/v1/sessions/{sid}").json()
    assert got["spend"]["probe"]["in_tokens"] == 1234
    assert got["spend"]["chat"]["in_tokens"] == 0


# ── v0.10.1 参数透传 ────────────────────────────────────────────


def _chat_body(sid: str, line: int, **over) -> dict:
    body = {
        "session_id": sid, "selected_text": "shell 和 Bash",
        "start_line": line, "end_line": line, "chip": "socratic", "user_text": "",
    }
    body.update(over)
    return body


def _q1_line() -> int:
    text = config.DEFAULT_HANDBOOK.read_text(encoding="utf-8").splitlines()
    return next(i for i, ln in enumerate(text, 1) if ln.startswith("**Q1. shell 和 Bash"))


def test_chat_forwards_limits_to_stream_chat(monkeypatch) -> None:
    """透传是活的。没有这条，「默认值 = 现状」和「现有测试全绿」可以同时成立
    而整条管道是空的。"""
    seen: dict = {}

    def fake_stream(sess, path, packet, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **kw):
        seen["limits"] = kw.get("limits")
        yield {"type": "done", "usage": {"context_tokens": 1, "completion_tokens": 1,
                                         "prompt_tokens": 1},
               "dynamic_chips": [], "has_substantive": False}

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        client.post("/v1/chat", json=_chat_body(
            sid, _q1_line(), limits={"max_tool_rounds": 7, "cross_book_reads": 3}))
    got = seen["limits"]
    assert got is not None, "limits 根本没传到 stream_chat"
    assert (got.max_tool_rounds, got.cross_book_reads) == (7, 3)
    assert got.probe_max_per_window == 40, "没填的那些必须还是默认"


def test_approve_forwards_limits_too(monkeypatch) -> None:
    """一轮跨两个请求。approve 不带 limits 的话，批准之后的后半轮
    就变成一场没有上限的对话。"""
    seen: dict = {}

    def fake_resume(sess, path, *, allow, pending_id, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **kw):
        seen["limits"] = kw.get("limits")
        yield {"type": "done", "usage": {"context_tokens": 1, "completion_tokens": 1,
                                         "prompt_tokens": 1},
               "dynamic_chips": [], "has_substantive": False}

    monkeypatch.setattr("pen.app.resume_chat", fake_resume)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.pending = {"id": "pid1", "name": "edit_file", "args": {},
                        "original_path": str(config.DEFAULT_HANDBOOK)}
        client.post("/v1/chat/approve", json={
            "session_id": sid, "pending_id": "pid1", "allow": False,
            "limits": {"max_tool_rounds": 5},
        })
    assert seen["limits"] is not None and seen["limits"].max_tool_rounds == 5


def test_probe_job_freezes_the_request_limits(monkeypatch, tmp_path) -> None:
    """probe 在守护线程里跑，请求早结束了——限流值必须在 done 那一刻
    当场冻进 job，理由和 cfg 一样。"""
    _isolate_pen(tmp_path, monkeypatch)
    seen: dict = {}

    def fake_stream(sess, path, packet, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **_kw):
        sess.last_assistant = "讲了一大段。" * 30
        sess.has_substantive = True
        yield {"type": "done", "usage": {"context_tokens": 1, "completion_tokens": 1,
                                         "prompt_tokens": 1},
               "dynamic_chips": [], "has_substantive": True}

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    monkeypatch.setattr("pen.app.probemod.spawn", lambda job, pid: seen.update(job=job))
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        client.post("/v1/chat", json=_chat_body(
            sid, _q1_line(), api_key="sk-x",
            limits={"probe_max_per_window": 7, "probe_read_lines": 33}))
    job = seen.get("job")
    assert job is not None, "深挖根本没起——这条测试就白写了"
    assert job.limits.probe_max_per_window == 7
    assert job.limits.probe_read_lines == 33
    assert job.limits.max_tool_rounds == 100, "没填的还是默认"


def test_a_bogus_limits_payload_is_clamped_not_a_422(monkeypatch) -> None:
    """设置页填错一个字符，读者该看到夹紧后的正常回复，不是一个红色 422。
    这就是请求体用 dict 而不是子模型的原因。"""
    seen: dict = {}

    def fake_stream(sess, path, packet, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **kw):
        seen["limits"] = kw.get("limits")
        yield {"type": "done", "usage": {"context_tokens": 1, "completion_tokens": 1,
                                         "prompt_tokens": 1},
               "dynamic_chips": [], "has_substantive": False}

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        resp = client.post("/v1/chat", json=_chat_body(
            sid, _q1_line(), limits={"max_tool_rounds": "一百", "probe_concurrency": 99999}))
    assert resp.status_code == 200, "填错不该 422"
    assert seen["limits"].max_tool_rounds == 100, "看不懂的当没给"
    assert seen["limits"].probe_concurrency == 8, "超范围的夹紧"


def test_old_client_without_limits_still_works(monkeypatch) -> None:
    """旧插件、web/ 那个客户端、curl 都不带 limits 字段。"""
    seen: dict = {}

    def fake_stream(sess, path, packet, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **kw):
        seen["limits"] = kw.get("limits")
        yield {"type": "done", "usage": {"context_tokens": 1, "completion_tokens": 1,
                                         "prompt_tokens": 1},
               "dynamic_chips": [], "has_substantive": False}

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        resp = client.post("/v1/chat", json=_chat_body(sid, _q1_line()))
    assert resp.status_code == 200
    from pen.config import default_limits

    assert seen["limits"] == default_limits()


def test_cooldown_is_wired_from_the_ledger_to_the_gate(monkeypatch, tmp_path) -> None:
    """冷却要真的接上：app 得把 sess.turns 和账本里的 last_probe_round
    一起递给闸门。不接的话闸门代码在跑而参数恒为默认，等于没有。"""
    from pen import probe_store

    _isolate_pen(tmp_path, monkeypatch)
    seen: dict = {}

    def fake_stream(sess, path, packet, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **_kw):
        sess.last_assistant = "讲了一大段。" * 30
        sess.has_substantive = True
        yield {"type": "done", "usage": {"context_tokens": 1, "completion_tokens": 1,
                                         "prompt_tokens": 1},
               "dynamic_chips": [], "has_substantive": True}

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    monkeypatch.setattr("pen.app.probemod.spawn", lambda job, pid: seen.update(job=job))

    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        # 假装刚刚**真探过**一次（不能用 refund：v0.10.9 起 refund 会把
        # 冷却一起退掉——那次探索根本没打过 LLM，不该冻住后面几轮）
        pid = probe_store.try_claim(sid, "swe-agent-v2", 0)
        probe_store.release(sid, pid)
        assert probe_store.load(sid).last_probe_round == 0

        body = _chat_body(sid, _q1_line(), api_key="sk-x",
                          limits={"probe_every_n_rounds": 5})
        resp = client.post("/v1/chat", json=body)
        assert resp.status_code == 200
        assert "job" not in seen, "隔得不够，这一轮不该起深挖"

        seen.clear()
        body2 = _chat_body(sid, _q1_line(), api_key="sk-x")  # 不设冷却
        client.post("/v1/chat", json=body2)
    assert "job" in seen, "不设冷却时照常探——证明拦住上一次的是冷却，不是别的"


# ── v0.10.9 账目与配额的边角 ────────────────────────────────────


def test_propose_persists_and_returns_the_fold_spend(monkeypatch, tmp_path) -> None:
    """写回那一格的账以前只在内存里：读者写回完关掉 Obsidian、或者 sidecar
    重启，那笔账就永久丢——而「花了钱看不见」正是这一版要解决的问题本身。"""
    from pen import session as sessmod
    from pen.meter import KIND_FOLD

    _isolate_pen(tmp_path, monkeypatch)

    def fake_fold(sess, llm=None, allow_env_fallback=True, lang="zh"):
        sess.spend[KIND_FOLD] = {"calls": 1, "in_tokens": 7777, "out_tokens": 333,
                                 "cached_tokens": 0, "reasoning_tokens": 0}
        return FOLD

    monkeypatch.setattr("pen.app.propose_fold_md", fake_fold)
    text = config.DEFAULT_HANDBOOK.read_text(encoding="utf-8").splitlines()
    line = next(i for i, ln in enumerate(text, 1) if ln.startswith("**Q1. shell 和 Bash"))
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.last_assistant = "讲了一大段。" * 30
        sess.last_anchor = {"start_line": line, "end_line": line, "level": "Level 0"}
        got = client.post("/v1/writeback/propose", json={"session_id": sid})
        assert got.status_code == 200, got.text
        assert got.json()["spend"]["fold"]["in_tokens"] == 7777, "回包要带上刚花的钱"
        # 落盘了吗——把内存里的缓存丢掉再从盘上读
        on_disk = sessmod.load_session(sid)
    assert on_disk is not None
    assert on_disk.spend["fold"]["in_tokens"] == 7777, "propose 必须落盘，否则重启就丢账"


def test_approve_done_also_carries_spend(monkeypatch, tmp_path) -> None:
    """done 是「唯一的自愈通道」，一轮以审批结尾时这条通道以前是断的。"""
    _isolate_pen(tmp_path, monkeypatch)

    def fake_resume(sess, path, *, allow, pending_id, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **_kw):
        yield {"type": "done", "usage": {"context_tokens": 1, "completion_tokens": 1,
                                         "prompt_tokens": 1},
               "dynamic_chips": [], "has_substantive": False}

    monkeypatch.setattr("pen.app.resume_chat", fake_resume)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.pending = {"id": "p1", "name": "edit_file", "args": {},
                        "original_path": str(config.DEFAULT_HANDBOOK)}
        resp = client.post("/v1/chat/approve",
                           json={"session_id": sid, "pending_id": "p1", "allow": False})
        done = next(
            json.loads(ln[6:]) for ln in resp.text.splitlines()
            if ln.startswith("data: ") and json.loads(ln[6:]).get("type") == "done"
        )
    assert "spend" in done, "approve 的 done 也要带账"
    assert set(done["spend"]) == {"chat", "probe", "fold"}


def test_usage_endpoint_aggregates_across_sessions(monkeypatch, tmp_path) -> None:
    """设置页那块统计：状态行第三格答「这一场」，这里答「一共」。
    读者在那道多选题里两项都勾了，v0.10.0 只做了前者。"""
    from pen import probe_store, session as sessmod
    from pen.meter import Meter

    _isolate_pen(tmp_path, monkeypatch)
    with TestClient(app) as client:
        empty = client.get("/v1/usage").json()
        assert empty["total"] == 0 and empty["sessions"] == 0

        # 两场对话，各记一笔主对话的账
        for i, n in enumerate((1000, 2000)):
            sid = client.post("/v1/sessions",
                              json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
            sess = STORE.get(sid)
            sess.spend["chat"] = {"calls": 1, "in_tokens": n, "out_tokens": 100,
                                  "cached_tokens": n // 2, "reasoning_tokens": 0}
            sessmod.save_session(sess)
            if i == 0:
                # 顺带给其中一场记一笔深挖的账（它落在另一个目录里）
                pid = probe_store.try_claim(sid, "swe-agent-v2", 0)
                m = Meter()
                m.add({"prompt_tokens": 500, "completion_tokens": 50})
                probe_store.release(sid, pid, spend=m.to_dict())

        got = client.get("/v1/usage").json()
    assert got["sessions"] == 2
    assert got["spend"]["chat"]["in_tokens"] == 3000, "两场的主对话要加起来"
    assert got["spend"]["probe"]["in_tokens"] == 500, "深挖那格从另一个目录合进来"
    assert got["spend"]["chat"]["cached_tokens"] == 1500, "缓存命中也要累计"
    assert got["total"] == 3000 + 100 + 100 + 500 + 50
    assert got["skipped"] == 0


def test_usage_endpoint_survives_a_corrupt_file(monkeypatch, tmp_path) -> None:
    """几千个会话文件里坏一个，不能让整块统计挂掉。"""
    _isolate_pen(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions",
                          json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.spend["chat"] = {"calls": 1, "in_tokens": 42, "out_tokens": 8,
                              "cached_tokens": 0, "reasoning_tokens": 0}
        from pen import session as sessmod

        sessmod.save_session(sess)
        (config.PEN_DIR / "sessions" / "broken.json").write_text("{不是 JSON", encoding="utf-8")
        got = client.get("/v1/usage").json()
    assert got["total"] == 50, "好的那份照样算得出"
    assert got["skipped"] == 1, "坏的那份要报出来，别装作没有"


def test_usage_endpoint_can_filter_by_handbook(monkeypatch, tmp_path) -> None:
    _isolate_pen(tmp_path, monkeypatch)
    from pen import session as sessmod

    with TestClient(app) as client:
        sid = client.post("/v1/sessions",
                          json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        sess = STORE.get(sid)
        sess.spend["chat"] = {"calls": 1, "in_tokens": 90, "out_tokens": 10,
                              "cached_tokens": 0, "reasoning_tokens": 0}
        sessmod.save_session(sess)
        mine = client.get("/v1/usage?handbook_id=swe-agent-v2").json()
        other = client.get("/v1/usage?handbook_id=别的书").json()
    assert mine["total"] == 100 and mine["sessions"] == 1
    assert other["total"] == 0 and other["sessions"] == 0


def test_usage_endpoint_never_takes_the_session_lock(monkeypatch, tmp_path) -> None:
    """老规矩：那把锁在 /v1/chat 整个请求期间持有，抢它会把读者下一次提问
    顶成 409。"""
    _isolate_pen(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions",
                          json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        lock = STORE.lock_for(sid)
        assert lock.acquire(blocking=False)
        try:
            got = client.get("/v1/usage")
        finally:
            lock.release()
    assert got.status_code == 200


# ── v0.12.1 点过的深题要消失，队列要往前走 ──────────────────────


def _seed_pool(sid: str, hid: str, n: int, state: str = "pending") -> None:
    """攒一池子**老**题：born_round=0、timing=later。

    这是死锁真实发生的形状——题在池子里躺了几轮没抛出去。
    拿 timing=now + atom="a" 造种子是测不到的：atom 永远匹配不上真实锚点，
    而 now 那条的另一半放行条件是「就是这一轮生的」，也不成立。
    """
    from pen import probe_store
    from pen.probe_store import DeepQuestion

    led = probe_store.load(sid, hid)
    for i in range(n):
        led.seq += 1
        led.pool.append(DeepQuestion(
            id=f"q{i}", text=f"第 {i} 个攒着的问题，它到底在讲哪一层？",
            seq=led.seq, state=state, born_round=0, atom="a", timing="later"))
    probe_store.save(led)


def test_a_full_queue_no_longer_deadlocks_the_whole_feature(monkeypatch, tmp_path) -> None:
    """**这是最严重的一条。**

    inbox() 是唯一会投递、也是唯一会跑 TTL 过期的地方，而它只被 /deep 端点调，
    那个端点又只在 deep_running 为真时才被前端轮询。于是池子攒够
    PROBE_PENDING_CAP 条之后：should_probe 永久返回 backlog-full → 不起探索 →
    不轮询 → 不投递也不过期 → **深挖静默停摆，读者只能新开会话。**

    修法是补上 v0.8.1 设计过却一直没实现的那条路：每轮 done 都把池子里成熟的
    题捎出来，不依赖探索跑不跑。
    """
    from pen import probe

    _isolate_pen(tmp_path, monkeypatch)

    def fake_stream(sess, path, packet, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **_kw):
        sess.last_assistant = "讲了一大段。" * 30
        sess.has_substantive = True
        yield {"type": "done", "usage": {"context_tokens": 1, "completion_tokens": 1,
                                         "prompt_tokens": 1},
               "dynamic_chips": [], "has_substantive": True}

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        _seed_pool(sid, "swe-agent-v2", config.PROBE_PENDING_CAP)
        sess = STORE.get(sid)
        sess.last_anchor = {"level": "Level 0", "start_line": 1, "end_line": 1}
        # 题在池子里躺了几轮：later 那条要等够两轮才成熟
        sess.turns = 5

        # 池子满了，探索这一轮确实起不来——闸门本身没错
        go, why = probe.should_probe(
            enabled=True, ok=True, chip="socratic", pending=False,
            reply="讲了一大段。" * 30, anchor=sess.last_anchor, probe_calls=0,
            pending_pool=config.PROBE_PENDING_CAP, has_llm=True)
        assert (go, why) == (False, "backlog-full")

        resp = client.post("/v1/chat", json=_chat_body(sid, _q1_line()))
        done = next(
            json.loads(ln[6:]) for ln in resp.text.splitlines()
            if ln.startswith("data: ") and json.loads(ln[6:]).get("type") == "done"
        )
    assert done.get("deep_running") is False, "探索确实没起——这是前提，不是 bug"
    assert done.get("deep_items"), (
        "探索没起，但池子里的题必须照样发出来——否则 backlog-full 就是个死结"
    )


def test_done_pops_the_queue_every_turn(monkeypatch, tmp_path) -> None:
    """点过一条之后，下一条要当轮顶上来，不用等下一次探索。"""
    _isolate_pen(tmp_path, monkeypatch)

    def fake_stream(sess, path, packet, llm=None, extra_roots=None,
                    allow_env_fallback=True, lang="zh", **_kw):
        sess.last_assistant = "讲了一大段。" * 30
        sess.has_substantive = True
        yield {"type": "done", "usage": {"context_tokens": 1, "completion_tokens": 1,
                                         "prompt_tokens": 1},
               "dynamic_chips": [], "has_substantive": True}

    monkeypatch.setattr("pen.app.stream_chat", fake_stream)
    monkeypatch.setattr("pen.app.probemod.spawn", lambda job, pid: None)
    seen: list[str] = []
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        _seed_pool(sid, "swe-agent-v2", 3)
        sess = STORE.get(sid)
        sess.last_anchor = {"level": "Level 0", "start_line": 1, "end_line": 1}
        sess.turns = 5
        for _ in range(3):
            resp = client.post("/v1/chat", json=_chat_body(sid, _q1_line()))
            done = next(
                json.loads(ln[6:]) for ln in resp.text.splitlines()
                if ln.startswith("data: ") and json.loads(ln[6:]).get("type") == "done"
            )
            seen += [c["text"] for c in done.get("deep_items") or []]
    fresh = [t for i, t in enumerate(seen) if t not in seen[:i]]
    assert len(fresh) >= 2, f"三轮下来只抛出 {len(fresh)} 条不同的题：{seen}"


def test_a_clicked_question_never_comes_back_on_reload(monkeypatch, tmp_path) -> None:
    """clicked 是**读者已经问过**的题。恢复回来等于关一次面板它就复活。"""
    from pen import probe_store

    _isolate_pen(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()["session_id"]
        _seed_pool(sid, "swe-agent-v2", 2, state="shown")
        led = probe_store.load(sid)
        asked = led.pool[0].text
        got = client.get(f"/v1/sessions/{sid}").json()
        assert any(c["text"] == asked for c in got["dyn_chips"]), "没问过的要在"

        assert probe_store.mark_clicked(sid, asked) is not None
        again = client.get(f"/v1/sessions/{sid}").json()
    texts = [c["text"] for c in again["dyn_chips"]]
    assert asked not in texts, "问过的题不能在重开面板之后复活"
    assert len(texts) == 1, "另一条没问过的还得留着"


def test_imported_book_name_reaches_the_session_on_disk(tmp_path: Path, monkeypatch) -> None:
    """v0.15.0 端到端：导一本别的书，落盘的 messages[0] 里就得是**那本书**。

    走完整条 HTTP（import → sessions），然后直接读 `.pen/sessions/<sid>.json`，
    因为这一版改的正是「建场那一刻写进 messages[0] 并落盘」的东西。
    """
    _isolate_pen(tmp_path, monkeypatch)
    book = tmp_path / "dqn.md"
    book.write_text(
        "# 从零手写 DQN · 强化学习通关手册\n\n"
        "# Level 0 — 从多臂老虎机到 MDP\n\n"
        "## 第三拍 · 出身：Bellman 1957\n\n"
        "**Q1. 折扣因子为什么必须小于 1？**\n\n"
        "因为 Bellman 算子要是 γ-压缩的。\n\n"
        "〔回读：第三拍 · 出身〕\n",
        encoding="utf-8",
    )
    with TestClient(app) as client:
        ok = client.post(
            "/v1/handbooks/import",
            json={
                "original_path": str(book),
                "handbook_id": "dqn",
                "vault_root": str(tmp_path),
            },
        )
        assert ok.status_code == 200, ok.text
        sid = client.post("/v1/sessions", json={"handbook_id": "dqn"}).json()["session_id"]

    saved = json.loads((tmp_path / "sessions" / f"{sid}.json").read_text(encoding="utf-8"))
    first = saved["messages"][0]["content"]
    assert "《从零手写 DQN · 强化学习通关手册》" in first
    assert "SWE" not in first, "别的书的会话里不该还写着那本手册"
    assert "book_title" not in saved, "它不落盘"
