"""TOOLS 花名册 + dispatch。"""

from __future__ import annotations

from typing import Any, Callable

from pen.agent.tools_impl import handle_edit_file, handle_read_file

Handler = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]

READ_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "按行读取当前手册原文、对照文件，或 [工作目录里的其他教材] 里列出的别本。"
            "返回带行号文本，每行格式「N\\t原文」，offset 从 1 起。行号只是坐标，不是文件内容。"
            "读当前手册就复制 [来源] handbook_path；读别的教材就复制那一段给出的 path。"
            "相对路径相对手册目录。不要读 ~/.zshrc、/etc、.env。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "default": 1},
                "limit": {"type": "integer", "default": 80},
            },
            "required": ["path"],
        },
    },
}

EDIT_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": (
            "在已登记的手册原文里做一次精确替换。"
            "必须先成功 read_file 同一路径**并拿到返回**，再调用本工具。"
            "不能和 read_file 放在同一批发出——那时你还没看到原文，old_string 只能靠猜。"
            "同一轮里接着调就行，不用等读者再说一遍。"
            "old_string 必须是去掉行号前缀后的纯原文，且在文件中恰好出现一次。"
            "不要替换整份文件。不要改其它路径。"
            "调用后会等读者审批。在工具结果说「已编辑」之前，不要声称已经写盘。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}

TOOLS: dict[str, dict[str, Any]] = {
    "read_file": {"schema": READ_FILE_SCHEMA, "handler": handle_read_file},
    "edit_file": {"schema": EDIT_FILE_SCHEMA, "handler": handle_edit_file},
}


def schemas() -> list[dict[str, Any]]:
    return [t["schema"] for t in TOOLS.values()]


def dispatch(name: str, args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    spec = TOOLS.get(name)
    if spec is None:
        return {
            "ok": False,
            "text": f"错误：未知工具 {name}",
            "resolved": "",
            "detail": "",
        }
    handler: Handler = spec["handler"]
    return handler(args, ctx)
