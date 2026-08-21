"""给 check-limits.mjs 用：把后端 merge_limits 在一组畸形输入上的结果吐成 JSON。

两张夹紧表是同一道闸的两个副本，只比 min/max/default 三个数只防了一半的漂——
算法本身（四舍五入 vs 截断、数组、十六进制）也得逐个比。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_agent = os.environ.get("SOCRATES_AGENT", "").strip()
_root = Path(_agent) if _agent else Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_root))

from pen.config import default_limits, merge_limits  # noqa: E402

CASES: list[object] = [
    "", " ", None, None, True, False,
    "abc", "一百", [5], {}, "0x10", "1e999",
    "NaN", float("inf"), float("-inf"),
    2.6, -2.6, 0, -1, 7, 999999, "7", " 7 ",
]
KEYS = ("max_tool_rounds", "probe_timeout_s", "cross_book_reads")

out: dict[str, float] = {}
for raw in CASES:
    for k in KEYS:
        got = getattr(merge_limits({k: raw}), k)
        # ensure_ascii=False：JS 的 JSON.stringify 不转义中文，
        # 默认的 \u4e00 会让两边的键对不上，测试就变成「查无此项」而不是
        # 「值不一致」——一种看起来像 bug 的假失败。
        key = json.dumps(raw if raw is not None else None, ensure_ascii=False)
        out[f"{k}|{key}"] = got
print(json.dumps(out))
