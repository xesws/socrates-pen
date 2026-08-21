from __future__ import annotations

from pathlib import Path

from pen import snapshots as snapmod
from pen.config import SNAPSHOT_KEEP


def _iso(tmp_path: Path, monkeypatch) -> None:
    lib = tmp_path / "libraries"
    lib.mkdir()
    monkeypatch.setattr(snapmod, "LIBRARIES_DIR", lib)


def test_undo_stack_two_steps_then_redo(tmp_path: Path, monkeypatch) -> None:
    _iso(tmp_path, monkeypatch)
    book = tmp_path / "note.md"
    book.write_text("A\n", encoding="utf-8")
    snapmod.take_snapshot("hid", book, "pre-edit")
    book.write_text("B\n", encoding="utf-8")
    snapmod.take_snapshot("hid", book, "pre-edit")
    book.write_text("C\n", encoding="utf-8")
    st = snapmod.status("hid")
    assert st["undo_n"] == 2
    assert st["can_undo"] is True
    assert st["can_redo"] is False

    snapmod.undo("hid", book)
    assert book.read_text(encoding="utf-8") == "B\n"
    st = snapmod.status("hid")
    assert st["undo_n"] == 1
    assert st["redo_n"] == 1

    snapmod.undo("hid", book)
    assert book.read_text(encoding="utf-8") == "A\n"
    st = snapmod.status("hid")
    assert st["undo_n"] == 0
    assert st["redo_n"] == 2

    snapmod.redo("hid", book)
    assert book.read_text(encoding="utf-8") == "B\n"

    snapmod.take_snapshot("hid", book, "pre-edit")
    book.write_text("D\n", encoding="utf-8")
    st = snapmod.status("hid")
    assert st["redo_n"] == 0
    assert st["can_redo"] is False


def test_undo_empty_raises(tmp_path: Path, monkeypatch) -> None:
    _iso(tmp_path, monkeypatch)
    book = tmp_path / "note.md"
    book.write_text("A\n", encoding="utf-8")
    try:
        snapmod.undo("hid", book)
        raise AssertionError("should have raised")
    except FileNotFoundError:
        pass
    assert book.read_text(encoding="utf-8") == "A\n"


def test_rollback_alias_pops(tmp_path: Path, monkeypatch) -> None:
    _iso(tmp_path, monkeypatch)
    book = tmp_path / "note.md"
    book.write_text("A\n", encoding="utf-8")
    snapmod.take_snapshot("hid", book, "pre")
    book.write_text("B\n", encoding="utf-8")
    snapmod.rollback("hid", book)
    assert book.read_text(encoding="utf-8") == "A\n"
    assert snapmod.status("hid")["undo_n"] == 0


def test_prune_caps_total(tmp_path: Path, monkeypatch) -> None:
    _iso(tmp_path, monkeypatch)
    book = tmp_path / "note.md"
    book.write_text("x\n", encoding="utf-8")
    for i in range(SNAPSHOT_KEEP + 3):
        book.write_text(f"{i}\n", encoding="utf-8")
        snapmod.take_snapshot("hid", book, "pre-edit")
    st = snapmod.status("hid")
    assert st["undo_n"] + st["redo_n"] <= SNAPSHOT_KEEP
