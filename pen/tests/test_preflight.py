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
    code, _ = preflight.check(_cfg(picky))
    assert code == ""


def test_a_dead_key_is_named_a_dead_key(picky: str) -> None:
    code, _ = preflight.check(_cfg(picky, key="sk-bad"))
    assert code == "bad-key"


def test_a_model_this_endpoint_does_not_have(picky: str) -> None:
    """读者截图里那条「意料外的错误（NotFoundError）」的真身。

    404 在 OpenAI 兼容节点上几乎只有一个意思，报成「意料外」等于没报。
    """
    code, model = preflight.check(_cfg(picky, model="typo-model"))
    assert code == "no-model"
    assert model == "typo-model"  # 文案要指名道姓说是哪个型号写错了


def test_an_endpoint_without_vision_is_caught_before_the_reader_pastes(picky: str) -> None:
    """**这条是这次修复的核心。**

    「节点收不收图」只有把图递过去才知道，所以体检开着视觉时必须真挂一张。
    不挂的话，读者要等到贴完图、发完一轮，才知道这个节点根本没有视觉。
    """
    code, _ = preflight.check(_cfg(picky, vision=True))
    assert code == "no-vision"


def test_vision_off_does_not_send_a_picture(picky: str) -> None:
    """反向闸：关着视觉时体检不许挂图——挂了就会在没有视觉的节点上误报。"""
    assert preflight.check(_cfg(picky, vision=False))[0] == ""
    assert not any("image_url" in str(p) for p in preflight.probe_messages(False))


def test_no_config_is_not_a_network_error() -> None:
    """压根没配 ≠ 连不上。两者的补救动作完全不同，混成一句读者会去查网络。"""
    code, _ = preflight.check(None)
    assert code == preflight.NO_CONFIG


def test_the_probe_is_one_token() -> None:
    """体检要便宜到可以在每次改配置时都跑。"""
    assert len(str(preflight.probe_messages(False))) < 60
