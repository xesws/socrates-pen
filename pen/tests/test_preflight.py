"""配置体检：**真往节点打一枪**，在读者发对话之前就说清能不能用。

病根（v0.22.2 读者报告）：`/v1/health` 回答的是「槽里有没有钥匙」，而读者
撞到的三种错——钥匙是废的、model 在这个节点上不存在、节点没有视觉——
它一概显示正常。于是设置页写着「已保存」，每一轮却是红字。

这份闸盯两件事：
  1. 三种错各自被认成**各自的码**（混成一个 unexpected 等于没检）
  2. 全对时不误报（体检要是会喊狼，读者第二天就不看它了）
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pen import preflight, tutor
from pen.config import LLMConfig


class _Picky(BaseHTTPRequestHandler):
    """一个会挑剔的假节点。按 key / model / 请求里带了什么，分别回不同的错。

    用真 HTTP 而不是 monkeypatch openai：体检的全部价值就在于它**真的打出去
    了**，桩掉网络的话这份闸测的是一个和线上无关的幻觉。v0.23.0 起体检走的是
    主对话那一枪的形状（流式 + 工具 + 推理档），所以这个节点也得**真的会流式**
    ——回一包 application/json 的话，SDK 在 stream=True 下压根解析不了。
    """

    # 每一枪的请求体，按顺序。测「日常绿灯只打一枪」全靠它。
    log: list[dict] = []

    def log_message(self, *a: object) -> None:  # noqa: D102
        pass

    def _json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _sse(self, model: str, want_usage: bool) -> None:
        """吐 SSE 分片。**读者那一枪就是这么收的。**

        不发 Content-Length：HTTP/1.0 下由关连接定界，httpx 会一路读到 EOF。
        体检收到第一片就会断开，所以后面几片多半写不出去——那是**正确行为**，
        不是错误，吞掉就行。
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        frames = [
            {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "hi"}}]},
        ]
        if want_usage:
            frames.append(
                {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1,
                                          "total_tokens": 4}}
            )
        try:
            for f in frames:
                head = {"id": "x", "object": "chat.completion.chunk", "created": 0,
                        "model": model}
                self.wfile.write(f"data: {json.dumps({**head, **f})}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _whole(self, model: str) -> None:
        self._json(200, {"id": "x", "object": "chat.completion", "model": model,
                         "choices": [{"index": 0,
                                      "message": {"role": "assistant", "content": "hi"},
                                      "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 3, "completion_tokens": 1,
                                   "total_tokens": 4}})

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        _Picky.log.append(body)
        auth = self.headers.get("Authorization", "")
        model = body.get("model", "")
        # 只看 messages，不看整个 body：工具 schema 也在请求体里，对整包做子串
        # 匹配的话，将来某个工具的描述里出现这个词就会静默把这条判定弄反。
        has_img = "image_url" in json.dumps(body.get("messages") or [])
        stream = bool(body.get("stream"))
        # 推理档那几个字段。thinking 是 extra_body 被 SDK 摊到顶层之后的样子。
        thinky = "reasoning_effort" in body or "thinking" in body

        def bad(msg: str) -> None:
            self._json(400, {"error": {"message": msg}})

        def ok() -> None:
            if stream:
                return self._sse(model, "stream_options" in body)
            self._whole(model)

        if "good-key" not in auth:
            return self._json(401, {"error": {"message": "invalid api key"}})
        if model == "picky-about-max-tokens":
            # 只嫌 max_tokens=1 太小。真实轮次不带这个 1，所以它其实是好的。
            if body.get("max_tokens") == 1:
                return bad("max_tokens must be at least 16")
            return ok()
        if model == "grumpy-model":
            # 因为别的原因回 400，而报文里恰好有个「像素味」的词。
            # 读者那台节点就是这么把体检骗了的。
            return bad("unsupported parameter: image_url is not allowed here")
        # ── 四种「主对话那一枪的形状」本身打不出去的节点 ──────────────
        if model == "no-tools-model" and "tools" in body:
            return bad("this model does not support function calling")
        if model == "no-stream-model" and stream:
            return bad("streaming is not supported for this model")
        if model == "no-usage-model" and "stream_options" in body:
            return bad('unknown parameter: "stream_options"')
        if model == "no-thinking-model" and thinky:
            return bad('Unknown name "thinking": Cannot find field')
        # Google 那台的真实脾气：裸 reasoning_effort 照收，**只嫌嵌套的
        # thinking**（它被 SDK 摊到了请求体顶层）。用来验「选哪家真的改了
        # 发出去的那一枪」。
        if model == "no-nested-thinking-model" and "thinking" in body:
            return bad('Unknown name "thinking": Cannot find field')
        if model == "collateral-model":
            # 全形状回 400，理由和梯子上任何一级都无关。而**减掉工具之后**
            # 它回 503——那个 503 是我们自己减出来的局面，不是读者的病。
            if "tools" not in body:
                return self._json(503, {"error": {"message": "service unavailable"}})
            return bad("context length 999999 exceeds the limit of 8192")
        if model == "garbage-stream-model" and stream:
            # 头是好的、200 也发了，**烂在正文里**。SDK 只包 create() 抛的错，
            # 迭代分片时抛的它一个都不包。
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                self.wfile.write(b"data: {not json at all\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if model not in ("real-model", "no-tools-model", "no-stream-model",
                         "no-usage-model", "no-thinking-model",
                         "no-nested-thinking-model"):
            return self._json(404, {"error": {"message": f"model {model} not found"}})
        if has_img:
            return bad("this model does not support image_url")
        ok()


@pytest.fixture(scope="module")
def picky() -> str:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Picky)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def _cfg(url: str, *, key: str = "good-key", model: str = "real-model", vision: bool = False):
    return LLMConfig(url, key, model, "settings", "off", vision=vision)


def test_a_working_config_reports_nothing(picky: str) -> None:
    """全对就是空码。**体检不许喊狼**——会喊狼的检查读者第二天就不看了。"""
    assert preflight.check(_cfg(picky)).code == ""


def test_a_dead_key_is_named_a_dead_key(picky: str) -> None:
    assert preflight.check(_cfg(picky, key="sk-bad")).code == "bad-key"


def test_a_model_this_endpoint_does_not_have(picky: str) -> None:
    """读者截图里那条「意料外的错误（NotFoundError）」的真身。

    404 在 OpenAI 兼容节点上几乎只有一个意思，报成「意料外」等于没报。
    """
    v = preflight.check(_cfg(picky, model="typo-model"))
    assert v.code == "no-model"
    assert v.model == "typo-model"  # 文案要指名道姓说是哪个型号写错了


def test_an_endpoint_without_vision_is_caught_before_the_reader_pastes(picky: str) -> None:
    """**这条是这次修复的核心。**

    「节点收不收图」只有把图递过去才知道，所以体检开着视觉时必须真挂一张。
    不挂的话，读者要等到贴完图、发完一轮，才知道这个节点根本没有视觉。
    """
    assert preflight.check(_cfg(picky, vision=True)).code == "no-vision"


def test_vision_off_does_not_send_a_picture(picky: str) -> None:
    """反向闸：关着视觉时体检不许挂图——挂了就会在没有视觉的节点上误报。"""
    assert preflight.check(_cfg(picky, vision=False)).code == ""
    assert not any("image_url" in str(p) for p in preflight.probe_messages(False))


def test_no_config_is_not_a_network_error() -> None:
    """压根没配 ≠ 连不上。两者的补救动作完全不同，混成一句读者会去查网络。"""
    assert preflight.check(None).code == preflight.NO_CONFIG


def test_the_probe_is_one_token() -> None:
    """体检要便宜到可以在每次改配置时都跑。"""
    assert len(str(preflight.probe_messages(False))) < 60


def test_a_400_that_merely_mentions_pictures_is_not_a_vision_reject(picky: str) -> None:
    """**v0.22.3 修的那个 bug。**

    读者把「图像理解」关了，体检因此没挂图。节点为别的原因回了 400，报文里
    恰好有个匹配词，于是状态栏一口咬定「节点拒收了图片，把图像理解关掉」——
    而他已经关了。没发图就不可能是拒图，这是按构造排除，不是猜。
    """
    v = preflight.check(_cfg(picky, model="grumpy-model", vision=False))
    assert v.code != "no-vision"
    assert v.code == "rejected"
    # 分不出类就转述节点原话——这是读者唯一能拿到的真理由。
    assert "image_url" in v.detail


def test_the_same_400_with_a_picture_attached_still_reads_as_no_vision(picky: str) -> None:
    """反向闸：真挂了图时，那个结论仍然要下。别把修复做成「永远不说」。"""
    assert preflight.check(_cfg(picky, model="real-model", vision=True)).code == "no-vision"


def test_the_probe_sends_no_field_of_its_own(picky: str) -> None:
    """**体检不许把自己招来的 400 报成读者的配置问题。**

    v0.22.x 那会儿探针带一个 `max_tokens=1` 省钱，而推理型模型常对最小输出
    有要求，于是它招来过读者根本撞不上的 400，再把它报成读者的配置问题。
    当时的补法是「分不出类就再打一枪、这次不带它」；v0.23.0 改成**根本不带**
    ——省钱靠收到第一片就断开。那类误报按构造消失，`picky-about-max-tokens`
    这个假节点第一枪就过。
    """
    _Picky.log.clear()
    assert preflight.check(_cfg(picky, model="picky-about-max-tokens")).code == ""
    assert all("max_tokens" not in b for b in _Picky.log), _Picky.log


def test_a_config_that_is_really_broken_still_fails_after_the_retry(picky: str) -> None:
    """反向闸：别把补打那一枪做成「什么都放行」。"""
    assert preflight.check(_cfg(picky, model="grumpy-model")).code == "rejected"
    assert preflight.check(_cfg(picky, key="sk-bad")).code == "bad-key"


# ── 端点层。**上面那些全在直调 check()，所以漏过一次真实的 500。** ──
#
# `_slot_report` 要把码翻成文案，而「分不出类」那条文案带 {detail} 占位符。
# check() 当时只回 (码, 型号)，app 拿不到 detail → KeyError → 500。
# 直调 check 的闸一条都没红。**闸得走读者真正走的那条路。**


def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from pen import config as configmod
    from pen.app import app

    monkeypatch.setattr(configmod, "PEN_DIR", tmp_path)
    return TestClient(app)


def test_the_endpoint_renders_every_code_without_blowing_up(picky, tmp_path, monkeypatch):
    """每个码都要能翻成一句话。**漏一个占位符就是一个 500。**"""
    c = _client(tmp_path, monkeypatch)
    for name, over in [
        ("钥匙废了", {"api_key": "sk-bad", "model": "real-model"}),
        ("型号不存在", {"api_key": "good-key", "model": "typo-model"}),
        ("没视觉", {"api_key": "good-key", "model": "real-model", "vision": True}),
        ("分不出类", {"api_key": "good-key", "model": "grumpy-model"}),
        ("全对", {"api_key": "good-key", "model": "real-model"}),
    ]:
        r = c.post("/v1/llm/preflight", json={"base_url": picky, **over})
        assert r.status_code == 200, f"{name} → {r.status_code} {r.text[:200]}"
        slot = r.json()["base"]
        assert isinstance(slot["message"], str)
        assert "{" not in slot["message"], f"{name} 的占位符没填上：{slot['message']}"


def test_the_endpoint_never_tells_a_reader_with_vision_off_to_turn_it_off(
    picky, tmp_path, monkeypatch
):
    """读者报的那一幕，走完整条路再验一遍。"""
    c = _client(tmp_path, monkeypatch)
    slot = c.post(
        "/v1/llm/preflight",
        json={"base_url": picky, "api_key": "good-key", "model": "grumpy-model", "vision": False},
    ).json()["base"]
    assert slot["code"] == "rejected"
    assert "图像理解" not in slot["message"]
    assert "image_url" in slot["message"]  # 节点的原话在里面


# ── v0.23.0：按主对话那一枪的真实形状打 ────────────────────────────
#
# 在这之前探针不带工具、不流式、不带推理档。也就是说：一个推理方言不认、
# 或者压根不支持工具调用的节点，状态栏给的是**绿灯**，读者要等对话里撞红字
# 才知道。v0.22.5 那个 Google 400 就是这么漏过去的。


def test_the_probe_wears_the_real_shape(picky: str) -> None:
    """探针那一枪必须和主对话逐字同源：流式、带 usage、带全部工具。"""
    _Picky.log.clear()
    assert preflight.check(_cfg(picky)).code == ""
    assert len(_Picky.log) == 1, f"日常绿灯该只打一枪，实际 {len(_Picky.log)}"
    sent = _Picky.log[0]
    assert sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True}
    assert {t["function"]["name"] for t in sent["tools"]} == {"read_file", "edit_file", "fetch"}


