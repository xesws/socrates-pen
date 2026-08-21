"""token 计量。**只数 token，绝不折算货币。**

不换算钱是拍板过的决定，不是没做完：

① 第三方兼容端点的单价不可靠（同一个 model 名在不同中转价差几倍），而且会过时；
② 缓存命中价差十倍，而命中率逐轮抖动；
③ OpenAI SDK 默认 max_retries=2，重发那次的 usage 我们**根本拿不到**，
   统计天生偏低（config.py 里那段 PROBE_TIMEOUT 的注释早就点过这件事）。

三条叠起来，乘出来的钱必然是假精度。token 至少是供应商亲口报的数。

和 tutor.usage_snapshot 是**两件事，永远不合并**：

    usage_snapshot  = 最后一枪的快照，答「此刻窗口占多大」（诊断用）
    meter           = 累加器，答「花了多少」（花费用）

键名刻意不一样（in_tokens/out_tokens vs prompt_tokens/completion_tokens）。
将来谁写出 `{**usage, **spend}`，拿到的是一个 9 键的怪东西而不是一个静默算错的
3 键字典——test_meter.py 里有一条测试把这个不相交守住。

本模块**不 import 任何 pen 内部模块**，只用 stdlib：session.py / probe_store.py /
tutor.py / probe.py 都要 import 它，多一条内部依赖就多一次环的机会。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 归类。落盘字段名靠这几个字面量，改动等于改盘上格式。
KIND_CHAT = "chat"  # 主对话：tutor._agent_loop 的两个收口点
KIND_PROBE = "probe"  # 后台深挖：probe._create
KIND_FOLD = "fold"  # 写回折叠块：tutor.propose_fold_md
# diagnose.narrate 不在这里：它按 handbook 索引，没有会话可挂，
# 只放进 HTTP 响应体，不进「本会话累计」。见 docs/v0.10.0。
KINDS = (KIND_CHAT, KIND_PROBE, KIND_FOLD)

_FIELDS = ("calls", "in_tokens", "out_tokens", "cached_tokens", "reasoning_tokens")


def blank() -> dict[str, int]:
    """一格空账。"""
    return {k: 0 for k in _FIELDS}


def blank_book() -> dict[str, dict[str, int]]:
    """三格空账本。"""
    return {k: blank() for k in KINDS}


def _num(v: Any) -> int:
    """任何东西 → 非负 int。None / 字符串 / 浮点 / 对象一律吃下去。"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _get(obj: Any, name: str) -> Any:
    """usage 可能是 SDK 的模型对象，也可能是一个裸 dict。

    openai 的 CompletionUsage 允许额外字段，所以 DeepSeek 多塞的
    prompt_cache_hit_tokens 是读得到的属性；而某些兼容层会直接把 dict 原样塞回来。
    两种都要认。getattr 也可能触发属性求值并抛异常，一并吞掉。
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def read_usage(raw: Any) -> dict[str, int]:
    """从 resp.usage 抠出 5 个数字。**这个函数绝不抛异常。**

    计量炸掉不能带崩一轮对话。现有测试里的假响应对象（test_probe.py 那个）
    **连 usage 属性都没有**，read_usage(None) 必须安静地回零——那条现有测试
    因此白送了一份 None 安全性的回归覆盖。

    缓存字段两个名字都认：

        OpenAI    usage.prompt_tokens_details.cached_tokens
        DeepSeek  usage.prompt_cache_hit_tokens

    本仓的 prompt 是刻意为前缀缓存设计的（probe.build_system、library_scan 的
    注释都写着），命中率是有意义的信息，不能一直不读。
    """
    prompt = _num(_get(raw, "prompt_tokens"))
    completion = _num(_get(raw, "completion_tokens"))

    cached = _num(_get(_get(raw, "prompt_tokens_details"), "cached_tokens"))
    if not cached:
        # 不存 prompt_cache_miss_tokens：miss = prompt - cached，
        # 多存一个字段就多一个将来会对不上的地方。
        cached = _num(_get(raw, "prompt_cache_hit_tokens"))
    reasoning = _num(_get(_get(raw, "completion_tokens_details"), "reasoning_tokens"))

    # 兜底夹紧。见过兼容层把 total 填进 cached 的；不夹的话
    # 「上下文 12.4k…其中缓存命中 71.2k」这种自相矛盾的行会直接印到状态行上。
    return {
        "calls": 1,
        "in_tokens": prompt,
        "out_tokens": completion,
        "cached_tokens": min(cached, prompt),
        "reasoning_tokens": min(reasoning, completion),
    }


def merge(*rows: dict[str, int] | None) -> dict[str, int]:
    """逐字段相加。非 dict 的一律跳过，不炸。"""
    out = blank()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for k in _FIELDS:
            out[k] += _num(row.get(k))
    return out


def total(row: dict[str, int] | None) -> int:
    """一格的 token 总数 = 输入 + 输出。将来的上限比的就是这个数。

    注意输入通常压倒输出（实测 20:1 以上），所以按总数设的上限实质上是输入闸。
    这是对的：本仓的成本病灶从来是「读到的东西进了 messages 之后每轮重发」。
    """
    if not isinstance(row, dict):
        return 0
    return _num(row.get("in_tokens")) + _num(row.get("out_tokens"))


def total_book(book: dict[str, dict[str, int]] | None) -> int:
    """整本账的 token 总数。"""
    if not isinstance(book, dict):
        return 0
    return sum(total(v) for v in book.values())


def coerce(raw: Any) -> dict[str, int]:
    """盘上读回来的一格。任何脏数据退化成 0，不让一份坏 JSON 顶掉整个会话。"""
    return merge(raw if isinstance(raw, dict) else None)


def coerce_book(raw: Any) -> dict[str, dict[str, int]]:
    """盘上读回来的一本账。旧快照里根本没这个键 → 全 0，不需要迁移。"""
    book = blank_book()
    if isinstance(raw, dict):
        for k in KINDS:
            book[k] = coerce(raw.get(k))
    return book


def over(spent: int, cap: int, *, headroom: int = 0) -> bool:
    """超没超。

    **`cap > 0` 这一半就是「0 = 不限」的全部实现，别改成别的判据**——
    它是「默认档逐字节一致」这条承诺的唯一实现点。

    headroom 是「还要留出多少给收口那一枪」。不留余量的话上限根本不是上限：
    本仓实测一句「另一本讲什么」触发 21 次 read_file、末轮 prompt 27k token，
    而累计花销是**二次增长**的（每轮把整段 messages 重发）。卡在线上时，
    收口那一枪的大小和上限之间没有任何关系——它只和 messages 有多长有关。
    """
    return cap > 0 and (spent + max(0, headroom)) >= cap


@dataclass
class Meter:
    """一次「活动」的累加器：一轮主对话 / 一次深挖 / 一次写回。

    实例由**单个线程独占**（probe 那个跑在后台线程上，但那次探索从头到尾
    只有它一个人碰），所以不加锁。跨线程共享的那份账靠 probe_store 的全局
    RLock 保护，见 probe_store.add_questions。
    """

    kind: str = KIND_CHAT
    row: dict[str, int] = field(default_factory=blank)

    def add(self, raw: Any) -> dict[str, int]:
        """吃一个 resp.usage，返回**这一次**的增量（调用方常要单独用它）。"""
        one = read_usage(raw)
        self.row = merge(self.row, one)
        return one

    @property
    def spent(self) -> int:
        """本 Meter 至今花掉的 token 总数。"""
        return total(self.row)

    @property
    def last_in(self) -> int:
        """本 Meter 见过的输入总量。将来给余量估算用。"""
        return _num(self.row.get("in_tokens"))

    def to_dict(self) -> dict[str, int]:
        return dict(self.row)
