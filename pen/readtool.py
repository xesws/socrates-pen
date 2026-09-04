"""只读 read_file，形状对齐 lab/level4，但走沙箱。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pen.config import MAX_OUTPUT, READ_LIMIT_DEFAULT
from pen.sandbox import SandboxError, assert_readable, resolve_read_target


def _cap_lines(numbered: list[str]) -> tuple[str, int]:
    """把带行号的行拼起来，超过 MAX_OUTPUT 就在**整行边界**停。

    返回 (正文, 保留的行数)。v0.26.0 之前是按字符硬切，最后一行常常只剩半句，
    而且只写「已截断」——模型不知道停在第几行、下一段该从哪儿读，于是要么重读
    整段，要么假装读完了。第一行本身就超过 MAX_OUTPUT 时保留它的硬切，
    否则一行都给不出。
    """
    out: list[str] = []
    used = 0
    for i, line in enumerate(numbered):
        if used + len(line) > MAX_OUTPUT:
            if i == 0:
                return line[:MAX_OUTPUT], 1
            break
        out.append(line)
        used += len(line)
    return "".join(out), len(out)


def read_file_report(
    original_path: Path,
    path: str,
    offset: int = 1,
    limit: int = READ_LIMIT_DEFAULT,
    extra_roots: list[Path] | None = None,
    *,
    resume_hint: bool = True,
) -> dict[str, Any]:
    """ok / resolved / text / lines / total / truncated。text 始终是给模型的字符串。

    尾注只在**还有没读到的行**时才加（被字符截断、或被 limit 截住而文件没到底）：
    读到文件尾的输出和以前逐字节一致。尾注不带行号前缀，压缩层的
    `_line_span` 认不到它，区间统计不受影响。

    `resume_hint=False` 关掉尾注：probe 拿这个函数取摘录，那里没有「接着读」
    这回事，尾注只会混进探索 prompt 和反引号校验的语料里。
    """
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
    total = len(lines)
    start = max(offset - 1, 0)
    chunk = lines[start : start + limit]
    if not chunk:
        body = f"(空文件或超出范围：文件共 {total} 行)" if total else "(空文件或超出范围)"
        return {
            "ok": True,
            "resolved": str(resolved),
            "text": body,
            "lines": [],
            "total": total,
            "truncated": False,
        }
    numbered = [f"{start + i + 1}\t{line}" for i, line in enumerate(chunk)]
    body, kept = _cap_lines(numbered)
    first = start + 1
    last = start + kept
    truncated = kept < len(chunk)
    sep = "" if body.endswith("\n") else "\n"
    if not resume_hint:
        pass
    elif truncated:
        body += (
            f"{sep}…（已截断：本次只到第 {last} 行，文件共 {total} 行。"
            f"接着读用 offset={last + 1}，limit 不超过 {kept} 行）"
        )
    elif last < total:
        body += f"{sep}（第 {first}–{last} 行，文件共 {total} 行；接着读 offset={last + 1}）"
    return {
        "ok": True,
        "resolved": str(resolved),
        "text": body,
        "lines": [first, last],
        "total": total,
        "truncated": truncated,
    }


def read_file_sandboxed(
    original_path: Path,
    path: str,
    offset: int = 1,
    limit: int = READ_LIMIT_DEFAULT,
    extra_roots: list[Path] | None = None,
) -> str:
    return read_file_report(original_path, path, offset, limit, extra_roots)["text"]