def test_the_shape_comes_from_the_main_shot_not_a_copy(picky: str) -> None:
    """**同源闸。** 主对话那一枪长出新字段时，探针要自动跟着长。

    照抄一份就是第二个定义点，而两个闸不同源在这个仓里已经踩过三次。
    """
    from pen.tutor import llm_create_kwargs

    _Picky.log.clear()
    cfg = _cfg(picky)
    preflight.check(cfg)
    want = llm_create_kwargs(
        cfg, messages=preflight.probe_messages(False), tools=_tool_schemas()
    )
    assert _Picky.log[0]["stream_options"] == want["stream_options"]
    assert len(_Picky.log[0]["tools"]) == len(want["tools"])


def _tool_schemas():
    from pen.agent.registry import schemas

    return schemas()


@pytest.mark.parametrize(
    "model,want",
    [
        ("no-tools-model", "no-tools"),
        ("no-usage-model", "no-usage"),
        ("no-stream-model", "no-stream"),
    ],
)
def test_the_ladder_names_the_one_thing_that_broke_it(
    picky: str, model: str, want: str
) -> None:
    """四样东西打不出去是同一个 400，而读者的补救动作完全不同。"""
    assert preflight.check(_cfg(picky, model=model)).code == want


def test_a_node_that_rejects_our_thinking_dialect_is_named(picky: str) -> None:
    """**这条红了就是 v0.22.5 那个 Google 400 又回来了，而且状态栏还给绿灯。**

    推理档必须是 off 以外的档：generic 厂商在 off 那一格本来就不发推理字段，
    没发的东西减不掉。
    """
    cfg = LLMConfig(picky, "good-key", "no-thinking-model", "settings", "high")
    v = preflight.check(cfg)
    assert v.code == "no-thinking"
    # 报的是第①枪的原话，不是后面几枪我们自己造出来的局面。
    assert "thinking" in v.detail


