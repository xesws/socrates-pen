"""厂商方言表。**这份闸的用处是：表写错了当场红，不用等真节点。**

全表只有 celeris 一行是本机实测的，其余来自各家文档。所以这里查的不是
「文档写得对不对」（查不了），而是三件我们自己能保证的事：

  1. 每一格发出去的东西**形状合法**——键名和取值都在白名单里，
     不会出现手滑写成 `reasoing_effort` 或者 `{"thinking": "enabled"}` 这种；
  2. 型号名**认到了该认的那一家**；
  3. 加一家不许动别家（现存的 celeris / GLM / Gemini 断言在
     test_tutor.py 和 test_reasoning.py 里原样留着，这份不重复）。
"""

from __future__ import annotations

import pytest

from pen.config import THINKING_LEVELS
from pen.providers import (
    AUTO,
    GENERIC,
    PROVIDER_KEYS,
    PROVIDERS,
    provider_for,
    thinking_on,
    thinking_wire,
)

# 线上只允许出现这两个键。多一个就是又一次 `Unknown name "thinking"`。
ALLOWED_KEYS = {"reasoning_effort", "extra_body"}
# 各家档名的并集。写错一个字母（"nono"、"higher"）会被这条抓住。
ALLOWED_EFFORT = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
ALLOWED_THINKING_TYPE = {"enabled", "disabled"}

CELLS = [
    (p.key, variant, level)
    for p in PROVIDERS.values()
    for variant in p.table
    for level in THINKING_LEVELS
]


def test_every_provider_covers_all_four_levels() -> None:
    """少一档就是一个 KeyError，而且只在读者刚好选到那一档时才炸。"""
    for p in PROVIDERS.values():
        for variant, table in p.table.items():
            assert set(table) == set(THINKING_LEVELS), (p.key, variant)


@pytest.mark.parametrize("key,variant,level", CELLS, ids=lambda v: str(v))
def test_every_cell_is_a_legal_wire_payload(key: str, variant: str, level: str) -> None:
    kwargs = PROVIDERS[key].table[variant][level].kwargs
    assert set(kwargs) <= ALLOWED_KEYS, (key, variant, level, kwargs)
    if "reasoning_effort" in kwargs:
        assert kwargs["reasoning_effort"] in ALLOWED_EFFORT
    if "extra_body" in kwargs:
        # 只允许 `{"thinking": {"type": …}}` 这一种嵌套。别的形状进不了这张表。
        assert set(kwargs["extra_body"]) == {"thinking"}
        assert set(kwargs["extra_body"]["thinking"]) == {"type"}
        assert kwargs["extra_body"]["thinking"]["type"] in ALLOWED_THINKING_TYPE


@pytest.mark.parametrize("variant", list(PROVIDERS[GENERIC].table))
@pytest.mark.parametrize("level", THINKING_LEVELS)
def test_generic_never_sends_extra_body(variant: str, level: str) -> None:
    """**v0.22.5 那个 400 的根因，做成回归闸。**

    嵌套的 `thinking` 对象是 DeepSeek / GLM / Kimi 的私货，而 SDK 会把
    `extra_body` 摊到请求体顶层。认不出的节点收到它就是
    `Unknown name "thinking": Cannot find field`。
    """
    assert "extra_body" not in PROVIDERS[GENERIC].table[variant][level].kwargs


@pytest.mark.parametrize(
    "model,want",
    [
        ("deepseek-v4-flash", "deepseek"),
        ("deepseek-chat", "deepseek"),
        ("gemini-3.8-flash", "google"),
        ("google/gemini-3-pro", "google"),  # 网关前缀里的名字照样认得出
        ("glm-5.3", "glm"),
        ("GLM-4.7", "glm"),
        ("kimi-k3", "kimi"),
        ("moonshot-v1-8k", "kimi"),
        ("muse-spark-1.3", "meta"),
        ("llama-4-scout", "meta"),
        ("gpt-4o", "openai"),
        ("o3-mini", "openai"),
        ("celeris-1-magnus", "celeris"),
        # 认不出 → generic。**这一格才是多数第三方节点的真实处境。**
        ("some-random-model", GENERIC),
        ("", GENERIC),
    ],
)
def test_auto_detection(model: str, want: str) -> None:
    assert provider_for(model).key == want


def test_openrouter_is_explicit_only() -> None:
    """它的型号名里带别家的名字，自动识别本来就该让给别家。"""
    assert PROVIDERS["openrouter"].match == ()
    assert provider_for("openrouter/auto").key != "openrouter"
    assert provider_for("anything", "openrouter").key == "openrouter"


@pytest.mark.parametrize("explicit", ["google", "GOOGLE", " google "])
def test_explicit_beats_the_guess(explicit: str) -> None:
    """读者比字符串匹配更知道自己在连什么——走网关、型号名被中转站改过的时候。"""
    assert provider_for("deepseek-v4-flash", explicit).key == "google"


@pytest.mark.parametrize("explicit", ["", AUTO, "no-such-vendor"])
def test_auto_and_junk_fall_back_to_the_guess(explicit: str) -> None:
    """认不得的值当没选，而不是报错——设置项绝不能把一轮对话弄挂。"""
    assert provider_for("deepseek-v4-flash", explicit).key == "deepseek"


