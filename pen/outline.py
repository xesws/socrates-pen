"""当前笔记的标题 / Q 大纲。不绑 SWE 手册的 Level 分类，不经 LLM。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

ATX_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
Q_RE = re.compile(r"^(\*\*Q\d+\. .+\*\*)\s*$")
HUITOU_RE = re.compile(r"^〔回读：")
DETAILS_CLOSE = "</details>"


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def file_outline(path: Path) -> dict[str, Any]:
    lines = _lines(path)
    n = len(lines)
    in_fence = False
    raw_h: list[tuple[int, int, str]] = []  # start, atx_level, text
    raw_q: list[int] = []
    for i, raw in enumerate(lines, start=1):
        s = raw.rstrip("\n")
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = ATX_RE.match(s)
        if m:
            raw_h.append((i, len(m.group(1)), m.group(2)))
            continue
        if Q_RE.match(s):
            raw_q.append(i)

    headings: list[dict[str, Any]] = []
    for hi, (start, lv, text) in enumerate(raw_h):
        end = n
        for sj, lvj, _t in raw_h[hi + 1 :]:
            if lvj <= lv:
                end = sj - 1
                break
        headings.append(
            {
                "level": lv,
                "text": text,
                "start_line": start,
                "end_line": end,
            }
        )

    questions: list[dict[str, Any]] = []
    for qi, q_line in enumerate(raw_q):
        nxt_q = raw_q[qi + 1] if qi + 1 < len(raw_q) else n + 1
        end = nxt_q - 1
        for hs, _lv, _t in raw_h:
            if q_line < hs < nxt_q:
                end = min(end, hs - 1)
                break
        huitou = None
        for j in range(q_line + 1, end + 1):
            if j <= n and HUITOU_RE.match(lines[j - 1]):
                huitou = j
                end = j
                break
        last_det = None
        for j in range(q_line, end + 1):
            if j <= n and DETAILS_CLOSE in lines[j - 1]:
                last_det = j
        if last_det is not None:
            insert_after = last_det
        elif huitou is not None:
            insert_after = huitou - 1
        else:
            insert_after = end
        questions.append(
            {
                "text": lines[q_line - 1].strip(),
                "start_line": q_line,
                "end_line": end,
                "insert_after_line": insert_after,
            }
        )

    return {"n_lines": n, "headings": headings, "questions": questions}
