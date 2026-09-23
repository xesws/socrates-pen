"""Shared wire helpers for the independently owned practice services."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROTOCOL = 1
QUESTION_TYPES = ("single_choice", "fill_blank", "short_answer")


def fingerprint(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scope_id(vault_root: str) -> str:
    path = Path(vault_root).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("vault_root must be an existing absolute directory")
    return fingerprint(str(path.resolve()))[:32]


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_question(q: dict[str, Any]) -> dict[str, Any]:
    """Allow-list: answer keys/rubric/source answer never leave before submission."""
    result = {k: q[k] for k in (
        "id", "version", "source_mq_id", "type", "purpose", "exam_form", "prompt",
        "point_ids", "estimated_seconds", "difficulty", "status", "choices",
    ) if k in q}
    if "blanks" in q:
        result["blanks"] = [{k: b[k] for k in ("id", "label") if k in b} for b in q["blanks"]]
    return result


def question_metadata(q: dict[str, Any]) -> dict[str, Any]:
    result = public_question(q)
    result["criteria"] = [{k: c[k] for k in ("id", "point_id", "max_score", "blank_id") if k in c}
                          for c in q.get("criteria", [])]
    return result
