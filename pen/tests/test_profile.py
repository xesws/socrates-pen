"""学习画像：规则分、BKT、缓存、编码器的校验。

fixture 是那场 78 轮对话的**编码表**——只有轮号、轴、类型、导师判定，
没有一个字的原话。它复现调研报告里的排序：磁带 1 / CI 1 / Python 1 /
HTTP 2 / 流式 3 / SDK 7 / agent 8 / 术语 未评。规则一动这条就红。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from pen import config, profile, trajectory
from pen.profile import Axis, Cache, Coding, Turn

# 轮号 轴 类型 [ok|x]。ok/x：VERIFY 是确认/纠正，REJECT 是读者对/错。
FIXTURE_78 = """
1 HTTP ASK | 2 HTTP ASK | 3 META | 4 HTTP ASK | 5 PY ASK | 6 PY DEMAND | 7 STREAM DEMAND | 8 PY ASK | 9 META
10 SDK ASK | 11 TERM DEMAND | 12 META | 13 META | 14 TAPE GAP | 15 META | 16 TAPE DEMAND | 17 META
18 SDK REJECT ok | 19 SDK REJECT ok | 20 SDK VERIFY ok | 21 SDK DEMAND | 22 SDK DEMAND | 23 META
24 SDK ASK | 25 SDK ASK | 26 STREAM DEMAND | 27 STREAM VERIFY x | 28 STREAM DECLARE | 29 META
30 TAPE ASK | 31 TAPE DECLARE | 32 META | 33 STREAM DEMAND | 34 SDK REJECT ok | 35 SDK VERIFY ok
36 STREAM ASK | 37 STREAM VERIFY ok | 38 STREAM DECLARE | 39 META | 40 TAPE ASK | 41 TAPE ASK | 42 TAPE ASK
43 META | 44 TAPE REJECT ok | 45 TAPE REJECT ok | 46 TAPE ASK | 47 CI REJECT x | 48 TAPE ASK
49 TAPE DECLARE | 50 TAPE DECLARE | 51 META | 52 META | 53 CI GAP | 54 META
55 AGENT VERIFY ok | 56 AGENT VERIFY ok | 57 AGENT VERIFY ok | 58 TAPE VERIFY ok | 59 TAPE ASK | 60 TAPE GAP
61 CI GAP | 62 CI GAP | 63 CI GAP | 64 META | 65 HTTP VERIFY x | 66 SDK VERIFY ok | 67 STREAM VERIFY x
68 HTTP ASK | 69 META | 70 HTTP GAP | 71 META | 72 PY GAP | 73 META | 74 PY VERIFY x | 75 PY GAP
76 META | 77 PY VERIFY x | 78 META
"""

EXPECTED = {"TAPE": 1, "CI": 1, "PY": 1, "HTTP": 2, "STREAM": 3, "SDK": 7, "AGENT": 8, "TERM": None}

_BASE = datetime(2026, 9, 2, 19, 47, tzinfo=timezone(timedelta(hours=-7)))


def _turn(idx: int, *, legacy: bool = False, chat: bool = True, text: str = "问", chip: str = "free") -> Turn:
    when = (_BASE + timedelta(minutes=idx * 2)).isoformat(timespec="seconds")
    return Turn(
        key=f"s|{when}", idx=idx, when=when, legacy=legacy, is_chat=chat, ok=True,
        chip=chip, picked="", reader_text=text, tutor_text="答", anchor={},
    )


def _coding(typ: str, axis: str | None, flag: str = "", *, legacy: bool = False) -> Coding:
    verify = None
    rr = None
    if typ == "VERIFY":
        verify = {"ok": "confirmed", "x": "corrected"}.get(flag, "unclear")
    if typ == "REJECT":
        rr = {"ok": True, "x": False}.get(flag)
    return Coding(type=typ, axis=axis, verify=verify, reject_right=rr, legacy=legacy)


def parse_fixture(text: str) -> dict[str, list[profile.Pair]]:
    by_axis: dict[str, list[profile.Pair]] = {}
    for cell in text.replace("\n", "|").split("|"):
        cell = cell.strip()
        if not cell:
            continue
        parts = cell.split()
        if parts[1] == "META":
            continue
        idx, axis, typ = int(parts[0]), parts[1], parts[2]
        flag = parts[3] if len(parts) > 3 else ""
        by_axis.setdefault(axis, []).append((_turn(idx), _coding(typ, axis, flag)))
    return by_axis


# ── 规则分 ────────────────────────────────────────────────────────────


def test_rule_score_fixture_78_turns_reproduces_research_ordering() -> None:
    got = {axis: profile.rule_score(seq)[0] for axis, seq in parse_fixture(FIXTURE_78).items()}
    assert got == EXPECTED


def test_fixture_details_tape_sdk_stream() -> None:
    fx = parse_fixture(FIXTURE_78)
    _score, _why, arcs, lapses = profile.rule_score(fx["TAPE"])
    assert [(lp.declare_idx, lp.reopen_idx) for lp in lapses] == [(31, 40), (49, 59)]
    assert sum(a.penalty for a in arcs) == 5
    _s, _w, sdk_arcs, _l = profile.rule_score(fx["SDK"])
    assert len(sdk_arcs) == 1
    a = sdk_arcs[0]
    assert (a.start_idx, a.end_idx, a.turns, a.asking, a.closed, a.penalty) == (18, 25, 7, 4, False, 4)
    _s, why, _a, stream_lapses = profile.rule_score(fx["STREAM"])
    assert [(lp.declare_idx, lp.reopen_idx) for lp in stream_lapses] == [(38, 67)]
    assert any("第37轮" in w for w in why), "被取消的那次 lapse 要在 why 里说明"


def test_arc_penalty_is_ceil_thirds_plus_one_when_open() -> None:
    def arc_of(n: int, closed: bool) -> int:
        seq = [(_turn(i), _coding("ASK", "A")) for i in range(1, n + 1)]
        if closed:
            seq[-1] = (_turn(n), _coding("DECLARE", "A"))
        arcs = profile.find_arcs(seq)
        return arcs[0].penalty if arcs else 0

    assert [arc_of(n, False) for n in (3, 4, 6, 7, 9, 12)] == [2, 3, 3, 4, 4, 4]
    assert [arc_of(n, True) for n in (3, 4, 7)] == [1, 2, 3]


def test_arc_breaks_on_gap_of_three_and_needs_two_asking() -> None:
    seq = [(_turn(1), _coding("ASK", "A")), (_turn(2), _coding("ASK", "A")), (_turn(5), _coding("ASK", "A"))]
    assert profile.find_arcs(seq) == []
    seq = [(_turn(1), _coding("ASK", "A")), (_turn(2), _coding("REJECT", "A", "ok")), (_turn(3), _coding("VERIFY", "A", "ok"))]
    assert profile.find_arcs(seq) == [], "只有一轮在问的段不是追问弧线"


def test_lapse_dedupes_by_reopen_and_confirmation_cancels() -> None:
    seq = [
        (_turn(1), _coding("DECLARE", "A")),
        (_turn(2), _coding("DECLARE", "A")),
        (_turn(5), _coding("ASK", "A")),
    ]
    lapses, holds = profile.find_lapses(seq)
    assert [(lp.declare_idx, lp.reopen_idx) for lp in lapses] == [(1, 5)]
    assert holds == []
    seq = [  # 轮号间隔 3，不连成弧线，只看 lapse 本身
        (_turn(1), _coding("DECLARE", "A")),
        (_turn(4), _coding("DEMAND", "A")),
        (_turn(7), _coding("ASK", "A")),
        (_turn(10), _coding("VERIFY", "A", "ok")),
        (_turn(13), _coding("DECLARE", "A")),
        (_turn(16), _coding("VERIFY", "A", "x")),
    ]
    lapses, holds = profile.find_lapses(seq)
    assert [(lp.declare_idx, lp.reopen_idx, lp.cancelled_by) for lp in lapses] == [(1, 4, 10), (13, 16, None)]
    score, why, arcs, counted = profile.rule_score(seq)
    assert arcs == [] and len(counted) == 1
    assert score == 6 - 2 + 1  # 一次 lapse、一次确认；第 16 轮的纠正是回点，不再扣


def test_declare_that_holds_and_adopted_and_caps() -> None:
    seq = [(_turn(1), _coding("VERIFY", "A", "ok")), (_turn(2), _coding("GAP", "A"))]
    seq += [(_turn(i), _coding("DECLARE", "A")) for i in range(3, 7)]
    score, why, _a, _l = profile.rule_score(seq)
    assert score == 6 + 1 - 1 + 2, "四次站住的 DECLARE 封顶 +2"
    assert any("×4" in w for w in why)
    seq[0] = (_turn(1), Coding(type="VERIFY", axis="A", verify="confirmed", adopted=True))
    assert profile.rule_score(seq)[0] == 10
    rejects = [(_turn(i), _coding("REJECT", "A", "ok")) for i in range(1, 7)]
    assert profile.rule_score(rejects)[0] == 6 + 2


def test_score_is_null_below_three_and_capped_below_six() -> None:
    two = [(_turn(1), _coding("VERIFY", "A", "ok")), (_turn(2), _coding("VERIFY", "A", "ok"))]
    score, why, _a, _l = profile.rule_score(two)
    assert score is None and "2" in why[0]
    three = two + [(_turn(3), _coding("VERIFY", "A", "ok"))]
    score, why, _a, _l = profile.rule_score(three)
    assert score == 8 and any("封顶" in w for w in why)


def test_why_is_localised_and_never_carries_reader_text() -> None:
    seq = [(_turn(i, text="秘密原话"), _coding("GAP", "A")) for i in range(1, 4)]
    zh = profile.rule_score(seq, "zh")[1]
    en = profile.rule_score(seq, "en")[1]
    assert any("自陈盲区" in w for w in zh) and any("blind spot" in w for w in en)
    assert all("秘密" not in w for w in zh + en)


# ── BKT ───────────────────────────────────────────────────────────────


def test_bkt_matches_hand_values() -> None:
    assert profile.bkt([]) is None
    assert profile.bkt([1]) == 0.71
    assert profile.bkt([0]) == 0.193
    assert profile.bkt([1, 0]) == 0.349
    assert profile.bkt([0, 1]) == 0.591


def test_bkt_observations_only_take_judged_facts() -> None:
    seq = [
        (_turn(1), _coding("ASK", "A")),
        (_turn(2), _coding("VERIFY", "A", "ok")),
        (_turn(3), _coding("VERIFY", "A", "x")),
        (_turn(4), _coding("VERIFY", "A", "")),
        (_turn(5), _coding("REJECT", "A", "ok")),
        (_turn(6), _coding("REJECT", "A", "x")),
        (_turn(7), _coding("REJECT", "A", "")),
        (_turn(8), _coding("GAP", "A")),
        (_turn(9), _coding("DECLARE", "A")),
        (_turn(10), _coding("DEMAND", "A")),
    ]
    assert profile.bkt_observations(seq) == [1, 0, 1, 0, 0]


def test_legacy_codings_count_in_n_but_not_in_score_or_bkt() -> None:
    axis = Axis(id="ax01", name="A", definition="", first_seen="2026-09-01T00:00:00+00:00")
    pairs = [(_turn(i, legacy=True), _coding("GAP", "ax01", legacy=True)) for i in range(1, 6)]
    pairs += [(_turn(i), _coding("VERIFY", "ax01", "ok")) for i in range(6, 9)]
    rep = profile.axis_report(axis, pairs, "zh")
    assert (rep["n"], rep["n_legacy"], rep["n_obs"]) == (8, 5, 3)
    assert rep["score"] == 8 and rep["mastery"] > 0.9
    assert all(e["idx"] >= 6 for e in rep["evidence"])


# ── 行归一与确定性编码 ───────────────────────────────────────────────


def _row(ts: str, **over: object) -> dict:
    base = {
        "session_id": "s1", "ts": ts, "chip": "free", "user_text": "为什么？",
        "anchor": {"level": "Level 1", "path": "/v/a.md", "start_line": 3}, "ok": True,
        "assistant_text": "因为。", "phase": "chat",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def test_is_legacy_keys_off_assistant_text() -> None:
    assert profile.is_legacy({"assistant_preview": "x"})
    assert not profile.is_legacy({"assistant_text": ""})


def test_load_turns_orders_utc_and_local_rows_together_and_numbers_every_row() -> None:
    trajectory.append_turn("hb-p", _row("2026-09-02T13:00:00+00:00", assistant_text=None, assistant_preview="旧"))
    trajectory.append_turn("hb-p", _row("2026-09-02T22:10:00+08:00"))  # 14:10 UTC，晚于上一条
    trajectory.append_turn("hb-p", _row("2026-09-02T06:30:00-07:00", phase="approve"))  # 13:30 UTC，夹中间
    turns = profile.load_turns("hb-p")
    assert [t.idx for t in turns] == [1, 2, 3]
    assert [t.legacy for t in turns] == [True, False, False]
    assert [t.is_chat for t in turns] == [True, False, True]
    assert turns[0].tutor_text == "旧" and turns[2].tutor_text == "因为。"


def test_deterministic_coding_meta_cases_and_chip_only_goes_to_model() -> None:
    assert profile.deterministic_coding(_turn(1, chat=False)).auto == "approve"
    assert profile.deterministic_coding(_turn(1, chip="writeback")).auto == "writeback"
    assert profile.deterministic_coding(_turn(1, text="", chip="free")).auto == "empty"
    assert profile.deterministic_coding(_turn(1, text="", chip="explain_zero")) is None
    assert profile.deterministic_coding(_turn(1)) is None


# ── 缓存 ──────────────────────────────────────────────────────────────


def test_cache_roundtrip_and_tolerance(tmp_path: Path) -> None:
    c = Cache(handbook_id="hb-c")
    c.axes.append(Axis("ax01", "HTTP", "传输层", ("http",), "2026-09-02T19:47:00-07:00"))
    c.codings["s|t"] = Coding(type="GAP", axis="ax01", gap_quote="没概念", evidence_quote="没概念")
    c.attempts["s|u"] = 2
    c.spend = {"calls": 1, "in_tokens": 5, "out_tokens": 1, "cached_tokens": 0, "reasoning_tokens": 0}
    dest = profile.save_cache(c)
    assert dest.parent == config.PEN_DIR / "profiles"
    back = profile.load_cache("hb-c")
    assert back.axes == c.axes and back.codings == c.codings and back.attempts == {"s|u": 2}
    assert back.spend["in_tokens"] == 5 and back.updated_at
    dest.write_text("{not json", encoding="utf-8")
    assert profile.load_cache("hb-c").codings == {}
    dest.write_text(json.dumps({"schema": 99, "codings": {"k": {"type": "ASK"}}}), encoding="utf-8")
    assert profile.load_cache("hb-c").codings == {}
    assert profile.load_cache("no/such").codings == {}


def test_retention_purge_never_touches_profiles(tmp_path: Path) -> None:
    from pen import retention

    dest = profile.save_cache(Cache(handbook_id="hb-r"))
    retention.purge_expired_sessions()
    assert dest.is_file()


# ── 编码器：解析、引文校验、轴归并、分批 ────────────────────────────


def test_parse_coder_json_tolerates_fence_and_preface() -> None:
    body = {"codings": [{"i": 1, "type": "ASK"}]}
    assert profile.parse_coder_json("好的：\n```json\n" + json.dumps(body) + "\n```") == body["codings"]
    assert profile.parse_coder_json("前言 " + json.dumps(body) + " 后记") == body["codings"]
    assert profile.parse_coder_json("nope") is None
    assert profile.parse_coder_json('{"codings": "x"}') is None
    assert profile.parse_coder_json("") is None


def test_apply_batch_enforces_substring_quotes_and_closed_vocabulary() -> None:
    cache = Cache(handbook_id="hb-a")
    batch = [
        _turn(1, text="我对这个没概念，录下来是啥"),
        _turn(2, text="我的理解是它像缓存，对吧"),
        _turn(3, text="给我英文术语"),
        _turn(4, text="", chip="explain_zero"),
    ]
    items = [
        {"i": 1, "type": "GAP", "axis": {"name": "磁带", "definition": "录制回放"}, "gap_quote": "没概念", "evidence_quote": "我对这个 没概念"},
        {"i": 2, "type": "VERIFY", "axis": "磁带", "verify": "wrong-word", "evidence_quote": "编造的引文"},
        {"i": 3, "type": "BOGUS", "axis": "ax01", "evidence_quote": "给我英文术语"},
        {"i": 4, "type": "ASK", "axis": "ax01", "evidence_quote": ""},
    ]
    coded = profile.apply_batch(batch, items, cache, "m")
    assert coded == 2
    assert cache.codings[batch[0].key].type == "GAP" and cache.codings[batch[0].key].axis == "ax01"
    assert batch[1].key not in cache.codings and cache.attempts[batch[1].key] == 1, "引文不是原话子串，整条作废"
    assert batch[2].key not in cache.codings and cache.attempts[batch[2].key] == 1
    chip = cache.codings[batch[3].key]
    from pen.session import chip_label

    assert (chip.type, chip.auto, chip.evidence_quote) == ("ASK", "chip", chip_label("explain_zero"))
    assert [a.name for a in cache.axes] == ["磁带"]


def test_gap_without_a_real_gap_quote_downgrades_to_ask_and_verify_defaults_unclear() -> None:
    cache = Cache(handbook_id="hb-g")
    batch = [_turn(1, text="这一段我没看懂"), _turn(2, text="它是不是一个 list？")]
    items = [
        {"i": 1, "type": "GAP", "axis": {"name": "A", "definition": ""}, "gap_quote": "基础为零", "evidence_quote": "没看懂"},
        {"i": 2, "type": "VERIFY", "axis": "A", "evidence_quote": "是不是一个 list"},
    ]
    assert profile.apply_batch(batch, items, cache, "m") == 2
    assert cache.codings[batch[0].key].type == "ASK" and cache.codings[batch[0].key].gap_quote == ""
    assert cache.codings[batch[1].key].verify == "unclear"


def test_resolve_axis_id_then_name_then_alias_then_new_and_cap() -> None:
    axes: list[Axis] = []
    a1 = profile.resolve_axis({"name": "HTTP / 传输层", "definition": "x"}, axes, "t1")
    assert a1 == "ax01" and axes[0].first_seen == "t1"
    assert profile.resolve_axis("ax01", axes, "t2") == "ax01"
    assert profile.resolve_axis("http传输层", axes, "t2") == "ax01"
    assert profile.resolve_axis({"name": "HTTP传输层", "definition": "y"}, axes, "t3") == "ax01"
    assert axes[0].first_seen == "t1" and "HTTP传输层" in axes[0].aliases
    assert profile.resolve_axis({"name": "Python 生成器"}, axes, "t4") == "ax02"
    assert profile.resolve_axis(None, axes, "t5") is None
    for i in range(profile.MAX_AXES):
        profile.resolve_axis({"name": f"轴{i}"}, axes, "t")
    assert len(axes) == profile.MAX_AXES
    assert profile.resolve_axis({"name": "再来一条"}, axes, "t") is None


def test_long_axis_name_is_truncated_once_and_still_recognised() -> None:
    """真跑抓到的：名字超过 NAME_MAX 被截，下一批模型再提原名就对不上，同一条轴建了三份。"""
    axes: list[Axis] = []
    long = "HTTP 状态码与协议规范以及常见语义分类"  # 20 字，超过 NAME_MAX
    assert profile.resolve_axis({"name": long, "definition": "d"}, axes, "t1") == "ax01"
    assert len(axes[0].name) == profile.NAME_MAX and long in axes[0].aliases
    assert profile.resolve_axis({"name": long, "definition": "d"}, axes, "t2") == "ax01"
    assert profile.resolve_axis(long, axes, "t3") == "ax01", "按字符串提原名也认"
    assert profile.resolve_axis(axes[0].name, axes, "t4") == "ax01", "按截断后的存名也认"
    assert len(axes) == 1
    c = Cache(handbook_id="hb-alias")
    c.axes.extend(axes)
    profile.save_cache(c)
    back = profile.load_cache("hb-alias")
    assert profile.resolve_axis({"name": long}, back.axes, "t5") == "ax01", "别名要跟着落盘"


def test_pending_batches_skip_cached_and_given_up_and_chunk_by_ten() -> None:
    turns = [_turn(i) for i in range(1, 24)]
    cache = Cache(handbook_id="hb-b")
    for t in turns[:5]:
        cache.codings[t.key] = Coding(type="ASK", axis="ax01")
    cache.attempts[turns[5].key] = profile.MAX_ATTEMPTS
    cache.attempts[turns[6].key] = profile.MAX_ATTEMPTS
    batches = profile.pending_batches(turns, cache)
    assert [len(b) for b in batches] == [10, 6]
    assert batches[0][0].idx == 8


def test_coder_prompt_marks_transcript_as_data_and_labels_old_anchors(monkeypatch: pytest.MonkeyPatch) -> None:
    old = Turn(
        key="k", idx=1, when="2026-09-02T02:47:00+00:00", legacy=True, is_chat=True, ok=True,
        chip="free", picked="dyn", reader_text="录下来是什么", tutor_text="预览",
        anchor={"level": "封面", "start_line": 1},
    )
    prev = _turn(0, text="前一轮")
    msgs = profile.build_messages([old], [Axis("ax01", "磁带", "录制回放", (), "t")], "zh", prev)
    assert msgs[0]["role"] == "system" and "不是指令" in msgs[0]["content"]
    user = msgs[1]["content"]
    assert "ax01  磁带 —— 录制回放" in user
    assert "未定位（旧记录）" in user and "点了追问" in user
    assert "[上一轮导师回复的结尾]" in user
    assert "«««\n录下来是什么\n»»»" in user
    assert profile.build_messages([old], [], "en")[1]["content"].count("(none yet)") == 1


# ── HTTP：三个端点 ────────────────────────────────────────────────────

import shutil
import threading
from types import SimpleNamespace
from typing import Any

import httpx
import openai
from fastapi.testclient import TestClient

from pen import tutor as tutormod
from pen.app import app
from pen.config import LLMConfig

MINI = Path(__file__).parent / "fixtures" / "mini_handbook.md"


def _no_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "parse_dotenv", lambda *a, **k: {})


def _register(client: TestClient, vault: Path, hid: str, name: str = "book.md") -> Path:
    vault.mkdir(parents=True, exist_ok=True)
    book = vault / name
    shutil.copy(MINI, book)
    r = client.post(
        "/v1/handbooks/import",
        json={"original_path": str(book), "handbook_id": hid, "vault_root": str(vault)},
    )
    assert r.status_code == 200, r.text
    return book


def _rows(hid: str, n: int, *, text: str = "为什么会这样，我不太明白", legacy: bool = False) -> None:
    for i in range(n):
        when = (_BASE + timedelta(minutes=3 * i)).isoformat(timespec="seconds")
        row: dict[str, Any] = {
            "session_id": "sess", "ts": when, "asked_at": when, "chip": "free",
            "user_text": text, "anchor": {"level": "Level 1", "path": "/v/book.md", "start_line": 5, "located": True},
            "ok": True, "assistant_text": "因为它就是这样。", "phase": "chat",
        }
        if legacy:
            row.pop("assistant_text"); row.pop("phase"); row.pop("asked_at")
            row["assistant_preview"] = "旧"
        trajectory.append_turn(hid, row)


def _ask_all(n: int, axis: dict[str, str] | None = None) -> str:
    ax = axis or {"name": "缓存", "definition": "为什么会这样"}
    return json.dumps({"codings": [{"i": i, "type": "ASK", "axis": ax if i == 1 else ax["name"], "evidence_quote": "为什么"} for i in range(1, n + 1)]})


def _stub_create(monkeypatch: pytest.MonkeyPatch, reply: Any) -> list[dict[str, Any]]:
    """把 profile._create 换成桩。reply 是字符串、异常或可调用；返回每次调用的记录。"""
    calls: list[dict[str, Any]] = []

    def fake(cfg: LLMConfig, messages: list[dict[str, str]], meter: Any = None, limits: Any = None) -> str:
        calls.append({"cfg": cfg, "messages": messages, "limits": limits})
        out = reply(len(calls)) if callable(reply) else reply
        if isinstance(out, BaseException):
            raise out
        if meter is not None:
            meter.add({"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})
        return out

    monkeypatch.setattr(profile, "_create", fake)
    return calls


def _auth_error() -> openai.AuthenticationError:
    req = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    return openai.AuthenticationError("bad key", response=httpx.Response(401, request=req), body=None)


def test_get_profile_without_cache_is_200_and_writes_nothing(tmp_path: Path) -> None:
    with TestClient(app) as client:
        _register(client, tmp_path / "vault", "hb-empty")
        _rows("hb-empty", 4)
        r = client.get("/v1/handbooks/hb-empty/profile")
        assert r.status_code == 200
        body = r.json()
        assert body["axes"] == [] and body["n_turns"] == 4 and body["n_uncoded"] == 4
        assert body["title"] and body["handbook_id"] == "hb-empty"
        assert not (config.PEN_DIR / "profiles").exists(), "GET 不落盘"
        assert client.get("/v1/handbooks/nope/profile").status_code == 404


def test_code_without_model_is_400_in_both_languages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_env_key(monkeypatch)
    with TestClient(app) as client:
        _register(client, tmp_path / "vault", "hb-nokey")
        zh = client.post("/v1/handbooks/hb-nokey/profile/code", json={})
        en = client.post("/v1/handbooks/hb-nokey/profile/code", json={}, headers={"Accept-Language": "en"})
        assert zh.status_code == 400 and en.status_code == 400
        assert zh.json()["detail"] != en.json()["detail"]
        assert "key" in en.json()["detail"].lower()


def test_code_uses_main_model_non_streaming_without_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    box: dict[str, Any] = {}

    class _Completions:
        def create(self, **kwargs: Any) -> Any:
            box["kwargs"] = kwargs
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=_ask_all(3)))],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12),
            )

    class _Client:
        def __init__(self, **kwargs: Any) -> None:
            box["client"] = kwargs
            self.chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setattr(openai, "OpenAI", _Client)
    with TestClient(app) as client:
        _register(client, tmp_path / "vault", "hb-wire")
        _rows("hb-wire", 3)
        r = client.post(
            "/v1/handbooks/hb-wire/profile/code",
            json={"api_key": "sk-test-1234567890", "model": "deepseek-chat", "thinking": "off",
                  "provider": "deepseek", "limits": {"probe_timeout_s": 77}},
        )
        assert r.status_code == 200, r.text
        kw = box["kwargs"]
        assert kw["stream"] is False and "tools" not in kw and kw["model"] == "deepseek-chat"
        assert kw["extra_body"] == {"thinking": {"type": "disabled"}}, "推理档走 providers 那张方言表"
        assert box["client"]["timeout"] == 77
        assert r.json()["n_coded"] == 3 and r.json()["remaining"] == 0
        assert r.json()["spend"]["in_tokens"] == 10
        assert [a["name"] for a in r.json()["axes"]] == ["缓存"]


def test_code_is_incremental_and_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_create(monkeypatch, lambda n: _ask_all(10 if n < 3 else 5))
    with TestClient(app) as client:
        _register(client, tmp_path / "vault", "hb-inc")
        _rows("hb-inc", 25)
        got = []
        for _ in range(3):
            r = client.post("/v1/handbooks/hb-inc/profile/code", json={"api_key": "sk-test-1234567890", "max_batches": 1})
            assert r.status_code == 200, r.text
            got.append((r.json()["coded"], r.json()["n_coded"], r.json()["remaining"], r.json()["uncoded_batches"]))
        assert got == [(10, 10, 15, 2), (10, 20, 5, 1), (5, 25, 0, 0)]
        r = client.post("/v1/handbooks/hb-inc/profile/code", json={"api_key": "sk-test-1234567890", "max_batches": 1})
        assert r.json()["coded"] == 0 and r.json()["remaining"] == 0
        assert len(calls) == 3, "编完了就不再调模型"
        raw = json.loads((config.PEN_DIR / "profiles" / "hb-inc.json").read_text(encoding="utf-8"))
        assert raw["schema"] == 1 and raw["spend"]["calls"] == 3 and len(raw["codings"]) == 25
        rep = client.get("/v1/handbooks/hb-inc/profile").json()
        assert rep["n_coded"] == 25 and rep["axes"][0]["n"] == 25 and rep["spend"]["calls"] == 3


def test_max_batches_is_clamped_not_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_create(monkeypatch, lambda n: _ask_all(10))
    with TestClient(app) as client:
        _register(client, tmp_path / "vault", "hb-clamp")
        _rows("hb-clamp", 30)
        r = client.post("/v1/handbooks/hb-clamp/profile/code", json={"api_key": "sk-test-1234567890", "max_batches": -5})
        assert r.status_code == 200 and len(calls) == 1
        r = client.post("/v1/handbooks/hb-clamp/profile/code", json={"api_key": "sk-test-1234567890", "max_batches": 999})
        assert r.status_code == 200 and r.json()["remaining"] == 0


def test_bad_json_three_times_gives_up_and_report_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_create(monkeypatch, "这不是 JSON")
    with TestClient(app) as client:
        _register(client, tmp_path / "vault", "hb-bad")
        _rows("hb-bad", 5)
        seen = []
        for _ in range(4):
            r = client.post("/v1/handbooks/hb-bad/profile/code", json={"api_key": "sk-test-1234567890", "max_batches": 1})
            assert r.status_code == 200
            seen.append(r.json()["remaining"])
        assert seen == [5, 5, 0, 0] and len(calls) == 3
        rep = client.get("/v1/handbooks/hb-bad/profile").json()
        assert (rep["n_uncoded"], rep["n_given_up"], rep["n_coded"]) == (0, 5, 0)
        assert rep["spend"]["calls"] == 3, "钱花了就是花了"


def test_provider_error_is_400_with_code_and_keeps_progress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_create(monkeypatch, lambda n: _ask_all(10) if n == 1 else _auth_error())
    with TestClient(app) as client:
        _register(client, tmp_path / "vault", "hb-err")
        _rows("hb-err", 15)
        r = client.post("/v1/handbooks/hb-err/profile/code", json={"api_key": "sk-test-1234567890", "max_batches": 3})
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert detail["code"] == tutormod.PROVIDER_BAD_KEY and detail["message"]
        rep = client.get("/v1/handbooks/hb-err/profile").json()
        assert rep["n_coded"] == 10 and rep["n_uncoded"] == 5, "炸之前那批已经落盘"
        assert rep["spend"]["calls"] == 1


def test_force_recodes_from_scratch_but_keeps_spend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_create(monkeypatch, lambda n: _ask_all(4, axis={"name": f"轴{n}", "definition": ""}))
    with TestClient(app) as client:
        _register(client, tmp_path / "vault", "hb-force")
        _rows("hb-force", 4)
        client.post("/v1/handbooks/hb-force/profile/code", json={"api_key": "sk-test-1234567890"})
        r = client.post("/v1/handbooks/hb-force/profile/code", json={"api_key": "sk-test-1234567890", "force": True})
        assert r.status_code == 200 and len(calls) == 2
        assert [a["name"] for a in r.json()["axes"]] == ["轴2"], "重算后旧轴不残留"
        assert r.json()["spend"]["calls"] == 2


def test_concurrent_code_calls_never_double_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    def slow(_n: int) -> str:
        time.sleep(0.2)
        return _ask_all(10)

    calls = _stub_create(monkeypatch, slow)
    with TestClient(app) as client:
        _register(client, tmp_path / "vault", "hb-conc")
        _rows("hb-conc", 10)
        results: list[dict[str, Any]] = []

        def go() -> None:
            results.append(client.post("/v1/handbooks/hb-conc/profile/code", json={"api_key": "sk-test-1234567890"}).json())

        threads = [threading.Thread(target=go) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(calls) == 1
        assert sorted(r["coded"] for r in results) == [0, 10]
        assert all(r["remaining"] == 0 for r in results)


def test_legacy_rows_are_coded_but_never_scored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def reply(_n: int) -> str:
        items = [{"i": i, "type": "VERIFY", "axis": {"name": "A", "definition": ""} if i == 1 else "A", "verify": "confirmed", "evidence_quote": "为什么"} for i in range(1, 7)]
        return json.dumps({"codings": items})

    _stub_create(monkeypatch, reply)
    with TestClient(app) as client:
        _register(client, tmp_path / "vault", "hb-old")
        _rows("hb-old", 6, legacy=True)
        r = client.post("/v1/handbooks/hb-old/profile/code", json={"api_key": "sk-test-1234567890"})
        assert r.status_code == 200 and r.json()["n_coded"] == 6
        rep = client.get("/v1/handbooks/hb-old/profile").json()
        assert rep["n_legacy"] == 6
        ax = rep["axes"][0]
        assert ax["n"] == 6 and ax["n_legacy"] == 6
        assert ax["score"] is None and ax["mastery"] is None and ax["evidence"] == []


def test_profiles_overview_filters_by_vault_and_validates_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_create(monkeypatch, lambda n: (_ for _ in ()).throw(AssertionError("书架不许调模型")))
    with TestClient(app) as client:
        va, vb = tmp_path / "va", tmp_path / "vb"
        _register(client, va, "hb-a1", "one.md")
        _register(client, va, "hb-a2", "two.md")
        _register(client, vb, "hb-b1", "one.md")
        _rows("hb-a1", 3)
        c = Cache(handbook_id="hb-a1")
        c.axes.append(Axis("ax01", "HTTP", "", (), "t"))
        for t in profile.load_turns("hb-a1"):
            c.codings[t.key] = Coding(type="GAP", axis="ax01")
        profile.save_cache(c)
        r = client.get("/v1/profiles", params={"vault_root": str(va)})
        assert r.status_code == 200, r.text
        body = r.json()
        assert sorted(b["handbook_id"] for b in body["books"]) == ["hb-a1", "hb-a2"]
        a1 = next(b for b in body["books"] if b["handbook_id"] == "hb-a1")
        # 三轮连着的 GAP：弧线未收口 −2，盲区 −3 → 1
        assert a1["n_turns"] == 3 and a1["n_axes"] == 1 and a1["weakest"][0]["score"] == 1
        assert a1["asked_most"][0]["name"] == "HTTP" and a1["asked_most"][0]["n"] == 3
        assert list(body["merged_by_title"].values()) == [["hb-a1", "hb-a2"]], "同标题的两次登记要指出来"
        assert client.get("/v1/profiles").status_code == 400
        assert client.get("/v1/profiles", params={"vault_root": "/"}).status_code == 400
        bad = client.get("/v1/profiles", params={"vault_root": str(tmp_path / "nope")})
        assert bad.status_code == 400 and "vault_root" in bad.json()["detail"]
        en = client.get("/v1/profiles", headers={"Accept-Language": "en"})
        assert "vault_root" in en.json()["detail"] and "required" in en.json()["detail"]


def test_profile_spend_stays_out_of_usage_and_handbook_is_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_create(monkeypatch, lambda n: _ask_all(3))
    with TestClient(app) as client:
        book = _register(client, tmp_path / "vault", "hb-usage")
        _rows("hb-usage", 3)
        before = (book.read_bytes(), book.stat().st_mtime_ns)
        assert client.post("/v1/handbooks/hb-usage/profile/code", json={"api_key": "sk-test-1234567890"}).status_code == 200
        assert client.get("/v1/handbooks/hb-usage/profile").status_code == 200
        assert client.get("/v1/profiles", params={"vault_root": str(tmp_path / "vault")}).status_code == 200
        usage = client.get("/v1/usage").json()
        assert usage["total"] == 0 and "profile" not in usage["spend"]
        assert (book.read_bytes(), book.stat().st_mtime_ns) == before
