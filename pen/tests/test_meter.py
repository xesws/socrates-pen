"""token 计量的离线测试。一个请求都不发。

守三样东西：
1. read_usage 吃任何脏输入都不炸（计量炸掉不能带崩一轮对话）
2. 两套键名永不相交（防 `{**usage, **spend}` 静默算错）
3. 深挖的账落在账本上，**不落在 PenSession 上**（那条红线）
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pen import meter
from pen.tutor import usage_snapshot


class _Explodes:
    """属性一读就炸。兼容层的模型对象可能带惰性求值的属性。"""

    @property
    def prompt_tokens(self) -> int:
        raise RuntimeError("boom")


@pytest.mark.parametrize(
    "raw",
    [
        None,
        SimpleNamespace(),
        {},
        {"prompt_tokens": None, "completion_tokens": None},
        {"prompt_tokens": "不是数字"},
        SimpleNamespace(prompt_tokens=-5, completion_tokens=-1),
        _Explodes(),
        "彻底不是 usage",
        [1, 2, 3],
    ],
)
def test_read_usage_never_raises_and_floors_at_zero(raw) -> None:
    got = meter.read_usage(raw)
    assert set(got) == {"calls", "in_tokens", "out_tokens", "cached_tokens", "reasoning_tokens"}
    # 上面每一个输入都读不出真数字，所以四个计数格必须全是 0。
    # 写成「>= 0」是空转断言：read_usage 永远返回非负数，那条恒真。
    assert got["in_tokens"] == 0
    assert got["out_tokens"] == 0
    assert got["cached_tokens"] == 0
    assert got["reasoning_tokens"] == 0


def test_read_usage_counts_the_call_even_when_usage_is_missing() -> None:
    """供应商没报数不等于这一枪没花钱。calls 数的是调用，不是账单。"""
    assert meter.read_usage(None)["calls"] == 1


def test_read_usage_reads_the_openai_shape() -> None:
    raw = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=20,
        prompt_tokens_details=SimpleNamespace(cached_tokens=64),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=8),
    )
    got = meter.read_usage(raw)
    assert (got["in_tokens"], got["out_tokens"]) == (100, 20)
    assert (got["cached_tokens"], got["reasoning_tokens"]) == (64, 8)


def test_read_usage_reads_the_deepseek_cache_alias() -> None:
    """DeepSeek 用 prompt_cache_hit_tokens，而本仓的 prompt 是刻意为前缀缓存
    设计的——不认这个名字，命中率就永远是 0。"""
    got = meter.read_usage({"prompt_tokens": 100, "prompt_cache_hit_tokens": 70})
    assert got["cached_tokens"] == 70


def test_openai_details_win_over_the_alias() -> None:
    raw = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=0,
        prompt_tokens_details=SimpleNamespace(cached_tokens=64),
        prompt_cache_hit_tokens=70,
    )
    assert meter.read_usage(raw)["cached_tokens"] == 64


def test_read_usage_accepts_a_plain_dict() -> None:
    """有些兼容层直接把 usage 的 dict 原样塞回来。"""
    got = meter.read_usage({"prompt_tokens": 7, "completion_tokens": 3})
    assert (got["in_tokens"], got["out_tokens"]) == (7, 3)


def test_read_usage_clamps_impossible_cached_and_reasoning() -> None:
    """见过兼容层把 total 填进 cached 的。不夹的话状态行会印出
    「上下文 12.4k … 其中缓存命中 71.2k」这种自相矛盾的一行。"""
    got = meter.read_usage({"prompt_tokens": 10, "prompt_cache_hit_tokens": 999})
    assert got["cached_tokens"] == 10
    got2 = meter.read_usage(
        SimpleNamespace(
            prompt_tokens=0,
            completion_tokens=5,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=999),
        )
    )
    assert got2["reasoning_tokens"] == 5


def test_spend_keys_never_collide_with_usage_snapshot() -> None:
    """把「两件事永不合并」从注释升级成结构性保证。

    将来谁写出 `{**usage, **spend}`，拿到的是一个 9 键的怪东西（一眼看得出不对），
    而不是一个静默算错的 3 键字典。
    """
    assert set(meter.blank()) & set(usage_snapshot(0, 0)) == set()


def test_merge_and_total_add_up() -> None:
    a = meter.read_usage({"prompt_tokens": 10, "completion_tokens": 2})
    b = meter.read_usage({"prompt_tokens": 30, "completion_tokens": 4})
    both = meter.merge(a, b)
    assert (both["calls"], both["in_tokens"], both["out_tokens"]) == (2, 40, 6)
    assert meter.total(both) == 46
    assert meter.total(None) == 0


def test_coerce_survives_a_corrupt_snapshot() -> None:
    assert meter.coerce("nope") == meter.blank()
    assert meter.coerce({"in_tokens": "x", "out_tokens": 5})["out_tokens"] == 5
    book = meter.coerce_book({"chat": {"in_tokens": 9}, "垃圾": 1})
    assert book["chat"]["in_tokens"] == 9
    assert set(book) == set(meter.KINDS)
    assert meter.total_book(book) == 9


def test_meter_accumulates_across_calls() -> None:
    m = meter.Meter(kind=meter.KIND_PROBE)
    m.add({"prompt_tokens": 100, "completion_tokens": 10})
    m.add({"prompt_tokens": 200, "completion_tokens": 20})
    assert m.spent == 330
    assert m.last_in == 300
    assert m.to_dict()["calls"] == 2


def test_two_meters_do_not_share_state() -> None:
    """dataclass 的可变默认值经典坑：field(default_factory=) 写错就串账。"""
    a, b = meter.Meter(), meter.Meter()
    a.add({"prompt_tokens": 5})
    assert b.spent == 0
