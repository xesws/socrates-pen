"""fetch：公网 GET 能通；内网 / 本机 / 非 http 必须拒。不打真网。"""

from __future__ import annotations

import socket
from typing import Any

import httpx
import pytest

from pen.agent import fetch as fetchmod
from pen.agent.fetch import blocked_reason, handle_fetch, html_to_lines, html_to_text
from pen.config import MAX_OUTPUT, READ_LIMIT_DEFAULT


@pytest.fixture(autouse=True)
def _no_live_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """example.com 解析成一个公网地址，其它名字不走真 DNS。页面缓存每条测试清空。"""
    fetchmod._reset_cache()

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
    assert out["text"] == "1\tDQN paper\n"
    assert "script" not in out["text"].lower()
    assert out["detail"] == "https://example.com/paper"
    assert set(out) == {"ok", "text", "resolved", "detail", "lines", "total", "truncated"}


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


def test_fetch_long_page_truncates_on_a_line_and_names_the_next_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    """v0.26 之前超过 MAX_OUTPUT 只写 `...(已截断)`：模型不知道停在哪、下一段从哪读。"""
    from pen.compact import _line_span

    blob = "".join(f"line {i} " + "x" * 100 + "\n" for i in range(1, 401)).encode("utf-8")

    def handler(url: str, headers: dict[str, str], ext: dict[str, Any]) -> _Resp:
        return _Resp(200, blob, headers={"content-type": "text/plain"})

    _patch_client(monkeypatch, handler)
    out = handle_fetch({"url": "https://example.com/long"}, {})
    assert out["ok"] is True
    assert out["truncated"] is True
    first, last = out["lines"]
    assert first == 1 and 1 < last < READ_LIMIT_DEFAULT
    assert out["total"] == 400
    assert "...(已截断)" not in out["text"]
    assert f"页面共 400 行。接着读用 offset={last + 1}" in out["text"]
    assert _line_span(out["text"]) == (1, last)
    body = out["text"].split("\n…（已截断", 1)[0]
    assert len(body) <= MAX_OUTPUT


def test_fetch_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(url: str, headers: dict[str, str], ext: dict[str, Any]) -> _Resp:
        return _Resp(404, b"nope")

    _patch_client(monkeypatch, handler)
    out = handle_fetch({"url": "https://example.com/missing"}, {})
    assert out["ok"] is False
    assert "404" in out["text"]


# ── v0.27.0：按段落编号、offset / limit 分段读、同一页续读不重新下载 ──


def test_html_to_lines_breaks_on_block_tags_only() -> None:
    raw = (
        "<html><head><title>T</title><script>x()</script></head><body>"
        "<h1>Head</h1><p>One   two</p><div>Three <b>bold</b> <i>it</i></div>"
        "<ul><li>a</li><li>b</li></ul><p></p><p>   </p><span>tail</span></body></html>"
    )
    assert html_to_lines(raw) == ["T", "Head", "One two", "Three bold it", "a", "b", "tail"]


def test_fetch_numbers_paragraphs_like_read_file(monkeypatch: pytest.MonkeyPatch) -> None:
    from pen.compact import _line_span

    def handler(url: str, headers: dict[str, str], ext: dict[str, Any]) -> _Resp:
        return _Resp(200, b"<h1>Title</h1><p>One</p><div>Two <b>bold</b></div><ul><li>a</li><li>b</li></ul>")

    _patch_client(monkeypatch, handler)
    out = handle_fetch({"url": "https://example.com/paper"}, {})
    assert out["text"] == "1\tTitle\n2\tOne\n3\tTwo bold\n4\ta\n5\tb\n"
    assert out["lines"] == [1, 5]
    assert out["total"] == 5
    assert out["truncated"] is False
    assert _line_span(out["text"]) == (1, 5)


def test_fetch_offset_limit_slices_and_resumes_without_a_second_download(monkeypatch: pytest.MonkeyPatch) -> None:
    downloads: list[str] = []
    page = "".join(f"<p>para {i}</p>" for i in range(1, 31)).encode("utf-8")

    def handler(url: str, headers: dict[str, str], ext: dict[str, Any]) -> _Resp:
        downloads.append(url)
        return _Resp(200, page)

    _patch_client(monkeypatch, handler)
    first = handle_fetch({"url": "https://example.com/paper", "limit": 10}, {})
    assert first["lines"] == [1, 10]
    assert first["text"].endswith("（第 1–10 行，页面共 30 行；接着读 offset=11）")
    second = handle_fetch({"url": "https://example.com/paper", "offset": "11", "limit": 10.0}, {})
    assert second["text"].startswith("11\tpara 11\n")
    assert second["lines"] == [11, 20]
    last = handle_fetch({"url": "https://example.com/paper", "offset": 21, "limit": 50}, {})
    assert last["text"].endswith("30\tpara 30\n")
    assert "接着读" not in last["text"]
    assert downloads == ["https://93.184.216.34/paper"]


def test_fetch_out_of_range_offset_reports_the_page_length(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(url: str, headers: dict[str, str], ext: dict[str, Any]) -> _Resp:
        return _Resp(200, b"<p>a</p><p>b</p><p>c</p>")

    _patch_client(monkeypatch, handler)
    out = handle_fetch({"url": "https://example.com/paper", "offset": 999}, {})
    assert out["ok"] is True
    assert out["text"] == "(空页面或超出范围：页面共 3 行)"
    assert out["lines"] == []


def test_fetch_non_html_keeps_its_own_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(url: str, headers: dict[str, str], ext: dict[str, Any]) -> _Resp:
        return _Resp(200, b'{"a": 1,\n "b": 2}\n', headers={"content-type": "application/json"})

    _patch_client(monkeypatch, handler)
    out = handle_fetch({"url": "https://example.com/data.json"}, {})
    assert out["text"] == '1\t{"a": 1,\n2\t "b": 2}\n'


def test_fetch_malformed_offset_is_a_tool_error_and_never_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(url: str, headers: dict[str, str], ext: dict[str, Any]) -> _Resp:
        raise AssertionError("must not download when the arguments are bad")

    _patch_client(monkeypatch, handler)
    for bad in ({"offset": "abc"}, {"limit": True}, {"offset": "L3"}):
        out = handle_fetch({"url": "https://example.com/paper", **bad}, {})
        assert out["ok"] is False
        assert "正整数" in out["text"]
        assert out["detail"] == "https://example.com/paper"


def test_fetch_error_pages_are_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def handler(url: str, headers: dict[str, str], ext: dict[str, Any]) -> _Resp:
        calls.append(1)
        return _Resp(503, b"later") if len(calls) == 1 else _Resp(200, b"<p>now</p>")

    _patch_client(monkeypatch, handler)
    assert handle_fetch({"url": "https://example.com/flaky"}, {})["ok"] is False
    assert handle_fetch({"url": "https://example.com/flaky"}, {})["text"] == "1\tnow\n"
    assert len(calls) == 2