def test_the_ladder_skips_rungs_that_remove_nothing(picky: str) -> None:
    """推理档是 off 时 wire 本来就是空的。

    白打一枪不说，还会把「我们压根没发推理字段」报成 no-thinking。
    """
    _Picky.log.clear()
    # generic 厂商 + off ⇒ 不发任何推理字段；这个节点只嫌工具。
    assert preflight.check(_cfg(picky, model="no-tools-model")).code == "no-tools"
    assert all("reasoning_effort" not in b for b in _Picky.log)
    # ① 全形状 → ② 减推理档（跳过）→ ③ 减工具 = 两枪，不是三枪。
    assert len(_Picky.log) == 2, _Picky.log


def test_a_dead_key_never_climbs_the_ladder(picky: str) -> None:
    """401 / 404 跟形状无关，减了也是白减。**一枪定案。**"""
    for over in ({"key": "sk-bad"}, {"model": "typo-model"}):
        _Picky.log.clear()
        assert preflight.check(_cfg(picky, **over)).code != ""
        assert len(_Picky.log) == 1, (over, _Picky.log)


def test_a_rung_that_breaks_something_else_does_not_steal_the_verdict(
    picky: str,
) -> None:
    """**梯子是诊断工具，不是新病人。**

    减配是我们为了定位自己造出来的局面。第③枪回 503，那个 503 属于「工具减
    掉之后的这台节点」，不属于读者那一枪——把它报出去，读者会去查一个他根本
    没撞上的故障。所以后面几枪只有两种用处：成了 → 指名道姓；换了个错 →
    收手，报回第①枪那条拒绝和它的原话。
    """
    v = preflight.check(_cfg(picky, model="collateral-model"))
    assert v.code == tutor.PROVIDER_REJECTED
    assert "context length" in v.detail  # 第①枪的原话
    assert "unavailable" not in v.detail  # 不是第③枪那个我们减出来的 503


