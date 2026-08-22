from __future__ import annotations

from pathlib import Path

# **故意**用 import 时冻住的绑定：下面 `test_real_handbook_index_readonly` 要验的是
# **那本真教材**（13083 行、80 道 Q），不是 conftest 换上去的 138 行 fixture。
# 模块 import 早于任何 fixture，所以这里拿到的是原值。别改成 `config.DEFAULT_HANDBOOK`——
# 那会让这条测试改去验 fixture，实验室仓对真教材的唯一覆盖就没了。
from pen.config import DEFAULT_HANDBOOK
from pen.index import build_index, check_index

FIXTURE = Path(__file__).parent / "fixtures" / "mini_handbook.md"


def test_mini_q1_not_confused_across_levels() -> None:
    idx = build_index(FIXTURE)
    # Q1 appears in Level 0 and Level 1
    l0 = idx.find_q("Level 0", "**Q1. shell 和 Bash 是什么关系？**")
    l1 = idx.find_q("Level 1", "**Q1. venv 是干什么的？**")
    assert l0.level == "Level 0"
    assert l1.level == "Level 1"
    assert l0.start_line != l1.start_line


def test_locate_inside_q() -> None:
    idx = build_index(FIXTURE)
    # line of Q1 title
    title_line = next(
        i
        for i, line in enumerate(FIXTURE.read_text(encoding="utf-8").splitlines(), 1)
        if line.startswith("**Q1. shell")
    )
    sec = idx.locate(title_line)
    assert sec.kind == "q"
    assert sec.level == "Level 0"
    assert sec.q_title == "**Q1. shell 和 Bash 是什么关系？**"


def test_locate_opening() -> None:
    idx = build_index(FIXTURE)
    sec = idx.locate(4)
    assert sec.level == "开篇"
    assert sec.kind == "other"


def test_mini_check_ok() -> None:
    idx = build_index(FIXTURE)
    assert check_index(idx) == []


def test_real_handbook_index_readonly() -> None:
    if not DEFAULT_HANDBOOK.is_file():
        return
    idx = build_index(DEFAULT_HANDBOOK)
    problems = check_index(idx)
    assert idx.n_lines > 10000
    qs = [s for s in idx.sections if s.kind == "q"]
    assert len(qs) == 80
    # 七个 Level 各有 Q1，主键必须带 Level
    q1s = [s for s in qs if s.q_title and s.q_title.startswith("**Q1.")]
    assert len(q1s) == 7
    levels = {s.level for s in q1s}
    assert "Level 0" in levels and "Level 6" in levels
    # 抽查：手册里 Q1 shell 那一行
    text = DEFAULT_HANDBOOK.read_text(encoding="utf-8").splitlines()
    line_no = next(i for i, ln in enumerate(text, 1) if ln.startswith("**Q1. shell 和 Bash"))
    sec = idx.locate(line_no)
    assert sec.level == "Level 0"
    assert sec.q_title == "**Q1. shell 和 Bash 是什么关系？**"
    assert problems == []
