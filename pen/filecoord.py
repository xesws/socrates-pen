"""Process-local note transactions. Never hold these locks while calling a model.

Path identity survives atomic replacement; inode aliases only join *live* locks,
so symlinks/case aliases cannot bypass a transaction or leave stale inode keys.
External editors do not participate in this protocol.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
import threading
from typing import Iterator


class FileChanged(ValueError):
    code = "FILE_CHANGED"


class _Entry:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.users = 0
        self.keys: set[object] = set()


_meta = threading.RLock()
_entries: dict[object, _Entry] = {}


def _keys(path: Path) -> list[object]:
    keys: list[object] = [str(path.expanduser().resolve())]
    try:
        st = path.stat()
        keys.append((st.st_dev, st.st_ino))
    except OSError:
        pass
    return keys


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    with _meta:
        keys = _keys(path)
        entry = next((_entries[k] for k in keys if k in _entries), None) or _Entry()
        for key in keys:
            _entries[key] = entry
            entry.keys.add(key)
        entry.users += 1
    try:
        with entry.lock:
            yield
    finally:
        with _meta:
            entry.users -= 1
            if not entry.users:
                for key in entry.keys:
                    if _entries.get(key) is entry:
                        del _entries[key]


def revision(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path: Path) -> str:
    # Preserve CRLF: the revision and the replacement refer to the same bytes.
    return path.read_bytes().decode("utf-8")


def require_revision(text: str, expected: str | None) -> None:
    if not expected or revision(text) != expected:
        raise FileChanged("FILE_CHANGED: 文件版本已变化或缺少读取版本。请重新 read_file 定位原文，再提新修改并重新审批。/ Read the current file again and request new approval.")


def atomic_write(path: Path, text: str) -> None:
    """Caller owns file_lock. Preserve permissions and register the new inode."""
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".pen-tmp", dir=path.parent)
    tmp = Path(name)
    try:
        os.fchmod(fd, path.stat().st_mode & 0o777)
        with os.fdopen(fd, "wb") as out:
            fd = -1
            out.write(text.encode("utf-8"))
            out.flush()
            os.fsync(out.fileno())
        with _meta:
            tmp.replace(path)
            entry = _entries.get(str(path.expanduser().resolve()))
            if entry:
                for key in _keys(path):
                    _entries[key] = entry
                    entry.keys.add(key)
    finally:
        if fd >= 0:
            os.close(fd)
        tmp.unlink(missing_ok=True)
