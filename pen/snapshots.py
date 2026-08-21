"""原文快照栈。只用于整篇回到上一版 / 重做，不当阅读对象。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Any

from pen.config import LIBRARIES_DIR, SNAPSHOT_KEEP
from pen.sandbox import assert_write_target


def snapshot_dir(handbook_id: str) -> Path:
    d = LIBRARIES_DIR / handbook_id / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _redo_dir(handbook_id: str) -> Path:
    d = snapshot_dir(handbook_id) / "redo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _undo_files(handbook_id: str) -> list[Path]:
    return sorted(p for p in snapshot_dir(handbook_id).glob("*.md") if p.is_file())


def _redo_files(handbook_id: str) -> list[Path]:
    return sorted(p for p in _redo_dir(handbook_id).glob("*.md") if p.is_file())


def _stamp_name(reason: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in reason)[:40]
    return f"{ts}-{safe}.md"


def _push(dest_dir: Path, src: Path, reason: str) -> Path:
    dest = dest_dir / _stamp_name(reason)
    shutil.copy2(src, dest)
    return dest


def _clear_redo(handbook_id: str) -> None:
    for f in _redo_files(handbook_id):
        f.unlink(missing_ok=True)


def status(handbook_id: str) -> dict[str, Any]:
    u = _undo_files(handbook_id)
    r = _redo_files(handbook_id)
    return {
        "can_undo": len(u) > 0,
        "can_redo": len(r) > 0,
        "undo_n": len(u),
        "redo_n": len(r),
    }


def take_snapshot(handbook_id: str, original_path: Path, reason: str) -> Path:
    src = assert_write_target(original_path, original_path)
    dest = _push(snapshot_dir(handbook_id), src, reason)
    _clear_redo(handbook_id)
    _prune(handbook_id)
    return dest


def latest_snapshot(handbook_id: str) -> Path | None:
    files = _undo_files(handbook_id)
    return files[-1] if files else None


def undo(handbook_id: str, original_path: Path) -> Path:
    """弹出 undo 最新一份盖回原文；当前篇进 redo。"""
    target = assert_write_target(original_path, original_path)
    files = _undo_files(handbook_id)
    if not files:
        exc = FileNotFoundError(f"没有可回退的快照：{handbook_id}")
        exc.i18n_key = "snapshot.none_to_undo"  # type: ignore[attr-defined]
        exc.i18n_args = {"handbook_id": handbook_id}  # type: ignore[attr-defined]
        raise exc
    snap = files[-1]
    _push(_redo_dir(handbook_id), target, "undone")
    shutil.copy2(snap, target)
    snap.unlink(missing_ok=True)
    _prune(handbook_id)
    return snap


def redo(handbook_id: str, original_path: Path) -> Path:
    """弹出 redo 最新一份盖回原文；当前篇进 undo。"""
    target = assert_write_target(original_path, original_path)
    files = _redo_files(handbook_id)
    if not files:
        exc = FileNotFoundError(f"没有可重做的快照：{handbook_id}")
        exc.i18n_key = "snapshot.none_to_redo"  # type: ignore[attr-defined]
        exc.i18n_args = {"handbook_id": handbook_id}  # type: ignore[attr-defined]
        raise exc
    snap = files[-1]
    _push(snapshot_dir(handbook_id), target, "pre-redo")
    shutil.copy2(snap, target)
    snap.unlink(missing_ok=True)
    _prune(handbook_id)
    return snap


def rollback(handbook_id: str, original_path: Path) -> Path:
    """兼容旧名：回到上一版。"""
    return undo(handbook_id, original_path)


def _prune(handbook_id: str) -> None:
    while True:
        undo_fs = _undo_files(handbook_id)
        redo_fs = _redo_files(handbook_id)
        if len(undo_fs) + len(redo_fs) <= SNAPSHOT_KEEP:
            return
        if undo_fs:
            undo_fs[0].unlink(missing_ok=True)
        elif redo_fs:
            redo_fs[0].unlink(missing_ok=True)
        else:
            return