@pytest.mark.parametrize(
    "model,variant",
    [
        ("gemini-3.8-flash", "forced"),
        ("gemini-2.5-flash", "optional"),
        ("gemini-pro", "forced"),  # 认不出版本 → 按关不掉处理
        ("glm-5.3-flash", "forced"),
        ("glm-5.2", "effort"),
        ("glm-4.6", "legacy"),
        ("kimi-k3", "k3"),
        ("kimi-k2.6", "k2"),
        ("moonshot-v1-8k", "plain"),
        ("gpt-5.6", "reasoning"),
        ("o3-mini", "reasoning"),
        ("gpt-4o", "chat"),  # 非推理型收到 reasoning_effort 就 400
        ("deepseek-reasoner", "forced"),
        ("deepseek-v4-flash", "v4"),
    ],
)
def test_variant_picking(model: str, variant: str) -> None:
    assert provider_for(model).variant(model) == variant


def test_non_reasoning_openai_gets_nothing() -> None:
    """gpt-4o 收到 reasoning_effort 的原话：

    `Unsupported parameter: 'reasoning.effort' is not supported with this model`
    """
    for lv in THINKING_LEVELS:
        assert thinking_wire("gpt-4o", lv) == {}


def test_meta_never_sends_none() -> None:
    """muse-spark 一直在想，`reasoning_effort:"none"` 直接 400。"""
    assert thinking_wire("muse-spark-1.3", "off") == {"reasoning_effort": "minimal"}
    assert "none" not in str([thinking_wire("muse-spark-1.3", lv) for lv in THINKING_LEVELS])


def test_kimi_k3_must_not_get_a_thinking_block() -> None:
    """官方文档：K3 「should not pass the thinking parameter」。"""
    for lv in THINKING_LEVELS:
        assert "extra_body" not in thinking_wire("kimi-k3", lv)
    assert thinking_wire("kimi-k3", "high") == {"reasoning_effort": "max"}


def test_kimi_k2_keeps_the_nested_shape() -> None:
    assert thinking_wire("kimi-k2.6", "off") == {"extra_body": {"thinking": {"type": "disabled"}}}


def test_deepseek_off_actually_turns_it_off() -> None:
    """**官方文档：不传任何推理字段时，思考模式默认开着、强度 high。**

    所以 v0.23.0 之前这一格发空 dict，读者选的「off」其实是满档思考——
    而 thinking_on 又判成 False，那段推理被我们扔掉，正好凑出 v0.22.4
    那条「reasoning_content 必须原样传回」的 400。
    """
    assert thinking_wire("deepseek-v4-flash", "off") == {
        "extra_body": {"thinking": {"type": "disabled"}}
    }
    assert thinking_on("deepseek-v4-flash", "off") is False
    # low/medium/high 逐字保持既有行为。
    assert thinking_wire("deepseek-v4-flash", "medium")["reasoning_effort"] == "medium"


def test_deepseek_reasoner_gets_nothing() -> None:
    """一直在想的那一路，既不收 thinking 也不收 reasoning_effort。"""
    for lv in THINKING_LEVELS:
        assert thinking_wire("deepseek-reasoner", lv) == {}


@pytest.mark.parametrize("key,variant,level", CELLS, ids=lambda v: str(v))
def test_keep_reasoning_is_false_only_when_we_said_off(
    key: str, variant: str, level: str
) -> None:
    """**拿不准一律 True。** 两种猜错的代价差得很远：

    猜 True 而节点不思考 → 它不吐推理，没东西可留，代价是零。
    猜 False 而节点在思考 → 丢掉正文，下一枪就是 v0.22.4 那条 400。
    """
    shot = PROVIDERS[key].table[variant][level]
    said_off = shot.kwargs.get("reasoning_effort") == "none" or (
        shot.kwargs.get("extra_body", {}).get("thinking", {}).get("type") == "disabled"
    )
    assert shot.keep_reasoning is not said_off, (key, variant, level, shot)


def test_the_wire_is_a_copy() -> None:
    """调用方会把它并进请求 kwargs。脏了表就是全进程的事。"""
    w = thinking_wire("glm-4.6", "high")
    w["extra_body"]["thinking"]["type"] = "wrecked"
    assert thinking_wire("glm-4.6", "high") == {"extra_body": {"thinking": {"type": "enabled"}}}


def test_provider_keys_start_with_auto() -> None:
    """前端下拉的第一档必须是「自动」，且 PROVIDER_KEYS 是前后端共用的那张名单。"""
    assert PROVIDER_KEYS[0] == AUTO
    assert set(PROVIDER_KEYS[1:]) == set(PROVIDERS)


@pytest.mark.parametrize("key", [k for k in PROVIDERS if k != GENERIC])
def test_every_named_provider_prefills_a_base_url(key: str) -> None:
    """选了一家却不给 Base URL，那个下拉就只是个装饰。generic 例外：没有官网。"""
    assert PROVIDERS[key].base_url.startswith("https://")


def test_a_bad_level_falls_back_to_off_not_a_crash() -> None:
    assert thinking_wire("deepseek-v4-flash", "nonsense") == thinking_wire(
        "deepseek-v4-flash", "off"
    )
