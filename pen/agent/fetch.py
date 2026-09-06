"""fetch：GET 一个公网 http(s) URL，把正文按段落编号交给模型。

不是搜索（搜索在 `pen/agent/search.py`）。私网 / 本机 / 元数据地址一律拒绝；
每一次跳转都再查一遍。解析出 IP 之后按这个 IP 去连，避免查的时候是公网、
连的时候换成 127.0.0.1。

v0.27.0 起正文是**一段一行**、带行号（`N\t段落`），offset / limit 和 read_file
一个用法，切片和尾注走 `readtool.slice_lines`。取回的行按 URL 缓存十分钟：
模型按尾注的 offset 续读时不再下载、不再走一遍跳转链——它第一次取的时候
已经过了全套校验。
"""

from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from pen.agent.tools_impl import _int_arg
from pen.config import READ_LIMIT_DEFAULT
from pen.readtool import slice_lines

FETCH_TIMEOUT_S = 10.0
FETCH_MAX_BYTES = 200_000
FETCH_MAX_REDIRECTS = 5
# 续读同一页不重新下载。不是读者旋钮：十分钟够一轮对话把一页翻完，
# 16 条够一轮里同时翻的几页；过期就重取。
FETCH_CACHE_TTL_S = 600.0
FETCH_CACHE_SIZE = 16
# 比这长的一行（段落 / 单行 JSON / 长 <pre>）拆成几行再进缓存：拆开之后每一行都
# 小于 MAX_OUTPUT，模型按行号总能读到，不会落进「本行剩余部分读不到」（四审）。
# 拆行点是稳定的（缓存里就是拆好的行），续读的 offset 不漂。
FETCH_LINE_CHARS = 1500
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template"})
# 这些标签开始或结束就换一行：一段一行，模型按行号续读时段落不会被切半。
_BLOCK_TAGS = frozenset(
    {
        "p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "th", "td",
        "section", "article", "header", "footer", "blockquote", "pre", "table",
        "ul", "ol", "dt", "dd", "hr", "title", "nav", "aside", "main", "figcaption",
    }
)
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_DEC_HOST = re.compile(r"^\d+$")
_HEX_HOST = re.compile(r"^0x[0-9a-fA-F]+$", re.I)


