"""TOOLS 花名册 + dispatch。"""

from __future__ import annotations

from typing import Any, Callable

from pen.agent.fetch import handle_fetch
from pen.agent.search import handle_search
from pen.agent.tools_impl import handle_edit_file, handle_read_file
from pen.config import MAX_OUTPUT, READ_LIMIT_DEFAULT, SEARCH_LIMIT_DEFAULT, SEARCH_LIMIT_MAX

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
            f"文件长就分段读：limit 默认 {READ_LIMIT_DEFAULT} 行，一次最多约 {MAX_OUTPUT} 字符，"
            "超出会截断并在末尾告诉你下一段的 offset。不要一次读整本书。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {
                    "type": "integer",
                    "default": 1,
                    "minimum": 1,
                    "description": "起始行号，从 1 起。接着上一段读就用它末尾给的 offset。",
                },
                "limit": {
                    "type": "integer",
                    "default": READ_LIMIT_DEFAULT,
                    "minimum": 1,
                    "description": f"读几行。默认 {READ_LIMIT_DEFAULT}，别一次要几千行——超过约 {MAX_OUTPUT} 字符会被截断。",
                },
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

FETCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "fetch",
        "description": (
            "GET 一个公网 http 或 https 页面，返回去掉标签、按段落编号的正文（格式 N\\t段落）。"
            "必须已经有一个完整 URL：没有 URL 先用 search，不要编链接，不要假装搜过网页。"
            f"长页面分段读：offset / limit 按段落行号，limit 默认 {READ_LIMIT_DEFAULT} 行，一次最多约 {MAX_OUTPUT} 字符，"
            "末尾会说页面共几行、下一段的 offset；同一页续读不会重新下载。"
            "不能取内网、本机、file://。一次只取一页。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "offset": {
                    "type": "integer",
                    "description": "从第几段（行号）开始读，默认 1。续读时用上一次尾注给的 offset。",
                    "default": 1,
                    "minimum": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": f"读几段（行），默认 {READ_LIMIT_DEFAULT}。",
                    "default": READ_LIMIT_DEFAULT,
                    "minimum": 1,
                },
            },
            "required": ["url"],
        },
    },
}

SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search",
        "description": (
            "在公网搜索，返回按相关度排好序的「标题 / URL / 摘要」列表。不知道 URL 时先用它，再用 fetch 取某一条的正文。"
            "query 写搜索引擎认的关键词（不是整句问题），可加 site:域名 或英文关键词。"
            f"一次 limit 最多 {SEARCH_LIMIT_MAX} 条；结果末尾会说共几条、还剩几条、下一段的 offset。"
            "同一个 query 不要反复搜，没结果就换词。不要拿它读网页。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词。"},
                "offset": {
                    "type": "integer",
                    "description": "从第几条结果开始，默认 1。翻页时用上一次尾注给的 offset。",
                    "default": 1,
                    "minimum": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": f"看几条，默认 {SEARCH_LIMIT_DEFAULT}，最多 {SEARCH_LIMIT_MAX}。",
                    "default": SEARCH_LIMIT_DEFAULT,
                    "minimum": 1,
                    "maximum": SEARCH_LIMIT_MAX,
                },
            },
            "required": ["query"],
        },
    },
}

TOOLS: dict[str, dict[str, Any]] = {
    "read_file": {"schema": READ_FILE_SCHEMA, "handler": handle_read_file},
    "edit_file": {"schema": EDIT_FILE_SCHEMA, "handler": handle_edit_file},
    "fetch": {"schema": FETCH_SCHEMA, "handler": handle_fetch},
    "search": {"schema": SEARCH_SCHEMA, "handler": handle_search},
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
