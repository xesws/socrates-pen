"""search：不用钥匙的公网搜索。三家纯 HTML / JSON 入口 → 筛 → 排 → 缓存 → 分页。

为什么不用 API：读者要的是「自己去搜」，不接 Firecrawl / Tavily，也不填任何钥匙。
2026-09-05 从读者机器实测（浏览器 UA）：DuckDuckGo 的 HTML 入口给 10 条带标题 /
URL / 摘要的结果；Bing 有 10 个 `b_algo` 块但链接是 `ck/a?…u=a1<base64>` 跳转串；
Wikipedia 的 `list=search` 无钥匙全文搜索中英都通。Mojeek 直接 Captcha、cn.bing
跳落地页、Brave 348KB JS 页，都不要。

**UA 必须像浏览器**：工具自己的 UA 打 DDG 拿到的是 202 挑战页。这是 DDG 未公开的
HTML 入口，一秒打十次会被限流——所以一次 search 每家入口只打一个请求（正常两次：
DDG 加 Wikipedia；DDG 倒了才有第三次 Bing），同一个 query 十分钟内从缓存切片
（offset 才稳定），翻页不再打引擎；同一个 query 同时到两个请求只打一次（single-flight）。

分工照 `pen/agent/fetch.py`：只有 httpx + 标准库。搜索请求只打写死的三个主机，
**不跟跳转**（一个 Location 就当引擎倒了），所以这里不用钉 IP；结果里的 URL 交给
fetch 时照常过 `parse_target`。三家响应都流式读、有字节上限和墙钟。筛选里对字面 IP
的内网判定复用 fetch 的两个函数，**不做 DNS**——那是取的时候的事。

排序是 Reciprocal Rank Fusion（每家 1/(60+rank) 求和）加一点关键词覆盖加成：两家都给
的自然上浮；覆盖加成只够翻过相邻一两名（标题里的命中比摘要重，拉丁词按词边界），
第 10 名标题摘要全命中也翻不过第 1 名——标题和摘要是站点自己写的，不能让它们
操纵排序。n ≤ 30，一次排序不到一毫秒。
"""

from __future__ import annotations

import base64
import html as htmlmod
import ipaddress
import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, parse_qsl, quote, urlparse

import httpx

from pen.agent.fetch import _is_blocked_ip, _weird_ipv4
from pen.agent.tools_impl import _int_arg
from pen.config import MAX_OUTPUT, SEARCH_LIMIT_DEFAULT, SEARCH_LIMIT_MAX

SEARCH_TIMEOUT_S = 8.0
# 结果页读到这么多字节就停：DDG 43KB、Bing 125KB，400KB 是四倍余量。
SEARCH_MAX_BYTES = 400_000
# 同一个 query 的结果池留多久、留几个。不是读者旋钮：十分钟够一轮对话翻完
# 两三页；64 个 query 够一轮里换着词搜。
SEARCH_CACHE_TTL_S = 600.0
SEARCH_CACHE_SIZE = 64
# 排好序之后最多留几条。三家合起来不到 25 条，30 只是兜底。
SEARCH_POOL_MAX = 30
# query 先截到这么长再做任何事：它进文案、进缓存键，模型偶尔会把整段话塞进来。
SEARCH_QUERY_CHARS = 200
TITLE_CHARS = 120
SNIPPET_CHARS = 200
# 比这长的 URL 不要：一条结果的预算是标题 + URL + 摘要，URL 撑爆了别的就装不下。
URL_CHARS = 400
_RRF_K = 60
# 关键词全命中给这么多分：1/61 − 1/62 ≈ 0.00026，所以标题全命中（0.7 × 0.0008）
# 刚好翻过相邻一名，摘要全命中（0.3 × 0.0008）翻不过；第 10 名全命中 1/70 + 0.0008
# 仍小于第 1 名的 1/61。
_OVERLAP_WEIGHT = 0.0008
_TITLE_SHARE = 0.7
_ENGINE_ORDER = {"ddg": 0, "bing": 1, "wiki": 2}
# 尾注的预留：三种尾注里最长的那句不到 200 字符。
_FOOTER_RESERVE = 260
# DDG / Bing 对非浏览器 UA 回挑战页；这一串是 2026 年的桌面 Chrome。
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_WIKI_UA = "socrates-pen (https://github.com/xesws/socrates-pen)"
_BROWSER_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.8,zh-CN,zh;q=0.6",
}
_CHALLENGE = re.compile(r"anomaly|captcha|are you a robot|unusual traffic|challenge", re.I)
_NO_RESULTS = re.compile(r"no-results|no results", re.I)
_TAG = re.compile(r"<[^>]+>")
_CJK = re.compile(r"[㐀-䶿一-鿿぀-ヿ가-힯]")
_LATIN_WORD = re.compile(r"[a-z0-9]{2,}")
_CJK_RUN = re.compile(r"[㐀-䶿一-鿿぀-ヿ가-힯]+")
_TRACKING = ("fbclid", "gclid", "ref", "ref_src", "spm")

