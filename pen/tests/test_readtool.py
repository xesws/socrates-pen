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


# ── v0.26.0：分段读。截断按行切、说清下一段 offset；错参是工具错误不是崩溃 ──


def _long_book(tmp_path: Path, n: int = 1000) -> Path:
    book = tmp_path / "long.md"
    book.write_text(
        "".join(f"第 {i} 行，这一行凑一点长度好让字符先到顶。\n" for i in range(1, n + 1)),
        encoding="utf-8",
    )
    return book


def _ctx(book: Path) -> dict:
    return {"original_path": book, "extra_roots": [book.parent], "handbook_id": ""}


def test_char_cap_cuts_on_a_line_boundary_and_says_where_to_resume(tmp_path: Path) -> None:
    from pen.compact import _line_span
    from pen.config import MAX_OUTPUT

    book = _long_book(tmp_path)
    report = read_file_report(book, str(book), offset=1, limit=1000)
    text = report["text"]
    assert report["truncated"] is True
    a, b = report["lines"]
    assert a == 1 and 1 < b < 1000
    assert report["total"] == 1000
    numbered = [ln for ln in text.splitlines() if "\t" in ln]
    assert numbered[-1].startswith(f"{b}\t")
    assert numbered[-1].endswith("好让字符先到顶。"), "最后一行必须是整行，不能切半"
    assert f"offset={b + 1}" in text
    assert "文件共 1000 行" in text
    assert f"limit 不超过 {b}" in text
    assert len(text) <= MAX_OUTPUT + 200
    assert _line_span(text) == (1, b), "尾注没有行号前缀，压缩层照旧认得出区间"


def test_limit_cut_footer_names_the_next_offset(tmp_path: Path) -> None:
    from pen.compact import _line_span

    book = _long_book(tmp_path)
    r = read_file_report(book, str(book), offset=1, limit=10)
    assert r["text"].endswith("（第 1–10 行，文件共 1000 行；接着读 offset=11）")
    assert r["truncated"] is False
    assert r["lines"] == [1, 10]
    assert _line_span(r["text"]) == (1, 10)


def test_reading_to_the_end_is_byte_identical_to_before(tmp_path: Path) -> None:
    book = tmp_path / "book.md"
    book.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    r = read_file_report(book, str(book), offset=2, limit=50)
    assert r["text"] == "2\tbeta\n3\tgamma\n"
    assert r["lines"] == [2, 3] and r["total"] == 3 and r["truncated"] is False


def test_out_of_range_says_how_long_the_file_is(tmp_path: Path) -> None:
    book = _long_book(tmp_path)
    r = read_file_report(book, str(book), offset=5000, limit=10)
    assert r["text"] == "(空文件或超出范围：文件共 1000 行)"
    assert r["lines"] == [] and r["total"] == 1000
    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")
    assert read_file_report(empty, str(empty))["text"] == "(空文件或超出范围)"


def test_default_limit_is_one_number_everywhere(tmp_path: Path) -> None:
    import inspect

    from pen.agent.registry import READ_FILE_SCHEMA
    from pen.agent.tools_impl import handle_read_file
    from pen.config import READ_LIMIT_DEFAULT

    props = READ_FILE_SCHEMA["function"]["parameters"]["properties"]
    assert props["limit"]["default"] == READ_LIMIT_DEFAULT
    assert "description" in props["offset"] and "description" in props["limit"]
    assert "分段" in READ_FILE_SCHEMA["function"]["description"]
    assert inspect.signature(read_file_report).parameters["limit"].default == READ_LIMIT_DEFAULT
    book = _long_book(tmp_path, n=200)
    out = handle_read_file({"path": str(book)}, _ctx(book))
    assert out["ok"] is True
    assert out["text"].count("\t") == READ_LIMIT_DEFAULT


def test_garbage_offset_or_limit_is_a_tool_error_not_a_crash(tmp_path: Path) -> None:
    from pen.agent.tools_impl import handle_read_file

    book = _long_book(tmp_path, n=20)
    for args in ({"offset": "L100"}, {"limit": True}, {"limit": "abc"}, {"offset": [1]}):
        out = handle_read_file({"path": str(book), **args}, _ctx(book))
        assert out["ok"] is False, args
        assert "正整数" in out["text"], args
        assert out["detail"] == str(book)
    # 数字串和整数值的 float 照收；0 和负数回落默认，跟以前一样
    assert handle_read_file({"path": str(book), "offset": "2", "limit": "1"}, _ctx(book))["text"].startswith("2\t")
    assert handle_read_file({"path": str(book), "offset": 2.0, "limit": 1.0}, _ctx(book))["text"].startswith("2\t")
    zero = handle_read_file({"path": str(book), "offset": 0, "limit": 0}, _ctx(book))
    assert zero["ok"] is True and zero["text"].startswith("1\t")


def test_probe_excerpts_can_switch_the_resume_hint_off(tmp_path: Path) -> None:
    """probe 拿同一个函数取摘录，尾注在那儿是噪音——只在工具结果里说「接着读」。"""
    book = _long_book(tmp_path)
    r = read_file_report(book, str(book), offset=1, limit=10, resume_hint=False)
    assert r["text"] == "".join(f"{i}\t第 {i} 行，这一行凑一点长度好让字符先到顶。\n" for i in range(1, 11))
    assert r["lines"] == [1, 10] and r["total"] == 1000


def test_a_single_line_longer_than_the_cap_is_reported_not_faked(tmp_path: Path) -> None:
    """二审 #3：第一行本身就超过 MAX_OUTPUT 时，以前硬切之后 kept=1、不标截断、
    offset 推到第 2 行——被切掉的那半行永远读不到，还声称读完了。"""
    from pen.config import MAX_OUTPUT

    book = tmp_path / "wide.md"
    book.write_text("x" * (MAX_OUTPUT + 500) + "\n第二行\n", encoding="utf-8")
    r = read_file_report(book, str(book), offset=1, limit=1)
    assert r["truncated"] is True
    assert r["lines"] == [1, 1]
    assert "超过" in r["text"] and "第 1 行" in r["text"]
    assert "offset=2" not in r["text"], "不能假装第 1 行读完了"
    assert len(r["text"]) <= MAX_OUTPUT + 200
    # 后面还有整行时，超长那行照样是「本次只到」的边界
    r2 = read_file_report(book, str(book), offset=1, limit=2)
    assert r2["truncated"] is True and r2["lines"] == [1, 1]


def test_digits_that_int_cannot_parse_are_still_a_tool_error(tmp_path: Path) -> None:
    """二审 #4：`"²".isdigit()` 为真但 int() 抛；几千位的数字串也抛。都不能炸。"""
    from pen.agent.tools_impl import handle_read_file

    book = _long_book(tmp_path, n=5)
    for bad in ("²", "9" * 5000, "١٢"):
        out = handle_read_file({"path": str(book), "offset": bad}, _ctx(book))
        assert out["ok"] is False and "正整数" in out["text"], bad
