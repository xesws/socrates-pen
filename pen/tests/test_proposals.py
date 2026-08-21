from __future__ import annotations

from pathlib import Path

import pytest

from pen import config
from pen import proposals as proposalsmod
from pen.index import build_index
from pen.insert import InsertPlan, apply_insert, plan_insert


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


@pytest.fixture()
def pen_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "PEN_DIR", tmp_path)
    return tmp_path


def test_put_get_survives_as_insert_plan(pen_home: Path, tmp_path: Path) -> None:
    dest = tmp_path / "mini_handbook.md"
    dest.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    idx = build_index(dest)
    plan = plan_insert(idx, dest, line=_q1_line(dest.read_text(encoding="utf-8")), fold_md=FOLD)
    proposalsmod.put(
        "cafe" * 8,
        {
            "handbook_id": "mini",
            "session_id": "sess1",
            "plan": plan,
            "diff": "---",
            "original_path": str(dest),
        },
    )
    loaded = proposalsmod.get("cafe" * 8)
    assert loaded is not None
    assert isinstance(loaded["plan"], InsertPlan)
    assert loaded["plan"].insert_after_line == plan.insert_after_line
    before = dest.read_text(encoding="utf-8")
    apply_insert(dest, loaded["plan"])
    after = dest.read_text(encoding="utf-8")
    assert len(after) > len(before)
    assert "🔍 实例 2：" in after


def test_delete_removes_file(pen_home: Path) -> None:
    plan = InsertPlan(
        mode="q_append",
        level="Level 0",
        q_title="Q",
        beat=None,
        insert_after_line=1,
        instance_n=1,
        fold_md="<details>\n\n<summary>x</summary>\n\nbody\n\n</details>\n",
    )
    proposalsmod.put("abcd" * 8, {"plan": plan, "session_id": "s", "handbook_id": "h"})
    assert (pen_home / "proposals" / f"{'abcd' * 8}.json").is_file()
    proposalsmod.delete("abcd" * 8)
    assert proposalsmod.get("abcd" * 8) is None
