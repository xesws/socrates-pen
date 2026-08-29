from __future__ import annotations

import base64

import openai
import httpx
import pytest

from pen.compact import content_text, is_summary_message, message_chars
from pen.session import PenSession, system_prompt
from pen.tutor import provider_error_message, stream_chat
from pen.vision import (
    IMAGE_PLACEHOLDER,
    apply_vision_clause,
    looks_like_vision_reject,
    normalize_images,
    strip_messages_for_disk,
    user_message_content,
)
from pen.config import LLMConfig

PNG = base64.b64encode(
    base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
).decode()


def test_normalize_accepts_tiny_png() -> None:
    got = normalize_images([{"mime": "image/png", "data": PNG}])
    assert len(got) == 1
    assert got[0]["mime"] == "image/png"


def test_normalize_rejects_too_many() -> None:
    with pytest.raises(ValueError, match="vision.too_many"):
        normalize_images([{"mime": "image/png", "data": PNG}] * 5)


def test_normalize_rejects_bad_mime() -> None:
    with pytest.raises(ValueError, match="vision.bad_type"):
        normalize_images([{"mime": "application/pdf", "data": PNG}])


def test_user_message_content_stays_string_without_images() -> None:
    assert user_message_content("hello", []) == "hello"


def test_user_message_content_is_multimodal_with_images() -> None:
    content = user_message_content("hello", [{"mime": "image/png", "data": PNG}])
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "hello"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_content_text_ignores_data_url_bytes() -> None:
    content = user_message_content("hello", [{"mime": "image/png", "data": PNG}])
    text = content_text(content)
    assert "hello" in text
    assert IMAGE_PLACEHOLDER in text
    assert "base64" not in text
    n = message_chars([{"role": "user", "content": content}])
    assert n < 80


def test_strip_messages_for_disk_drops_pixels() -> None:
    content = user_message_content("pkt", [{"mime": "image/png", "data": PNG}])
    stripped = strip_messages_for_disk([{"role": "user", "content": content}])
    blob = str(stripped)
    assert "base64" not in blob
    assert IMAGE_PLACEHOLDER in blob


def test_session_to_dict_strips_pixels_keeps_ram() -> None:
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    content = user_message_content("pkt", [{"mime": "image/png", "data": PNG}])
    sess.messages.append({"role": "user", "content": content})
    dumped = sess.to_dict()
    assert "base64" not in str(dumped["messages"])
    assert "base64" in str(sess.messages[-1]["content"])


def test_stream_chat_refuses_images_when_vision_off() -> None:
    sess = PenSession(session_id="s" * 32, handbook_id="demo")
    cfg = LLMConfig("http://x", "sk", "m", "t", vision=False)
    events = list(
        stream_chat(
            sess,
            __import__("pathlib").Path("x.md"),
            "pkt",
            llm=cfg,
            allow_env_fallback=False,
            images=[{"mime": "image/png", "data": PNG}],
        )
    )
    assert events[0]["type"] == "error"
    assert "图像理解" in events[0]["message"]
    assert len(sess.messages) == 1  # only system; user not appended


def test_apply_vision_clause_toggles() -> None:
    base = system_prompt("zh")
    on = apply_vision_clause(base, enabled=True, lang="zh")
    assert "不要说自己看不见图" in on
    off = apply_vision_clause(on, enabled=False, lang="zh")
    assert "不要说自己看不见图" not in off


def test_looks_like_vision_reject() -> None:
    req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    exc = openai.BadRequestError(
        "invalid image_url",
        response=httpx.Response(400, request=req),
        body={"error": {"message": "images are not supported"}},
    )
    assert looks_like_vision_reject(exc)
    assert "图像理解" in provider_error_message(exc) or "视觉" in provider_error_message(exc)


def test_summary_mark_still_string() -> None:
    msg = {"role": "user", "content": "<!--pen:compact-->\nhello"}
    assert is_summary_message(msg)
