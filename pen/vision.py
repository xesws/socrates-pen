"""对话框贴图：OpenAI 兼容的 image_url 段，不进 agent 工具。"""

from __future__ import annotations

import base64
import binascii
from typing import Any

ALLOWED_MIME = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
MAX_IMAGES = 4
MAX_BYTES = 2 * 1024 * 1024
VISION_MARK = "<!--pen:vision-->"
IMAGE_PLACEHOLDER = "[pasted image]"

_MIME_ALIAS = {"image/jpg": "image/jpeg"}


def normalize_mime(raw: str) -> str:
    got = (raw or "").strip().lower()
    return _MIME_ALIAS.get(got, got)


def normalize_images(raw: Any) -> list[dict[str, str]]:
    """把请求体里的 images 收成 [{mime, data}]。坏输入抛 ValueError，args[0] 是 i18n 键。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("vision.bad_type")
    if len(raw) > MAX_IMAGES:
        raise ValueError("vision.too_many")
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("vision.bad_type")
        mime = normalize_mime(str(item.get("mime") or ""))
        data = str(item.get("data") or "").strip()
        if mime not in ALLOWED_MIME:
            raise ValueError("vision.bad_type")
        if not data:
            raise ValueError("vision.bad_type")
        if data.startswith("data:"):
            _, _, data = data.partition(",")
            data = data.strip()
        try:
            blob = base64.b64decode(data, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("vision.bad_type") from exc
        if not blob:
            raise ValueError("vision.bad_type")
        if len(blob) > MAX_BYTES:
            raise ValueError("vision.too_big")
        out.append({"mime": mime, "data": data})
    return out


def image_parts(images: list[dict[str, str]]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for img in images:
        mime = normalize_mime(img.get("mime") or "")
        data = (img.get("data") or "").strip()
        if mime not in ALLOWED_MIME or not data:
            continue
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{data}"},
            }
        )
    return parts


def user_message_content(packet: str, images: list[dict[str, str]]) -> str | list[dict[str, Any]]:
    """无图仍是字符串，有图才变成 OpenAI 多段 content。"""
    parts = image_parts(images)
    if not parts:
        return packet
    return [{"type": "text", "text": packet}, *parts]


def content_text(content: Any) -> str:
    """抽给 compact / 字符估算的纯文本。不算 data URL。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
                continue
            if not isinstance(part, dict):
                continue
            kind = str(part.get("type") or "")
            if kind == "text":
                chunks.append(str(part.get("text") or ""))
            elif kind in ("image_url", "image"):
                chunks.append(IMAGE_PLACEHOLDER)
        return "\n".join(c for c in chunks if c)
    return str(content)


def strip_image_parts(content: Any) -> Any:
    """丢掉像素，换成占位文本。**摘图这件事只有这一份实现。**

    两个调用方，理由不同但动作完全一样：落盘时不写像素；「图像理解」关着时
    不把像素发上线。分家写两份，就会出现「盘上摘干净了、线上还在发」——
    那正是 v0.22.2 修的那个 bug。
    """
    if not isinstance(content, list):
        return content
    out: list[Any] = []
    for part in content:
        if isinstance(part, dict) and str(part.get("type") or "") in ("image_url", "image"):
            out.append({"type": "text", "text": IMAGE_PLACEHOLDER})
        else:
            out.append(part)
    return out


def strip_images(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """整段历史摘图，返回**副本**。没有图就原样返回那个 list，零成本。

    非破坏是有意的：读者把「图像理解」关掉只是说「这一路别发图」，不是说
    「把我贴过的图烧了」。再打开时那些像素还在 session.messages 里。
    """
    if not any(has_image_parts(m.get("content")) for m in messages):
        return messages
    return [{**m, "content": strip_image_parts(m.get("content"))} for m in messages]


def has_image_parts(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(
        isinstance(p, dict) and str(p.get("type") or "") in ("image_url", "image")
        for p in content
    )


def vision_clause(lang: str) -> str:
    if lang == "en":
        return (
            f"\n{VISION_MARK}\n"
            "The reader may paste images in the chat box. Those pixels arrive as "
            "image parts on the user message. If you can see them, talk about what "
            "is in the picture. Do not claim you cannot see images.\n"
        )
    return (
        f"\n{VISION_MARK}\n"
        "读者可能在对话框里粘贴图片。图在用户消息的 image 段里。"
        "看得见就按图说，不要说自己看不见图。\n"
    )


def apply_vision_clause(system_content: str, *, enabled: bool, lang: str) -> str:
    text = system_content or ""
    if VISION_MARK in text:
        text = text.split(VISION_MARK, 1)[0].rstrip()
    if enabled:
        return text + vision_clause(lang)
    return text


def looks_like_vision_reject(exc: BaseException) -> bool:
    bits = " ".join(
        str(x).lower()
        for x in (
            exc,
            getattr(exc, "message", ""),
            getattr(exc, "body", ""),
        )
        if x
    )
    return any(
        token in bits
        for token in (
            "image_url",
            "image url",
            "vision",
            "multimodal",
            "does not support image",
            "doesn't support image",
            "images are not supported",
            "invalid image",
        )
    )
