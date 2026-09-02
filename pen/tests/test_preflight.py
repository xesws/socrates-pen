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

from pen import preflight
from pen.config import LLMConfig


class _Picky(BaseHTTPRequestHandler):
    """按 key / model / 有没有图，分别回 401 / 404 / 400。

    用真 HTTP 而不是 monkeypatch openai：体检的全部价值就在于它**真的打出去
    了**，桩掉网络的话这份闸测的是一个和线上无关的幻觉。
    """

    def log_message(self, *a: object) -> None:  # noqa: D102
        pass

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        auth = self.headers.get("Authorization", "")
        model = body.get("model", "")
        has_img = "image_url" in json.dumps(body)

        def send(code: int, payload: dict) -> None:
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        if "good-key" not in auth:
            return send(401, {"error": {"message": "invalid api key"}})
        if model == "picky-about-max-tokens":
            # 只嫌 max_tokens=1 太小。真实轮次不带这个 1，所以它其实是好的。
            if body.get("max_tokens") == 1:
                return send(400, {"error": {"message": "max_tokens must be at least 16"}})
            return send(200, {"id": "x", "object": "chat.completion", "model": model,
                              "choices": [{"index": 0, "message": {"role": "assistant",
                                                                   "content": "hi"},
                                           "finish_reason": "stop"}],
                              "usage": {"prompt_tokens": 3, "completion_tokens": 1,
                                        "total_tokens": 4}})
        if model == "grumpy-model":
            # 因为别的原因回 400，而报文里恰好有个「像素味」的词。
            # 读者那台节点就是这么把体检骗了的。
            return send(400, {"error": {"message": "unsupported parameter: image_url is not allowed here"}})
        if model != "real-model":
            return send(404, {"error": {"message": f"model {model} not found"}})
        if has_img:
            return send(400, {"error": {"message": "this model does not support image_url"}})
        send(
            200,
            {
                "id": "x",
                "object": "chat.completion",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hi"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            },
        )


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


def test_the_probe_does_not_blame_the_reader_for_its_own_shortcut(picky: str) -> None:
    """**体检不许把自己招来的 400 报成读者的配置问题。**

    那个 `max_tokens=1` 是我们为省钱加的，推理型模型常对最小输出有要求。
    误报和漏报一样坏——读者会去改一套本来没坏的设置。分不出类的 400 要再
    打一枪、这次不带 max_tokens；不错就说明是我们自己绊的。
    """
    assert preflight.check(_cfg(picky, model="picky-about-max-tokens")).code == ""


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
