"""只读 read_file，形状对齐 lab/level4，但走沙箱。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pen.config import MAX_OUTPUT
from pen.sandbox import SandboxError, assert_readable, resolve_read_target


def truncate(s: str) -> str:
    return s if len(s) <= MAX_OUTPUT else s[:MAX_OUTPUT] + "\n...(已截断)"


def read_file_report(
    original_path: Path,
    path: str,
    offset: int = 1,
    limit: int = 200,
    extra_roots: list[Path] | None = None,
) -> dict[str, Any]:
    """ok / resolved / text。text 始终是给模型的字符串。"""
    try:
        resolved = assert_readable(original_path, path, extra_roots=extra_roots)
    except SandboxError as exc:
        tried = str(resolve_read_target(original_path, path))
        return {"ok": False, "resolved": tried, "text": f"错误：{exc}"}
    try:
        lines = resolved.read_text(encoding="utf-8").splitlines(keepends=True)
    except Exception as exc:
        return {
            "ok": False,
            "resolved": str(resolved),
            "text": f"错误：无法读取 {path}：{exc}",
        }
    start = max(offset - 1, 0)
    chunk = lines[start : start + limit]
    if not chunk:
        body = "(空文件或超出范围)"
    else:
        body = truncate("".join(f"{start + i + 1}\t{line}" for i, line in enumerate(chunk)))
    return {"ok": True, "resolved": str(resolved), "text": body}


def read_file_sandboxed(
    original_path: Path,
    path: str,
    offset: int = 1,
    limit: int = 200,
    extra_roots: list[Path] | None = None,
) -> str:
    return read_file_report(original_path, path, offset, limit, extra_roots)["text"]