def test_a_stream_that_dies_mid_flight_is_a_verdict_not_a_500(picky: str) -> None:
    """**SDK 只包 `create()` 抛的错**，迭代分片时抛的一个都不包。

    这台节点 200 和 SSE 头都发了，烂在正文里——解析第一片时抛
    `json.JSONDecodeError`。漏在网外的话它会一路穿过 check()、穿过
    `_slot_report`，变成 `/v1/llm/preflight` 的一个 500：读者点「测试连接」
    等到的不是判词，是转圈。
    """
    v = preflight.check(_cfg(picky, model="garbage-stream-model"))
    assert v.code != ""  # 最要紧的是**它返回了**，而不是抛出去
    assert v.model == "garbage-stream-model"


def test_the_endpoint_renders_the_new_codes_too(picky, tmp_path, monkeypatch):
    """新码也要能翻成一句话。**漏一个占位符就是一个 500。**"""
    c = _client(tmp_path, monkeypatch)
    for name, over in [
        ("没工具", {"model": "no-tools-model"}),
        ("不流式", {"model": "no-stream-model"}),
        ("不认 usage", {"model": "no-usage-model"}),
        ("不认推理档", {"model": "no-thinking-model", "thinking": "high"}),
    ]:
        r = c.post(
            "/v1/llm/preflight",
            json={"base_url": picky, "api_key": "good-key", **over},
        )
        assert r.status_code == 200, f"{name} → {r.status_code} {r.text[:200]}"
        slot = r.json()["base"]
        assert slot["ok"] is False
        assert "{" not in slot["message"], f"{name} 的占位符没填上：{slot['message']}"


