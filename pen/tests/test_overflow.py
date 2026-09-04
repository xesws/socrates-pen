"""上下文超限：认出来、读出数字、把这一批工具结果退给模型。

三组断言各防一件事：

  判定    六家节点的溢出报文都要认成 too-long；普通 400 / 视觉拒收 / TPM 限流不能误判。
  数字    「这一枪多少、上限多少」要从报文里读出来——退给模型的那句话靠它们说服模型改小区间。
  退批    只退尾巴那一批工具结果，edit_file 不退，退过的不再退，收口枪的假 user 要跳过。
"""

from __future__ import annotations

import json

import httpx
import openai

from pen.overflow import (
    OVERFLOW_MARK,
    OVERFLOW_MARK_EN,
    is_overflow_stub,
    looks_like_context_overflow,
    overflow_numbers,
    stub_trailing_batch,
)
from pen.tutor import (
    FORCE_ANSWER,
    PROVIDER_NO_VISION,
    PROVIDER_REJECTED,
    PROVIDER_TOO_LONG,
    PROVIDER_UNEXPECTED,
    provider_error_code,
    provider_error_message,
)


def _exc(message: str, *, status: int = 400, code: str | None = None) -> openai.APIStatusError:
    req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    err: dict = {"message": message}
    if code:
        err["code"] = code
    cls = openai.BadRequestError if status == 400 else openai.APIStatusError
    return cls("bad", response=httpx.Response(status, request=req), body={"error": err})


OPENAI = (
    "This model's maximum context length is 131072 tokens. However, you requested "
    "140000 tokens (135000 in the messages, 5000 in the completion). "
    "Please reduce the length of the messages or completion."
)
CEREBRAS = (
    "you requested 16384 output tokens and your prompt contains at least 114689 input "
    "tokens, for a total of at least 131073 tokens"
)
GOOGLE = "The input token count (500000) exceeds the maximum number of tokens allowed (1048576)."
ANTHROPIC = "prompt is too long: 250000 tokens > 200000 maximum"
OPENAI_NEW = "Your input exceeds the context window of this model. Please adjust your input and try again."


# ── 判定 ────────────────────────────────────────────────────────────


def test_six_shapes_of_overflow_are_all_too_long() -> None:
    for text in (OPENAI, CEREBRAS, GOOGLE, ANTHROPIC, OPENAI_NEW):
        assert looks_like_context_overflow(_exc(text)), text
        assert provider_error_code(_exc(text), sent_image=False) == PROVIDER_TOO_LONG, text
    coded = _exc("Invalid request.", code="context_length_exceeded")
    assert provider_error_code(coded, sent_image=False) == PROVIDER_TOO_LONG
    big = _exc("Request too large: prompt tokens exceed the model context", status=413)
    assert provider_error_code(big, sent_image=False) == PROVIDER_TOO_LONG


def test_a_big_request_body_is_not_a_context_overflow() -> None:
    """二审 #5：「request too large」单独不算——图片把请求体撑爆也是这句话，
    退批、折叠都救不了它。得同时提到 token 或 context 才是窗口的事。"""
    entity = _exc("Request Entity Too Large", status=413)
    assert not looks_like_context_overflow(entity)
    assert provider_error_code(entity, sent_image=False) == PROVIDER_UNEXPECTED
    count = _exc("input token count is 5; billing enabled")
    assert not looks_like_context_overflow(count)


def test_ordinary_400s_stay_rejected_and_tpm_413_is_not_overflow() -> None:
    assert provider_error_code(_exc("boom"), sent_image=False) == PROVIDER_REJECTED
    assert provider_error_code(_exc("unsupported parameter"), sent_image=False) == PROVIDER_REJECTED
    assert (
        provider_error_code(_exc("max_tokens must be less than or equal to 8192"), sent_image=False)
        == PROVIDER_REJECTED
    )
    tpm = _exc(
        "Request too large for model llama on tokens per minute (TPM): Limit 6000, Requested 7000",
        status=413,
    )
    assert not looks_like_context_overflow(tpm)
    assert provider_error_code(tpm, sent_image=False) == PROVIDER_UNEXPECTED


def test_overflow_wins_over_the_vision_guess_but_a_pure_vision_reject_stays() -> None:
    vision = _exc("this model does not support image_url input")
    assert provider_error_code(vision, sent_image=True) == PROVIDER_NO_VISION
    both = _exc(OPENAI + " (image_url parts count too)")
    assert provider_error_code(both, sent_image=True) == PROVIDER_TOO_LONG


def test_reader_facing_line_names_the_model_and_never_a_key() -> None:
    exc = _exc("context length exceeded; key sk-secret-do-not-leak-abcdefgh was used")
    line = provider_error_message(exc, "zh", "qwen-3.8-27b")
    assert "qwen-3.8-27b" in line
    assert "上下文" in line
    assert "sk-secret-do-not-leak" not in line
    assert "[key]" in line
    en = provider_error_message(exc, "en", "qwen-3.8-27b")
    assert "context" in en.lower() and "sk-secret-do-not-leak" not in en


# ── 数字 ────────────────────────────────────────────────────────────


