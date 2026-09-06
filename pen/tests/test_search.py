"""search：不用钥匙的公网搜索。解析 / 筛选 / 排序 / 缓存 / 分页，不打真网。

fixture 是手写的最小 HTML，照 2026-09-05 实测的真页面结构：DDG 的 `result__a`
+ `uddg=`、Bing 的 `b_algo` + `ck/a?…u=a1<base64>`、Wikipedia 的 `list=search`。
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest

from pen.agent import search as searchmod
from pen.agent.search import Hit, _EngineDown, handle_search
from pen.config import MAX_OUTPUT, SEARCH_LIMIT_DEFAULT, SEARCH_LIMIT_MAX


def _b64(url: str) -> str:
    return "a1" + base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


DDG_HTML = """
<div class="result results_links results_links_deep web-result">
 <h2 class="result__title"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fduckduckgo.com%2Fy.js%3Fad_domain%3Dstudy.com%26ad_provider%3Dbingv7aa&amp;rut=ad">Study.com | Socratic</a></h2>
 <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fduckduckgo.com%2Fy.js%3Fad_domain%3Dstudy.com&amp;rut=ad">Ad snippet</a>
</div>
<div class="result results_links results_links_deep web-result">
 <h2 class="result__title"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ftilt.colostate.edu%2Fthe-socratic-method%2F&amp;rut=1">The <b>Socratic</b> Method: Fostering Critical Thinking</a></h2>
 <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ftilt.colostate.edu%2Fthe-socratic-method%2F&amp;rut=1">This teaching tip explores how the <b>Socratic Method</b> can be used &amp; why.</a>
</div>
<div class="result results_links results_links_deep web-result">
 <h2 class="result__title"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FSocratic_method&amp;rut=2">Socratic method - Wikipedia</a></h2>
 <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FSocratic_method&amp;rut=2">The Socratic method is a form of argumentative dialogue.</a>
</div>
<div class="result results_links results_links_deep web-result">
 <h2 class="result__title"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.example.org%2Fnosnip%2F&amp;rut=3">No snippet here</a></h2>
