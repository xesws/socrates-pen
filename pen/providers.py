"""厂商方言：UI 那四档「思考」→ 每家节点真正收的字段。**全仓唯一的定义点。**

## 为什么要有这张表

在这之前，「按型号名分叉」是 `thinking_wire()` 里的一串 `if`，只认三家，
其余一律走 DeepSeek 那一支：

    out["reasoning_effort"] = lv
    out["extra_body"] = {"thinking": {"type": "enabled"}}

而 `extra_body` 里的键会被 OpenAI SDK **摊到请求体顶层**。于是任何没有
`thinking` 这个字段的兼容层收到它就当场 400——Google 的原话是
`Unknown name "thinking": Cannot find field`（v0.22.5 那个读者报告）。
Kimi K3、Meta 的 muse-spark、OpenAI 的 gpt-4o 今天一样会炸，只是还没人报。

所以这一版把「哪家收什么」从代码里搬进表里：一格一个**字面的线上 payload**，
读的人一眼就能核对，写错了 `test_providers.py` 当场红，不用等真节点。

## 认不出的那一家发什么

`generic` 发**裸 `reasoning_effort`**，永远不发 `extra_body`。
`reasoning_effort` 是 OpenAI 兼容层事实上的通用拼法；嵌套的 `thinking` 对象
是 DeepSeek / GLM / Kimi 的私货。把私货当默认，就是上面那个 400 的成因。

## 这张表有多可信

**只有 celeris 那一行是本机实测的**（v0.22.0 每一格都打过一枪量出来）。
其余全部来自各家官方文档——和 v0.22.5 的 Gemini 一样。每个 Provider 的
docstring 里都写了这一行的出处，别把两种强度当成一回事。

猜错的兜底不是小心，是 `pen/preflight.py`：体检按主对话那一枪的真实形状打，
方言不认会当场变成状态栏上一句人话，而不是对话里一个红字气泡。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pen.config import THINKING_LEVELS

# 设置页那个下拉的第一档：不选，按型号名猜。**老库升级后的默认值。**
AUTO = "auto"

# 认不出时落到哪一家。
GENERIC = "generic"


@dataclass(frozen=True)
class Shot:
    """一家厂商的一个档位：这一档到底往线上发什么。"""

    # 并进 chat.completions.create() 的字段。{} = 这一档什么都不发，用节点默认。
    kwargs: dict[str, Any]

    # 这一枪要不要**保留节点吐回来的思考正文**（`thinking_on()` 读的就是它）。
    #
    # 取值规则：**只有我们明确发了「关」才写 False，拿不准一律 True。**
    # 两种猜错的代价差得很远——
    #   猜 True 而节点其实不思考：它不吐推理，就没东西可留，代价是零。
    #   猜 False 而节点其实在思考：丢掉那段正文，下一枪就是 v0.22.4 那条 400
    #   （「thinking 模式下必须把 reasoning_content 原样传回」）。
    # 所以这一列不是「这个模型聪不聪明」，是「万一有推理，我们留不留」。
    keep_reasoning: bool


def _effort(value: str, *, keep: bool = True) -> Shot:
    """只发裸 `reasoning_effort`。兼容层最通用的那种拼法。"""
    return Shot({"reasoning_effort": value}, keep)


def _nested(kind: str, effort: str = "") -> Shot:
    """DeepSeek / GLM / Kimi K2 那套嵌套 `thinking` 对象，可选再带一个档位。"""
    kwargs: dict[str, Any] = {"extra_body": {"thinking": {"type": kind}}}
    if effort:
        kwargs["reasoning_effort"] = effort
    return Shot(kwargs, kind == "enabled")


def _quiet() -> Shot:
    """什么都不发，随节点默认。**keep 仍是 True**——理由见 Shot.keep_reasoning。"""
    return Shot({}, True)


def _same(shot: Shot) -> dict[str, Shot]:
    """四档发一样的东西。给「这家压根没有推理档可调」的型号用。"""
    return {lv: shot for lv in THINKING_LEVELS}


@dataclass(frozen=True)
class Provider:
    """一家厂商。`key` 就是设置页下拉的值，也是随请求上行的那个字符串。"""

    key: str

    # 自动识别：型号名（小写）含这些子串里的任何一个就算这一家。
    # 空元组 = **只能显式选**，绝不自动命中。
    match: tuple[str, ...]

    # 设置页选中这一家时给 Base URL 的预填值。空串 = 不预填。
    base_url: str

    # (型号名正则, 变体名)，从上往下第一个命中。末条恒为 (r"", …) 兜底。
    variants: tuple[tuple[str, str], ...]

    # 变体名 → {off/low/medium/high: Shot}。
    table: dict[str, dict[str, Shot]]

    def variant(self, model: str) -> str:
        mid = _model_id(model)
        for pattern, name in self.variants:
            if not pattern or re.search(pattern, mid):
                return name
        return self.variants[-1][1]

    def shot(self, model: str, level: str) -> Shot:
        return self.table[self.variant(model)][level]


def _model_id(model: str) -> str:
    return (model or "").strip().lower()


# ── 表本身。**书写顺序就是自动识别的判定顺序**（越具体的越靠前）──────────

_DEEPSEEK = Provider(
    key="deepseek",
    match=("deepseek",),
    base_url="https://api.deepseek.com",
    # deepseek-reasoner 是「一直在想」的那一路，既不收 thinking 也不收
    # reasoning_effort，四档全发空。
    variants=((r"deepseek-reasoner", "forced"), (r"", "v4")),
    table={
        "v4": {
            # **官方文档：思考模式默认启用，默认强度 high。** 也就是说这一格
            # 以前发空 dict 的时候，读者选的「off」其实是满档思考——而
            # thinking_on 又判成 False，于是那段推理被我们扔掉，正好凑出
            # v0.22.4 那条 400。所以 off 必须明确地关。
            "off": _nested("disabled"),
            # 文档的映射是 low→low、medium→high、high→high、max→max。
            # high 发 max 是在兑现 thinking_wire 自己的契约「high = 该节点顶档」
            # ——GLM 那两支早就是 max，DeepSeek V4 现在也有这一档。
            # medium 保留字面量：它是官方认的兼容别名（实际等同 high），
            # 发什么读者在设置页就看见什么，不必替他翻译一道。
            "low": _nested("enabled", "low"),
            "medium": _nested("enabled", "medium"),
            "high": _nested("enabled", "max"),
        },
        "forced": _same(_quiet()),
    },
)

_GLM = Provider(
    key="glm",
    match=("glm",),
    base_url="https://open.bigmodel.cn/api/paas/v4",
    # 5.3 / 5.3-FLASH 官方写明 thinking.type=disabled 会 400；5.2 起才认
    # reasoning_effort，且只认 low/high/max。4.x 只有嵌套那一套。
    variants=((r"glm-5\.3", "forced"), (r"glm-5\.2", "effort"), (r"", "legacy")),
    table={
        "forced": {
            "off": _nested("enabled", "low"),  # 关不掉 → 落到最低档
            "low": _nested("enabled", "low"),
            "medium": _nested("enabled", "high"),
            "high": _nested("enabled", "max"),
        },
        "effort": {
            "off": _nested("disabled"),
            "low": _nested("enabled", "low"),
            "medium": _nested("enabled", "high"),
            "high": _nested("enabled", "max"),
        },
        "legacy": {
            "off": _nested("disabled"),
            "low": _nested("enabled"),
            "medium": _nested("enabled"),
            "high": _nested("enabled"),
        },
    },
)

_CELERIS = Provider(
    key="celeris",
    match=("celeris",),
    base_url="https://inference.celeris.ai/celeris-1-magnus/v1",
    variants=((r"", "default"),),
    # **全表唯一一行本机实测的。** 实测传 high 当场 400，节点原话
    # `Supported types are xhigh (default), medium, and low`——它没有 high。
    # off 走 none：实测 0 个 reasoning token、0.36 秒。
    # extra_body.thinking 这一路它收下但完全不理会（disabled 照样吐 49 个
    # reasoning token），所以不发那把空枪。
    table={
        "default": {
            "off": _effort("none", keep=False),
            "low": _effort("low"),
            "medium": _effort("medium"),
            "high": _effort("xhigh"),
        }
    },
)

_GOOGLE = Provider(
    key="google",
    match=("gemini",),
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    # 文档：Gemini 3 起「reasoning cannot be turned off」。**2.5 那一代也不是
    # 整代都能关**——只有 Flash / Flash-Lite 收 thinkingBudget=0，2.5 Pro 官方
    # 写明「N/A: Cannot disable thinking」、最低预算 128。所以 Pro 得单挑出来，
    # 否则 off 那一档在 2.5 Pro 上就是一个 400。
    #
    # **认不出版本按「关不掉」处理**——发 low 最坏是慢一点，发 none 给一个
    # 关不掉的型号是 400，两种错的代价不对等。
    variants=(
        (r"gemini-(?:[3-9]|\d{2,})", "forced"),
        (r"gemini-[\d.]+-pro", "forced"),
        (r"gemini-\d", "optional"),
        (r"", "forced"),
    ),
    # 兼容层自己就认裸 reasoning_effort（minimal/low→low、medium→medium、
    # high→high）。文档另给的 extra_body.google.thinking_config 是第二种嵌套
    # 形状，**没采用**：这条更窄的路文档同样写了支持。
    table={
        "forced": {
            "off": _effort("low"),
            "low": _effort("low"),
            "medium": _effort("medium"),
            "high": _effort("high"),
        },
        "optional": {
            "off": _effort("none", keep=False),
            "low": _effort("low"),
            "medium": _effort("medium"),
            "high": _effort("high"),
        },
    },
)

_OPENAI = Provider(
    key="openai",
    match=("gpt-", "o1-", "o3-", "o4-"),
    base_url="https://api.openai.com/v1",
    # **非推理型收到 reasoning_effort 就 400**（原话：`Unsupported parameter:
    # 'reasoning.effort' is not supported with this model`）。所以默认变体是
    # plain：认不出的一律什么都不发，宁可少一个旋钮，不要一个打不出去的请求。
    # 三条例外必须排在版本规则前面，否则会被 gpt-5+ 那条正则一并卷进推理型：
    #   -chat      gpt-5-chat-latest / gpt-5.2-chat-latest 是**非推理**型号，
    #              原话 `Invalid 'reasoning_effort' for non-reasoning model`
    #   gpt-5-pro  只走 Responses API，且 effort 只收 high；本仓固定走 Chat
    #              Completions，四档里三档必错。发空的至少不会因为档位被拒。
    #   ?:^|/      网关会把型号名写成 openai/o3-mini。锚死在串首就漏了它，
    #              于是 o 系列悄悄落进 plain，推理档变成一个摆设。
    variants=(
        (r"-chat", "plain"),
        (r"gpt-5[\d.]*-pro", "plain"),
        (r"(?:^|/)o\d", "reasoning"),
        (r"gpt-(?:[5-9]|\d{2,})", "reasoning"),
        (r"", "plain"),
    ),
    table={
        # 推理型关不掉思考（能关的那几个型号还认 none，但认不认按版本走，
        # 猜错就是 400）。落到最低档，同 Gemini 3 / GLM-5.3 那两支的处理。
        "reasoning": {
            "off": _effort("low"),
            "low": _effort("low"),
            "medium": _effort("medium"),
            "high": _effort("high"),
        },
        # gpt-4 系列不是推理模型；gpt-5-chat / gpt-5-pro 也落这一格。
        "plain": _same(_quiet()),
    },
)

_KIMI = Provider(
    key="kimi",
    match=("kimi", "moonshot"),
    base_url="https://api.moonshot.ai/v1",
    # 官方文档：K3 用顶层 reasoning_effort（low/high/max，关不掉），且
    # **明说不要传 thinking**；K2.x 用嵌套 thinking；moonshot-v1 那批老型号
    # 压根没有思考。传错一边就是 400，所以这三条必须分开。
    variants=(
        (r"moonshot-v1", "plain"),
        # K2.7 的 thinking.type **只收 enabled，传 disabled 直接报错**
        # （官方原话）。落进下面那个 k2 的话，off 那一档就是一个必炸的请求。
        (r"kimi-k2\.7", "forced"),
        (r"kimi-k(?:[3-9]|\d{2,})", "k3"),
        (r"", "k2"),
    ),
    table={
        "k3": {
            "off": _effort("low"),
            "low": _effort("low"),
            "medium": _effort("high"),
            "high": _effort("max"),
        },
        # keep 不发：官方说「省略或传合法值 all 都按 all 处理」，那就别发。
        "forced": _same(_nested("enabled")),
        "k2": {
            "off": _nested("disabled"),
            "low": _nested("enabled"),
            "medium": _nested("enabled"),
            "high": _nested("enabled"),
        },
        "plain": _same(_quiet()),
    },
)

_META = Provider(
    key="meta",
    # **只认 muse-spark。** 一度也收了 "llama"，那是错的：Together / Groq /
    # Ollama / vLLM 上任何一个 llama-3/4 节点都会被当成 Muse Spark，连 off 都会
    # 发一个它多半不认的 reasoning_effort。一家的方言不能推广到一个生态。
    match=("muse-spark",),
    base_url="https://api.meta.ai/v1",
    variants=((r"", "default"),),
    # 文档：reasoning_effort 收 minimal/low/medium/high/xhigh，而
    # **muse-spark 一直在想，传 none 直接 400**。所以 off 落到 minimal，
    # high 落到 xhigh——「high = 该节点顶档」是这张表的通则，它的顶档是 xhigh。
    table={
        "default": {
            "off": _effort("minimal"),
            "low": _effort("low"),
            "medium": _effort("medium"),
            "high": _effort("xhigh"),
        }
    },
)

_OPENROUTER = Provider(
    key="openrouter",
    # **空元组：只能显式选。** 它的型号名带厂商前缀（google/gemini-3-pro），
    # 而那个前缀里的 "gemini" 已经会被 Google 那一行自动认走——多数时候这正是
    # 对的。这一格的价值在于「自动认错了」的时候有地方翻案，以及预填 base URL。
    match=(),
    base_url="https://openrouter.ai/api/v1",
    variants=((r"", "default"),),
    table={
        "default": {
            "off": _quiet(),
            "low": _effort("low"),
            "medium": _effort("medium"),
            "high": _effort("high"),
        }
    },
)

_GENERIC = Provider(
    key=GENERIC,
    match=(),
    base_url="",
    variants=((r"", "default"),),
    # **绝不发 extra_body。** 这一行就是整版的主要修复：认不出的节点从此拿到的
    # 是通用拼法，而不是 DeepSeek 的私货。
    table={
        "default": {
            "off": _quiet(),
            "low": _effort("low"),
            "medium": _effort("medium"),
            "high": _effort("high"),
        }
    },
)

PROVIDERS: dict[str, Provider] = {
    p.key: p
    for p in (
        _CELERIS,
        _GOOGLE,
        _DEEPSEEK,
        _GLM,
        _KIMI,
        _META,
        _OPENAI,
        _OPENROUTER,
        _GENERIC,
    )
}

# 设置页下拉的全部合法值。**前端那张表必须与它逐键一致**
# （scripts/check-providers.mjs 机械守着）。
PROVIDER_KEYS: tuple[str, ...] = (AUTO, *PROVIDERS)


def provider_for(model: str, explicit: str = "") -> Provider:
    """这个型号该按哪一家的方言发。**全仓唯一的厂商判定点。**

    显式选了就听读者的——他比字符串匹配更知道自己在连什么，尤其是走网关、
    或者型号名被中转站改过的时候。没选（或选了个我们不认的值）才按名字猜，
    再猜不出走 generic。
    """
    key = (explicit or "").strip().lower()
    if key and key != AUTO and key in PROVIDERS:
        return PROVIDERS[key]
    mid = _model_id(model)
    for p in PROVIDERS.values():
        if any(frag in mid for frag in p.match):
            return p
    return PROVIDERS[GENERIC]


def _shot(model: str, level: str, explicit: str = "") -> Shot:
    lv = (level or "off").strip().lower()
    if lv not in THINKING_LEVELS:
        lv = "off"
    return provider_for(model, explicit).shot(model, lv)


def thinking_wire(model: str, level: str, provider: str = "") -> dict[str, Any]:
    """UI 四档 → 节点真正收的字段。空 dict = 不传推理。

    返回的是**副本**：调用方（`llm_create_kwargs`）会把它并进请求 kwargs，
    脏了表就是全进程的事。
    """
    shot = _shot(model, level, provider)
    return {k: _copy(v) for k, v in shot.kwargs.items()}


def thinking_on(model: str, level: str, provider: str = "") -> bool:
    """这一枪要不要留住节点吐回来的思考正文。

    **和 thinking_wire 读的是同一格**（`Shot.keep_reasoning`），不是第二张表。
    「UI 的 off 对某些型号仍然是开着的」这件事只有那一格知道。
    """
    return _shot(model, level, provider).keep_reasoning


def _copy(v: Any) -> Any:
    return {k: _copy(x) for k, x in v.items()} if isinstance(v, dict) else v
