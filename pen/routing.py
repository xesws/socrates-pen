"""Fast Mode 的路由判定：这一轮走快模型还是基座。

**只读轮走快模型，可能写盘的轮走基座。** 判定必须是**事前**的——路由要在
`stream_chat` 之前就把 `LLMConfig` 定下来，而「模型到底想不想写」要等它开口
才知道。所以这里靠确定性信号预判，漏判由 `pen/tutor.py` 的 edit_file 绊线兜住
（快轮拦下 edit_file、当场换基座重打一枪）。

判定形状照 `pen/probe.py:should_probe`：纯函数、只收关键字参、返回
`(结果, 命名理由)`，理由按「更具体的排前面」排序。

所有气泡——固定的、自定义的、动态追问、深挖题——都走同一个 `/v1/chat`，
区别只在 `chip` 和 `user_text`，所以这张表就是「哪些气泡走快模型」的
唯一定义点：

    socratic / explain_zero / examples   只读     → fast
    search                               压根不进模型（app.py 短路）
    writeback                            天然写回 → base
    free                                 动态追问 / 深挖题 / 读者手打都走这里，
                                         看 user_text 有没有写入意图
    u.* 且 writeback=True                → base
    u.* 且 writeback=False               看读者自己写的 prompt
    /v1/chat/approve 的后半轮            恒 base（那一半必然执行 edit_file）
"""

from __future__ import annotations

import re

from pen.questions import is_chore

FAST = "fast"
BASE = "base"

# 天然会改写原文的固定芯片。search 不在这儿——它在 app.py 就被短路了，
# 压根不进模型，列进来会让人以为它还要路由。
WRITE_CHIPS = ("writeback",)

# 「这句话是在要求动手改手册」。
#
# **锚宾语，不光认动词。** 第一版只列动词（改写|替换|插入|改成…），拿 15 条真实
# 只读提问一量，误判 3 条，而且三条全是 tradeoff 问句：
#   「为什么 dispatch 不直接替换成 match 语句，代价在哪？」
#   「如果把学习率改成 0.1 会怎样？」
#   「what would happen if we replace the buffer with a queue?」
# 这正是深挖题的形状，也正是 Fast Mode 最该服务的那一类。
# （`pen/questions.py:_CHORE` 的注释里记着同一个坑：第一版拿「替换成」连坐，
# 把正当的 tradeoff 题也杀了。同一个教训，这里又踩了一次。）
#
# 代价模型也要说清，因为它决定这张表该多宽。**tutor 那道 edit_file 绊线在**：
#   漏判 → 写入轮跑到快模型。绊线当场拦下、换基座重打，代价是一枪（约 1 秒）。
#   误判 → 只读轮跑到基座。整个提速没了，而这是最常见的轮次。
# 两边不对称，且不对称的方向和直觉相反——所以这张表要**准**，不是要宽。
# 兜底交给绊线，那是确定性信号，比任何词表都准。
#
# `_CHORE` 仍是「杂务」的唯一定义点，下面直接调 is_chore() 当第二个信号，
# 这里只补它之外的部分，不长出第二张同义表。
_OBJ = r"(?:手册|原文|笔记|正文|文件|文档|第.拍|这一?[段节]|那一?[段节])"
_VERB = r"(?:改写|重写|润色|订正|修订|校对|替换|插入|补充|补上|删掉|删除|整理)"
# 不跨句：句读一断就不是同一件事了。
_NEAR = r"[^。！？\n]"
_WRITE_INTENT = re.compile(
    # 这几个词本身就指向手册，不用再锚宾语
    r"写回|写进|写入|沉淀进|归档进"
    # 动词 + 12 字内的手册类宾语
    rf"|{_VERB}{_NEAR}{{0,12}}{_OBJ}"
    # 宾语在前的语序
    rf"|{_OBJ}{_NEAR}{{0,12}}{_VERB}"
    # 祈使：读者在指派动作，而不是在问问题
    rf"|(?:帮我|顺手|你来|请你|麻烦你){_NEAR}{{0,8}}(?:改|写|补|加|插|删|润色|整理)"
    # 英文：同样锚住宾语或介词短语，别光认动词
    r"|rewrite\s+(?:this|the|it)|write\s+(?:it\s+)?(?:back|into)"
    r"|insert\s+(?:it\s+)?(?:into|in|after|before)|append\s+(?:it\s+)?to"
    r"|patch\s+the|edit\s+the|update\s+the\s+(?:file|handbook|note|doc)"
    r"|save\s+(?:it\s+)?(?:to|into)",
    re.I,
)


def wants_write(text: str) -> bool:
    """这段话里有没有「动手改手册」的意思。空串永远是 False。

    两个信号取并集：本模块这张锚宾语的表，加上 questions.is_chore()。
    """
    got = (text or "").strip()
    if not got:
        return False
    return bool(_WRITE_INTENT.search(got)) or is_chore(got)


def route_for(
    *,
    fast_on: bool,
    has_fast_cfg: bool,
    chip: str = "",
    writeback: bool = False,
    user_text: str = "",
    custom_prompt: str = "",
) -> tuple[str, str]:
    """这一轮走哪个模型。返回 `(fast|base, 理由)`，放行时理由是空串。

    纯函数、零成本、绝不抛。**任何一条命中就是 base**——保守的方向是基座：
    走错到基座只是慢一点，走错到快模型才可能让一次改写由小模型执笔。

    `writeback` 由调用方算好（自定义芯片的 id 是 u.xxxx，下面按 id 匹配那条
    认不出它）——和 `probe.should_probe` 收这个入参是同一个理由。
    """
    if not fast_on:
        return BASE, "fast-off"
    if not has_fast_cfg:
        # 开关开着但没配快模型的钥匙。不报错、不拦对话，只是不生效。
        return BASE, "no-fast-key"
    if writeback or chip in WRITE_CHIPS:
        return BASE, "writeback-chip"
    if wants_write(user_text) or wants_write(custom_prompt):
        return BASE, "write-intent"
    return FAST, ""
