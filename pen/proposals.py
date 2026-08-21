"""写回提案落盘。RAM 写穿，重启后仍能 apply。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pen import config
from pen.insert import InsertPlan

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def proposals_dir() -> Path:
    dest = config.PEN_DIR / "proposals"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _path(proposal_id: str) -> Path:
    if not _SAFE_ID.match(proposal_id):
        raise ValueError(f"非法 proposal_id：{proposal_id!r}")
    return proposals_dir() / f"{proposal_id}.json"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _plan_to_dict(plan: InsertPlan | dict[str, Any]) -> dict[str, Any]:
    if isinstance(plan, InsertPlan):
        return asdict(plan)
    return dict(plan)


def _plan_from_dict(raw: dict[str, Any]) -> InsertPlan:
    rs = raw.get("replace_start")
    re_ = raw.get("replace_end")
    return InsertPlan(
        mode=str(raw["mode"]),
        level=str(raw["level"]),
        q_title=raw.get("q_title"),
        beat=raw.get("beat"),
        insert_after_line=int(raw["insert_after_line"]),
        instance_n=int(raw["instance_n"]),
        fold_md=str(raw["fold_md"]),
        replace_start=int(rs) if rs is not None else None,
        replace_end=int(re_) if re_ is not None else None,
    )


def put(proposal_id: str, record: dict[str, Any]) -> Path:
    config.ensure_pen_dirs()
    payload = dict(record)
    payload["proposal_id"] = proposal_id
    payload["plan"] = _plan_to_dict(record["plan"])
    dest = _path(proposal_id)
    _atomic_write(dest, json.dumps(payload, ensure_ascii=False))
    return dest


def get(proposal_id: str) -> dict[str, Any] | None:
    try:
        dest = _path(proposal_id)
    except ValueError:
        return None
    if not dest.is_file():
        return None
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "plan" not in data:
        return None
    plan_raw = data["plan"]
    if not isinstance(plan_raw, dict):
        return None
    data["plan"] = _plan_from_dict(plan_raw)
    return data


def delete(proposal_id: str) -> None:
    try:
        dest = _path(proposal_id)
    except ValueError:
        return
    try:
        dest.unlink(missing_ok=True)
    except OSError:
        return
