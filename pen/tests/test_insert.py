from __future__ import annotations

from pathlib import Path

import pytest

from pen.index import build_index
from pen.insert import (
    InsertError,
    apply_insert,
    plan_after_line,
    plan_insert,
    plan_replace_range,
    render_new_text,
)
from pen.outline import file_outline
from pen.snapshots import rollback, take_snapshot

FIXTURE = Path(__file__).parent / "fixtures" / "mini_handbook.md"

FOLD = """<details>

<summary>🔍 实例 1：苏格拉底补的例子</summary>

```text
伪代码：shell 是一类，Bash 是一个
```

</details>
"""


def _q1_line(text: str) -> int:
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith("**Q1. shell"):
            return i
    raise AssertionError("missing Q1")


def test_insert_appends_instance_2_before_huitou(tmp_path: Path) -> None:
    dest = tmp_path / "mini_handbook.md"
    dest.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    before = dest.read_text(encoding="utf-8")
    idx = build_index(dest)
    plan = plan_insert(idx, dest, line=_q1_line(before), fold_md=FOLD)
    assert plan.mode == "q_append"
    assert plan.instance_n == 2
    apply_insert(dest, plan)
    after = dest.read_text(encoding="utf-8")
    assert after != before
    assert len(after) > len(before)
    assert "- **TL;DR：**" in after
    assert after.count("〔回读：第三拍 · 出身〕") >= 1
    # 新块在回读之前
    q1 = after.split("**Q1. shell", 1)[1].split("**Q2.", 1)[0]
    assert "🔍 实例 2：" in q1
    assert q1.index("🔍 实例 2：") < q1.index("〔回读：")
    assert q1.index("</details>") < q1.index("〔回读：")
    # 只增：原文关键句都还在
    assert "- **(a) 概念/定义 + 对比：**" in q1
    assert "- **(c) 为什么 + 反例：**" in q1


def test_lint_rejects_huitou_in_fold(tmp_path: Path) -> None:
    dest = tmp_path / "mini_handbook.md"
    dest.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    idx = build_index(dest)
    bad = FOLD.replace("伪代码", "〔回读：伪造〕伪代码")
    with pytest.raises(InsertError):
        plan_insert(idx, dest, line=_q1_line(dest.read_text(encoding="utf-8")), fold_md=bad)


def test_does_not_write_other_file(tmp_path: Path) -> None:
    dest = tmp_path / "mini_handbook.md"
    other = tmp_path / "other.md"
    dest.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    other.write_text("keep\n", encoding="utf-8")
    idx = build_index(dest)
    plan = plan_insert(idx, dest, line=_q1_line(dest.read_text(encoding="utf-8")), fold_md=FOLD)
    apply_insert(dest, plan)
    assert other.read_text(encoding="utf-8") == "keep\n"


def test_snapshot_rollback_restores_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pen import snapshots as snapmod
    from pen import config as cfg

    monkeypatch.setattr(snapmod, "LIBRARIES_DIR", tmp_path / "libraries")
    monkeypatch.setattr(cfg, "LIBRARIES_DIR", tmp_path / "libraries")

    dest = tmp_path / "mini_handbook.md"
    dest.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    original_bytes = dest.read_bytes()
    idx = build_index(dest)
    plan = plan_insert(idx, dest, line=_q1_line(dest.read_text(encoding="utf-8")), fold_md=FOLD)
    take_snapshot("mini", dest, "pre")
    apply_insert(dest, plan)
    assert dest.read_bytes() != original_bytes
    rollback("mini", dest)
    assert dest.read_bytes() == original_bytes


def test_level1_q1_not_polluted(tmp_path: Path) -> None:
    dest = tmp_path / "mini_handbook.md"
    dest.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    idx = build_index(dest)
    plan = plan_insert(idx, dest, line=_q1_line(dest.read_text(encoding="utf-8")), fold_md=FOLD)
    new = render_new_text(dest.read_text(encoding="utf-8"), plan)
    l1 = new.split("**Q1. venv", 1)[1]
    assert "苏格拉底补的例子" not in l1


def test_after_line_on_plain_note(tmp_path: Path) -> None:
    dest = tmp_path / "note.md"
    dest.write_text("# 随便\n\nhello\nworld\n", encoding="utf-8")
    plan = plan_after_line(dest, FOLD, 3)
    assert plan.mode == "after_line"
    assert plan.insert_after_line == 3
    apply_insert(dest, plan)
    text = dest.read_text(encoding="utf-8")
    assert text.index("hello") < text.index("苏格拉底补的例子")
    assert text.index("苏格拉底补的例子") < text.index("world")


def test_after_line_oob(tmp_path: Path) -> None:
    dest = tmp_path / "note.md"
    dest.write_text("a\nb\n", encoding="utf-8")
    with pytest.raises(InsertError):
        plan_after_line(dest, FOLD, 99)


def test_outline_any_heading(tmp_path: Path) -> None:
    dest = tmp_path / "note.md"
    dest.write_text("# 随便\n\nbody\n\n## 子节\n\nx\n", encoding="utf-8")
    ol = file_outline(dest)
    titles = [h["text"] for h in ol["headings"]]
    assert titles == ["随便", "子节"]
    assert ol["questions"] == []
    h0 = ol["headings"][0]
    assert h0["start_line"] == 1
    assert h0["end_line"] == 7


def test_replace_range_drops_only_that_span(tmp_path: Path) -> None:
    dest = tmp_path / "note.md"
    dest.write_text("a\nb\nc\nd\n", encoding="utf-8")
    plan = plan_replace_range(dest, FOLD, 2, 3)
    apply_insert(dest, plan)
    text = dest.read_text(encoding="utf-8")
    assert text.startswith("a\n")
    assert "\nb\n" not in text and not text.startswith("b")
    assert "\nc\n" not in text
    assert text.rstrip().endswith("d")
    assert "苏格拉底补的例子" in text