</div>
"""

DDG_CHALLENGE = "<html><body>If this error persists, please let us know: anomaly detected</body></html>"

BING_HTML = (
    '<ol id="b_results">'
    '<li class="b_algo" data-id iid="SERP.1"><div class="b_tpcn"><a class="tilk" href="https://www.bing.com/ck/a?!&amp;&amp;p=x&amp;u='
    + _b64("https://tilt.colostate.edu/the-socratic-method/")
    + '&amp;ntb=1">tilt.colostate.edu</a></div>'
    '<h2><a target="_blank" href="https://www.bing.com/ck/a?!&amp;&amp;p=x&amp;u='
    + _b64("https://tilt.colostate.edu/the-socratic-method/")
    + '&amp;ntb=1" h="ID=SERP,1">The <strong>Socratic</strong> Method</a></h2>'
    '<div class="b_caption"><p class="b_lineclamp2">Snippet one about <strong>teaching</strong>.</p></div></li>'
    '<li class="b_algo"><h2><a target="_blank" href="https://plato.stanford.edu/entries/socrates/">Socrates (SEP)</a></h2>'
    '<div class="b_caption"><p>Snippet two.</p></div></li>'
    '<li class="b_algo"><h2><a target="_blank" href="https://www.bing.com/ck/a?!&amp;&amp;p=x&amp;u=a1!!!not-base64&amp;ntb=1">Broken</a></h2>'
    '<div class="b_caption"><p>Snippet three.</p></div></li>'
    "</ol>"
)

WIKI_JSON = {
    "query": {
        "search": [
            {"title": "Socratic method", "snippet": 'The <span class="searchmatch">Socratic</span> method is a form of dialogue.'},
            {"title": "Socratic questioning", "snippet": "Disciplined questioning."},
        ]
    }
}


class _Resp:
    def __init__(self, status: int, text: str = "", data: Any = None) -> None:
        self.status_code = status
        self.text = text
        self._data = data

    def json(self) -> Any:
        if self._data is None:
            raise ValueError("not json")
        return self._data


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler: Any) -> list[str]:
    """handler(host, params, headers) -> _Resp；返回 hosts 记录，数引擎打了几次。"""
    hosts: list[str] = []

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            assert kwargs.get("trust_env") is False

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> _Resp:
            host = httpx.URL(url).host
            hosts.append(host)
            return handler(host, params or {}, headers or {})

    monkeypatch.setattr(httpx, "Client", _Client)
    return hosts


def _all_up(host: str, params: dict[str, Any], headers: dict[str, str]) -> _Resp:
    if host == "html.duckduckgo.com":
        assert "Mozilla" in headers.get("User-Agent", "")
        return _Resp(200, DDG_HTML)
    if host == "www.bing.com":
        return _Resp(200, BING_HTML)
    if host.endswith("wikipedia.org"):
        return _Resp(200, "", WIKI_JSON)
    raise AssertionError(host)


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    searchmod._reset_cache()


# ── 解析 ──


def test_ddg_parser_decodes_uddg_drops_ads_and_strips_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _all_up)
    with httpx.Client(trust_env=False) as client:
        hits = searchmod._ddg(client, "socratic method")
    assert [h.url for h in hits] == [
        "https://tilt.colostate.edu/the-socratic-method/",
        "https://en.wikipedia.org/wiki/Socratic_method",
        "https://www.example.org/nosnip/",
    ]
    assert hits[0].title == "The Socratic Method: Fostering Critical Thinking"
    assert hits[0].snippet == "This teaching tip explores how the Socratic Method can be used & why."
    assert hits[2].snippet == ""  # 没摘要的不吞下一条的标题
    assert [h.rank for h in hits] == [1, 2, 3]
    assert all(h.engine == "ddg" for h in hits)


def test_ddg_challenge_page_means_engine_down_but_no_results_means_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, lambda host, p, h: _Resp(202, DDG_CHALLENGE))
    with httpx.Client(trust_env=False) as client:
        with pytest.raises(_EngineDown):
            searchmod._ddg(client, "x")
    _patch_client(monkeypatch, lambda host, p, h: _Resp(200, "<html><body>No results.</body></html>"))
    with httpx.Client(trust_env=False) as client:
        assert searchmod._ddg(client, "x") == []


def test_bing_parser_decodes_click_redirects_and_drops_undecodable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _all_up)
    with httpx.Client(trust_env=False) as client:
        hits = searchmod._bing(client, "socratic method")
    assert [h.url for h in hits] == [
        "https://tilt.colostate.edu/the-socratic-method/",
        "https://plato.stanford.edu/entries/socrates/",
    ]
    assert hits[0].title == "The Socratic Method"
    assert hits[0].snippet == "Snippet one about teaching."
    assert hits[1].engine == "bing" and hits[1].rank == 2
    _patch_client(monkeypatch, lambda host, p, h: _Resp(200, "<html><body>nothing</body></html>"))
    with httpx.Client(trust_env=False) as client:
        with pytest.raises(_EngineDown):
            searchmod._bing(client, "x")


def test_wikipedia_picks_language_by_script_and_strips_searchmatch(monkeypatch: pytest.MonkeyPatch) -> None:
    hosts = _patch_client(monkeypatch, _all_up)
    with httpx.Client(trust_env=False) as client:
        en = searchmod._wikipedia(client, "socratic method")
        zh = searchmod._wikipedia(client, "苏格拉底 方法")
    assert hosts == ["en.wikipedia.org", "zh.wikipedia.org"]
    assert en[0].url == "https://en.wikipedia.org/wiki/Socratic_method"
    assert en[0].title == "Socratic method"
    assert en[0].snippet == "The Socratic method is a form of dialogue."
    assert zh[0].url.startswith("https://zh.wikipedia.org/wiki/")
    assert [h.rank for h in en] == [1, 2] and en[0].engine == "wiki"

    def boom(host: str, p: dict[str, Any], h: dict[str, str]) -> _Resp:
        raise httpx.ConnectError("no route")

    _patch_client(monkeypatch, boom)
    with httpx.Client(trust_env=False) as client:
        assert searchmod._wikipedia(client, "socratic method") == []


# ── 筛选与排序（纯函数）──


def _hit(url: str, engine: str = "ddg", rank: int = 1, title: str = "T", snippet: str = "") -> Hit:
    return Hit(url=url, title=title, snippet=snippet, engine=engine, rank=rank)


def test_filter_merges_duplicates_and_drops_private_or_non_http() -> None:
    merged = searchmod._merge(
        [
            _hit("http://www.x.com/a/", "ddg", 1, title="A"),
            _hit("https://x.com/a?utm_source=foo&fbclid=1", "wiki", 1, title="A again", snippet="longer snippet"),
            _hit("http://127.0.0.1/secret", "ddg", 2),
            _hit("http://localhost/x", "ddg", 3),
            _hit("file:///etc/passwd", "ddg", 4),
            _hit("http://2130706433/", "ddg", 5),
            _hit("https://y.com/b", "ddg", 6, title=""),
            _hit("https://z.com/c#frag", "ddg", 7, title="Z"),
        ]
    )
    urls = sorted(m.url for m in merged)
    assert urls == ["http://www.x.com/a/", "https://z.com/c#frag"]
    x = next(m for m in merged if "x.com" in m.url)
    assert x.ranks == {"ddg": 1, "wiki": 1}
    assert x.title == "A"
    assert x.snippet == "longer snippet"


def test_filter_caps_title_and_snippet_length() -> None:
    merged = searchmod._merge([_hit("https://a.com/", title="t" * 500, snippet="s" * 900)])
    assert len(merged[0].title) == searchmod.TITLE_CHARS
    assert len(merged[0].snippet) == searchmod.SNIPPET_CHARS


def test_rank_fuses_engines_and_lets_overlap_nudge_but_not_flip() -> None:
    both = searchmod._merge([_hit("https://both.com/", "ddg", 2), _hit("https://both.com/", "wiki", 1)])
    top = searchmod._merge([_hit("https://top.com/", "ddg", 1)])
    order = [h.url for h in searchmod._rank("socratic method", both + top)]
    assert order == ["https://both.com/", "https://top.com/"]  # 两家都给的压过单家第一

    a = searchmod._merge([_hit("https://a.com/", "ddg", 1, title="unrelated page")])
    b = searchmod._merge([_hit("https://b.com/", "ddg", 2, title="socratic method teaching guide")])
    assert [h.url for h in searchmod._rank("socratic method teaching", a + b)] == ["https://b.com/", "https://a.com/"]
    far = searchmod._merge([_hit("https://far.com/", "ddg", 25, title="socratic method teaching guide")])
    assert [h.url for h in searchmod._rank("socratic method teaching", a + far)] == ["https://a.com/", "https://far.com/"]

    tie1 = searchmod._merge([_hit("https://t1.com/", "bing", 1)])
    tie2 = searchmod._merge([_hit("https://t2.com/", "ddg", 1)])
    assert [h.url for h in searchmod._rank("", tie1 + tie2)] == ["https://t2.com/", "https://t1.com/"]
    assert [h.url for h in searchmod._rank("", tie2 + tie1)] == ["https://t2.com/", "https://t1.com/"]


def test_cjk_query_tokens_are_bigrams() -> None:
    assert searchmod._tokens("苏格拉底 method") == {"苏格", "格拉", "拉底", "method"}


# ── handle_search：分页、缓存、尾注 ──


def _many(n: int) -> Any:
    def handler(host: str, params: dict[str, Any], headers: dict[str, str]) -> _Resp:
        if host == "html.duckduckgo.com":
            blocks = "".join(
                f'<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fsite{i}.com%2Fp&amp;rut=1">Title {i}</a>'
                f'<a class="result__snippet" href="#">Snippet {i} ' + "s" * 300 + "</a>"
                for i in range(1, n + 1)
            )
            return _Resp(200, blocks)
        if host.endswith("wikipedia.org"):
            return _Resp(200, "", {"query": {"search": []}})
        raise AssertionError(host)

    return handler


def test_handle_search_pages_from_cache_and_tells_the_model_what_is_left(monkeypatch: pytest.MonkeyPatch) -> None:
    hosts = _patch_client(monkeypatch, _many(12))
    first = handle_search({"query": "  socratic   method "}, {})
    assert first["ok"] is True
    assert first["detail"] == "socratic method" and first["resolved"] == "socratic method"
    assert first["total"] == 12 and first["range"] == [1, SEARCH_LIMIT_DEFAULT]
    assert first["text"].startswith(f"搜索「socratic method」：共 12 条，这里是第 1–{SEARCH_LIMIT_DEFAULT} 条。\n1. Title 1\n   https://site1.com/p\n   Snippet 1 ")
    assert f"（还有 {12 - SEARCH_LIMIT_DEFAULT} 条没看；接着看用 offset={SEARCH_LIMIT_DEFAULT + 1}，limit 不超过 {SEARCH_LIMIT_MAX}。" in first["text"]
    assert "fetch" in first["text"]
    engine_calls = len(hosts)

    second = handle_search({"query": "socratic method", "offset": "6", "limit": 4.0}, {})
    assert len(hosts) == engine_calls  # 第二页从缓存切，没再打引擎
    assert second["range"] == [6, 9]
    assert "6. Title 6" in second["text"] and "10. Title 10" not in second["text"]
    assert "接着看用 offset=10" in second["text"]

    last = handle_search({"query": "Socratic Method", "offset": 10, "limit": 50}, {})
    assert len(hosts) == engine_calls  # 大小写不同也是同一个 query
    assert last["range"] == [10, 12]
    assert "已经是最后一条" in last["text"] and "接着看" not in last["text"]

    beyond = handle_search({"query": "socratic method", "offset": 13}, {})
    assert beyond["ok"] is True and beyond["range"] == []
    assert beyond["text"] == "（超出范围：「socratic method」共 12 条，没有第 13 条起的结果）"

    clamped = handle_search({"query": "socratic method", "limit": 50}, {})
    assert clamped["range"] == [1, SEARCH_LIMIT_MAX]


def test_handle_search_malformed_arguments_are_tool_errors_that_never_hit_the_net(monkeypatch: pytest.MonkeyPatch) -> None:
    hosts = _patch_client(monkeypatch, _all_up)
    empty = handle_search({"query": "   "}, {})
    assert empty["ok"] is False and "需要 query" in empty["text"]
    for bad in ({"offset": "L3"}, {"limit": True}, {"offset": "abc"}):
        out = handle_search({"query": "x", **bad}, {})
        assert out["ok"] is False and "正整数" in out["text"]
        assert out["detail"] == "x"
    assert hosts == []


def test_handle_search_falls_back_to_bing_when_ddg_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(host: str, params: dict[str, Any], headers: dict[str, str]) -> _Resp:
        if host == "html.duckduckgo.com":
            return _Resp(202, DDG_CHALLENGE)
        return _all_up(host, params, headers)

    hosts = _patch_client(monkeypatch, handler)
    out = handle_search({"query": "socratic method"}, {})
    assert out["ok"] is True
    assert hosts == ["html.duckduckgo.com", "www.bing.com", "en.wikipedia.org"]
    assert "plato.stanford.edu" in out["text"]
    assert "维基百科" not in out["text"]


def test_handle_search_serves_wikipedia_alone_with_a_note_when_general_engines_are_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(host: str, params: dict[str, Any], headers: dict[str, str]) -> _Resp:
        if host.endswith("wikipedia.org"):
            return _Resp(200, "", WIKI_JSON)
        return _Resp(202, DDG_CHALLENGE)

    _patch_client(monkeypatch, handler)
    out = handle_search({"query": "socratic method"}, {})
    assert out["ok"] is True
    assert out["text"].startswith("通用搜索入口暂时不可用，下面是维基百科的结果。\n搜索「socratic method」：共 2 条")
    assert "https://en.wikipedia.org/wiki/Socratic_method" in out["text"]


def test_handle_search_reports_when_every_engine_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(host: str, params: dict[str, Any], headers: dict[str, str]) -> _Resp:
        if host.endswith("wikipedia.org"):
            raise httpx.ConnectError("no route")
        return _Resp(202, DDG_CHALLENGE)

    _patch_client(monkeypatch, handler)
    out = handle_search({"query": "socratic method"}, {})
    assert out["ok"] is False
    assert out["text"] == "错误：搜索入口暂时不可用（DuckDuckGo 和 Bing 都没有返回结果）。用手上的材料作答，或请读者给一个 URL。"
    assert searchmod._cache_get("socratic method") is None  # 失败不缓存


def test_handle_search_no_results_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(host: str, params: dict[str, Any], headers: dict[str, str]) -> _Resp:
        if host.endswith("wikipedia.org"):
            return _Resp(200, "", {"query": {"search": []}})
        return _Resp(200, "<html><body>No results.</body></html>")

    _patch_client(monkeypatch, handler)
    out = handle_search({"query": "zzzz qqqq"}, {})
    assert out["ok"] is True and out["total"] == 0 and out["range"] == []
    assert out["text"] == "搜索「zzzz qqqq」没有结果。换更短、更具体的词，或去掉引号 / site: 再试。"


def test_search_cache_expires_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    hosts = _patch_client(monkeypatch, _many(3))
    now = [1000.0]
    monkeypatch.setattr(searchmod.time, "monotonic", lambda: now[0])
    handle_search({"query": "q"}, {})
    n = len(hosts)
    handle_search({"query": "q", "offset": 2}, {})
    assert len(hosts) == n
    now[0] += searchmod.SEARCH_CACHE_TTL_S + 1
    handle_search({"query": "q"}, {})
    assert len(hosts) == 2 * n


def test_search_text_never_exceeds_max_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _many(30))
    out = handle_search({"query": "socratic method", "limit": SEARCH_LIMIT_MAX}, {})
    assert out["total"] == 30
    assert len(out["text"]) <= MAX_OUTPUT
    assert out["range"] == [1, SEARCH_LIMIT_MAX]
