"""追问过滤的黄金集。

fixtures/question_quality.json 是从 408 个落盘会话里挖出的真实生成 + 人工标注。
改 pen/questions.py 的规则时先跑这个——**防误杀比防漏更重要**：
漏一条平庸问题只是没帮上忙，误杀一条好问题是把功能做坏了。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pen.questions import (
    clean_candidates,
    is_chore,
    is_navigation,
    looks_like_placeholder,
    normalize_qkey,
    similarity,
    strip_bullet,
)
from pen.session import FIXED_CHIPS, PROMPT_EXAMPLE_LINES, SYSTEM_PROMPT

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "question_quality.json").read_text(encoding="utf-8")
)
ITEMS = FIXTURE["items"]
LABELS = [c["label"] for c in FIXED_CHIPS]


def _by(label: str) -> list[str]:
    return [i["text"] for i in ITEMS if i["label"] == label]


def _passes(text: str, **kw) -> bool:
    return bool(
        clean_candidates(
            [text],
            example_lines=PROMPT_EXAMPLE_LINES,
            fixed_labels=LABELS,
            limit=9,
            **kw,
        )
    )


def test_fixture_is_not_empty() -> None:
    assert len(ITEMS) >= 40
    for kind in ("placeholder", "chore", "navigation", "good"):
        assert _by(kind), f"黄金集缺 {kind} 样本"


@pytest.mark.parametrize("text", _by("placeholder"))
def test_placeholders_are_dropped(text: str) -> None:
    assert looks_like_placeholder(text)
    assert not _passes(text)


@pytest.mark.parametrize("text", _by("navigation"))
def test_navigation_is_dropped(text: str) -> None:
    assert is_navigation(text)
    assert not _passes(text)


@pytest.mark.parametrize("text", _by("chore"))
def test_writeback_chores_are_dropped(text: str) -> None:
    assert not _passes(text)


@pytest.mark.parametrize("text", _by("good"))
def test_good_questions_survive(text: str) -> None:
    """最重要的一条断言。任何规则调整都不许让这里变红。"""
    assert _passes(text), f"误杀了好问题：{text}"


def test_echoing_the_reader_is_dropped() -> None:
    echoes = _by("echoes_user")
    assert echoes, "黄金集里应有复读样本"
    for text in echoes:
        assert _passes(text), "没有 user_text 时它本身是条好问题"
        assert not _passes(text, user_text=text), "复读读者原话必须被挡"


def test_local_syntax_questions_still_pass_the_realtime_layer() -> None:
    """实时层的定位就是「基础、局部的实时问题」，不砍这类。
    深层探索另有槽位校验（v0.8.1），别把两层的判据混在一起。"""
    for text in _by("local"):
        assert _passes(text), f"实时层不该砍局部题：{text}"


def test_placeholder_prefix_is_stripped_not_dropped() -> None:
    raw = "下一问 1：第三拍那五个例子，哪几个能挪来给第一、二拍当练习？"
    assert strip_bullet(raw) == "第三拍那五个例子，哪几个能挪来给第一、二拍当练习？"
    got = clean_candidates([raw], example_lines=PROMPT_EXAMPLE_LINES, fixed_labels=LABELS)
    assert len(got) == 1 and not got[0]["text"].startswith("下一问")


def test_prompt_example_lines_are_rejected() -> None:
    """模型照抄示范句 → 整条作废。"""
    for line in PROMPT_EXAMPLE_LINES:
        assert not _passes(line), f"示范句被当成真问题下发了：{line}"


def test_system_prompt_has_no_copyable_placeholder() -> None:
    """最便宜的看门狗：历史上「下一问 1」真的变成过按钮。"""
    assert "下一问" not in SYSTEM_PROMPT


def test_fixed_chip_labels_are_rejected() -> None:
    for label in LABELS:
        assert not _passes(label), f"复述固定芯片文案：{label}"


def test_duplicates_within_one_batch_are_collapsed() -> None:
    a = "七块积木里，messages 为什么不算文件？"
    got = clean_candidates([a, a, a], example_lines=PROMPT_EXAMPLE_LINES, fixed_labels=LABELS, limit=9)
    assert len(got) == 1


def test_asked_history_is_respected() -> None:
    a = "七块积木里，messages 为什么不算文件？"
    assert not _passes(a, asked=[a])


def test_limit_is_enforced() -> None:
    raw = [i["text"] for i in ITEMS if i["label"] in ("good", "neutral")]
    got = clean_candidates(raw, example_lines=PROMPT_EXAMPLE_LINES, fixed_labels=LABELS, limit=2)
    assert len(got) == 2
    assert all(c["kind"] == "quick" for c in got)


def test_normalize_and_similarity() -> None:
    assert normalize_qkey("- `echo` 的用法？") == normalize_qkey("echo的用法")
    assert similarity("完全一样的一句话", "完全一样的一句话") == 1.0
    assert similarity("七块积木里 messages 为什么不算文件", "退出码 0 代表什么") < 0.2


def test_navigation_does_not_overreach() -> None:
    """第一版规则拿「第X拍开头且无问号」判导航，误伤了这条。"""
    assert not is_navigation("第五拍 Q2 的 heredoc 引号题怎么解")
    assert not is_chore("为什么第一个参考实现偏偏是 mini-swe-agent，而不是 LangChain？")


# ── 双语：长度带不能拿中文的尺子去卡英文 ──────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Why is mini-swe-agent the first reference implementation and not LangChain?",
        "In the seven building blocks, why is messages not counted as a file?",
        "What breaks if the allowlist is checked before the danger scan runs?",
    ],
)
def test_english_good_questions_survive(text: str) -> None:
    """插件是双语的。中文一个字就是一个信息单位，英文去掉空格后字符数是它的
    两三倍——拿中文那条带去卡英文，好问题会被整批挡掉。"""
    assert _passes(text), f"英文好问题被长度带误杀：{text}"


@pytest.mark.parametrize("text", ["Why?", "Does echo need quotes?", "OK?"])
def test_english_too_short_is_still_dropped(text: str) -> None:
    assert not _passes(text)


def test_english_wall_of_text_is_dropped() -> None:
    assert not _passes("word " * 80 + "why?")


def test_length_band_picks_by_script() -> None:
    from pen.questions import MAX_CHARS, MAX_CHARS_LATIN, _length_band

    assert _length_band("七块积木里 messages 为什么不算文件？")[1] == MAX_CHARS
    assert _length_band("Why is messages not counted as a file here?")[1] == MAX_CHARS_LATIN


def test_chore_filter_does_not_eat_legitimate_design_questions() -> None:
    """第一版拿 替换成/占位/行号 三个词连坐，把正当的 tradeoff / altitude 题
    也杀了。黄金集里没有这类样本，「零误伤」的结论没覆盖到这一片。"""
    from pen.questions import is_chore

    for text in [
        "为什么 dispatch 不直接替换成 match 语句，代价在哪？",
        "工具返回里带行号前缀，对模型改写原文是帮忙还是添乱？",
        "手册里那些占位块是编排上的脚手架，还是读者真该去填的作业？",
        "第七拍的例子替换成真实框架的写法，哪一步会先崩？",
    ]:
        assert not is_chore(text), f"误杀了正当问题：{text}"


@pytest.mark.parametrize("text", _by("chore"))
def test_chore_filter_still_catches_real_chores(text: str) -> None:
    from pen.questions import is_chore

    assert is_chore(text), f"漏掉了写回类操作：{text}"
