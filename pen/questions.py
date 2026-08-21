"""追问候选的清洗与过滤。实时层和 v0.8.1 的深层探索共用这一套。

这里只做确定性的硬规则——能用正则和集合运算判死的才放进来。
「问题够不够有技术含量」那种判断留给 prompt 和深层的槽位校验，
词表挡不住，硬挡只会误杀。

规则是拿 408 个落盘会话里挖出的 879 条真实生成（去重 47 条）调出来的，
在那批数据上零误伤。改规则前先跑 test_questions.py 的黄金集。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

# 整条就是个占位符，没有实质内容。
_PLACEHOLDER_ONLY = re.compile(
    r"^(下一问|下一个问题|问题原文|问题|追问|next\s*question|question)"
    r"\s*[0-9一二三四五六]*\s*[:：]?\s*$",
    re.I,
)
# 「下一问 1：第三拍那五个例子…」——剥掉前缀后是好问题，不能整条丢。
_PLACEHOLDER_PREFIX = re.compile(
    r"^(下一问|下一个问题|追问|next\s*question)\s*[0-9一二三四五六]*\s*[:：]\s*",
    re.I,
)
# 纯带路，没有智力含量。目录干的活。
_NAV_LEAD = re.compile(r"^(带我读|接着往下读|继续读|往下读|下一节|直接去|翻到|先看看|回到)")
_NAV_TAIL = re.compile(r"(接着往下读|继续读|往下读|先看看第|直接去)[。.]?$")
# 写回/校对这类编辑操作。那是 writeback 按钮的活，不是学习问题。
# 只挡「请你动手改手册」，不挡「聊聊这个设计」——第一版拿
# 替换成/占位/行号 三个词连坐，把「为什么 dispatch 不直接替换成 match 语句，
# 代价在哪？」这种正当的 tradeoff 题也杀了。宁可漏也别误杀。
_CHORE = re.compile(
    r"(写回(手册|原文|第.拍)|把.{0,20}写(回|进)|校对.{0,8}(行号|目录)"
    r"|要不要一起(补|替换|改)|要不要(顺手|一起)?(补|替换|沉淀))"
)
# 连个疑问的意思都没有的，不是问题。
_INTERROGATIVE = re.compile(r"[？?]|吗|什么|啥|为什么|怎么|如何|哪|是不是|能不能|会不会|多少|几")
_STRIP = re.compile(r"[`*_~\s，。？！、；：,.?!;:\"'()（）「」【】\[\]]+")

# 长度带按语言分。中文一个字就是一个信息单位，英文去掉空格后字符数是它的
# 两三倍——拿中文的带去卡英文，会把「Why is mini-swe-agent the first reference
# implementation and not LangChain?」这种好问题直接挡掉（实测 65 字符）。
MIN_CHARS = 8
MAX_CHARS = 60
MIN_CHARS_LATIN = 20
MAX_CHARS_LATIN = 170
_CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff]")


def _length_band(text: str) -> tuple[int, int]:
    cjk = len(_CJK.findall(text))
    # 半数以上是汉字/假名就按中文算；混排的短句也归中文，那一带更严
    return (MIN_CHARS, MAX_CHARS) if cjk * 2 >= len(text.replace(" ", "")) else (MIN_CHARS_LATIN, MAX_CHARS_LATIN)


def strip_bullet(raw: str) -> str:
    """剥掉 markdown 列表标记和占位符前缀。"""
    s = raw.strip()
    s = re.sub(r"^[-*+]\s*", "", s)
    s = re.sub(r"^\d+[.)]\s*", "", s)
    s = _PLACEHOLDER_PREFIX.sub("", s).strip()
    return s


def normalize_qkey(s: str) -> str:
    """去重用的规范化键：剥掉标记和标点，只留实质字符。"""
    return _STRIP.sub("", strip_bullet(s)).lower()


def _bigrams(s: str) -> set[str]:
    k = normalize_qkey(s)
    if len(k) < 2:
        return {k} if k else set()
    return {k[i : i + 2] for i in range(len(k) - 1)}


def similarity(a: str, b: str) -> float:
    """bigram Jaccard。0 = 毫不相干，1 = 规范化后完全一样。"""
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


def looks_like_placeholder(s: str) -> bool:
    """模型把 prompt 里的示范文字原样抄了出来。"""
    t = strip_bullet(s)
    if not t:
        return True
    if _PLACEHOLDER_ONLY.match(t):
        return True
    if t.startswith("<") and t.endswith(">"):
        return True
    if set(t) <= set(".…·"):
        return True
    return False


def is_navigation(s: str) -> bool:
    """纯带路。注意只挡确定无疑的——第一版拿「第X拍开头且无问号」判，
    误伤了「第五拍 Q2 的 heredoc 引号题怎么解」。"""
    t = strip_bullet(s)
    return bool(_NAV_LEAD.match(t) or _NAV_TAIL.search(t))


def is_chore(s: str) -> bool:
    return bool(_CHORE.search(strip_bullet(s)))


def clean_candidates(
    raw: Iterable[str],
    *,
    example_lines: Iterable[str] = (),
    fixed_labels: Iterable[str] = (),
    user_text: str = "",
    asked: Sequence[str] = (),
    limit: int = 2,
    kind: str = "quick",
) -> list[dict[str, Any]]:
    """把模型吐出来的行清洗成可下发的芯片。挡不住的一律放行——
    这一层只负责「明显不该出现的别出现」，不负责评判深度。"""
    examples = {normalize_qkey(e) for e in example_lines}
    fixed = list(fixed_labels)
    asked_keys = {normalize_qkey(a) for a in asked}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in raw:
        text = strip_bullet(str(item))
        if not text or looks_like_placeholder(text):
            continue
        key = normalize_qkey(text)
        lo, hi = _length_band(text)
        if len(key) < lo or len(key) > hi:
            continue
        if key in examples:  # 抄了 prompt 里的示范句
            continue
        if not _INTERROGATIVE.search(text):
            continue
        if is_navigation(text) or is_chore(text):
            continue
        if key in seen or key in asked_keys:
            continue
        if any(similarity(text, lbl) >= 0.55 for lbl in fixed):
            continue
        # 把读者刚才那句话换个说法再问一遍——他自己刚问过。
        if user_text and similarity(text, user_text) >= 0.65:
            continue
        if any(similarity(text, prev["text"]) >= 0.72 for prev in out):
            continue
        seen.add(key)
        out.append({"id": f"{kind[0]}{len(out)}", "kind": kind, "text": text})
        if len(out) >= limit:
            break
    return out
