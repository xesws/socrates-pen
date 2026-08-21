"""审批：只读自动过；edit_file 每次问人；未知工具拒绝。read-first 硬闸。"""

from __future__ import annotations

from pathlib import Path

READ_FIRST_MSG = (
    "错误：edit_file 之前必须先成功 read_file 同一路径。"
    "请先 read_file 看准带行号的原文（格式 N\\t原文），下一轮再单独调用 edit_file"
    "（不要和 read_file 写在同一批 tool_calls 里）。"
    "old_string 必须是去掉行号前缀后的纯原文。"
)


def decide(name: str) -> str:
    if name == "read_file":
        return "allow"
    if name == "edit_file":
        return "ask"
    return "deny"


def read_first_block(
    name: str,
    resolved: Path | None,
    read_before: set[Path],
) -> str | None:
    """硬闸：edit_file 必须在更早一轮成功读过同一路径。同批 read+edit 也拦。"""
    if name != "edit_file":
        return None
    if resolved is None:
        return READ_FIRST_MSG
    got = resolved.expanduser().resolve()
    allowed = {p.expanduser().resolve() for p in read_before}
    if got not in allowed:
        return READ_FIRST_MSG
    return None

