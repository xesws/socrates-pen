"""思考正文的去留：**产出它的那一枪要把它带回去。**

## 为什么非留不可

有些节点在 thinking 模式下**要求把 `reasoning_content` 原样回传**（读者那台
的原话：`The reasoning_content in the thinking mode must be passed back to the
API.`）。而 `_StreamedMessage` 一直只数思考的字数、把正文扔掉，于是任何
「模型调了一次工具、下一枪接着说」的对话都会在第二枪吃 400。

这不是 Fast Mode 的病——纯基座、thinking 开着、翻一次手册，一模一样。
Fast Mode 只是让它变得必然（快模型总是先读、再交班给基座）。它直到 v0.22.3
才看得见：在那之前这条 400 被渲染成「请核对 Base URL、model 和 API Key」。

## 为什么换了模型也照传

绊线换回基座时，历史里那条带 `tool_calls` 的 assistant 消息是**快模型**产的。
照传它的思考正文看着别扭，但那恰恰是事实：**就是这段推理导出了那次工具调用**。
OpenAI 兼容的 `reasoning_content` 只是个字符串，没有签名、没有归属校验；
摘掉它反而会让基座又撞回同一个 400。

## 为什么要封顶、要遗忘

思考正文实测能到上千个分片，而 `_agent_loop` 每一枪都重发整段历史。不封顶
就是按轮次翻倍地烧钱，还会撑破快模型那道硬窗口闸。所以：

  - 单条封顶 `MAX_CHARS`
  - **新一轮 user 消息到来时，把之前所有轮次的思考正文忘掉**——那条契约
    只约束「当前这条工具链」，上一轮的推理没人要
"""

from __future__ import annotations

from typing import Any

# 单条思考正文的上限。够满足「必须回传」这条契约，又不至于让历史无限膨胀。
# 和 vision.py 的 MAX_IMAGES / MAX_BYTES 同性质：安全阀，不是给读者调的旋钮。
MAX_CHARS = 8000

FIELD = "reasoning_content"


def clip(text: str) -> str:
    """封顶。截断不影响契约——节点要的是这个字段在，不是它一字不差。"""
    return text[:MAX_CHARS] if len(text) > MAX_CHARS else text


def forget(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """摘掉所有思考正文，返回**副本**。没有就原样返回那个 list，零成本。

    两个调用方，理由不同但动作一样：新一轮开始时清掉旧轮次的（省钱），
    落盘时不写（会话文件里塞几万字推理没有意义）。
    """
    if not any(m.get(FIELD) for m in messages):
        return messages
    return [{k: v for k, v in m.items() if k != FIELD} for m in messages]


def chars(messages: list[dict[str, Any]]) -> int:
    """思考正文占的字符数。**上了线就得进窗口估算**——不算的话，快模型那道
    硬窗口闸会低估，而它量的正是「这一枪有多大」。"""
    return sum(len(str(m.get(FIELD) or "")) for m in messages)
