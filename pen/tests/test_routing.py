"""Fast Mode 的路由判定。

两组断言各自防一件事：

  黄金集   词表的准头。**误判比漏判贵**——漏判有 tutor 那道 edit_file 绊线兜着，
           误判是把最常见的只读轮赶回基座，整个提速就没了。
  理由表   闸门的顺序和命名，形状照 test_probe 的 should_probe 参数化表。
"""

from __future__ import annotations

import pytest

from pen.routing import BASE, FAST, route_for, wants_write

# ── 黄金集 ──────────────────────────────────────────────────────────
# 读者真的在要求动手改手册。这些必须走基座。
WANTS_WRITE = [
    "把刚才那段写回手册",
    "帮我把这个补进第三拍",
    "改写一下这一段的措辞",
    "顺手把这道题插到题目那一节",
    "把解答写进原文",
    "替换掉原文里那句话",
    "校对一下行号",
    "要不要一起补上这段",
    "把这段润色一下写进笔记",
    "把它插入到 questions 那一节",
    "帮我改一下这段的说法",
    "请你把这一节整理一下",
    "rewrite this section",
    "write it back into the handbook",
    "insert into the questions section",
]

# 正当的只读提问。**这些一条都不许被判成写入**。
# 前三条是第一版真的误伤过的 tradeoff 问句——那正是深挖题的形状，
# 也正是 Fast Mode 最该服务的一类。后面几条是陷阱：句子里有写类动词或
# 手册类宾语，但说的根本不是「动手改」。
READ_ONLY = [
    "为什么 dispatch 不直接替换成 match 语句，代价在哪？",
    "如果把学习率改成 0.1 会怎样？",
    "what would happen if we replace the buffer with a queue?",
    "这里为什么选 A 不选 B？",
    "给我举个例子",
    "问我一个问题",
    "给我一段代码",
    "经验回放为什么能打破样本相关性？",
    "目标网络和主网络的区别是什么？",
    "这段和前面第几拍讲的是同一件事吗？",
    "当我零基础，讲清楚再给两个例子",
    "为什么不用更新的写法？",
    "这个设计的代价是什么？",
    "把 gamma 调大意味着什么？",
    "why is the target network updated so rarely?",
    "这里的删除操作是 O(1) 吗？",
    "为什么要先插入再排序？",
    "这一段讲的是什么？",
    "插入排序和归并排序哪个更稳定？",
]


@pytest.mark.parametrize("text", WANTS_WRITE)
def test_real_write_requests_are_caught(text: str) -> None:
    assert wants_write(text) is True


@pytest.mark.parametrize("text", READ_ONLY)
def test_plain_questions_are_not_mistaken_for_edits(text: str) -> None:
    """误判一条，就有一类问题永远享受不到 Fast Mode。"""
    assert wants_write(text) is False


def test_empty_is_never_a_write() -> None:
    assert wants_write("") is False
    assert wants_write("   ") is False
    assert wants_write(None) is False  # type: ignore[arg-type]


def test_chore_table_is_reused_not_duplicated() -> None:
    """questions.is_chore() 命中的，这里也必须命中。

    两张表分家的话，同一句话在「算不算杂务」和「走哪个模型」上会给出
    互相矛盾的答案。
    """
    from pen.questions import is_chore

    for text in WANTS_WRITE:
        if is_chore(text):
            assert wants_write(text), f"is_chore 认了但路由没认：{text}"


# ── 理由表 ──────────────────────────────────────────────────────────

_BASE = dict(fast_on=True, has_fast_cfg=True, chip="socratic", writeback=False, user_text="")


@pytest.mark.parametrize(
    "over,reason",
    [
        ({"fast_on": False}, "fast-off"),
        ({"has_fast_cfg": False}, "no-fast-key"),
        ({"chip": "writeback"}, "writeback-chip"),
        # 自定义写回泡泡的 id 是 u.xxxx，按 id 匹配那条认不出它，靠 writeback 入参
        ({"chip": "u.a1b2c3", "writeback": True}, "writeback-chip"),
        ({"user_text": "把这段写回手册"}, "write-intent"),
        # 读者自己写的泡泡 prompt 也要看
        ({"chip": "u.a1b2c3", "custom_prompt": "把解答插入到题目那一节"}, "write-intent"),
    ],
)
def test_gate_falls_back_to_base_with_a_named_reason(over: dict, reason: str) -> None:
    route, why = route_for(**{**_BASE, **over})
    assert route == BASE
    assert why == reason


@pytest.mark.parametrize(
    "chip,user_text",
    [
        ("socratic", ""),
        ("explain_zero", ""),
        ("examples", ""),
        # 动态追问和深挖题都发 chip="free" + 问题原文进 user_text
        ("free", "这段和前面第几拍讲的是同一件事吗？"),
        ("free", "为什么这里选 A 不选 B，代价是什么？"),
        # 读者自己的只读泡泡
        ("u.a1b2c3", ""),
    ],
)
def test_read_only_bubbles_go_fast(chip: str, user_text: str) -> None:
    """固定只读芯片、动态追问、深挖题、只读的自定义泡泡——全走快模型。"""
    route, why = route_for(**{**_BASE, "chip": chip, "user_text": user_text})
    assert route == FAST
    assert why == ""


def test_reasons_are_ordered_most_specific_first() -> None:
    """同时命中多条时报最具体的那个。

    排序纪律同 probe.should_probe：读者该先听到更具体的那条理由。
    """
    # 开关没开 + 也没钥匙 + 还是写回芯片 → 报最外层的 fast-off
    assert route_for(**{**_BASE, "fast_on": False, "has_fast_cfg": False, "chip": "writeback"}) == (
        BASE,
        "fast-off",
    )
    # 有钥匙 + 写回芯片 + 写入词 → 报芯片那条（比猜词更确定）
    assert route_for(**{**_BASE, "chip": "writeback", "user_text": "把这段写回手册"}) == (
        BASE,
        "writeback-chip",
    )


def test_search_chip_is_not_in_the_write_table() -> None:
    """search 在 app.py 就被短路了，压根不进模型。

    列进 WRITE_CHIPS 会让人以为它还要路由——那是个假的分支。
    """
    from pen.routing import WRITE_CHIPS

    assert "search" not in WRITE_CHIPS
