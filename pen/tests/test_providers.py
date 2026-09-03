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
    flatten_unsigned,
    provider_for,
    signed,
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
        # **llama 不归 Meta 认领。** Together / Groq / Ollama / vLLM 上的
        # llama-3/4 和 Muse Spark 没有任何方言关系，认走了连 off 都会发一个
        # 它多半不认的 reasoning_effort。
        ("llama-4-scout", GENERIC),
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
        ("gpt-4o", "plain"),  # 非推理型收到 reasoning_effort 就 400
        # 名字里带 -chat 的是**非推理**型号，原话
        # `Invalid 'reasoning_effort' for non-reasoning model: gpt-5-chat-latest`。
        # 排在版本规则前面，否则被 gpt-5+ 那条一并卷进推理型。
        ("gpt-5-chat-latest", "plain"),
        ("gpt-5.2-chat-latest", "plain"),
        # 只走 Responses API，且 effort 只收 high；本仓固定走 Chat Completions。
        ("gpt-5-pro", "plain"),
        # 网关会把型号名写成 openai/o3-mini。锚死在串首就漏了它。
        ("openai/o3-mini", "reasoning"),
        # Gemini 2.5 **Pro** 关不掉思考（官方：N/A: Cannot disable thinking），
        # 只有 Flash / Flash-Lite 收 thinkingBudget=0。整代按 optional 处理，
        # Pro 的 off 就是一个 400。
        ("gemini-2.5-pro", "forced"),
        ("gemini-2.5-flash-lite", "optional"),
        # K2.7 的 thinking.type 只收 enabled，传 disabled 直接报错。
        ("kimi-k2.7-code", "forced"),
        ("kimi-k2.7-code-highspeed", "forced"),
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


# ── Codex 那轮审出来的七条，逐条钉住 ────────────────────────────────


def test_gemini_25_pro_cannot_turn_thinking_off() -> None:
    """官方：2.5 Pro「N/A: Cannot disable thinking」，最低预算 128。

    只有 Flash / Flash-Lite 收 thinkingBudget=0。**这条红了就是 2.5 Pro 上
    每一个 off 档都在打 400。**
    """
    assert thinking_wire("gemini-2.5-pro", "off") == {"reasoning_effort": "low"}
    assert thinking_on("gemini-2.5-pro", "off") is True
    # 反向闸：别把修复做成「2.5 整代都关不掉」。
    assert thinking_wire("gemini-2.5-flash", "off") == {"reasoning_effort": "none"}


def test_kimi_k27_must_never_be_told_to_stop_thinking() -> None:
    """官方原话：K2.7 的 thinking.type「只支持 enabled，传 disabled 会报错」。"""
    for m in ("kimi-k2.7-code", "kimi-k2.7-code-highspeed"):
        for lv in THINKING_LEVELS:
            assert thinking_wire(m, lv) == {"extra_body": {"thinking": {"type": "enabled"}}}
            assert thinking_on(m, lv) is True
    # 反向闸：K2.6 仍然关得掉。
    assert thinking_wire("kimi-k2.6", "off") == {"extra_body": {"thinking": {"type": "disabled"}}}


@pytest.mark.parametrize("model", ["gpt-5-chat-latest", "gpt-5.2-chat-latest", "gpt-5-pro"])
def test_openai_chat_and_pro_get_no_reasoning_field(model: str) -> None:
    """两条都会 400：

    -chat 是非推理型号（`Invalid 'reasoning_effort' for non-reasoning model'`）；
    gpt-5-pro 只走 Responses API 且 effort 只收 high。
    """
    for lv in THINKING_LEVELS:
        assert thinking_wire(model, lv) == {}


def test_a_gateway_prefixed_o_series_still_gets_its_dial() -> None:
    """网关写成 openai/o3-mini。锚死串首就漏了它，推理档变成一个摆设。"""
    assert thinking_wire("openai/o3-mini", "high") == {"reasoning_effort": "high"}
    assert thinking_wire("o3-mini", "high") == {"reasoning_effort": "high"}


def test_llama_is_not_muse_spark() -> None:
    """**一家的方言不能推广到一个生态。**

    Together / Groq / Ollama / vLLM 上任何一个 llama 节点都不该被当成
    Muse Spark——连 off 都会发一个它多半不认的 reasoning_effort=minimal。
    """
    for m in ("llama-4-scout", "meta-llama/Llama-3.3-70B-Instruct", "llama3:8b"):
        assert provider_for(m).key == GENERIC
        assert thinking_wire(m, "off") == {}


def test_high_is_the_endpoint_top_tier_everywhere() -> None:
    """thinking_wire 的契约是「high = 该节点顶档」。逐家兑现，别只兑现一半。"""
    top = {
        "deepseek-v4-flash": "max",
        "glm-5.3": "max",
        "glm-5.2": "max",
        "celeris-1-magnus": "xhigh",
        "muse-spark-1.3": "xhigh",
        "kimi-k3": "max",
    }
    for model, want in top.items():
        assert thinking_wire(model, "high")["reasoning_effort"] == want, model


# ── 跨厂商的历史：发得出去，且只对要求签名的那一家动手 ──────────────

SIG = {"extra_content": {"google": {"thought_signature": "EsECCr4"}}}