class _HTMLLines(HTMLParser):
    """块级标签断行、行内空白合并、空行丢；script / style 整段跳过。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._buf: list[str] = []
        self._skip = 0
        self._pre = 0

    def _flush(self) -> None:
        text = "".join(self._buf)
        self._buf = []
        if self._pre:
            for piece in text.split("\n"):
                piece = piece.rstrip()
                if piece.strip():
                    self.lines.append(piece)
            return
        line = " ".join(text.split())
        if line:
            self.lines.append(line)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if tag in _BLOCK_TAGS:
            self._flush()
            if tag == "pre":
                self._pre += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            if self._skip:
                self._skip -= 1
            return
        if tag in _BLOCK_TAGS:
            self._flush()
            if tag == "pre" and self._pre:
                self._pre -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._buf.append(data)

    def done(self) -> list[str]:
        self._flush()
        return self.lines


def html_to_lines(raw: str) -> list[str]:
    """HTML → 一段一行的纯文本行（不带换行符）。解析器炸了就退化成一整行。"""
    parser = _HTMLLines()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return [" ".join(raw.split())] if raw.strip() else []
    return parser.done()


def html_to_text(raw: str) -> str:
    return " ".join(html_to_lines(raw))


def _fail(text: str, *, url: str, resolved: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "text": text,
        "resolved": resolved or url,
        "detail": url,
    }


_CACHE: OrderedDict[str, tuple[float, list[str], str, bool]] = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _reset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _cache_get(url: str) -> tuple[list[str], str, bool] | None:
    with _CACHE_LOCK:
        hit = _CACHE.get(url)
        if hit is None:
            return None
        ts, lines, resolved, capped = hit
        if time.monotonic() - ts > FETCH_CACHE_TTL_S:
            del _CACHE[url]
            return None
        _CACHE.move_to_end(url)
        return lines, resolved, capped


def _cache_put(url: str, lines: list[str], resolved: str, capped: bool) -> None:
    with _CACHE_LOCK:
        _CACHE[url] = (time.monotonic(), lines, resolved, capped)
        _CACHE.move_to_end(url)
        while len(_CACHE) > FETCH_CACHE_SIZE:
            _CACHE.popitem(last=False)


def _split_long(lines: list[str]) -> list[str]:
    """超过 FETCH_LINE_CHARS 的行在最后一个空白处拆；没有空白就硬切。"""
    out: list[str] = []
    for line in lines:
        while len(line) > FETCH_LINE_CHARS:
            cut = line.rfind(" ", 0, FETCH_LINE_CHARS)
            if cut <= 0:
                cut = FETCH_LINE_CHARS
            piece = line[:cut].rstrip()
            if piece:
                out.append(piece)
            line = line[cut:].lstrip()
        if line:
            out.append(line)
    return out


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


def _decode_body(content: bytes, content_type: str) -> list[str]:
    """字节 → 行。HTML 一段一行；别的（JSON、纯文本）按它自己的换行。"""
    charset = "utf-8"
    lower = content_type.lower()
    if "charset=" in lower:
        charset = lower.split("charset=", 1)[1].split(";", 1)[0].strip().strip("\"'") or "utf-8"
    try:
        text = content.decode(charset, errors="replace")
    except LookupError:
        text = content.decode("utf-8", errors="replace")
    if "html" in lower or text.lstrip()[:15].lower().startswith(("<!doctype html", "<html")):
        return _split_long(html_to_lines(text))
    return _split_long(text.splitlines())


def _read_capped(resp: httpx.Response, deadline: float) -> tuple[bytes, bool] | str:
    """(正文, 是否在字节上限处截断)。截断要传到切片层：不然「页面共 N 行」是在撒谎（四审）。"""
    buf = bytearray()
    capped = False
    try:
        for chunk in resp.iter_bytes():
            if time.monotonic() > deadline:
                return "错误：取网页超时。"
            room = FETCH_MAX_BYTES - len(buf)
            if room <= 0:
                capped = True
                break
            if len(chunk) > room:
                capped = True
            buf.extend(chunk[:room])
            if capped:
                break
    except httpx.TimeoutException:
        return "错误：取网页超时。"
    except httpx.RequestError as exc:
        return f"错误：取网页失败：{exc}"
    return bytes(buf), capped


def handle_fetch(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    url = str(args.get("url") or "").strip()
    if not url:
        return _fail("错误：fetch 需要 url。", url="")
    offset = _int_arg(args, "offset", 1)
    limit = _int_arg(args, "limit", READ_LIMIT_DEFAULT)
    if offset is None or limit is None:
        return _fail(
            "错误：offset 和 limit 必须是正整数（例如 offset=81, limit=80）。"
            "offset 是起始段落的行号，limit 是读几行。",
            url=url,
        )
    cached = _cache_get(url)
    if cached is None:
        got = _download(url)
        if isinstance(got, dict):
            return got
        lines, resolved, capped = got
        _cache_put(url, lines, resolved, capped)
    else:
        lines, resolved, capped = cached
    report = slice_lines([line + "\n" for line in lines], offset, limit, unit="页面")
    if capped and (not report["lines"] or report["lines"][1] >= report["total"]):
        # 模型读到了我们手里的最后一行（或越界）：这时它会以为到了页尾，得说清楚。
        report["text"] += (
            f"\n（注意：这一页超过 {FETCH_MAX_BYTES} 字节，后面的没取到；"
            f"「页面共 {report['total']} 行」只算取到的部分）"
        )
    return {"ok": True, "resolved": resolved, "detail": url, "capped": capped, **report}


def _download(url: str) -> tuple[list[str], str, bool] | dict[str, Any]:
    """走完跳转链、钉 IP、限字节，返回 (行, 最终 URL, 是否截断)；失败返回 `_fail` 的 dict。"""
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
                        got = _read_capped(resp, deadline)
                        if isinstance(got, str):
                            return _fail(got, url=url, resolved=current)
                        body, capped = got
                        ctype = resp.headers.get("content-type") or ""
                        lines = _decode_body(body, ctype)
                        if capped and len(lines) > 1:
                            lines = lines[:-1]  # 最后一行多半切了一半
                        return lines, target.logical, capped
                except httpx.TimeoutException:
                    return _fail("错误：取网页超时。", url=url, resolved=current)
                except httpx.RequestError as exc:
                    return _fail(f"错误：取网页失败：{exc}", url=url, resolved=current)
    except Exception as exc:
        return _fail(f"错误：取网页失败：{exc}", url=url, resolved=current)
    return _fail("错误：重定向次数太多。", url=url, resolved=current)
