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
    strip_images,
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


def test_strip_images_drops_pixels() -> None:
    content = user_message_content("pkt", [{"mime": "image/png", "data": PNG}])
    stripped = strip_images([{"role": "user", "content": content}])
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
    # 只有**真发了图**才能下这个结论。没发图时同一个异常必须换一种说法——
    # 见 test_tutor.py::test_a_reader_who_pasted_no_picture_is_never_told_to_turn_vision_off
    got = provider_error_message(exc, sent_image=True)
    assert "图像理解" in got or "视觉" in got


def test_summary_mark_still_string() -> None:
    msg = {"role": "user", "content": "<!--pen:compact-->\nhello"}
    assert is_summary_message(msg)


# ── 「图像理解」这个开关到底生不生效 ────────────────────────────────
#
# 读者报的病：贴过一张图，节点拒收；把开关关掉，**每一轮还在被拒**。
# 原因是入口那道闸只看「这一轮新贴的图」，而 session.messages 常驻内存、
# 上一轮的 image_url 段跟着每一枪原样重发。
#
# 所以闸必须看**真发出去的那一枪**，不能看会话——看会话的话，把图从历史里
# 删掉也能让断言变绿，而那是另一种（破坏性的）行为。


def _shots_of(monkeypatch, tmp_path, *, vision: bool) -> list[dict]:
    """跑一轮，返回每一枪真发给节点的 kwargs。"""
    import openai
    from pen.tests.test_fast_loop import _Recorder, _book

    book = _book(tmp_path)
    rec = _Recorder([("答完了。" * 20, [])])
    monkeypatch.setattr(openai, "OpenAI", rec.client)
    sess = PenSession(session_id="v" * 32, handbook_id="demo")
    # 上一轮贴过图，这一轮没贴新的 —— 就是读者那个处境。
    sess.messages.append(
        {"role": "user", "content": user_message_content("上一轮，带图", [{"mime": "image/png", "data": PNG}])}
    )
    sess.messages.append({"role": "assistant", "content": "好"})
    cfg = LLMConfig("http://x/v1", "sk", "m", "t", "off", vision=vision)
    list(
        stream_chat(
            sess, book, "包", llm=cfg, extra_roots=[tmp_path], allow_env_fallback=False
        )
    )
    return rec.shots


def _wire_has_image(shot: dict) -> bool:
    from pen.vision import has_image_parts

    return any(has_image_parts(m.get("content")) for m in shot["messages"])


def test_turning_vision_off_stops_sending_old_pictures(monkeypatch, tmp_path) -> None:
    """开关关掉之后，历史里那张图不许再上线。**这条红了就是读者报的那个 bug。**"""
    shots = _shots_of(monkeypatch, tmp_path, vision=False)
    assert shots, "至少得打出一枪"
    assert not any(_wire_has_image(s) for s in shots)


def test_vision_on_still_sends_them(monkeypatch, tmp_path) -> None:
    """反向闸：别把「关掉」修成了「永远不发」。"""
    shots = _shots_of(monkeypatch, tmp_path, vision=True)
    assert any(_wire_has_image(s) for s in shots)


def test_the_pictures_survive_in_the_session(monkeypatch, tmp_path) -> None:
    """摘图是**非破坏**的：关掉再打开，那些像素还在。

    换成就地删历史也能让上面那条变绿，但读者会永久丢图——
    他关的只是「这一路别发图」，不是「把我贴过的图烧了」。
    """
    import openai
    from pen.tests.test_fast_loop import _Recorder, _book

    book = _book(tmp_path)
    rec = _Recorder([("答完了。" * 20, [])])
    monkeypatch.setattr(openai, "OpenAI", rec.client)
    sess = PenSession(session_id="v" * 32, handbook_id="demo")
    sess.messages.append(
        {"role": "user", "content": user_message_content("带图", [{"mime": "image/png", "data": PNG}])}
    )
    off = LLMConfig("http://x/v1", "sk", "m", "t", "off", vision=False)
    list(stream_chat(sess, book, "包", llm=off, extra_roots=[tmp_path], allow_env_fallback=False))
    assert "base64" in str(sess.messages[1]["content"])


def test_strip_images_is_free_when_there_are_none() -> None:
    """没图就原样返回**同一个 list**。每一枪都重建一遍历史是白花钱。"""
    msgs = [{"role": "user", "content": "纯文本"}]
    assert strip_images(msgs) is msgs