_DDG_URL = "https://html.duckduckgo.com/html/"
_BING_URL = "https://www.bing.com/search"
_DDG_A = re.compile(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DDG_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)
_BING_H2 = re.compile(r"<h2[^>]*>\s*<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>\s*</h2>", re.S)
_BING_P = re.compile(r"<p[^>]*>(.*?)</p>", re.S)


class _EngineDown(Exception):
    """入口没给结果页：挑战页、非 200、跳转、结构变了、连不上。和「搜到零条」不是一回事。"""


@dataclass(frozen=True)
class Hit:
    url: str
    title: str
    snippet: str
    engine: str
    rank: int


@dataclass
class _Merged:
    url: str
    title: str
    snippet: str
    ranks: dict[str, int] = field(default_factory=dict)


def _clean(fragment: str) -> str:
    return " ".join(htmlmod.unescape(_TAG.sub("", fragment)).split())


def _client() -> httpx.Client:
    return httpx.Client(timeout=SEARCH_TIMEOUT_S, trust_env=False, follow_redirects=False)


def _get(client: httpx.Client, url: str, *, params: dict[str, Any], headers: dict[str, str]) -> tuple[int, str]:
    """流式读一个入口，最多 SEARCH_MAX_BYTES、SEARCH_TIMEOUT_S。非 200 不读正文。"""
    deadline = time.monotonic() + SEARCH_TIMEOUT_S
    buf = bytearray()
    with client.stream("GET", url, params=params, headers=headers) as resp:
        status = int(resp.status_code)
        if status == 200:
            for chunk in resp.iter_bytes():
                if time.monotonic() > deadline:
                    raise _EngineDown("deadline")
                room = SEARCH_MAX_BYTES - len(buf)
                if room <= 0:
                    break
                buf.extend(chunk[:room])
                if len(buf) >= SEARCH_MAX_BYTES:
                    break
    return status, bytes(buf).decode("utf-8", errors="replace")


def _well_formed(url: str) -> str | None:
    """http(s)、有主机、urlparse 不炸的才要。`http://[::1` 这种 urlparse 会抛 ValueError。"""
    try:
        parsed = urlparse(url)
        parsed.port
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    return url


# ── 三个入口 ──


def _ddg_target(href: str) -> str | None:
    """`//duckduckgo.com/l/?uddg=<encoded>&rut=…` → 真 URL；广告和解不出的给 None。"""
    try:
        href = htmlmod.unescape(href)
        if "ad_domain" in href or "/y.js" in href:
            return None
        if href.startswith("//"):
            href = "https:" + href
        parsed = urlparse(href)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
            got = parse_qs(parsed.query).get("uddg", [""])[0]
            if "ad_domain" in got or "/y.js" in got:
                return None
            return _well_formed(got) if got else None
        return _well_formed(href)
    except ValueError:
        return None


def _ddg(client: httpx.Client, query: str) -> list[Hit]:
    status, text = _get(client, _DDG_URL, params={"q": query}, headers=_BROWSER_HEADERS)
    if status != 200:
        raise _EngineDown(f"duckduckgo HTTP {status}")
    anchors = list(_DDG_A.finditer(text))
    if not anchors:
        # 挑战页、或结构变了都算倒（换 Bing）；只有页面明说没结果才是零条。
        if _CHALLENGE.search(text) or not _NO_RESULTS.search(text):
            raise _EngineDown("duckduckgo gave no result anchors")
        return []
    hits: list[Hit] = []
    for i, m in enumerate(anchors):
        url = _ddg_target(m.group(1))
        if not url:
            continue
        # 摘要只在这条和下一条标题之间找：没摘要的结果不能吞掉下一条的标题。
        end = anchors[i + 1].start() if i + 1 < len(anchors) else len(text)
        sm = _DDG_SNIPPET.search(text, m.end(), end)
        hits.append(
            Hit(
                url=url,
                title=_clean(m.group(2)),
                snippet=_clean(sm.group(1)) if sm else "",
                engine="ddg",
                rank=len(hits) + 1,
            )
        )
    if not hits:
        raise _EngineDown("duckduckgo anchors found but none parsed")
    return hits


