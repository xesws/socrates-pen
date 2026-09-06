"""search：不用钥匙的公网搜索。解析 / 筛选 / 排序 / 缓存 / 分页，不打真网。

fixture 是手写的最小 HTML，照 2026-09-05 实测的真页面结构：DDG 的 `result__a`
+ `uddg=`、Bing 的 `b_algo` + `ck/a?…u=a1<base64>`、Wikipedia 的 `list=search`。
"""

from __future__ import annotations

import base64
import json
import threading
import time
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
DDG_NO_RESULTS = '<html><body><div class="no-results">No results.</div></body></html>'

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
    '<li class="b_algo"><h2><a target="_blank" href="http://[::1">Malformed</a></h2></li>'
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
    """假响应：search 走 `client.stream(...)`，只读 status_code 和 iter_bytes()。"""

    def __init__(self, status: int, text: str = "", data: Any = None, chunks: list[bytes] | None = None) -> None:
        self.status_code = status
        self._chunks = chunks if chunks is not None else [(json.dumps(data) if data is not None else text).encode("utf-8")]
        self.consumed = 0

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def iter_bytes(self) -> Any:
        for c in self._chunks:
            self.consumed += len(c)
            yield c


def _patch_client(monkeypatch: pytest.MonkeyPatch, handler: Any) -> list[str]:
    """handler(host, params, headers) -> _Resp；返回 hosts 记录，数引擎打了几次。"""
    hosts: list[str] = []

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            assert kwargs.get("trust_env") is False
            assert kwargs.get("follow_redirects") is False, "search 只打写死的主机，不跟跳转"

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def stream(self, method: str, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> _Resp:
            assert method == "GET"
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
        return _Resp(200, data=WIKI_JSON)
    raise AssertionError(host)


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    searchmod._reset_cache()


# ── 解析 ──


def test_ddg_parser_decodes_uddg_drops_ads_and_strips_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _all_up)
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
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


def test_ddg_zero_anchors_is_engine_down_unless_the_page_says_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """四审：结构变了 / 挑战页 → 引擎倒（换 Bing）；页面明说没结果才是零条。"""
    for body, status in ((DDG_CHALLENGE, 202), ("<html><body><div class='links'>markup changed</div></body></html>", 200), ("", 302)):
        _patch_client(monkeypatch, lambda host, p, h, body=body, status=status: _Resp(status, body))
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            with pytest.raises(_EngineDown):
                searchmod._ddg(client, "x")
    _patch_client(monkeypatch, lambda host, p, h: _Resp(200, DDG_NO_RESULTS))
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        assert searchmod._ddg(client, "x") == []


def test_one_malformed_url_does_not_kill_the_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = (
        '<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=http%3A%2F%2F%5B%3A%3A1&amp;rut=1">Broken</a>'
        + DDG_HTML
    )
    _patch_client(monkeypatch, lambda host, p, h: _Resp(200, bad))
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        hits = searchmod._ddg(client, "x")
    assert [h.title for h in hits][:1] == ["The Socratic Method: Fostering Critical Thinking"]
    assert len(hits) == 3


def test_bing_parser_decodes_click_redirects_and_drops_undecodable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _all_up)
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        hits = searchmod._bing(client, "socratic method")
    assert [h.url for h in hits] == [
        "https://tilt.colostate.edu/the-socratic-method/",
        "https://plato.stanford.edu/entries/socrates/",
    ]
    assert hits[0].title == "The Socratic Method"
    assert hits[0].snippet == "Snippet one about teaching."
    assert hits[1].engine == "bing" and hits[1].rank == 2
    _patch_client(monkeypatch, lambda host, p, h: _Resp(200, "<html><body>nothing</body></html>"))
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        with pytest.raises(_EngineDown):
            searchmod._bing(client, "x")


def test_wikipedia_picks_language_by_script_and_strips_searchmatch(monkeypatch: pytest.MonkeyPatch) -> None:
    hosts = _patch_client(monkeypatch, _all_up)
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
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
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        assert searchmod._wikipedia(client, "socratic method") == []


