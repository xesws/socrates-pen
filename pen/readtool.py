"""只读 read_file，形状对齐 lab/level4，但走沙箱。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pen import filecoord
from pen.config import MAX_OUTPUT, READ_LIMIT_DEFAULT
from pen.sandbox import SandboxError, assert_readable, resolve_read_target


def _cap_lines(numbered: list[str]) -> tuple[str, int, bool]:
    """把带行号的行拼起来，超过 MAX_OUTPUT 就在**整行边界**停。

    返回 (正文, 保留的行数, 第一行是不是只给了开头)。v0.26.0 之前是按字符硬切，
    最后一行常常只剩半句，而且只写「已截断」——模型不知道停在第几行、下一段该
    从哪儿读，于是要么重读整段，要么假装读完了。第一行本身就超过 MAX_OUTPUT 时
    保留它的硬切（否则一行都给不出），但要**如实说它没读完**，不能把 offset
    推到下一行去——被切掉的那半行按行号永远读不到（二审 #3）。
    """
    out: list[str] = []
    used = 0
    for i, line in enumerate(numbered):
        if used + len(line) > MAX_OUTPUT:
            if i == 0:
                return line[:MAX_OUTPUT], 1, True
            break
        out.append(line)
        used += len(line)
    return "".join(out), len(out), False


def slice_lines(
    lines: list[str],
    offset: int,
    limit: int,
    *,
    unit: str = "文件",
    resume_hint: bool = True,
) -> dict[str, Any]:
    """按 offset / limit 切一段、编行号、按 MAX_OUTPUT 在整行边界停、加尾注。

    **分段读的唯一定义点**：read_file 读磁盘文件、fetch 读网页正文（v0.27.0，
    按段落一行）都走这里，模型看到的是同一种格式 `N\t原文` 和同一套尾注，
    压缩层的 `_line_span`、退批的「第 a–b 行」对两种结果都认。`unit` 只换
    尾注里的名词（「文件共 N 行」/「页面共 N 行」）。`lines` 是带换行符的行
    （`splitlines(keepends=True)` 的形状）。

    尾注只在**还有没读到的行**时才加（被字符截断、或被 limit 截住而没到底）：
    读到底的输出没有尾注。尾注不带行号前缀，`_line_span` 认不到它，区间统计
    不受影响。`resume_hint=False` 关掉尾注：probe 拿它取摘录，那里没有「接着读」
    这回事，尾注只会混进探索 prompt 和反引号校验的语料里。
    """
    total = len(lines)
    start = max(offset - 1, 0)
    chunk = lines[start : start + limit]
    if not chunk:
        body = f"(空{unit}或超出范围：{unit}共 {total} 行)" if total else f"(空{unit}或超出范围)"
        return {"text": body, "lines": [], "total": total, "truncated": False}
    numbered = [f"{start + i + 1}\t{line}" for i, line in enumerate(chunk)]
    body, kept, partial = _cap_lines(numbered)
    first = start + 1
    last = start + kept
    truncated = partial or kept < len(chunk)
    sep = "" if body.endswith("\n") else "\n"
    if not resume_hint:
        pass
    elif partial:
        body += (
            f"{sep}…（已截断：第 {first} 行本身超过 {MAX_OUTPUT} 字符，这里只有它的开头，"
            f"按行号读不到它的剩余部分；{unit}共 {total} 行）"
        )
    elif truncated:
        body += (
            f"{sep}…（已截断：本次只到第 {last} 行，{unit}共 {total} 行。"
            f"接着读用 offset={last + 1}，limit 不超过 {kept} 行）"
        )
    elif last < total:
        body += f"{sep}（第 {first}–{last} 行，{unit}共 {total} 行；接着读 offset={last + 1}）"
    return {"text": body, "lines": [first, last], "total": total, "truncated": truncated}


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

    读磁盘 + 沙箱校验在这里；切片、编号、尾注在 `slice_lines`。
    """
    try:
        resolved = assert_readable(original_path, path, extra_roots=extra_roots)
    except SandboxError as exc:
        tried = str(resolve_read_target(original_path, path))
        return {"ok": False, "resolved": tried, "text": f"错误：{exc}"}
    try:
        text = filecoord.read_text(resolved)
        lines = text.splitlines(keepends=True)
    except Exception as exc:
        return {
            "ok": False,
            "resolved": str(resolved),
            "text": f"错误：无法读取 {path}：{exc}",
        }
    report = slice_lines(lines, offset, limit, unit="文件", resume_hint=resume_hint)
    return {"ok": True, "resolved": str(resolved), "revision": filecoord.revision(text), **report}


def read_file_sandboxed(
    original_path: Path,
    path: str,
    offset: int = 1,
    limit: int = READ_LIMIT_DEFAULT,
    extra_roots: list[Path] | None = None,
) -> str:
    return read_file_report(original_path, path, offset, limit, extra_roots)["text"]