def _bing_target(href: str) -> str | None:
    """`bing.com/ck/a?…&u=a1<base64url>` → 真 URL；直链原样；解不出的给 None。"""
    try:
        href = htmlmod.unescape(href)
        parsed = urlparse(href)
        if parsed.netloc.endswith("bing.com") and parsed.path.startswith("/ck/"):
            packed = parse_qs(parsed.query).get("u", [""])[0]
            if not packed.startswith("a1"):
                return None
            raw = packed[2:]
            url = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
            return _well_formed(url)
        return _well_formed(href)
    except (ValueError, UnicodeDecodeError):
        return None


def _bing(client: httpx.Client, query: str) -> list[Hit]:
    status, text = _get(client, _BING_URL, params={"q": query}, headers=_BROWSER_HEADERS)
    if status != 200:
        raise _EngineDown(f"bing HTTP {status}")
    blocks = text.split('<li class="b_algo"')[1:]
    if not blocks:
        raise _EngineDown("bing gave no result blocks")
    hits: list[Hit] = []
    for block in blocks:
        h2 = _BING_H2.search(block)
        if not h2:
            continue
        url = _bing_target(h2.group(1))
        if not url:
            continue
        pm = _BING_P.search(block, h2.end())
        hits.append(
            Hit(
                url=url,
                title=_clean(h2.group(2)),
                snippet=_clean(pm.group(1)) if pm else "",
                engine="bing",
                rank=len(hits) + 1,
            )
        )
    if not hits:
        raise _EngineDown("bing blocks found but none parsed")
    return hits


def _wikipedia(client: httpx.Client, query: str) -> list[Hit]:
    """补充来源，失败静默：概念题它最准，而且最不怕被拦。JSON 形状不对也静默。"""
    lang = "zh" if _CJK.search(query) else "en"
    hits: list[Hit] = []
    try:
        status, text = _get(
            client,
            f"https://{lang}.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": 5,
                "format": "json",
                "utf8": 1,
            },
            headers={"User-Agent": _WIKI_UA, "Accept": "application/json"},
        )
        if status != 200:
            return []
        data = json.loads(text)
        section = data.get("query") if isinstance(data, dict) else None
        rows = section.get("search") if isinstance(section, dict) else None
        if not isinstance(rows, list):
            return []
        for row in rows:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            hits.append(
                Hit(
                    url=f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                    title=title,
                    snippet=_clean(str(row.get("snippet") or "")),
                    engine="wiki",
                    rank=len(hits) + 1,
                )
            )
    except Exception:
        return hits
    return hits


# ── 筛选与排序（纯函数）──


def _canonical(url: str) -> str | None:
    """去重用的键；不是 http(s)、没主机、内网字面 IP、localhost 给 None。scheme 不进键。"""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if not host or host in ("localhost", "localhost.localdomain"):
        return None
    weird = _weird_ipv4(host)
    if weird is not None:
        if _is_blocked_ip(weird):
            return None
    else:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None and _is_blocked_ip(literal):
            return None
    if host.startswith("www."):
        host = host[4:]
    if port:
        host = f"{host}:{port}"
    kept = [
        f"{k}={v}"
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not (k.lower().startswith("utm_") or k.lower() in _TRACKING)
    ]
    path = parsed.path.rstrip("/") or "/"
    return f"{host}{path}" + (f"?{'&'.join(kept)}" if kept else "")


