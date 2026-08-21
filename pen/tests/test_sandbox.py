from __future__ import annotations

from pathlib import Path

import pytest

from pen.config import DEFAULT_HANDBOOK, REPO_ROOT
from pen.sandbox import (
    SandboxError,
    assert_handbook_path,
    assert_readable,
    assert_write_target,
    parse_vault_root,
)


def test_write_only_original(tmp_path: Path) -> None:
    original = tmp_path / "book.md"
    other = tmp_path / "other.md"
    original.write_text("a", encoding="utf-8")
    other.write_text("b", encoding="utf-8")
    assert assert_write_target(original, original) == original.resolve()
    with pytest.raises(SandboxError):
        assert_write_target(original, other)
    with pytest.raises(SandboxError):
        assert_write_target(original, "/etc/passwd")


def test_read_allowlist(tmp_path: Path) -> None:
    original = tmp_path / "book.md"
    sibling = tmp_path / "lab" / "notes.txt"
    sibling.parent.mkdir()
    original.write_text("a", encoding="utf-8")
    sibling.write_text("b", encoding="utf-8")
    outsider = tmp_path.parent / "nope.md"
    outsider.write_text("x", encoding="utf-8")
    assert assert_readable(original, original)
    assert assert_readable(original, sibling, extra_roots=[tmp_path])
    with pytest.raises(SandboxError):
        assert_readable(original, outsider, extra_roots=[tmp_path])
    with pytest.raises(SandboxError):
        assert_readable(original, tmp_path / ".env")


def test_relative_not_tied_to_cwd(tmp_path: Path, monkeypatch) -> None:
    original = tmp_path / "book.md"
    original.write_text("a", encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)
    got = assert_readable(original, "book.md")
    assert got == original.resolve()


def test_handbook_path_allows_default_and_rejects_outsiders(tmp_path: Path, monkeypatch) -> None:
    assert assert_handbook_path(DEFAULT_HANDBOOK) == DEFAULT_HANDBOOK.resolve()
    outsider = tmp_path / "secret.md"
    outsider.write_text("# x\n", encoding="utf-8")
    with pytest.raises(SandboxError, match="允许的根"):
        assert_handbook_path(outsider)
    py = REPO_ROOT / "pen" / "app.py"
    with pytest.raises(SandboxError, match="Markdown"):
        assert_handbook_path(py)
    env = tmp_path / ".env"
    env.write_text("K=1\n", encoding="utf-8")
    with pytest.raises(SandboxError, match="受保护"):
        assert_handbook_path(env)
    monkeypatch.setenv("PEN_ALLOW_ROOTS", str(tmp_path))
    assert assert_handbook_path(outsider) == outsider.resolve()


def test_handbook_path_extra_roots_without_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PEN_ALLOW_ROOTS", raising=False)
    outsider = tmp_path / "secret.md"
    outsider.write_text("# x\n", encoding="utf-8")
    assert assert_handbook_path(outsider, extra_roots=[tmp_path]) == outsider.resolve()
    with pytest.raises(SandboxError, match="允许的根"):
        assert_handbook_path(outsider, extra_roots=[tmp_path / "nope"])
    with pytest.raises(SandboxError, match="文件系统根"):
        parse_vault_root("/")
    assert parse_vault_root(None) == []
    assert parse_vault_root(str(tmp_path)) == [tmp_path.resolve()]
    missing = tmp_path / "not-a-dir"
    with pytest.raises(SandboxError, match="不是目录"):
        parse_vault_root(str(missing))


def test_read_blocks_obsidian_but_allows_plain_data_json(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    note = vault / "notes" / "book.md"
    plain = vault / "notes" / "data.json"
    plugin_data = vault / ".obsidian" / "plugins" / "socrates-pen" / "data.json"
    note.parent.mkdir(parents=True)
    plugin_data.parent.mkdir(parents=True)
    note.write_text("# vault 里的手册\n", encoding="utf-8")
    plain.write_text("{}\n", encoding="utf-8")
    plugin_data.write_text('{"key": "sk-x"}\n', encoding="utf-8")
    assert assert_readable(note, plain, extra_roots=[vault]) == plain.resolve()
    with pytest.raises(SandboxError, match="受保护"):
        assert_readable(note, plugin_data, extra_roots=[vault])
    with pytest.raises(SandboxError, match="受保护"):
        assert_readable(note, "../.obsidian/plugins/socrates-pen/data.json")


def test_handbook_path_rejects_book_under_obsidian(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    obs_book = vault / ".obsidian" / "book.md"
    obs_book.parent.mkdir(parents=True)
    obs_book.write_text("# x\n", encoding="utf-8")
    with pytest.raises(SandboxError, match="受保护"):
        assert_handbook_path(obs_book, extra_roots=[vault])


def test_protected_path_is_case_insensitive(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    note = vault / "book.md"
    weird = vault / ".OBSIDIAN" / "plugins" / "socrates-pen" / "data.json"
    env_local = vault / ".env.local"
    note.parent.mkdir(parents=True)
    weird.parent.mkdir(parents=True)
    note.write_text("# x\n", encoding="utf-8")
    weird.write_text("{}\n", encoding="utf-8")
    env_local.write_text("K=1\n", encoding="utf-8")
    with pytest.raises(SandboxError, match="受保护"):
        assert_readable(note, weird, extra_roots=[vault])
    with pytest.raises(SandboxError, match="受保护"):
        assert_readable(note, env_local, extra_roots=[vault])
