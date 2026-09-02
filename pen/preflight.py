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

from typing import Any

from pen.config import LLMConfig

# 1×1 透明 PNG。**故意用最小的那张**：体检要便宜，而「收不收图」和图多大无关。
PROBE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# 没有 cfg 可体检。和 provider_* 那组码平级，前端一视同仁地拿来当状态。
NO_CONFIG = "no-config"


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


def check(cfg: LLMConfig | None, *, timeout: float = 20.0) -> tuple[str, str]:
    """体检一套配置。返回 `(码, 出错时那句话要用的 model 名)`。

    码为空串表示这套配置**真的能用**——不是「看起来填齐了」。
    """
    from openai import OpenAI, OpenAIError

    from pen.tutor import provider_error_code

    if cfg is None:
        return NO_CONFIG, ""
    client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key, timeout=timeout)
    try:
        client.chat.completions.create(
            model=cfg.model,
            messages=probe_messages(bool(cfg.vision)),
            max_tokens=1,
            # **不带 thinking_wire。** 体检要回答的是「钥匙 / 型号 / 视觉」这三件，
            # 推理档配错了是另一条错误（provider.bad_thinking），混进来会让
            # 一个 400 说不清是哪一格的问题。
        )
    except (OpenAIError, OSError, TimeoutError) as exc:
        return provider_error_code(exc), cfg.model
    return "", cfg.model