def test_wikipedia_odd_json_shapes_are_skipped_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    shapes: list[Any] = [
        {"query": {"search": None}},
        {"query": "x"},
        [],
        {"query": {"search": [None, "str", {"title": "", "snippet": "x"}, {"title": "Real", "snippet": "ok"}]}},
    ]
    for data in shapes:
        _patch_client(monkeypatch, lambda host, p, h, data=data: _Resp(200, data=data))
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            hits = searchmod._wikipedia(client, "x")
        assert [h.title for h in hits] in ([], ["Real"])


def test_search_reads_at_most_the_byte_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """四审：三家响应都流式读、有字节上限，巨大页面不会整个进内存。"""
    junk = [b"<div>" + b"x" * 65_536 + b"</div>"] * 60  # 约 4 MB
    resp = _Resp(200, chunks=[DDG_HTML.encode("utf-8"), *junk])
    _patch_client(monkeypatch, lambda host, p, h: resp if host == "html.duckduckgo.com" else _Resp(200, data={"query": {"search": []}}))
    out = handle_search({"query": "socratic method"}, {})
    assert out["ok"] is True and out["total"] == 3
    assert resp.consumed <= searchmod.SEARCH_MAX_BYTES + 65_536 + 11


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
            _hit("https://long.com/" + "p" * 500, "ddg", 8, title="too long a url"),
        ]
    )
    urls = sorted(m.url for m in merged)
    assert urls == ["https://x.com/a?utm_source=foo&fbclid=1", "https://z.com/c#frag"]
    x = next(m for m in merged if "x.com" in m.url)
    assert x.ranks == {"ddg": 1, "wiki": 1}
    assert x.title == "A"
    assert x.snippet == "longer snippet"


def test_merge_prefers_https_when_both_schemes_appear() -> None:
    merged = searchmod._merge([_hit("http://x.com/a", "ddg", 1), _hit("https://x.com/a", "bing", 3)])
    assert [m.url for m in merged] == ["https://x.com/a"]
    merged = searchmod._merge([_hit("https://x.com/a", "ddg", 1), _hit("http://x.com/a", "bing", 3)])
    assert [m.url for m in merged] == ["https://x.com/a"]


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
    # 四审：第 10 名摘要标题全命中也翻不过第 1 名——加成只够挪相邻几位
    tenth = searchmod._merge([_hit("https://tenth.com/", "ddg", 10, title="socratic method teaching", snippet="socratic method teaching")])
    assert [h.url for h in searchmod._rank("socratic method teaching", a + tenth)] == ["https://a.com/", "https://tenth.com/"]
    # 摘要里的命中比标题里的轻：只靠摘要翻不过相邻一名
    snip = searchmod._merge([_hit("https://snip.com/", "ddg", 2, title="page", snippet="socratic method teaching")])
    assert [h.url for h in searchmod._rank("socratic method teaching", a + snip)] == ["https://a.com/", "https://snip.com/"]
    # 拉丁词按词边界：presocratic 不算 socratic
    pre = searchmod._merge([_hit("https://pre.com/", "ddg", 1, title="presocratic thinkers")])
    exact = searchmod._merge([_hit("https://exact.com/", "ddg", 2, title="socratic")])
    assert [h.url for h in searchmod._rank("socratic", pre + exact)] == ["https://exact.com/", "https://pre.com/"]

    tie1 = searchmod._merge([_hit("https://t1.com/", "bing", 1)])
    tie2 = searchmod._merge([_hit("https://t2.com/", "ddg", 1)])
    assert [h.url for h in searchmod._rank("", tie1 + tie2)] == ["https://t2.com/", "https://t1.com/"]
    assert [h.url for h in searchmod._rank("", tie2 + tie1)] == ["https://t2.com/", "https://t1.com/"]


def test_cjk_query_tokens_are_bigrams() -> None:
    assert searchmod._tokens("苏格拉底 method") == {"苏格", "格拉", "拉底", "method"}


# ── handle_search：分页、缓存、尾注 ──