def test_numbers_are_read_from_each_shape() -> None:
    assert overflow_numbers(_exc(OPENAI)) == (140000, 131072)
    # Cerebras 只报「至少多少」，不报上限；「you requested 16384 output tokens」
    # 那个 16384 是输出预算，绝不能当成这一枪的大小。
    assert overflow_numbers(_exc(CEREBRAS)) == (131073, 0)
    assert overflow_numbers(_exc(GOOGLE)) == (500000, 1048576)
    assert overflow_numbers(_exc(ANTHROPIC)) == (250000, 200000)
    assert overflow_numbers(_exc(OPENAI_NEW)) == (0, 0)
    assert overflow_numbers(_exc("boom")) == (0, 0)


def test_tiny_numbers_are_noise_not_windows() -> None:
    assert overflow_numbers(_exc("maximum context length is 8 tokens, you requested 9 tokens")) == (0, 0)


# ── 退批 ────────────────────────────────────────────────────────────


def _tc(cid: str, name: str, args: dict) -> dict:
    return {
        "id": cid,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def _msgs(book: str) -> list[dict]:
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "packet"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                _tc("c1", "read_file", {"path": book, "offset": 1, "limit": 50}),
                _tc("c2", "fetch", {"url": "https://example.com/p"}),
                _tc("c3", "edit_file", {"path": book, "old_string": "a", "new_string": "b"}),
            ],
        },
        # 60 行：比退回文案长，才轮得到被退（短结果不换，见 test_short_results_…）
        {"role": "tool", "tool_call_id": "c1", "content": "".join(f"{i}\tline {i} of the handbook body\n" for i in range(1, 61))},
        {"role": "tool", "tool_call_id": "c2", "content": "网页正文。" * 100},
        {"role": "tool", "tool_call_id": "c3", "content": "已编辑 note.md（第 3 行起替换 1 处，现 9 行）"},
    ]


def test_trailing_batch_is_stubbed_with_numbers_and_ranges_but_edits_are_kept() -> None:
    msgs = _msgs("/v/note.md")
    got = stub_trailing_batch(msgs, model="qwen-3.8-27b", got=70000, limit=65536, lang="zh", nth=1, cap=3)
    assert [g["tool_call_id"] for g in got] == ["c1", "c2"]
    assert got[0]["name"] == "read_file" and got[0]["span"] == (1, 60) and got[0]["chars"] > 0
    c1 = msgs[3]["content"]
    assert c1.startswith(OVERFLOW_MARK)
    for needle in ("第 1–60 行", "qwen-3.8-27b", "70000", "65536", "offset", "limit", "1/3"):
        assert needle in c1, needle
    c2 = msgs[4]["content"]
    assert c2.startswith(OVERFLOW_MARK) and "fetch" in c2 and "整页" in c2
    assert msgs[5]["content"].startswith("已编辑"), "edit_file 的结果是写回记录，不退"
    assert msgs[2]["tool_calls"], "assistant 那条一个字不动"


def test_the_closing_shots_fake_user_is_skipped_over() -> None:
    msgs = _msgs("/v/note.md")
    msgs.append({"role": "user", "content": FORCE_ANSWER["zh"]})
    got = stub_trailing_batch(msgs, model="m", got=1, limit=0, lang="zh", nth=2, cap=3)
    assert [g["tool_call_id"] for g in got] == ["c1", "c2"]
    assert msgs[-1]["content"] == FORCE_ANSWER["zh"]
    assert "节点没说" in msgs[3]["content"]


def test_already_stubbed_or_no_tool_tail_returns_nothing_and_changes_nothing() -> None:
    msgs = _msgs("/v/note.md")
    assert stub_trailing_batch(msgs, model="m", got=1, limit=1, lang="zh", nth=1, cap=3)
    frozen = [dict(m) for m in msgs]
    assert stub_trailing_batch(msgs, model="m", got=1, limit=1, lang="zh", nth=2, cap=3) == []
    assert msgs == frozen
    head = _msgs("/v/note.md")[:2]
    assert stub_trailing_batch(head, model="m", got=1, limit=1, lang="zh", nth=1, cap=3) == []
    assert head == _msgs("/v/note.md")[:2]


def test_english_stub_and_estimate_wording() -> None:
    msgs = _msgs("/v/note.md")
    stub_trailing_batch(msgs, model="m", got=42000, limit=0, lang="en", nth=1, cap=3, estimated=True)
    c1 = msgs[3]["content"]
    assert c1.startswith(OVERFLOW_MARK_EN)
    assert "offset" in c1 and "limit" in c1 and "estimate" in c1.lower()
    assert is_overflow_stub(c1) and is_overflow_stub(OVERFLOW_MARK + "x")
    assert not is_overflow_stub("1\tline")


def test_short_results_are_never_replaced_by_a_longer_stub() -> None:
    """二审 #6：叫 shrink 就得真变小。两个字的错误结果换成 170 字的 stub 是在
    把上下文撑大，下一枪照样 400 而且白烧一次。"""
    msgs = _msgs("/v/note.md")
    msgs[3]["content"] = "错误：找不到。"
    msgs[4]["content"] = "网页正文。" * 100
    got = stub_trailing_batch(msgs, model="m", got=70000, limit=65536, lang="zh", nth=1, cap=3)
    assert [g["tool_call_id"] for g in got] == ["c2"]
    assert msgs[3]["content"] == "错误：找不到。"
    assert len(msgs[4]["content"]) < len("网页正文。" * 100)
    only_short = _msgs("/v/note.md")
    only_short[3]["content"] = "错误：找不到。"
    only_short[4]["content"] = "错误：取网页超时。"
    assert stub_trailing_batch(only_short, model="m", got=1, limit=1, lang="zh", nth=1, cap=3) == []
