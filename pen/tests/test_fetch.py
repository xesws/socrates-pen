"""fetch：公网 GET 能通；内网 / 本机 / 非 http 必须拒。不打真网。"""

from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest

from pen.agent.fetch import blocked_reason, handle_fetch, html_to_text
from pen.config import MAX_OUTPUT


@pytest.fixture(autouse=True)
def _no_live_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """example.com 解析成一个公网地址，其它名字不走真 DNS。"""

    def fake(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        if host == "example.com":
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", port or 0))]
        raise socket.gaierror(8, "don't live-resolve in tests")

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_html_to_text_drops_script_and_collapses_space() -> None:
    raw = "<html><script>alert(1)</script><style>x{}</style><p>Hello   world</p></html>"
    text = html_to_text(raw)
    assert "alert" not in text
    assert "Hello world" in text


def test_blocked_reason_rejects_non_http_and_private() -> None:
    assert blocked_reason("file:///etc/passwd") is not None
    assert blocked_reason("ftp://example.com/") is not None
    assert blocked_reason("http://127.0.0.1:8765/") is not None
    assert blocked_reason("http://169.254.169.254/") is not None
    assert blocked_reason("http://192.168.1.1/") is not None
    assert blocked_reason("http://10.0.0.1/") is not None
    assert blocked_reason("http://[::1]/") is not None
    assert blocked_reason("http://localhost/") is not None
    assert blocked_reason("https://user:pass@example.com/") is not None
    assert blocked_reason("not-a-url") is not None
    assert blocked_reason("https://example.com/paper") is None
    assert blocked_reason("http://0.0.0.0/") is not None
    assert blocked_reason("http://[::ffff:127.0.0.1]/") is not None
    assert blocked_reason("http://2130706433/") is not None
    assert blocked_reason("http://0x7f000001/") is not None
    assert blocked_reason("http://example.com:abc/") is not None
    assert blocked_reason("http://[::1") is not None


class _Resp:
    def __init__(
        self,
        status: int,
        content: bytes,
        headers: dict[str, str] | None = None,
        url: str = "https://example.com/paper",
    ) -> None:
        self.status_code = status
        self.content = content
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}
        self.url = url


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    class _Stream:
        def __init__(self, resp: _Resp) -> None:
            self.status_code = resp.status_code
            self.headers = resp.headers
            self.url = resp.url
            self._content = resp.content

        def __enter__(self) -> _Stream:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def iter_bytes(self) -> Any:
            yield self._content

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            assert kwargs.get("trust_env") is False
            assert kwargs.get("follow_redirects") is False

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def stream(
            self,
            method: str,
            url: str,
            headers: dict[str, str] | None = None,
            extensions: dict[str, Any] | None = None,
        ) -> _Stream:
            assert method == "GET"
            return _Stream(handler(url, headers or {}, extensions or {}))

    monkeypatch.setattr(httpx, "Client", _Client)


def test_fetch_returns_stripped_html(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(url: str, headers: dict[str, str], ext: dict[str, Any]) -> _Resp:
        assert "93.184.216.34" in url
        assert headers.get("Host") == "example.com"
        assert ext.get("sni_hostname") == "example.com"
        return _Resp(200, b"<html><script>x</script><p>DQN paper</p></html>")

    _patch_client(monkeypatch, handler)
    out = handle_fetch({"url": "https://example.com/paper"}, {})
    assert out["ok"] is True
    assert "DQN paper" in out["text"]
    assert "script" not in out["text"].lower()
    assert out["detail"] == "https://example.com/paper"
    assert set(out) == {"ok", "text", "resolved", "detail"}


def test_fetch_refuses_loopback_without_http() -> None:
    out = handle_fetch({"url": "http://127.0.0.1:8765/v1/chat"}, {})
    assert out["ok"] is False
    assert "内网" in out["text"] or "本机" in out["text"]


def test_fetch_refuses_metadata_ip() -> None:
    out = handle_fetch({"url": "http://169.254.169.254/latest/meta-data/"}, {})
    assert out["ok"] is False


def test_fetch_refuses_empty_url() -> None:
    out = handle_fetch({"url": "  "}, {})
    assert out["ok"] is False
    assert "需要 url" in out["text"]


def test_fetch_does_not_follow_redirect_into_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def handler(url: str, headers: dict[str, str], ext: dict[str, Any]) -> _Resp:
        seen.append(url)
        if "93.184.216.34" in url:
            return _Resp(
                302,
                b"",
                headers={"location": "http://127.0.0.1:8765/secret"},
                url=url,
            )
        raise AssertionError(f"must not GET {url}")

    _patch_client(monkeypatch, handler)
    out = handle_fetch({"url": "https://example.com/hop"}, {})
    assert out["ok"] is False
    assert len(seen) == 1
    assert "内网" in out["text"] or "本机" in out["text"]


def test_fetch_does_not_follow_redirect_into_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(url: str, headers: dict[str, str], ext: dict[str, Any]) -> _Resp:
        return _Resp(302, b"", headers={"location": "file:///etc/passwd"}, url=url)

    _patch_client(monkeypatch, handler)
    out = handle_fetch({"url": "https://example.com/hop"}, {})
    assert out["ok"] is False


def test_fetch_malformed_url_is_tool_error_not_crash() -> None:
    out = handle_fetch({"url": "http://example.com:abc/"}, {})
    assert out["ok"] is False
    assert "ok" in out and "text" in out
    broken = handle_fetch({"url": "http://[::1"}, {})
    assert broken["ok"] is False


def test_fetch_truncates_to_max_output(monkeypatch: pytest.MonkeyPatch) -> None:
    blob = ("word " * (MAX_OUTPUT // 4 + 50)).encode("utf-8")

    def handler(url: str, headers: dict[str, str], ext: dict[str, Any]) -> _Resp:
        return _Resp(200, blob, headers={"content-type": "text/plain"})

    _patch_client(monkeypatch, handler)
    out = handle_fetch({"url": "https://example.com/long"}, {})
    assert out["ok"] is True
    assert len(out["text"]) <= MAX_OUTPUT + len("\n...(已截断)")
    assert out["text"].endswith("...(已截断)")


def test_fetch_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(url: str, headers: dict[str, str], ext: dict[str, Any]) -> _Resp:
        return _Resp(404, b"nope")

    _patch_client(monkeypatch, handler)
    out = handle_fetch({"url": "https://example.com/missing"}, {})
    assert out["ok"] is False
    assert "404" in out["text"]
