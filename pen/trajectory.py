"""Append-only chat turns under .pen/trajectories/. Never touches the handbook."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pen import config

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
    row.setdefault("ts", datetime.now(timezone.utc).isoformat())
    row["handbook_id"] = handbook_id
    dest = _path(handbook_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return dest


def load_turns(handbook_id: str) -> list[dict[str, Any]]:
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
        if isinstance(ev, dict) and ev.get("anchor"):
            out.append(ev)
    return out