def _call(cid: str, *, sig: bool) -> dict:
    c = {"id": cid, "type": "function",
         "function": {"name": "read_file", "arguments": '{"path":"x"}'}}
    return {**c, **SIG} if sig else c


def _round(cid: str, *, sig: bool) -> list[dict]:
    return [
        {"role": "assistant", "content": "看一下", "tool_calls": [_call(cid, sig=sig)]},
        {"role": "tool", "tool_call_id": cid, "content": "原文"},
    ]


def test_only_google_asks_for_a_signature() -> None:
    """别家一个都不要求。多标一家就是白白把结构化的往返压成文本。"""
    want = {p.key for p in PROVIDERS.values() if p.sig_path}
    assert want == {"google"}


@pytest.mark.parametrize(
    "call,path,ok",
    [
        (_call("a", sig=True), ("extra_content", "google", "thought_signature"), True),
        (_call("a", sig=False), ("extra_content", "google", "thought_signature"), False),
        # 半截路径断在中间：不许把 KeyError 当成「有签名」。
        ({"extra_content": {"google": {}}}, ("extra_content", "google", "thought_signature"), False),
        ({"extra_content": "不是 dict"}, ("extra_content", "google", "thought_signature"), False),
        # 空签名值 = 没有。
        ({"extra_content": {"google": {"thought_signature": ""}}},
         ("extra_content", "google", "thought_signature"), False),
        # 不要求签名的家：什么都算过。
        (_call("a", sig=False), (), True),
    ],
)
def test_signed_reads_the_whole_path(call: dict, path: tuple, ok: bool) -> None:
    assert signed(call, path) is ok


def test_a_vendorless_round_is_flattened_for_google() -> None:
    """**读者撞的就是这一条。**

    Fast Mode 的快轮（Celeris）产的 tool_call 身上没有 Google 签名，切回基座
    的第一枪就是 `Function call is missing a thought_signature`。压平之后
    信息还在——「我调过 read_file、参数是这些、结果是这些」——只是不再是
    结构化的调用。对**已经执行完**的往返来说这没有损失。
    """
    h = [{"role": "user", "content": "hi"}, *_round("a", sig=False)]
    out = flatten_unsigned(h, "gemini-3.8-flash")
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert not any(m.get("tool_calls") for m in out)
    assert "read_file" in out[1]["content"] and "看一下" in out[1]["content"]
    assert '{"path":"x"}' in out[1]["content"]   # 参数不能丢
    assert "原文" in out[2]["content"]            # 工具结果更不能丢


def test_googles_own_rounds_keep_their_shape() -> None:
    """**这条红了，bce3b0b 那个修复就白做了。**

    Gemini 自己产的往返带着签名、发得出去，压平它只会平白丢掉结构。
    """
    h = _round("a", sig=True)
    out = flatten_unsigned(h, "gemini-3.8-flash")
    assert out == h
    assert out[0]["tool_calls"][0]["extra_content"] == SIG["extra_content"]


def test_a_mixed_history_only_loses_the_unsigned_half() -> None:
    """换模型的会话里两种往返是混着的。签了名的那些一条都不许动。"""
    h = [*_round("a", sig=False), *_round("b", sig=True)]
    out = flatten_unsigned(h, "gemini-3.8-flash")
    assert [m["role"] for m in out] == ["assistant", "user", "assistant", "tool"]
    assert out[2:] == h[2:]


def test_an_orphan_tool_result_never_survives_alone() -> None:
    """assistant 压平了，配对的 tool 结果就成了没主人的孤儿——**它本身也非法**。

    只认自己压平过的那些 id：历史里别的 tool 结果（配着签了名的调用）要原样留。
    """
    h = [*_round("a", sig=False), *_round("b", sig=True)]
    out = flatten_unsigned(h, "gemini-3.8-flash")
    assert out[1]["role"] == "user"    # a 的结果跟着走了
    assert out[3]["role"] == "tool"    # b 的结果原样留着
    assert out[3]["tool_call_id"] == "b"


@pytest.mark.parametrize("model", ["deepseek-v4-flash", "celeris-1-magnus", "gpt-5.6", "kimi-k3"])
def test_every_other_vendor_gets_the_very_same_list(model: str) -> None:
    """**老库和别家逐字节不变。**

    返回同一个列表对象，不是「内容相等的另一个列表」——连拷贝都不做，
    这条路上一个字节都没动过。
    """
    h = [*_round("a", sig=False)]
    assert flatten_unsigned(h, model) is h


def test_the_flattening_rides_the_real_shot() -> None:
    """闸得走读者真正走的那条路：llm_create_kwargs 是「那一枪长什么样」的
    唯一定义点，压平挂在别处就是第二个定义点。
    """
    from pen.config import LLMConfig
    from pen.tutor import llm_create_kwargs

    h = [{"role": "user", "content": "hi"}, *_round("a", sig=False)]
    google = LLMConfig("https://x/v1", "k", "gemini-3.8-flash", "settings", "medium")
    sent = llm_create_kwargs(google, messages=h)["messages"]
    assert not any(m.get("tool_calls") for m in sent)
    other = LLMConfig("https://x/v1", "k", "celeris-1-magnus", "settings", "medium")
    assert llm_create_kwargs(other, messages=h)["messages"] is h
