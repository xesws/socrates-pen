from __future__ import annotations

from pathlib import Path

from pen.readtool import read_file_report, read_file_sandboxed


def test_read_numbers_and_denies_escape(tmp_path: Path) -> None:
    book = tmp_path / "book.md"
    book.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    out = read_file_sandboxed(book, str(book), offset=2, limit=1)
    assert out.startswith("2\tbeta")
    denied = read_file_sandboxed(book, "/etc/passwd")
    assert denied.startswith("错误：")


def test_relative_name_ignores_process_cwd(tmp_path: Path, monkeypatch) -> None:
    book = tmp_path / "SWE-Agent通关手册v2.md"
    book.write_text("hello\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    report = read_file_report(book, "SWE-Agent通关手册v2.md")
    assert report["ok"] is True
    assert "hello" in report["text"]
    assert Path(report["resolved"]) == book.resolve()


def test_empty_path_reads_original(tmp_path: Path, monkeypatch) -> None:
    book = tmp_path / "book.md"
    book.write_text("only-original\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "..")
    report = read_file_report(book, "")
    assert report["ok"] is True
    assert "only-original" in report["text"]


def test_backticks_and_line_suffix(tmp_path: Path) -> None:
    book = tmp_path / "book.md"
    book.write_text("x\n", encoding="utf-8")
    report = read_file_report(book, "`book.md`:3-10")
    assert report["ok"] is True


def test_relative_lab_from_foreign_cwd(tmp_path: Path, monkeypatch) -> None:
    book = tmp_path / "book.md"
    lab = tmp_path / "lab" / "notes.txt"
    lab.parent.mkdir()
    book.write_text("book\n", encoding="utf-8")
    lab.write_text("labnote\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)
    report = read_file_report(book, "lab/notes.txt", extra_roots=[tmp_path])
    assert report["ok"] is True
    assert "labnote" in report["text"]
