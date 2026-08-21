"""外科手术插入折叠块。目标永远是 original_path。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pen.index import HandbookIndex, Section
from pen.sandbox import assert_write_target

INSTANCE_RE = re.compile(r"🔍 实例 (\d+)")


class InsertError(ValueError):
    """折叠块或锚点不合法。"""


@dataclass
class InsertPlan:
    mode: str
    level: str
    q_title: str | None
    beat: str | None
    insert_after_line: int  # 1-based：此行之后插入；替换时为 start-1
    instance_n: int
    fold_md: str
    replace_start: int | None = None  # 1-based inclusive
    replace_end: int | None = None


def lint_fold(fold_md: str) -> list[str]:
    errors: list[str] = []
    text = fold_md.replace("\r\n", "\n").strip("\n") + "\n"
    if "<details>" not in text or "</details>" not in text:
        errors.append("缺少 <details> / </details>")
        return errors
    if text.count("<details>") != text.count("</details>"):
        errors.append("<details> 与 </details> 数量不一致")
    # 要求 details 前后、summary 后有空行（GitHub 渲染契约）
    if not re.search(r"\n<details>\n\n", "\n" + text):
        errors.append("<details> 后必须空一行")
    if not re.search(r"</summary>\n\n", text):
        errors.append("</summary> 后必须空一行")
    if not re.search(r"\n\n</details>\n", text + "\n"):
        errors.append("</details> 前必须空一行")
    sm = re.search(r"<summary>(.*?)</summary>", text, re.S)
    if sm:
        inner = sm.group(1)
        if "<<" in inner or re.search(r"<[a-zA-Z/]", inner):
            errors.append("<summary> 内含未转义的 <")
    if "- **TL;DR：**" in text or "- **(a)" in text:
        errors.append("折叠块不得改写 / 复制 TL;DR 或 (a)(b)(c)")
    if "〔回读：" in text:
        errors.append("折叠块不得包含 〔回读〕")
    return errors


def next_instance_n(section_text: str) -> int:
    nums = [int(n) for n in INSTANCE_RE.findall(section_text)]
    return (max(nums) if nums else 0) + 1


def normalize_fold(fold_md: str, instance_n: int, summary_hint: str | None) -> str:
    body = fold_md.replace("\r\n", "\n").strip()
    if "<details>" not in body:
        title = summary_hint or "苏格拉底补充"
        body = (
            f"<details>\n\n"
            f"<summary>🔍 实例 {instance_n}：{title}</summary>\n\n"
            f"{body}\n\n"
            f"</details>"
        )
    else:
        body = re.sub(
            r"<summary>\s*🔍 实例 \d+：",
            f"<summary>🔍 实例 {instance_n}：",
            body,
            count=1,
        )
        if "<summary>" in body and "🔍 实例" not in body.split("<summary>", 1)[1][:80]:
            body = body.replace("<summary>", f"<summary>🔍 实例 {instance_n}：", 1)
    # 强制空行契约
    body = re.sub(r"<details>\s*", "<details>\n\n", body, count=1)
    body = re.sub(r"</summary>\s*", "</summary>\n\n", body, count=1)
    body = re.sub(r"\s*</details>", "\n\n</details>", body, count=1)
    body = body.strip() + "\n"
    return body


def _last_details_close_line(lines: list[str], start: int, end: int) -> int | None:
    """1-based inclusive range → line number of last </details>."""
    last = None
    for i in range(start, end + 1):
        if "</details>" in lines[i - 1]:
            last = i
    return last


def plan_insert(
    idx: HandbookIndex,
    original_path: Path,
    *,
    line: int,
    fold_md: str,
    summary_hint: str | None = None,
) -> InsertPlan:
    section: Section = idx.locate(line)
    text = original_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    sec_text = "\n".join(lines[section.start_line - 1 : section.end_line])
    n = next_instance_n(sec_text)

    if section.kind == "q":
        mode = "q_append"
        close = _last_details_close_line(lines, section.start_line, section.end_line)
        if close is None:
            # 插在 回读 前一行；若没有 details，插在 (d) 之后、回读之前
            insert_after = section.end_line - 1
        else:
            insert_after = close
    elif section.kind == "teaching":
        mode = "teaching_append"
        insert_after = min(line, section.end_line)
    else:
        mode = "doubt_fold"
        insert_after = min(line, section.end_line)

    fold = normalize_fold(fold_md, n, summary_hint)
    errors = lint_fold(fold)
    if errors:
        raise InsertError("；".join(errors))
    return InsertPlan(
        mode=mode,
        level=section.level,
        q_title=section.q_title,
        beat=section.beat,
        insert_after_line=insert_after,
        instance_n=n,
        fold_md=fold,
        replace_start=None,
        replace_end=None,
    )


def _prepared_fold(
    path: Path,
    fold_md: str,
    summary_hint: str | None,
    count_in: str | None = None,
) -> tuple[str, int]:
    text = count_in if count_in is not None else path.read_text(encoding="utf-8")
    n = next_instance_n(text)
    fold = normalize_fold(fold_md, n, summary_hint)
    errors = lint_fold(fold)
    if errors:
        raise InsertError("；".join(errors))
    return fold, n


def plan_after_line(
    path: Path,
    fold_md: str,
    after_line: int,
    *,
    summary_hint: str | None = None,
    mode: str = "after_line",
    level: str = "",
    q_title: str | None = None,
    beat: str | None = None,
    count_in: str | None = None,
) -> InsertPlan:
    n_lines = len(path.read_text(encoding="utf-8").splitlines())
    if after_line < 0 or after_line > n_lines:
        raise InsertError(f"行号越界：{after_line}（全文 {n_lines} 行）")
    fold, inst = _prepared_fold(path, fold_md, summary_hint, count_in)
    return InsertPlan(
        mode=mode,
        level=level,
        q_title=q_title,
        beat=beat,
        insert_after_line=after_line,
        instance_n=inst,
        fold_md=fold,
        replace_start=None,
        replace_end=None,
    )


def plan_replace_range(
    path: Path,
    fold_md: str,
    start_line: int,
    end_line: int,
    *,
    summary_hint: str | None = None,
    mode: str = "replace_range",
    level: str = "",
    q_title: str | None = None,
    beat: str | None = None,
) -> InsertPlan:
    n_lines = len(path.read_text(encoding="utf-8").splitlines())
    if start_line < 1 or end_line < start_line or end_line > n_lines:
        raise InsertError(f"替换区间非法：{start_line}-{end_line}（全文 {n_lines} 行）")
    fold, inst = _prepared_fold(path, fold_md, summary_hint)
    return InsertPlan(
        mode=mode,
        level=level,
        q_title=q_title,
        beat=beat,
        insert_after_line=start_line - 1,
        instance_n=inst,
        fold_md=fold,
        replace_start=start_line,
        replace_end=end_line,
    )


def render_new_text(original_text: str, plan: InsertPlan) -> str:
    lines = original_text.splitlines()
    fold_lines = plan.fold_md.strip("\n").splitlines()
    if plan.replace_start is not None and plan.replace_end is not None:
        start, end = plan.replace_start, plan.replace_end
        if start < 1 or end < start or end > len(lines):
            raise InsertError(f"替换行越界：{start}-{end}")
        head = lines[: start - 1]
        tail = lines[end:]
    else:
        if plan.insert_after_line < 0 or plan.insert_after_line > len(lines):
            raise InsertError(f"插入行越界：{plan.insert_after_line}")
        head = lines[: plan.insert_after_line]
        tail = lines[plan.insert_after_line :]
    block: list[str] = []
    if head and head[-1].strip() != "":
        block.append("")
    block.extend(fold_lines)
    block.append("")
    new_lines = head + block + tail
    return "\n".join(new_lines) + "\n"


def unified_diff(original_text: str, new_text: str, path_label: str) -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            original_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{path_label}",
            tofile=f"b/{path_label}",
        )
    )


def describe_plan(plan: InsertPlan) -> str:
    inst = plan.instance_n
    if plan.replace_start is not None and plan.replace_end is not None:
        label = (plan.beat or plan.q_title or "").replace("*", "").strip()
        extra = f"「{label}」" if label else ""
        return (
            f"将替换{extra}第 {plan.replace_start}–{plan.replace_end} 行，"
            f"写成「🔍 实例 {inst}」。"
        )
    place = (plan.q_title or plan.beat or "").replace("*", "").strip()
    if place:
        return f"将作为「🔍 实例 {inst}」插在「{place}」后面（约第 {plan.insert_after_line} 行）。"
    return f"将作为「🔍 实例 {inst}」插在第 {plan.insert_after_line} 行之后。"


def apply_insert(original_path: Path, plan: InsertPlan) -> str:
    """原地写入原文。返回新全文。"""
    target = assert_write_target(original_path, original_path)
    old = target.read_text(encoding="utf-8")
    new = render_new_text(old, plan)
    if new == old:
        raise InsertError("插入后文本没有变化")
    if plan.replace_start is None and len(new) < len(old):
        raise InsertError("拒绝缩短原文（只增不删）")
    tmp = target.with_suffix(target.suffix + ".pen-tmp")
    tmp.write_text(new, encoding="utf-8")
    tmp.replace(target)
    return new
