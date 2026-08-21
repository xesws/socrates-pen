"""登记本机原始教材路径。不复制正文。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from pen.config import (
    DEFAULT_HANDBOOK,
    DEFAULT_HANDBOOK_ID,
    LIBRARIES_DIR,
    ensure_pen_dirs,
)
from pen.index import HandbookIndex, build_index, save_index
from pen.sandbox import SandboxError, assert_handbook_path

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class RegisterError(ValueError):
    """登记失败。带 i18n_key 时 app 层按用户语言查表，否则回落中文原文。"""

    def __init__(self, message: str, *, key: str | None = None, **args: object) -> None:
        super().__init__(message)
        self.i18n_key = key
        self.i18n_args = args


@dataclass
class HandbookMeta:
    handbook_id: str
    title: str
    original_path: str
    imported_at: str
    mtime: float
    allow_root: str | None = None


def _meta_from_dict(data: dict) -> HandbookMeta:
    return HandbookMeta(
        handbook_id=data["handbook_id"],
        title=data["title"],
        original_path=data["original_path"],
        imported_at=data["imported_at"],
        mtime=data["mtime"],
        allow_root=data.get("allow_root"),
    )


def _require_id(handbook_id: str) -> str:
    if not _SAFE_ID.match(handbook_id):
        raise RegisterError(f"非法 handbook_id：{handbook_id!r}", key="handbook.bad_id", got=repr(handbook_id))
    return handbook_id


def _meta_path(handbook_id: str) -> Path:
    return LIBRARIES_DIR / _require_id(handbook_id) / "meta.json"


def _index_path(handbook_id: str) -> Path:
    return LIBRARIES_DIR / _require_id(handbook_id) / "index.json"


def extra_roots_for(handbook_id: str) -> list[Path] | None:
    meta = get(handbook_id)
    if meta is None or not meta.allow_root:
        return None
    return [Path(meta.allow_root)]


def register(
    original_path: str | Path,
    handbook_id: str | None = None,
    extra_roots: list[Path] | None = None,
) -> HandbookMeta:
    ensure_pen_dirs()
    try:
        path = assert_handbook_path(original_path, extra_roots=extra_roots)
    except SandboxError as exc:
        # 把 i18n key 一起带过去，否则重新包装会把它丢掉，英文用户又看到中文
        raise RegisterError(
            str(exc),
            key=getattr(exc, "i18n_key", None),
            **(getattr(exc, "i18n_args", None) or {}),
        ) from exc
    if not path.is_file():
        raise _tagged(FileNotFoundError(f"找不到教材：{path}"), "handbook.not_found", path=str(path))
    hid = _require_id(handbook_id or _suggest_id(path))
    allow_root: str | None = None
    resolved = path.resolve()
    for raw in extra_roots or []:
        root = Path(raw).expanduser().resolve()
        if resolved == root or root in resolved.parents:
            allow_root = str(root)
            break
    existing = get(hid)
    if existing and Path(existing.original_path).resolve() == resolved:
        # 同一本书重复登记：不整本重建索引，只补登记新的允许根，过没过期交给 refresh_if_stale。
        if allow_root and allow_root != existing.allow_root:
            existing.allow_root = allow_root
            _meta_path(hid).write_text(
                json.dumps(asdict(existing), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return refresh_if_stale(hid)
    idx = build_index(path)
    meta = HandbookMeta(
        handbook_id=hid,
        title=idx.title,
        original_path=str(path),
        imported_at=datetime.now(timezone.utc).isoformat(),
        mtime=path.stat().st_mtime,
        allow_root=allow_root,
    )
    dest = LIBRARIES_DIR / hid
    dest.mkdir(parents=True, exist_ok=True)
    _meta_path(hid).write_text(
        json.dumps(asdict(meta), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    save_index(idx, _index_path(hid))
    return meta


def ensure_default() -> HandbookMeta | None:
    """实验室检出里登记默认手册。pip 安装后没有那份文件，空操作，让 sidecar 能起。"""
    if DEFAULT_HANDBOOK.is_file():
        existing = get(DEFAULT_HANDBOOK_ID)
        if existing and Path(existing.original_path).resolve() == DEFAULT_HANDBOOK.resolve():
            return refresh_if_stale(DEFAULT_HANDBOOK_ID)
        return register(DEFAULT_HANDBOOK, DEFAULT_HANDBOOK_ID)
    return None


def list_handbooks() -> list[HandbookMeta]:
    ensure_pen_dirs()
    out: list[HandbookMeta] = []
    for meta_file in LIBRARIES_DIR.glob("*/meta.json"):
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        out.append(_meta_from_dict(data))
    out.sort(key=lambda m: m.imported_at)
    return out


def get(handbook_id: str) -> HandbookMeta | None:
    try:
        p = _meta_path(handbook_id)
    except RegisterError:
        return None
    if not p.is_file():
        return None
    return _meta_from_dict(json.loads(p.read_text(encoding="utf-8")))


def load_index(handbook_id: str) -> HandbookIndex:
    meta = get(handbook_id)
    if meta is None:
        raise KeyError(handbook_id)
    refresh_if_stale(handbook_id)
    return HandbookIndex.from_json(json.loads(_index_path(handbook_id).read_text(encoding="utf-8")))


def _tagged(exc: Exception, key: str, **args: object) -> Exception:
    """给标准库异常挂上 i18n key，供 app 层查表。"""
    exc.i18n_key = key  # type: ignore[attr-defined]
    exc.i18n_args = args  # type: ignore[attr-defined]
    return exc


def refresh_if_stale(handbook_id: str) -> HandbookMeta:
    meta = get(handbook_id)
    if meta is None:
        raise KeyError(handbook_id)
    path = Path(meta.original_path)
    if not path.is_file():
        raise _tagged(FileNotFoundError(f"原文消失：{path}"), "handbook.file_missing", path=str(path))
    mtime = path.stat().st_mtime
    if abs(mtime - meta.mtime) > 0.001 or not _index_path(handbook_id).is_file():
        idx = build_index(path)
        meta.mtime = mtime
        meta.title = idx.title
        _meta_path(handbook_id).write_text(
            json.dumps(asdict(meta), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        save_index(idx, _index_path(handbook_id))
    return meta


def _suggest_id(path: Path) -> str:
    stem = path.stem
    slug = "".join(c.lower() if c.isalnum() else "-" for c in stem).strip("-")
    return slug or "handbook"