def _merge(hits: list[Hit]) -> list[_Merged]:
    """筛 + 合并：同一条（规范化 URL 相同）合成一项，各家的名次都留着给排序用。

    两家给的 scheme 不同时留 https 那份：交给 fetch 的链接不该因为先来的是 http 就降级。
    """
    by_key: dict[str, _Merged] = {}
    for h in hits:
        if len(h.url) > URL_CHARS:
            continue
        key = _canonical(h.url)
        if key is None:
            continue
        title = h.title[:TITLE_CHARS]
        snippet = h.snippet[:SNIPPET_CHARS]
        got = by_key.get(key)
        if got is None:
            by_key[key] = _Merged(url=h.url, title=title, snippet=snippet, ranks={h.engine: h.rank})
            continue
        if h.engine not in got.ranks or h.rank < got.ranks[h.engine]:
            got.ranks[h.engine] = h.rank
        if h.url.startswith("https://") and not got.url.startswith("https://"):
            got.url = h.url
        if not got.title:
            got.title = title
        if len(snippet) > len(got.snippet):
            got.snippet = snippet
    return [m for m in by_key.values() if m.title]


def _tokens(query: str) -> set[str]:
    """拉丁按空白切、小写、≥2 字符；CJK 取二元组（单字的段整段一个词）。"""
    q = query.lower()
    out = set(_LATIN_WORD.findall(q))
    for run in _CJK_RUN.findall(q):
        if len(run) == 1:
            out.add(run)
        out.update(run[i : i + 2] for i in range(len(run) - 1))
    return out


def _coverage(tokens: set[str], text: str) -> float:
    """query 里的词有多大比例出现在 text 里。拉丁词按词边界（presocratic 不算 socratic）。"""
    if not tokens:
        return 0.0
    low = text.lower()
    n = 0
    for t in tokens:
        if t.isascii():
            if re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", low):
                n += 1
        elif t in low:
            n += 1
    return n / len(tokens)


def _score(query_tokens: set[str], m: _Merged) -> float:
    rrf = sum(1.0 / (_RRF_K + r) for r in m.ranks.values())
    if not query_tokens:
        return rrf
    covered = _TITLE_SHARE * _coverage(query_tokens, m.title) + (1 - _TITLE_SHARE) * _coverage(
        query_tokens, m.snippet
    )
    return rrf + _OVERLAP_WEIGHT * covered


def _rank(query: str, merged: list[_Merged]) -> list[Hit]:
    toks = _tokens(query)

    def best_engine(m: _Merged) -> str:
        return min(m.ranks, key=lambda e: (_ENGINE_ORDER.get(e, 9), m.ranks[e]))

    def key(m: _Merged) -> tuple[float, int, int]:
        best = best_engine(m)
        return (-_score(toks, m), _ENGINE_ORDER.get(best, 9), m.ranks[best])

    out: list[Hit] = []
    for m in sorted(merged, key=key):
        best = best_engine(m)
        out.append(Hit(url=m.url, title=m.title, snippet=m.snippet, engine=best, rank=m.ranks[best]))
    return out


# ── 缓存 + single-flight ──

_CACHE: OrderedDict[str, tuple[float, list[Hit], str]] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_INFLIGHT: dict[str, threading.Lock] = {}
_INFLIGHT_MAX = 256


def _reset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
        _INFLIGHT.clear()


def _cache_get(key: str) -> tuple[list[Hit], str] | None:
    with _CACHE_LOCK:
        got = _CACHE.get(key)
        if got is None:
            return None
        ts, hits, note = got
        if time.monotonic() - ts > SEARCH_CACHE_TTL_S:
            del _CACHE[key]
            return None
        _CACHE.move_to_end(key)
        return hits, note


def _cache_put(key: str, hits: list[Hit], note: str) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), hits, note)
        _CACHE.move_to_end(key)
        while len(_CACHE) > SEARCH_CACHE_SIZE:
            _CACHE.popitem(last=False)


def _key_lock(key: str) -> threading.Lock:
    """同一个 query 的并发请求排队：第一个打引擎，后面的从它写的缓存里切。"""
    with _CACHE_LOCK:
        if len(_INFLIGHT) > _INFLIGHT_MAX:
            _INFLIGHT.clear()
        lock = _INFLIGHT.get(key)
        if lock is None:
            lock = _INFLIGHT[key] = threading.Lock()
        return lock


# ── 池：先 DDG，倒了换 Bing；Wikipedia 总是补一份 ──

_WIKI_ONLY_NOTE = "通用搜索入口暂时不可用，下面是维基百科的结果。"
_REBUILT_NOTE = "（结果池已过期，重新搜了一次；序号可能和上次不一致。）"


