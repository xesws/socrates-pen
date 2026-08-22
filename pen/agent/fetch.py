"""fetch：GET 一个公网 http(s) URL，把正文交给模型。

不是搜索。私网 / 本机 / 元数据地址一律拒绝；每一次跳转都再查一遍。
"""

from __future__ import annotations

import ipaddress
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from pen.config import MAX_OUTPUT

FETCH_TIMEOUT_S = 10.0
FETCH_MAX_BYTES = 200_000
FETCH_MAX_REDIRECTS = 5
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template"})


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._chunks.append(data)


def html_to_text(raw: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return " ".join(raw.split())
    return " ".join("".join(parser._chunks).split())


def _fail(text: str, *, url: str, resolved: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "text": text,
        "resolved": resolved or url,
        "detail": url,
    }


def _ok(text: str, *, url: str, resolved: str) -> dict[str, Any]:
    if len(text) > MAX_OUTPUT:
        text = text[:MAX_OUTPUT] + "\n...(已截断)"
    return {"ok": True, "text": text, "resolved": resolved, "detail": url}


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def blocked_reason(url: str) -> str | None:
    """URL 本身或解析出的 IP 不能取时，返回给模型看的错误句。能取则 None。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "错误：fetch 只接受 http 或 https。"
    host = parsed.hostname
    if not host:
        return "错误：URL 没有主机名。"
    if parsed.username or parsed.password:
        return "错误：fetch 不接受带用户名或密码的 URL。"
    host = host.strip("[]")
    if host.lower() in {"localhost", "localhost.localdomain"}:
        return "错误：不能取内网或本机地址。"
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_blocked_ip(literal):
            return "错误：不能取内网或本机地址。"
        return None
    try:
        infos = socket.getaddrinfo(host, parsed.port or 0, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return "错误：解析不到这个主机。"
    ips = {ipaddress.ip_address(info[4][0]) for info in infos}
    if not ips:
        return "错误：解析不到这个主机。"
    if any(_is_blocked_ip(ip) for ip in ips):
        return "错误：不能取内网或本机地址。"
    return None


def _decode_body(content: bytes, content_type: str) -> str:
    charset = "utf-8"
    lower = content_type.lower()
    if "charset=" in lower:
        charset = lower.split("charset=", 1)[1].split(";", 1)[0].strip().strip("\"'") or "utf-8"
    try:
        text = content.decode(charset, errors="replace")
    except LookupError:
        text = content.decode("utf-8", errors="replace")
    if "html" in lower or text.lstrip()[:15].lower().startswith(("<!doctype html", "<html")):
        return html_to_text(text)
    return text


def handle_fetch(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    url = str(args.get("url") or "").strip()
    if not url:
        return _fail("错误：fetch 需要 url。", url="")
    reason = blocked_reason(url)
    if reason:
        return _fail(reason, url=url)

    current = url
    try:
        with httpx.Client(
            timeout=FETCH_TIMEOUT_S,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for _ in range(FETCH_MAX_REDIRECTS + 1):
                reason = blocked_reason(current)
                if reason:
                    return _fail(reason, url=url, resolved=current)
                try:
                    resp = client.get(
                        current,
                        headers={"User-Agent": "socrates-pen fetch", "Accept": "text/*, application/json"},
                    )
                except httpx.TimeoutException:
                    return _fail("错误：取网页超时。", url=url, resolved=current)
                except httpx.RequestError as exc:
                    return _fail(f"错误：取网页失败：{exc}", url=url, resolved=current)
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = (resp.headers.get("location") or "").strip()
                    if not loc:
                        return _fail("错误：重定向没有 Location。", url=url, resolved=current)
                    current = urljoin(str(resp.url) if resp.url else current, loc)
                    continue
                if resp.status_code >= 400:
                    return _fail(
                        f"错误：取网页得到 HTTP {resp.status_code}。",
                        url=url,
                        resolved=str(resp.url) or current,
                    )
                body = resp.content[:FETCH_MAX_BYTES]
                ctype = resp.headers.get("content-type") or ""
                text = _decode_body(body, ctype)
                return _ok(text, url=url, resolved=str(resp.url) or current)
    except Exception as exc:
        return _fail(f"错误：取网页失败：{exc}", url=url, resolved=current)
    return _fail("错误：重定向次数太多。", url=url, resolved=current)
