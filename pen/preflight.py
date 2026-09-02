"""配置体检：**真往节点打一枪**，看这套设置到底能不能用。

## 为什么非得真打

`/v1/health` 回答的是「槽里有没有钥匙」——那是配置的**形状**。读者报的处境
是形状全对、行为全错：钥匙是废的、model 字符串在这个节点上不存在、或者节点
根本没有视觉。这些只有节点自己知道，问本机问不出来。

所以体检发一条最小的真请求：`max_tokens=1`、不带工具、一句 "hi"。开着
「图像理解」时再挂一个 1×1 的 PNG——**节点收不收图，只有把图递过去才知道**。

## 花销

一枪的输入约十几个 token，输出 1 个。1×1 PNG 的 base64 是 68 个字符。
按最贵的档算也不到千分之一分钱。它只在**配置变了**的时候跑，不进轮询。

## 错在哪，由谁说

不自己判。异常一律交给 `tutor.provider_error_code()`——那是「节点这一下为什么
失败」的唯一定义点，对话里的红字气泡用的也是它。分家的话，同一个 404 会在
气泡里叫「意料外的错误」、在体检里叫「没这个模型」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pen.config import LLMConfig

# 1×1 透明 PNG。**故意用最小的那张**：体检要便宜，而「收不收图」和图多大无关。
PROBE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# 没有 cfg 可体检。和 provider_* 那组码平级，前端一视同仁地拿来当状态。
NO_CONFIG = "no-config"


@dataclass(frozen=True)
class Verdict:
    """体检结论。

    三个都是字符串，所以**不用元组**——位置解包在这种形状下迟早会拧反，
    而拧反的后果是把节点的原话当成型号名显示出来。
    """

    code: str  # 空串 = 这套配置真的能用
    model: str  # 文案要指名道姓说是哪个型号写错了
    detail: str  # 节点自己那句话。分不出类时全靠它


def probe_messages(vision: bool) -> list[dict[str, Any]]:
    """体检那一枪的 messages。开着视觉就挂图——这是「节点拒不拒图」的全部检法。"""
    if not vision:
        return [{"role": "user", "content": "hi"}]
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{PROBE_PNG}"},
                },
            ],
        }
    ]


def check(cfg: LLMConfig | None, *, timeout: float = 20.0) -> Verdict:
    """体检一套配置。

    码为空串表示这套配置**真的能用**——不是「看起来填齐了」。

    ## 为什么有第二枪

    第一枪带 `max_tokens=1`，图便宜。可**那个 1 是我们自己加的**：推理型模型
    常常对最小输出有要求，于是体检可能招来一个真实对话根本不会撞上的 400，
    然后把它当成读者的配置问题报出去。

    误报和漏报一样坏——读者会去改一套本来没坏的设置。所以分不出类的 400
    要再打一枪，这一枪不带 `max_tokens`，形状和真实轮次一致：还错就是真错，
    不错就说明第一枪是被我们自己的省钱手法绊倒的。

    只在失败路径上多打这一枪，所以日常一分钱不多花。
    """
    from openai import OpenAI, OpenAIError

    from pen.tutor import provider_detail, provider_error_code, PROVIDER_REJECTED

    if cfg is None:
        return Verdict(NO_CONFIG, "", "")
    client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=timeout)
    sent_image = bool(cfg.vision)

    def shot(**extra: Any) -> tuple[str, str]:
        try:
            client.chat.completions.create(
                model=cfg.model,
                messages=probe_messages(sent_image),
                # **不带 thinking_wire。** 体检要回答的是「钥匙 / 型号 / 视觉」这
                # 三件，推理档配错了是另一条错误，混进来会让一个 400 说不清是
                # 哪一格的问题。
                **extra,
            )
        except (OpenAIError, OSError, TimeoutError) as exc:
            # 没挂图就绝不可能是「节点拒收了图片」。**这是按构造排除，不是猜。**
            # v0.22.2 少了这个参数，于是关着视觉的读者被告知「把图像理解关掉」。
            return provider_error_code(exc, sent_image=sent_image), provider_detail(exc)
        return "", ""

    code, detail = shot(max_tokens=1)
    if code == PROVIDER_REJECTED:
        code, detail = shot()
    return Verdict(code, cfg.model, detail)
