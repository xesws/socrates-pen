"""fetch：GET 一个公网 http(s) URL，把正文交给模型。

不是搜索。私网 / 本机 / 元数据地址一律拒绝；每一次跳转都再查一遍。
解析出 IP 之后按这个 IP 去连，避免查的时候是公网、连的时候换成 127.0.0.1。
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from pen.config import MAX_OUTPUT

FETCH_TIMEOUT_S = 10.0
FETCH_MAX_BYTES = 200_000
FETCH_MAX_REDIRECTS = 5
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template"})
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_DEC_HOST = re.compile(r"^\d+$")
_HEX_HOST = re.compile(r"^0x[0-9a-fA-F]+$", re.I)


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


def _v4_inside(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        inner = _v4_inside(ip)
        if inner is not None:
            ip = inner
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT:
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _weird_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """十进制 / 十六进制写成的 IPv4，getaddrinfo 在有的系统上会解成 127.0.0.1。"""
    if _DEC_HOST.fullmatch(host):
        try:
            n = int(host)
        except ValueError:
            return None
        if 0 <= n <= 0xFFFFFFFF:
            return ipaddress.IPv4Address(n)
        return None
    if _HEX_HOST.fullmatch(host):
        try:
            return ipaddress.IPv4Address(int(host, 16))
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class _Target:
    logical: str
    connect: str
    host_header: str
    sni: str


def _netloc_for(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, port: int | None, scheme: str) -> str:
    host = f"[{ip}]" if ip.version == 6 else str(ip)
    default = 443 if scheme == "https" else 80
    if port and port != default:
        return f"{host}:{port}"
    return host


def parse_target(url: str) -> _Target | str:
    """能取则返回钉死 IP 的连接目标；否则返回给模型看的错误句。"""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return "错误：URL 不合法。"
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
    weird = _weird_ipv4(host)
    if weird is not None:
        if _is_blocked_ip(weird):
            return "错误：不能取内网或本机地址。"
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address = weird
    else:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            if _is_blocked_ip(literal):
                return "错误：不能取内网或本机地址。"
            ip = literal
        else:
            try:
                infos = socket.getaddrinfo(host, port or 0, type=socket.SOCK_STREAM)
            except socket.gaierror:
                return "错误：解析不到这个主机。"
            ips = [ipaddress.ip_address(info[4][0]) for info in infos]
            if not ips:
                return "错误：解析不到这个主机。"
            if any(_is_blocked_ip(item) for item in ips):
                return "错误：不能取内网或本机地址。"
            ip = ips[0]
    default = 443 if parsed.scheme == "https" else 80
    host_header = host if not port or port == default else f"{host}:{port}"
    connect = urlunparse(
        (
            parsed.scheme,
            _netloc_for(ip, port, parsed.scheme),
            parsed.path or "/",
            parsed.params,
            parsed.query,
            "",
        )
    )
    return _Target(logical=url, connect=connect, host_header=host_header, sni=host)


def blocked_reason(url: str) -> str | None:
    got = parse_target(url)
    return got if isinstance(got, str) else None


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


def _read_capped(resp: httpx.Response, deadline: float) -> bytes | str:
    buf = bytearray()
    try:
        for chunk in resp.iter_bytes():
            if time.monotonic() > deadline:
                return "错误：取网页超时。"
            room = FETCH_MAX_BYTES - len(buf)
            if room <= 0:
                break
            buf.extend(chunk[:room])
            if len(buf) >= FETCH_MAX_BYTES:
                break
    except httpx.TimeoutException:
        return "错误：取网页超时。"
    except httpx.RequestError as exc:
        return f"错误：取网页失败：{exc}"
    return bytes(buf)


def handle_fetch(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    url = str(args.get("url") or "").strip()
    if not url:
        return _fail("错误：fetch 需要 url。", url="")
    current = url
    try:
        with httpx.Client(
            timeout=FETCH_TIMEOUT_S,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            deadline = time.monotonic() + FETCH_TIMEOUT_S
            for _ in range(FETCH_MAX_REDIRECTS + 1):
                target = parse_target(current)
                if isinstance(target, str):
                    return _fail(target, url=url, resolved=current)
                headers = {
                    "User-Agent": "socrates-pen fetch",
                    "Accept": "text/*, application/json",
                    "Host": target.host_header,
                }
                ext = {"sni_hostname": target.sni} if target.connect.startswith("https:") else {}
                try:
                    with client.stream(
                        "GET",
                        target.connect,
                        headers=headers,
                        extensions=ext,
                    ) as resp:
                        if resp.status_code in (301, 302, 303, 307, 308):
                            loc = (resp.headers.get("location") or "").strip()
                            if not loc:
                                return _fail("错误：重定向没有 Location。", url=url, resolved=current)
                            current = urljoin(target.logical, loc)
                            continue
                        if resp.status_code >= 400:
                            return _fail(
                                f"错误：取网页得到 HTTP {resp.status_code}。",
                                url=url,
                                resolved=target.logical,
                            )
                        body = _read_capped(resp, deadline)
                        if isinstance(body, str):
                            return _fail(body, url=url, resolved=current)
                        ctype = resp.headers.get("content-type") or ""
                        text = _decode_body(body, ctype)
                        return _ok(text, url=url, resolved=target.logical)
                except httpx.TimeoutException:
                    return _fail("错误：取网页超时。", url=url, resolved=current)
                except httpx.RequestError as exc:
                    return _fail(f"错误：取网页失败：{exc}", url=url, resolved=current)
    except Exception as exc:
        return _fail(f"错误：取网页失败：{exc}", url=url, resolved=current)
    return _fail("错误：重定向次数太多。", url=url, resolved=current)
