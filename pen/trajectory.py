"""Append-only chat turns under .pen/trajectories/. Never touches the handbook.

**这是分析读者的唯一原料。** 会话文件七天就被 retention 扫掉，手册会被写回改掉，
只有这份 JSONL 是不删不改的。所以每一行要**自足**：读者打的整句话（不截 240 字）、
苏格拉底的整段回答（不是 200 字预览）、抛给读者的两条追问、读者这句是不是点了
追问、走的快模型还是主模型、什么时候问的、答了多久。v0.24.0 之前这些一样都没有，
「user log」退化成了「读过哪几道题的标题」。

一轮对话一行，`phase="chat"`。写回要人批准时一轮跨两个请求，批准后那半截另起
一行 `phase="approve"`——它不是新的一轮，`is_turn` 说了算，数轮次的地方都问它。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pen import config
from pen.clock import now_iso

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def trajectories_dir() -> Path:
    dest = config.PEN_DIR / "trajectories"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _path(handbook_id: str) -> Path:
    if not _SAFE_ID.match(handbook_id):
        raise ValueError(f"非法 handbook_id：{handbook_id!r}")
    return trajectories_dir() / f"{handbook_id}.jsonl"


def append_turn(handbook_id: str, record: dict[str, Any]) -> Path:
    config.ensure_pen_dirs()
    row = dict(record)
    # 答完的时刻。问的时刻由调用方以 asked_at 传进来——两者之差就是读者等了多久。
    row.setdefault("ts", now_iso())
    row["handbook_id"] = handbook_id
    dest = _path(handbook_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return dest


def is_turn(row: Any) -> bool:
    """这一行是不是一轮对话本身。

    approve 那半截（`phase="approve"`）不是：它是同一轮的后半段，单独数就把每次
    写回数成两轮。老行没有 phase 字段，一律当 chat。
    """
    return isinstance(row, dict) and str(row.get("phase") or "chat") == "chat"


def load_turns(handbook_id: str) -> list[dict[str, Any]]:
    """整本书的全部行。只跳坏 JSON 和空对象——以前没有 anchor 的行也被静默扔掉，
    而读者的数据不该在读的时候被筛，要筛让消费方自己筛（`is_turn` / `diagnose.is_curriculum`）。
    """
    dest = _path(handbook_id)
    if not dest.is_file():
        return []
    out: list[dict[str, Any]] = []
    for raw in dest.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev:
            out.append(ev)
    return out
