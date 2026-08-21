"""Deterministic weak-spot report from persisted turns. No handbook writes."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

WEAK_MIN_HITS = 2
CHIP_WEIGHT = {
    "explain_zero": 2,
    "examples": 2,
    "free": 2,
    "socratic": 1,
}

_SKIP_LEVELS = {"封面", "开篇", "Capstone", "附录"}
_TICK = re.compile(r"`([^`]+)`")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{1,40}")
_Q_NUM = re.compile(r"^Q\d+\.?$", re.IGNORECASE)
_JUNK = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "if",
    "mode",
    "input",
    "system",
    "prompt",
    "then",
    "else",
    "not",
    "yes",
    "no",
    "or",
    "to",
    "of",
}
_HINT_TOKENS = (
    "export",
    "source",
    "runtime",
    "append",
    "messages",
    "tool_calls",
    "shell=True",
    "chmod",
    "heredoc",
    "dispatch",
    "approval",
    "execute-auto",
    "HARD_DENY",
    "rm -rf",
    "create",
    "subprocess",
)


def is_curriculum(anchor: dict[str, Any] | None) -> bool:
    if not anchor:
        return False
    kind = str(anchor.get("kind") or "")
    level = str(anchor.get("level") or "")
    if level in _SKIP_LEVELS:
        return False
    if kind == "q":
        return True
    return kind == "teaching" and level.startswith("Level")


def atom_key(anchor: dict[str, Any]) -> str:
    kind = str(anchor.get("kind") or "other")
    level = str(anchor.get("level") or "")
    title = str(anchor.get("q_title") or anchor.get("beat") or level)
    return f"{kind}|{level}|{title}"


def label_of(anchor: dict[str, Any]) -> str:
    q = str(anchor.get("q_title") or "").replace("**", "").strip()
    if q:
        return q
    beat = anchor.get("beat")
    level = str(anchor.get("level") or "")
    if beat:
        return f"{level} · {beat}"
    return level or "未定位"


def is_junk_keyword(token: str) -> bool:
    t = token.strip().strip("`")
    if not t or len(t) > 48:
        return True
    if _Q_NUM.match(t):
        return True
    if t.lower() in _JUNK:
        return True
    if len(t) <= 2 and t.isascii() and t.isalpha():
        return True
    return False


def _pos_line(anchor: dict[str, Any]) -> int | None:
    raw = anchor.get("start_line")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def extract_keywords(
    *,
    title: str = "",
    selected: str = "",
    user: str = "",
) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(raw: str, src: str) -> None:
        token = raw.strip().strip("`")
        if is_junk_keyword(token):
            return
        key = token.lower()
        if key in seen:
            return
        seen.add(key)
        found.append({"token": token, "src": src})

    parts = (("title", title), ("selected", selected), ("user", user))
    for src, text in parts:
        for m in _TICK.finditer(text or ""):
            add(m.group(1), src)
    for src, text in parts:
        low = (text or "").lower()
        for hint in _HINT_TOKENS:
            if hint.lower() in low:
                add(hint, src)
    if not found:
        for src, text in parts:
            for m in _IDENT.finditer(text or ""):
                add(m.group(0), src)
                if len(found) >= 8:
                    return found
    return found[:8]


def aggregate(turns: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {}
    level_hits: dict[str, int] = defaultdict(int)
    n_curriculum = 0

    for ev in turns:
        anchor = ev.get("anchor") if isinstance(ev.get("anchor"), dict) else {}
        if not is_curriculum(anchor):
            continue
        n_curriculum += 1
        key = atom_key(anchor)
        chip = str(ev.get("chip") or "socratic")
        w = CHIP_WEIGHT.get(chip, 1)
        words = extract_keywords(
            title=str(anchor.get("q_title") or ""),
            selected=str(anchor.get("selected_text") or ""),
            user=str(ev.get("user_text") or ""),
        )
        slot = buckets.get(key)
        if slot is None:
            slot = {
                "key": key,
                "level": str(anchor.get("level") or ""),
                "kind": str(anchor.get("kind") or ""),
                "label": label_of(anchor),
                "q_title": anchor.get("q_title"),
                "beat": anchor.get("beat"),
                "hits": 0,
                "weight": 0,
                "keywords": [],
                "keyword_src": [],
                "start_line": _pos_line(anchor),
            }
            buckets[key] = slot
        slot["hits"] += 1
        slot["weight"] += w
        if slot["start_line"] is None:
            slot["start_line"] = _pos_line(anchor)
        srcs: list[dict[str, str]] = slot["keyword_src"]
        have = {k["token"].lower() for k in srcs}
        for item in words:
            low = item["token"].lower()
            if low in have:
                continue
            have.add(low)
            srcs.append(item)
        slot["keyword_src"] = srcs[:8]
        slot["keywords"] = [k["token"] for k in slot["keyword_src"]]
        level_hits[str(anchor.get("level") or "")] += 1

    denom = max(n_curriculum, 1)
    spots = sorted(
        buckets.values(),
        key=lambda s: (-int(s["hits"]), -int(s["weight"]), str(s["label"])),
    )
    for spot in spots:
        spot["pct"] = round(100.0 * int(spot["hits"]) / denom, 1)

    levels = [
        {
            "level": lv,
            "hits": n,
            "pct": round(100.0 * n / denom, 1),
        }
        for lv, n in sorted(level_hits.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    weak = [s for s in spots if int(s["hits"]) >= WEAK_MIN_HITS]
    footprints = [s for s in spots if int(s["hits"]) < WEAK_MIN_HITS]
    return {
        "n_turns": len(turns),
        "n_curriculum": n_curriculum,
        "levels": levels,
        "weak": weak,
        "footprints": footprints,
    }


def narrate_prompt(report: dict[str, Any]) -> tuple[str, str]:
    slim = {
        "levels": report.get("levels") or [],
        "weak": [
            {
                "level": s.get("level"),
                "label": s.get("label"),
                "hits": s.get("hits"),
                "pct": s.get("pct"),
                "keywords": s.get("keywords") or [],
            }
            for s in (report.get("weak") or [])[:8]
        ],
    }
    system = (
        "你是苏格拉底，坐在读者旁边。根据计数报告写不超过 8 句中文评语，"
        "指出短板挂在哪几道手册 Q / 哪一关上。不要编造报告里没有的题目，"
        "不要索要原话，不要提写回手册。"
    )
    user = "报告（只有计数和关键词）：\n" + json.dumps(
        slim, ensure_ascii=False, indent=2
    )
    return system, user


def narrate(report: dict[str, Any], meter: Any = None) -> str:
    """诊断评语。

    meter 这一格**不进「本会话累计」**：这个端点按 handbook 索引，没有会话
    可挂，为一个读者手动触发、每次一调用的东西新开第四个 store 不值得。
    调用方（app）把它直接放进 HTTP 响应体，谁想看谁看。见 docs/v0.10.0。
    """
    from pen.config import resolve_llm

    cfg = resolve_llm()
    if cfg is None:
        raise RuntimeError("没有模型配置，只显示计数报告")
    from openai import OpenAI

    system, user = narrate_prompt(report)
    client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=60.0)
    resp = client.chat.completions.create(
        model=cfg.model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        stream=False,
    )
    if meter is not None:
        meter.add(getattr(resp, "usage", None))
    return (resp.choices[0].message.content or "").strip()