# ── 设置页选的那一家，真的改了发出去的那一枪吗 ──────────────────────
#
# 上面所有断言都在直调 check() 或者手搓 LLMConfig，而读者走的是
# 设置页 → llmPayload → 请求体 → LlmOverrideBody → merge_llm → LLMConfig
# → llm_create_kwargs → thinking_wire 这一长串。中间**任何一环忘了透传
# provider**，上面那些闸一条都不会红。「闸得走读者真正走的那条路」。


@pytest.mark.parametrize(
    "provider,want",
    [
        # 通用写法只发裸 reasoning_effort → 这台节点收。**这就是这一版的主要修复。**
        ("generic", ""),
        # DeepSeek 的方言带嵌套 thinking → 这台节点拒 → 梯子指名道姓。
        ("deepseek", "no-thinking"),
    ],
)
def test_the_chosen_provider_reaches_the_wire(
    picky, tmp_path, monkeypatch, provider: str, want: str
):
    """**这条红了，说明厂商下拉是个摆设。**"""
    c = _client(tmp_path, monkeypatch)
    slot = c.post(
        "/v1/llm/preflight",
        json={
            "base_url": picky,
            "api_key": "good-key",
            "model": "no-nested-thinking-model",
            "thinking": "high",
            "provider": provider,
        },
    ).json()["base"]
    assert slot["code"] == want, slot


def test_an_unknown_model_no_longer_gets_deepseeks_private_field(
    picky, tmp_path, monkeypatch
):
    """v0.22.5 那个 Google 400 的回归闸，走整条路再验一遍。

    在这之前，认不出的型号一律落到 DeepSeek 那一支，于是带着一个顶层
    `thinking` 出门——Google 的兼容层当场 `INVALID_ARGUMENT`。
    """
    c = _client(tmp_path, monkeypatch)
    slot = c.post(
        "/v1/llm/preflight",
        json={
            "base_url": picky,
            "api_key": "good-key",
            "model": "no-nested-thinking-model",
            "thinking": "high",
        },
    ).json()["base"]
    assert slot["ok"] is True, slot
