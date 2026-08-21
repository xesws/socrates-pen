"""read_file / edit_file。写只允许登记原文。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pen.meter import over
from pen.readtool import read_file_report
from pen import config
from pen.sandbox import SandboxError, assert_write_target, resolve_read_target
from pen import libraries, snapshots


def limits_of(ctx: dict[str, Any]) -> config.RuntimeLimits:
    """ctx 里认下来的上限。**取不到时回落 config 现值，绝不回落 0。**

    回落 0 会让所有手工拼 ctx 的调用方（测试里就是这么拼的）从「第 9 次停」
    静默变成「第 1 次停」——那种回归看起来像模型突然变笨了，没人会怀疑到闸上。

    这是本文件读上限的**唯一入口**。别在别处再 `ctx.get("limits")` 一次：
    书架的闸 vs read_file 的闸、两处各拼一遍根、两处各筛一遍书，
    本仓已经踩过三次同一个坑。
    """
    got = ctx.get("limits")
    return got if isinstance(got, config.RuntimeLimits) else config.default_limits()


def _occurrences(text: str, needle: str) -> int:
    """重叠也算：'aaa' 里 'aa' 出现 2 次。"""
    if not needle:
        return 0
    n = 0
    start = 0
    while True:
        i = text.find(needle, start)
        if i < 0:
            return n
        n += 1
        start = i + 1


def _is_cross_book(original: Path, resolved: str) -> bool:
    """读的是不是当前手册以外的东西。

    比 inode 而不是比路径字符串：APFS 默认大小写不敏感，模型把 handbook_path 的
    大小写抄错（Handbook.md / handbook.md）读到的是**同一个文件**，
    但 `resolve()` 不规范化大小写，字符串比较会判成跨书，白白吃掉预算。
    实测在 macOS 上确实会撞到。samefile 需要两边都存在——读成功了才走到这里，
    所以正常成立；万一 race 掉了就回落到路径比较。
    """
    try:
        got = Path(resolved).expanduser().resolve()
        mine = original.expanduser().resolve()
        try:
            return not got.samefile(mine)
        except OSError:
            return got != mine
    except Exception:
        return False


def handle_read_file(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    original = Path(ctx["original_path"])
    raw_path = str(args.get("path") or "").strip() or str(original)
    report = read_file_report(
        original,
        raw_path,
        offset=int(args.get("offset", 1) or 1),
        limit=int(args.get("limit", 80) or 80),
        extra_roots=ctx.get("extra_roots") or [],
    )
    text = str(report["text"])
    # 读当前手册以外的东西才计预算（别的教材、lab/ 对照文件）。
    # 读当前手册不计，本职阅读一次都不受影响。
    # 超了不报错、不中断：返回一句让它收敛的话。报错会让模型换个 offset 再试，
    # 那正是我们要止住的循环。
    if report["ok"] and _is_cross_book(original, str(report["resolved"])):
        spent = int(ctx.get("cross_book_chars") or 0)
        reads = int(ctx.get("cross_book_reads") or 0)
        # 闸值从 ctx 取。**别写回 `from pen.config import CROSS_BOOK_CHARS`**：
        # 那是导入期冻结，设置页改完这台 sidecar 要重启才认，
        # 而同一个进程可能还伺候着另一个 vault。
        lim = limits_of(ctx)
        # 第三道闸（v0.10.6）：本轮**累计 token** 到线就不再开新书。
        # 和上面两道不是一回事：字符/次数量的是「这次读了多少」，这道量的是
        # 「这一轮已经烧了多少」——跨书正文进了 session.messages 之后每轮重发，
        # 真实代价是「字符数 × 剩余轮数」，前两道闸看不见这个复利。
        # 它只能是后置的：读的时候根本不知道那段文本值多少 token。
        # cap=0（默认）时 over() 恒为 False，下面两道闸一个字节都不变。
        if over(int(ctx.get("turn_tokens") or 0), lim.max_tokens_cross_book):
            return {
                "ok": True,
                "text": (
                    "本轮的 token 预算快到线了，别再翻别的教材。用已经读到的内容作答，"
                    "并且告诉读者你只读了哪几段、还有哪些没看。"
                ),
                "resolved": str(report["resolved"]),
                "detail": raw_path,
            }
        if spent >= lim.cross_book_chars or reads >= lim.cross_book_reads:
            return {
                "ok": True,
                "text": (
                    "本轮翻别的教材的额度用完了。用已经读到的内容作答，"
                    "并且告诉读者你只读了哪几段、还有哪些没看——不要接着翻，也不要凭书名猜。"
                ),
                "resolved": str(report["resolved"]),
                "detail": raw_path,
            }
        ctx["cross_book_chars"] = spent + len(text)
        ctx["cross_book_reads"] = reads + 1
    return {
        "ok": bool(report["ok"]),
        "text": text,
        "resolved": str(report["resolved"]),
        "detail": raw_path,
    }


def handle_edit_file(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    original = Path(ctx["original_path"])
    raw_path = str(args.get("path") or "").strip() or str(original)
    old = str(args.get("old_string") or "")
    new = str(args.get("new_string") or "")
    tried = str(resolve_read_target(original, raw_path))
    if not old.strip():
        return {
            "ok": False,
            "text": "错误：old_string 不能为空。先 read_file 看准要换的那一小段。",
            "resolved": tried,
            "detail": raw_path,
        }
    try:
        target = assert_write_target(original, tried)
    except SandboxError as exc:
        return {"ok": False, "text": f"错误：{exc}", "resolved": tried, "detail": raw_path}
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "text": f"错误：无法读取 {target.name}：{exc}",
            "resolved": str(target),
            "detail": raw_path,
        }
    if old.strip() == text.strip():
        return {
            "ok": False,
            "text": "错误：禁止把整份原文当 old_string。请只替换需要改的那一小段。",
            "resolved": str(target),
            "detail": raw_path,
        }
    n = _occurrences(text, old)
    if n == 0:
        return {
            "ok": False,
            "text": "错误：原文里找不到这段 old_string。请再 read_file，用去掉行号前缀后的纯原文逐字复制（不要带 12\\t）。",
            "resolved": str(target),
            "detail": raw_path,
        }
    if n > 1:
        return {
            "ok": False,
            "text": f"错误：old_string 在原文里出现了 {n} 次。请加上下文让它只出现一次。",
            "resolved": str(target),
            "detail": raw_path,
        }
    hid = str(ctx.get("handbook_id") or "")
    if hid:
        snapshots.take_snapshot(hid, original, "pre-edit")
    line = text[: text.index(old)].count("\n") + 1
    updated = text.replace(old, new, 1)
    tmp = target.with_suffix(target.suffix + ".pen-tmp")
    tmp.write_text(updated, encoding="utf-8")
    tmp.replace(target)
    if hid:
        try:
            libraries.refresh_if_stale(hid)
        except Exception:
            pass
    return {
        "ok": True,
        "text": f"已编辑 {target.name}（第 {line} 行起替换 1 处，现 {len(updated.splitlines())} 行）",
        "resolved": str(target),
        "detail": raw_path,
        "line": line,
    }
