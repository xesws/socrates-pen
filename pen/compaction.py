"""压缩策略层：按 mode 选一种把上下文压小的办法。

**这个模块存在的理由是一条硬约束。** 基座是 1M 窗口，怎么折都不容易出事；
快模型只有 131072（而且是 input + max_tokens **合计**，供应商请求时就硬拒）。
两者需要的压缩不是同一件事：

  基座 / 手动命令  到阈值折一次，折成固定形状，**就地改会话**    → RollingSummary
  Fast Mode        按预算逐级降，压到够用为止，**只给副本**      → FastWindow

第二条的「只给副本」不是洁癖，是必需的：`compact_session` 会把
`session.compacted` 置真，而全仓没有任何地方把它改回 False。Fast 那一路
只要走一次破坏性压缩，**这场会话之后连基座轮次都永远拿不到目录 / 邻域 / 书架**。

所以这里的分工是：**策略只负责「算出该发什么」，一律返回 CompactionPlan，
谁都不写回 session。** 想写回的那条路（`compact.py:compact_session`）自己写，
它是唯一一处破坏性入口。

抽槽 / 存根 / slim 那些原语不在这儿重写一份——它们是
`pen/compact.py` 的，这里直接用。那个模块是「怎么把一段 messages 变小」的
唯一定义点，本模块只决定「这一次用哪几档、压到多小」。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pen.compact import (
    # 三个原语。带下划线是 compact.py 的模块内约定，不是「别人不许用」——
    # 本模块是它的策略层，共用同一份实现正是这次重构的目的。
    _allowed_files,
    _slim_if_user,
    _stub_tools,
    fold_messages,
    message_chars,
    split_last_turn,
)
from pen.config import FAST_MAX_OUTPUT, FAST_WINDOW
from pen.vision import content_text

# **比 probe/questions 那两个 _CJK 宽**，是有意的：那两处是拿来「切词」和
# 「判语言」的，只要汉字；这里是拿来估 token 的，中文标点「，。」和全角字符
# 同样占一个 token 量级，漏掉它们就会把这部分按英文系数摊薄。
# 三段：CJK 符号与标点 / 汉字 / 全角形式。
_CJK = re.compile(r"[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]")

# 每 token 折合多少字符。**两个数都刻意取低**，因为这里的估算是拿来卡硬窗口的，
# 估低了就等于在对话中途吃供应商的 400。
#
# 这两个数是**对着真端点量出来的**，不是查来的。四个样本，拿
# celeris-1-magnus 的 usage.prompt_tokens 当真值（已扣掉 chat 模板的固定开销）：
#
#   样本                        字符    估算   实测   估算/实测
#   纯中文手册体                6600   4280   3844    1.11x
#   中英混排（代码 + 说明）    13560   5480   5283    1.04x   ← 最薄的一格
#   read_file 的带行号原文     19889   8229   7893    1.04x
#   纯英文散文                 12900   4300   1954    2.20x
#
# 拉丁那个数从「常见 ~4 字/token」收到 2.5，就是因为上表中间两格：这个插件的
# 拉丁部分主要是**代码和标识符**（`target_net(next_state).max(1)[0].detach()`），
# 它的 token 密度远高于英文散文，按 4 算会当场估低。纯英文散文因此被高估两倍多，
# 那是**故意换来的**——高估只是早折一轮，低估是对话中途 400。
#
# ⚠️ 不要拿 compact.message_chars()/2 来做这件事。那个 2 是给
# should_auto_compact 用的，方向相反——它宁可**晚**折，估低了无害；
# 这里估低了就是 400。两处系数不同源是有意的，别合并。
_CHARS_PER_TOKEN_CJK = 1.5
_CHARS_PER_TOKEN_LATIN = 2.5


def est_tokens(messages: Sequence[dict[str, Any]]) -> int:
    """偏高的窗口估算。宁可早折一轮，不要吃供应商的 400。

    **按字符加权相加，不按整体占比二选一。** 早先写成「CJK 过半就整段用 1.5，
    否则整段用 3.0」，那是个断崖：同一段上下文，多贴几个长 ASCII 路径就能把
    占比压到阈值以下，估值当场减半。实测踩到过——同一个夹具在两个不同长度的
    临时目录下走到了不同的压缩档。中英混排正是这个插件的常态（手册是中文、
    路径和代码是 ASCII），断崖式的估算迟早会在某一格给出宽松系数然后撑破窗口。

    加权相加没有断崖，而且两端都仍然偏高。
    """
    # message_chars 是「哪些字段算进上下文」的唯一定义点（正文 + tool_calls 的
    # 参数和名字）。这里只借它的总数，再把汉字那部分单独数出来分摊系数——
    # 不重写一遍遍历，免得两处对「什么算上下文」的理解分家。
    chars = message_chars(messages)
    if chars <= 0:
        return 0
    cjk = 0
    for m in messages:
        text = content_text(m.get("content"))
        if text:
            cjk += len(_CJK.findall(text))
    other = max(0, chars - cjk)
    return int(cjk / _CHARS_PER_TOKEN_CJK + other / _CHARS_PER_TOKEN_LATIN)


@dataclass(frozen=True)
class Budget:
    """一次请求的上下文预算。

    三个数分开放是有意的：`target` 是读者能调的旋钮，`window` / `max_output`
    是供应商的物理事实。只有 `input_cap()` 把它们合成一个数——**那是这条
    算式的唯一定义点**，别在调用方再算一遍 `window - max_output`。
    """

    # 读者/默认设的输入目标（token）。0 = 不压。
    target: int
    # 供应商窗口，input + output 合计。0 = 不限。
    window: int = FAST_WINDOW
    # 这一路会请求的最大输出。窗口是合计的，所以这部分必须从输入里先扣掉。
    max_output: int = FAST_MAX_OUTPUT

    def input_cap(self) -> int:
        """真正能用的输入上限。0 = 不限。

        读者把 target 填得比物理上限还大时按物理上限走——与其让他在对话
        中途吃 400，不如在这里静默夹住（对齐 merge_limits「只夹紧不报错」）。
        """
        hard = 0
        if self.window > 0:
            hard = max(0, self.window - max(0, self.max_output))
        if self.target <= 0:
            return hard
        if hard <= 0:
            return self.target
        return min(self.target, hard)


@dataclass
class CompactionPlan:
    """「这一次该发什么」。**纯结果，没有任何 session 状态被改过。**"""

    messages: list[dict[str, Any]]
    est_tokens: int
    # 有没有真的动过。False 时 messages 就是原表，调用方可以放心直接用。
    changed: bool = False
    # 降了哪几档。进 SSE 让读者看得见这一轮被压了什么。
    steps: list[str] = field(default_factory=list)
    # 压完还是超。False = 别硬发，退回基座——把上下文压成废墟去迁就窗口，
    # 还不如让大窗口的模型跑。
    fits: bool = True


class Strategy(Protocol):
    """名字 + 一个 plan()。实现放在下面，注册在 STRATEGIES。"""

    name: str

    def plan(
        self,
        session: Any,
        *,
        budget: Budget,
        allow_paths: Sequence[Path] = (),
        original_path: Path | None = None,
    ) -> CompactionPlan: ...


def _fold_plan(
    session: Any,
    msgs: list[dict[str, Any]],
    allowed: Sequence[Path],
) -> list[dict[str, Any]] | None:
    """折一次，拿新 messages。session 只被读，不被写。"""
    fold = fold_messages(
        msgs,
        allowed=allowed,
        lang=session.lang,
        book_title=session.book_title,
        last_chips=session.last_chips,
        last_anchor=session.last_anchor,
    )
    return None if fold is None else fold.messages


class RollingSummary:
    """现有滚动摘要的形状：折成 `[system, 摘要, 最后一轮]`，一步到位。

    **不看预算**——它的语义就是「到阈值了就折一次」，阈值判定在
    `compact.should_auto_compact`，不在这里。`budget` 收下但不用，
    是为了让两个策略共用一个签名。
    """

    name = "rolling"

    def plan(
        self,
        session: Any,
        *,
        budget: Budget,
        allow_paths: Sequence[Path] = (),
        original_path: Path | None = None,
    ) -> CompactionPlan:
        msgs = list(session.messages or [])
        allowed = _allowed_files(allow_paths, original_path, session)
        folded = _fold_plan(session, msgs, allowed)
        if folded is None:
            return CompactionPlan(messages=msgs, est_tokens=est_tokens(msgs))
        return CompactionPlan(
            messages=folded,
            est_tokens=est_tokens(folded),
            changed=True,
            steps=["fold"],
        )


class FastWindow:
    """预算驱动的降级阶梯，返回副本。

    严格照仓里既有的压缩哲学（`cap_selected_text` / `neighborhood` /
    `_thin_by_level` 全都这样）：**够用就原样返回，超了才降级，
    降完在文本里明说降了什么。**

    阶梯从最不可能马上要用的东西开始扔：

      0  预算够      原样返回，changed=False，零成本
      1  stub-history  更早回合的工具结果换存根（正文丢了可以再 read）
      2  slim-history  更早回合 user 包里的目录/邻域/书架换成一句引用
      3  fold          整段折成滚动摘要（这一步也会动最后一轮）
      -  仍然超       fits=False，调用方退回基座

    最后一轮**留到第 3 档才动**：那是模型这会儿正要用的材料，先扔它等于
    为了省窗口把这一轮问废。
    """

    name = "window"

    def plan(
        self,
        session: Any,
        *,
        budget: Budget,
        allow_paths: Sequence[Path] = (),
        original_path: Path | None = None,
    ) -> CompactionPlan:
        msgs = list(session.messages or [])
        cap = budget.input_cap()
        est = est_tokens(msgs)
        if cap <= 0 or est <= cap:
            return CompactionPlan(messages=msgs, est_tokens=est)

        allowed = _allowed_files(allow_paths, original_path, session)
        lang = session.lang or "zh"
        steps: list[str] = []

        cut = split_last_turn(msgs)
        if cut is None:
            # 连一个真 user 包都找不到（只有 system，或只剩旧摘要）。
            # 没有可折的结构，如实说压不下去，别假装做了什么。
            return CompactionPlan(messages=msgs, est_tokens=est, fits=False)
        system, middle, last_turn = cut

        def _done() -> CompactionPlan:
            return CompactionPlan(
                messages=[*system, *middle, *last_turn],
                est_tokens=est,
                changed=bool(steps),
                steps=list(steps),
                fits=est <= cap,
            )

        # ── 阶梯 1 ────────────────────────────────────────────────
        if middle:
            stubbed, _dropped = _stub_tools(middle, allowed, lang)
            if stubbed != middle:
                middle = stubbed
                steps.append("stub-history")
                est = est_tokens([*system, *middle, *last_turn])
                if est <= cap:
                    return _done()

        # ── 阶梯 2 ────────────────────────────────────────────────
        if middle:
            slimmed = [_slim_if_user(m, lang) for m in middle]
            if slimmed != middle:
                middle = slimmed
                steps.append("slim-history")
                est = est_tokens([*system, *middle, *last_turn])
                if est <= cap:
                    return _done()

        # ── 阶梯 3 ────────────────────────────────────────────────
        folded = _fold_plan(session, [*system, *middle, *last_turn], allowed)
        if folded is not None:
            steps.append("fold")
            est = est_tokens(folded)
            return CompactionPlan(
                messages=folded,
                est_tokens=est,
                changed=True,
                steps=list(steps),
                fits=est <= cap,
            )
        return _done()


# 名字 → 策略。形状照 `pen/agent/registry.py` 的 TOOLS 表：一张 dict，
# 一个查表函数，**未命中回落到一个明确的默认**而不是抛错
# （同 `chips.chip_intent` 认不出芯片就回落 free）。
STRATEGIES: dict[str, Strategy] = {
    "rolling": RollingSummary(),
    "window": FastWindow(),
}

# 路由名 → 策略名。这张表是「fast 走哪种压缩」的唯一定义点。
_BY_ROUTE = {"fast": "window", "base": "rolling"}


def strategy_for(route: str) -> Strategy:
    """按路由取压缩策略。认不出的一律回落 rolling（= 今天的行为）。"""
    return STRATEGIES[_BY_ROUTE.get(route, "rolling")]


def fast_budget(limits: Any) -> Budget:
    """从 RuntimeLimits 取快模式预算。

    这是 `fast_context_tokens` 这个旋钮的**活消费方**——
    `test_config.test_every_limit_is_actually_read_somewhere` 盯着这个名字。
    """
    return Budget(target=int(getattr(limits, "fast_context_tokens", 0) or 0))