def search_pool(query: str) -> tuple[list[Hit], str]:
    """(排好序的结果池, 给模型的说明)。DDG 和 Bing 都倒、Wikipedia 也空 → `_EngineDown`。"""
    general: list[Hit] | None
    with _client() as client:
        try:
            general = _ddg(client, query)
        except (_EngineDown, httpx.HTTPError):
            try:
                general = _bing(client, query)
            except (_EngineDown, httpx.HTTPError):
                general = None
        wiki = _wikipedia(client, query)
    if general is None and not wiki:
        raise _EngineDown("duckduckgo and bing are both down")
    note = _WIKI_ONLY_NOTE if general is None else ""
    pool = _rank(query, _merge((general or []) + wiki))
    return pool[:SEARCH_POOL_MAX], note


# ── 给模型看的文本 ──


def _render(
    query: str, hits: list[Hit], note: str, offset: int, limit: int, *, rebuilt: bool = False
) -> tuple[str, list[int]]:
    """逐条在 MAX_OUTPUT 预算内追加，range 只报真交付了的；尾注永远装得下。"""
    total = len(hits)
    if total == 0:
        return f"搜索「{query}」没有结果。换更短、更具体的词，或去掉引号 / site: 再试。", []
    start = offset - 1
    chunk = hits[start : start + limit]
    if not chunk:
        return f"（超出范围：「{query}」共 {total} 条，没有第 {offset} 条起的结果）", []
    first = start + 1

    def head(last: int) -> list[str]:
        lines: list[str] = []
        if rebuilt:
            lines.append(_REBUILT_NOTE)
        if note:
            lines.append(note)
        lines.append(f"搜索「{query}」：共 {total} 条，这里是第 {first}–{last} 条。")
        return lines

    budget = MAX_OUTPUT - _FOOTER_RESERVE - len("\n".join(head(start + len(chunk))))
    items: list[str] = []
    used = 0
    for n, h in enumerate(chunk, start=first):
        item = f"{n}. {h.title}\n   {h.url}" + (f"\n   {h.snippet}" if h.snippet else "")
        if items and used + len(item) + 1 > budget:
            break
        items.append(item)
        used += len(item) + 1
    last = first + len(items) - 1
    lines = head(last) + items
    if last < total:
        cut = "这一段先到这里（回复有长度上限）；" if len(items) < len(chunk) else ""
        lines.append(
            f"（{cut}还有 {total - last} 条没看；接着看用 offset={last + 1}，limit 不超过 {SEARCH_LIMIT_MAX}。"
            "要读某一条的正文用 fetch，长页面按 fetch 的 offset / limit 分段。）"
        )
    else:
        lines.append(
            "（已经是最后一条。要读某一条的正文用 fetch。不合适就换更具体的词，"
            "或加 site:域名 限定，不要重复搜同一个 query。）"
        )
    return "\n".join(lines), [first, last]


def _fail(text: str, query: str) -> dict[str, Any]:
    return {"ok": False, "text": text, "resolved": query, "detail": query}


def handle_search(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    query = " ".join(str(args.get("query") or "").split())[:SEARCH_QUERY_CHARS]
    if not query:
        return _fail("错误：search 需要 query。", "")
    offset = _int_arg(args, "offset", 1)
    limit = _int_arg(args, "limit", SEARCH_LIMIT_DEFAULT)
    if offset is None or limit is None:
        return _fail(
            "错误：offset 和 limit 必须是正整数（例如 offset=6, limit=5）。"
            "offset 是从第几条起，limit 是看几条。",
            query,
        )
    limit = min(limit, SEARCH_LIMIT_MAX)
    key = query.lower()
    rebuilt = False
    with _key_lock(key):
        cached = _cache_get(key)
        if cached is None:
            try:
                hits, note = search_pool(query)
            except _EngineDown:
                return _fail(
                    "错误：搜索入口暂时不可用（DuckDuckGo 和 Bing 都没有返回结果）。"
                    "用手上的材料作答，或请读者给一个 URL。",
                    query,
                )
            except Exception as exc:
                return _fail(f"错误：搜索失败：{type(exc).__name__}。用手上的材料作答。", query)
            _cache_put(key, hits, note)
            # 带着旧 offset 来、池却没了（过期 / 被挤掉）：如实说这是新搜的一池。
            rebuilt = offset > 1
        else:
            hits, note = cached
    text, span = _render(query, hits, note, offset, limit, rebuilt=rebuilt)
    return {
        "ok": True,
        "text": text,
        "resolved": query,
        "detail": query,
        "total": len(hits),
        "range": span,
    }