def _many(n: int, *, url_pad: int = 0, sleep: float = 0.0) -> Any:
    def handler(host: str, params: dict[str, Any], headers: dict[str, str]) -> _Resp:
        if host == "html.duckduckgo.com":
            if sleep:
                time.sleep(sleep)
            blocks = "".join(
                f'<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fsite{i}.com%2Fp{"q" * url_pad}&amp;rut=1">Title {i}</a>'
                f'<a class="result__snippet" href="#">Snippet {i} ' + "s" * 300 + "</a>"
                for i in range(1, n + 1)
            )
            return _Resp(200, blocks)
        if host.endswith("wikipedia.org"):
            return _Resp(200, data={"query": {"search": []}})
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


def test_render_never_cuts_an_item_and_range_matches_what_was_delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    """四审：先按整段 chunk 算 range 再硬切文本，range 会谎报。逐条在预算内追加。"""
    _patch_client(monkeypatch, _many(10, url_pad=350))
    out = handle_search({"query": "socratic method", "limit": SEARCH_LIMIT_MAX}, {})
    assert out["total"] == 10
    first, last = out["range"]
    assert first == 1 and 1 < last < 10
    assert len(out["text"]) <= MAX_OUTPUT
    assert f"{last}. Title {last}\n" in out["text"] and f"{last + 1}. Title" not in out["text"]
    assert f"接着看用 offset={last + 1}" in out["text"]
    nxt = handle_search({"query": "socratic method", "offset": last + 1, "limit": SEARCH_LIMIT_MAX}, {})
    assert nxt["range"][0] == last + 1


def test_absurdly_long_queries_are_cut_before_anything_else(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, lambda host, p, h: _Resp(200, DDG_NO_RESULTS) if host == "html.duckduckgo.com" else _Resp(200, data={"query": {"search": []}}))
    out = handle_search({"query": "q" * 5000}, {})
    assert out["total"] == 0
    assert len(out["text"]) <= MAX_OUTPUT
    assert len(out["detail"]) == searchmod.SEARCH_QUERY_CHARS


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
            return _Resp(302, "")  # 跟跳转是关着的：一个 Location 就是引擎倒了
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
            return _Resp(200, data=WIKI_JSON)
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
            return _Resp(200, data={"query": {"search": []}})
        return _Resp(200, DDG_NO_RESULTS)

    _patch_client(monkeypatch, handler)
    out = handle_search({"query": "zzzz qqqq"}, {})
    assert out["ok"] is True and out["total"] == 0 and out["range"] == []
    assert out["text"] == "搜索「zzzz qqqq」没有结果。换更短、更具体的词，或去掉引号 / site: 再试。"


def test_search_cache_expires_after_ttl_and_a_late_page_says_the_pool_was_rebuilt(monkeypatch: pytest.MonkeyPatch) -> None:
    hosts = _patch_client(monkeypatch, _many(3))
    now = [1000.0]
    monkeypatch.setattr(searchmod.time, "monotonic", lambda: now[0])
    handle_search({"query": "q"}, {})
    n = len(hosts)
    page2 = handle_search({"query": "q", "offset": 2}, {})
    assert len(hosts) == n and "重新搜" not in page2["text"]
    now[0] += searchmod.SEARCH_CACHE_TTL_S + 1
    late = handle_search({"query": "q", "offset": 2}, {})
    assert len(hosts) == 2 * n
    assert late["text"].startswith("（结果池已过期，重新搜了一次；序号可能和上次不一致。）\n")


def test_concurrent_misses_search_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """四审：查缓存→打引擎→写缓存不是 single-flight，两个请求同时 miss 会各打一遍。"""
    hosts = _patch_client(monkeypatch, _many(3, sleep=0.15))
    outs: list[dict[str, Any]] = []

    def go() -> None:
        outs.append(handle_search({"query": "same"}, {}))

    threads = [threading.Thread(target=go) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert hosts.count("html.duckduckgo.com") == 1
    assert all(o["total"] == 3 for o in outs)


def test_search_text_never_exceeds_max_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, _many(30))
    out = handle_search({"query": "socratic method", "limit": SEARCH_LIMIT_MAX}, {})
    assert out["total"] == 30
    assert len(out["text"]) <= MAX_OUTPUT
    assert out["range"] == [1, SEARCH_LIMIT_MAX]
