"""深挖池子的语义测试。

投递是「至少一次」：丢一个响应不会丢问题，重复请求同一个 since 返回同样的东西。
成熟度闸门是纯确定性的，只能把 now 降成 later，永远不能反过来。
"""

from __future__ import annotations

import pytest

from pen import config, probe_store
from pen.probe_store import DeepQuestion


@pytest.fixture(autouse=True)
def _tmp_pen(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PEN_DIR", tmp_path / ".pen")
    monkeypatch.setattr(config, "LIBRARIES_DIR", tmp_path / ".pen" / "libraries")
    config.ensure_pen_dirs()


def _q(text: str, **kw) -> DeepQuestion:
    base = dict(id="d1", text=text, timing="now", atom="A", born_round=0)
    base.update(kw)
    return DeepQuestion(**base)


def test_empty_ledger_reads_as_blank() -> None:
    led = probe_store.load("nobody")
    assert led.pool == [] and led.probe_calls == 0 and led.running == []


def test_claim_is_exclusive_per_session() -> None:
    """同一会话同时只允许一个 probe。抢不到就跳过，不排队——
    排队意味着上下文已经过期还要再花一次钱。"""
    a = probe_store.try_claim("s1", "h", 1)
    assert a
    assert probe_store.try_claim("s1", "h", 1) is None
    probe_store.release("s1", a)
    assert probe_store.try_claim("s1", "h", 2)


def test_claim_counts_against_the_session_budget() -> None:
    pid = probe_store.try_claim("s2", "h", 1)
    probe_store.release("s2", pid)
    got = probe_store.budget("s2")
    assert got["used"] == 1 and got["max"] == config.PROBE_MAX_PER_SESSION
    # 窗口配额也要报出去：不然读者只会看到「深题突然不来了」，没有解释
    assert got["window_used"] == 1 and got["window_max"] == config.PROBE_MAX_PER_WINDOW


def test_add_questions_assigns_monotonic_seq_and_dedupes() -> None:
    pid = probe_store.try_claim("s3", "h", 0)
    probe_store.add_questions("s3", pid, [_q("甲问题够长吗？"), _q("甲问题够长吗？"), _q("乙问题也够长？")])
    led = probe_store.load("s3")
    assert [q.seq for q in led.pool] == [1, 2]
    assert led.running == [], "add_questions 要顺手放掉占坑"


def test_inbox_releases_at_most_one_per_call() -> None:
    pid = probe_store.try_claim("s4", "h", 0)
    probe_store.add_questions("s4", pid, [_q("甲问题够长吗？"), _q("乙问题也够长？")])
    got = probe_store.inbox("s4", since=0, atom="A", level="Level 0", now_round=0)
    assert len(got["items"]) == probe_store.MAX_RELEASE_PER_TURN == 1
    assert got["running"] == []


def test_inbox_is_idempotent_for_the_same_cursor() -> None:
    """至少一次投递：同一个 since 重复问，答案一样。"""
    pid = probe_store.try_claim("s5", "h", 0)
    probe_store.add_questions("s5", pid, [_q("甲问题够长吗？")])
    first = probe_store.inbox("s5", since=0, atom="A", now_round=0)
    again = probe_store.inbox("s5", since=0, atom="A", now_round=0)
    assert first["items"] == again["items"] != []


def test_now_items_wait_when_the_reader_has_moved_on() -> None:
    """timing=now 但读者已经走到别的地方了 → 压着不抛。"""
    pid = probe_store.try_claim("s6", "h", 0)
    probe_store.add_questions("s6", pid, [_q("甲问题够长吗？", atom="A", born_round=0)])
    moved = probe_store.inbox("s6", since=0, atom="B", level="Level 3", now_round=3)
    assert moved["items"] == []
    back = probe_store.inbox("s6", since=0, atom="A", level="Level 0", now_round=3)
    assert len(back["items"]) == 1


def test_later_items_surface_when_the_reader_arrives() -> None:
    pid = probe_store.try_claim("s7", "h", 0)
    probe_store.add_questions(
        "s7", pid, [_q("挂在六关的那个问题？", timing="later", target="Level 6", born_round=0)]
    )
    early = probe_store.inbox("s7", since=0, atom="A", level="Level 0", now_round=0)
    assert early["items"] == [], "读者还没走到那一关"
    arrived = probe_store.inbox("s7", since=0, atom="A", level="Level 6", now_round=1)
    assert len(arrived["items"]) == 1


def test_stale_items_are_dropped_not_shown() -> None:
    pid = probe_store.try_claim("s8", "h", 0)
    probe_store.add_questions("s8", pid, [_q("过期的那条问题？", born_round=0)])
    late = probe_store.inbox("s8", since=0, atom="A", now_round=probe_store.ITEM_TTL_TURNS + 1)
    assert late["items"] == []
    assert probe_store.load("s8").pool[0].state == "dropped"


def test_cursor_only_advances_past_what_was_actually_delivered() -> None:
    """一次探索产两条、每轮只放一条。游标要是推到池子最大 seq，
    第二条就永远够不着了——later 通道会整条死掉。"""
    pid = probe_store.try_claim("s9", "h", 0)
    probe_store.add_questions("s9", pid, [_q("甲问题够长吗？"), _q("乙问题也够长？")])
    first = probe_store.inbox("s9", since=0, atom="A", now_round=0)
    assert len(first["items"]) == 1
    assert first["cursor"] == 1, f"只投递了 seq=1，游标不该跳过 seq=2：{first['cursor']}"
    second = probe_store.inbox("s9", since=first["cursor"], atom="A", now_round=1)
    assert len(second["items"]) == 1, "第二条必须够得着"
    assert second["items"][0]["text"] != first["items"][0]["text"]


def test_pool_still_drains_after_two_have_been_shown() -> None:
    """服务端不该拿「同时可见 2 条」当终身闸门：visible_count 只增不减，
    抛满之后每一条 pending 都会在进 _ripe 之前被跳过，池子再也不衰减，
    pending 一路堆到 PROBE_PENDING_CAP，从第四轮起永久停探。"""
    pid = probe_store.try_claim("s9b", "h", 0)
    probe_store.add_questions(
        "s9b", pid, [_q(f"第 {i} 个够长的问题在这里？") for i in range(4)]
    )
    cur = 0
    seen = []
    for r in range(4):
        got = probe_store.inbox("s9b", since=cur, atom="A", now_round=r)
        cur = got["cursor"]
        seen += [i["text"] for i in got["items"]]
    assert len(set(seen)) == 4, f"四条都该轮到，实际只放出 {len(set(seen))} 条"


def test_stale_items_drop_even_when_others_were_already_shown() -> None:
    """TTL 以前藏在 _ripe 里，放行名额用完就走不到，等于永不生效。"""
    pid = probe_store.try_claim("s9c", "h", 0)
    probe_store.add_questions(
        "s9c", pid, [_q("先放出去这条够长吗？"), _q("这条会过期掉的吧？")]
    )
    probe_store.inbox("s9c", since=0, atom="A", now_round=0)
    probe_store.inbox("s9c", since=0, atom="A", now_round=probe_store.ITEM_TTL_TURNS + 1)
    states = {q.text: q.state for q in probe_store.load("s9c").pool}
    assert states["这条会过期掉的吧？"] == "dropped", states
    assert probe_store.load("s9c").pending_count() == 0


def test_mark_clicked_matches_after_normalisation() -> None:
    pid = probe_store.try_claim("s10", "h", 0)
    probe_store.add_questions("s10", pid, [_q("白名单和危险检测的顺序为什么不能换？", grounding="open")])
    hit = probe_store.mark_clicked("s10", "白名单和危险检测的顺序为什么不能换？")
    assert hit is not None and hit.state == "clicked" and hit.grounding == "open"
    assert probe_store.mark_clicked("s10", "毫不相干的一句话") is None


def test_asked_only_lists_what_was_actually_shown() -> None:
    pid = probe_store.try_claim("s11", "h", 0)
    probe_store.add_questions("s11", pid, [_q("甲问题够长吗？"), _q("乙问题也够长？")])
    assert probe_store.asked("s11") == []
    probe_store.inbox("s11", since=0, atom="A", now_round=0)
    assert len(probe_store.asked("s11")) == 1


def test_ledger_survives_a_corrupt_file(tmp_path) -> None:
    probe_store.probes_dir().joinpath("s12.json").write_text("{坏掉的", encoding="utf-8")
    assert probe_store.load("s12").pool == []


def test_unknown_fields_in_a_stored_item_are_ignored() -> None:
    """将来加字段时，旧盘上的记录不能让整个池子读不出来。"""
    led = probe_store.SessionLedger.from_dict(
        {"session_id": "s13", "pool": [{"id": "x", "text": "甲？", "未来字段": 1}]}
    )
    assert len(led.pool) == 1 and led.pool[0].text == "甲？"


def test_advancing_the_cursor_stops_redelivery() -> None:
    """幂等的另一半：前端把 since 推过去之后就不能再收到同一条，
    否则芯片会一直重复冒出来。"""
    pid = probe_store.try_claim("s14", "h", 0)
    probe_store.add_questions("s14", pid, [_q("甲问题够长吗？")])
    first = probe_store.inbox("s14", since=0, atom="A", now_round=0)
    assert first["items"] and first["cursor"] > 0
    after = probe_store.inbox("s14", since=first["cursor"], atom="A", now_round=0)
    assert after["items"] == []


def test_clicked_items_are_never_redelivered() -> None:
    pid = probe_store.try_claim("s15", "h", 0)
    probe_store.add_questions("s15", pid, [_q("甲问题够长吗？")])
    probe_store.inbox("s15", since=0, atom="A", now_round=0)
    probe_store.mark_clicked("s15", "甲问题够长吗？")
    assert probe_store.inbox("s15", since=0, atom="A", now_round=0)["items"] == []


def test_orphaned_running_is_reaped_after_a_restart() -> None:
    """进程被杀时 running 留在盘上。不回收的话 try_claim 永远抢不到坑，
    前端也会对着一个不会完成的幽灵轮询到超时。"""
    from datetime import datetime, timedelta, timezone

    pid = probe_store.try_claim("s16", "h", 0)
    assert pid and probe_store.try_claim("s16", "h", 0) is None
    led = probe_store.load("s16")
    # v0.10.2：窗口不再是常量，而是从本次的超时派生的（见 orphan_window 的
    # 注释——300 秒盖不住一次跨书探索，实测 351 秒）。所以这里要按真窗口算。
    window = probe_store.orphan_window(led.running_timeout)
    stale = datetime.now(timezone.utc) - timedelta(seconds=window + 60)
    led.running_since = stale.isoformat()
    probe_store.save(led)
    assert probe_store.load("s16").running == [], "孤儿没被回收"
    assert probe_store.try_claim("s16", "h", 1), "回收后应能重新占坑"


def test_unparsable_running_since_is_reaped_too() -> None:
    probe_store.try_claim("s17", "h", 0)
    led = probe_store.load("s17")
    led.running_since = "不是时间"
    probe_store.save(led)
    assert probe_store.load("s17").running == []


def test_window_quota_survives_new_sessions(monkeypatch) -> None:
    """每会话 8 次挡不住「开一堆新会话」。窗口配额才是真正的成本上限——
    这个常量以前定义了却没人读，等于没有上限。"""
    monkeypatch.setattr(config, "PROBE_MAX_PER_WINDOW", 3)
    got = []
    for i in range(6):
        pid = probe_store.try_claim(f"day-{i}", "bk", 0)
        got.append(bool(pid))
        if pid:
            probe_store.release(f"day-{i}", pid)
    assert got == [True, True, True, False, False, False]
    assert probe_store.quota_count("bk") == 3


def test_quota_resets_on_a_new_hour() -> None:
    import json

    probe_store.try_claim("hour-x", "bk2", 0)
    stale = probe_store._quota_path("bk2")
    stale.write_text(json.dumps({"hour": "1999-01-01T00", "count": 99}), encoding="utf-8")
    assert probe_store.quota_count("bk2") == 0


def test_quota_status_reports_the_window() -> None:
    probe_store.try_claim("hour-y", "bk3", 0)
    got = probe_store.quota_status("bk3")
    assert got["used"] == 1 and got["window"] == "hour" and got["max"] > 0


def test_release_with_refund_gives_the_quota_back() -> None:
    """一次 LLM 都没打就失败了（起线程失败、抢不到信号量），不该扣配额。
    失败越多能用的次数越少，那是反的。"""
    for i in range(3):
        pid = probe_store.try_claim(f"refund-{i}", "rb", 0)
        probe_store.release(f"refund-{i}", pid, refund=True)
    assert probe_store.quota_count("rb") == 0
    led = probe_store.load("refund-0")
    assert led.probe_calls == 0


def test_release_without_refund_keeps_the_charge() -> None:
    pid = probe_store.try_claim("norefund", "rb2", 0)
    probe_store.release("norefund", pid)
    assert probe_store.load("norefund").probe_calls == 1
    assert probe_store.quota_count("rb2") == 1


def test_concurrent_writers_leave_no_temp_files() -> None:
    """固定用 <path>.tmp 的话，两个写者会互相 replace 掉对方的临时文件，
    抛出未捕获的 FileNotFoundError。"""
    import threading

    errs: list[str] = []

    def worker() -> None:
        try:
            for _ in range(25):
                led = probe_store.load("conc", "cb")
                led.seq += 1
                probe_store.save(led)
        except Exception as exc:  # noqa: BLE001
            errs.append(repr(exc))

    ths = [threading.Thread(target=worker) for _ in range(6)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert not errs, errs
    assert not list(probe_store.probes_dir().glob("*.tmp"))


def test_now_and_later_from_one_probe_both_reach_the_reader() -> None:
    """双通道的完整剧本，也是 P0-1 的看门狗。

    一次探索产 1 条 now + 1 条 later。now 当场抛；later 压着，等读者自己走到
    它挂的那一关再浮出来。游标要是推到池子最大 seq，later 那条就永久够不着——
    双通道的一半直接死掉，而 prompt 还写着「拿不准就写 later」。
    """
    pid = probe_store.try_claim("dual", "h", 1)
    probe_store.add_questions(
        "dual",
        pid,
        [
            _q("眼前这条够长的问题在这里？", timing="now", atom="A", born_round=1),
            _q("挂在六关那条问题够长吗？", timing="later", target="Level 6", born_round=1),
        ],
    )
    cur, got = 0, []

    def poll(level: str, rnd: int) -> None:
        nonlocal cur
        box = probe_store.inbox("dual", since=cur, atom="A", level=level, now_round=rnd)
        cur = box["cursor"]
        got.extend(i["text"] for i in box["items"])

    poll("Level 0", 1)
    assert len(got) == 1, "now 那条该当场抛"
    poll("Level 0", 2)
    assert len(got) == 1, "读者还没走到 Level 6，later 不该抛"
    poll("Level 6", 3)
    assert len(got) == 2, f"走到那一关了，later 必须浮出来：{got}"
    assert probe_store.load("dual").pending_count() == 0


def test_later_delivery_does_not_confuse_the_other_books_level(tmp_path, monkeypatch) -> None:
    """target 只是模型填的一个字符串，别的书也有「Level 1」。跨书题里它可能抄的是
    别本的关名，于是读者走到**当前书**的 Level 1 时，一条讲别人家 Level 1 的题
    就弹出来了。"""
    from pen.probe_store import DeepQuestion, _ripe

    def q(anchors):
        return DeepQuestion(
            id="i", text="t", axis="bridge", grounding="book", timing="later",
            target="Level 1", atom="a", born_round=1, seq=1, anchors=anchors,
        )

    here = {"level": "Level 1", "start_line": 1}
    cross = {"book": "别本", "start_line": 60}
    at = dict(atom="x", level="Level 1", now_round=1)
    assert _ripe(q([here]), **at) is True, "纯本册题行为不该变"
    assert _ripe(q([here, cross]), **at) is True, "本册锚就在这一关，该弹"
    assert _ripe(q([{"level": "Level 5", "start_line": 70}, cross]), **at) is False, \
        "本册锚在 Level 5，却因为别本的 Level 1 弹了出来"


# ── v0.10.2 孤儿窗口必须盖得住一次探索 ─────────────────────────


def test_orphan_window_always_outlasts_the_worst_case_run() -> None:
    """窗口短于真实耗时 = 在真线程还活着时清空 running = try_claim 能再起一个
    = **同一场对话两份深挖并行，各花各的钱**。

    这不是理论：v0.10.2 之前窗口是死的 300 秒，而 deeppoll.ts 自己记着
    实测一次跨书探索 351 秒。
    """
    for timeout in (30.0, 90.0, 150.0, 300.0):
        window = probe_store.orphan_window(timeout)
        worst = timeout * 3 * 2  # SDK 默认重试 3 次 × explore 最多两次调用
        assert window > worst, f"超时 {timeout} 时窗口 {window} 盖不住最坏 {worst}"


def test_the_old_351_second_run_would_no_longer_be_reaped_early() -> None:
    """钉住那个实测数字。默认档 150 秒超时下，351 秒的探索不该被当成孤儿。"""
    assert probe_store.orphan_window(150.0) > 351.0
    assert probe_store.ORPHAN_AFTER_SECONDS < 351.0, "旧的死常量确实盖不住——这就是病因"


def test_a_ledger_without_the_snapshot_falls_back_to_the_constant() -> None:
    """老账本没有 running_timeout（=0）→ 退回常量 → 行为与 v0.10.1 一致，
    不需要任何迁移。"""
    assert probe_store.orphan_window(0.0) == probe_store.ORPHAN_AFTER_SECONDS
    assert probe_store.orphan_window() == probe_store.ORPHAN_AFTER_SECONDS
    led = probe_store.SessionLedger.from_dict({"session_id": "old", "running": []})
    # 哨兵是 -1 不是 0：0 是合法配额（「本场不探」），拿 0 当「没有快照」
    # 会让报表永远报不出它。
    assert led.running_timeout == -1.0
    assert (led.max_per_session, led.max_per_window) == (-1, -1)
    assert probe_store.orphan_window(led.running_timeout) == probe_store.ORPHAN_AFTER_SECONDS


def test_try_claim_snapshots_the_limits_it_actually_used() -> None:
    """GET /deep 没有请求体，读不到设置页。报表只能靠这份快照——
    否则会出现「执行用 7、界面显示 40」这种两个来源的老毛病。"""
    from dataclasses import replace

    from pen.config import default_limits

    lim = replace(default_limits(), probe_timeout_s=90.0,
                  probe_max_per_session=3, probe_max_per_window=7)
    pid = probe_store.try_claim("snap1", "h", 0, lim)
    assert pid
    led = probe_store.load("snap1")
    assert led.running_timeout == 90.0
    assert (led.max_per_session, led.max_per_window) == (3, 7)
    rep = probe_store.budget("snap1")
    assert (rep["max"], rep["window_max"]) == (3, 7), "报表要报执行时用的那个数"


def test_budget_report_falls_back_when_there_is_no_snapshot() -> None:
    """还没探过的会话（try_claim 一次都没跑）报默认值，不报 0。"""
    rep = probe_store.budget("never-probed")
    assert rep["max"] == config.PROBE_MAX_PER_SESSION
    assert rep["window_max"] == config.PROBE_MAX_PER_WINDOW


def test_round_zero_round_trips_and_is_not_read_back_as_never() -> None:
    """`int(raw.get("last_probe_round") or -99)` 在值是 0 时会得 -99——
    第 0 轮（一场对话的第一轮）探过之后，落盘再读回来变成「从没探过」。

    这个字段死着的时候无害，v0.10.4 开始冷却要读它，就成了
    「第一轮之后冷却失效一次」。
    """
    pid = probe_store.try_claim("round0", "h", 0)
    assert pid
    assert probe_store.load("round0").last_probe_round == 0, "第 0 轮不能被读成 -99"
    probe_store.release("round0", pid)
    assert probe_store.load("round0").last_probe_round == 0

    led = probe_store.SessionLedger.from_dict({"session_id": "x"})
    assert led.last_probe_round == -99, "真的没有这个键时才回落 -99"
    led2 = probe_store.SessionLedger.from_dict({"session_id": "x", "last_probe_round": "垃圾"})
    assert led2.last_probe_round == -99, "脏数据也回落"


def test_a_quota_of_zero_is_reported_as_zero_not_as_the_default() -> None:
    """夹紧表明文允许把配额设成 0（「本场不探」/「本小时不探」）。
    `led.max or config.X` 会把合法的 0 吃掉——报表于是永远报不出它，
    读者调完只看到「深题突然不来了」，零解释。

    **这正是那几行注释想根治的病，v0.10.2 却在自己身上犯了一次。**
    """
    from dataclasses import replace

    from pen.config import default_limits

    lim = replace(default_limits(), probe_max_per_session=0, probe_max_per_window=0)
    pid = probe_store.try_claim("zeroq", "h", 0, lim)
    assert pid is None, "窗口配额 0 就该抢不到"
    # 手工写一份快照（真实场景下是上一次用非零配额探过之后读者改成了 0）
    led = probe_store.load("zeroq", "h")
    led.max_per_session = 0
    led.max_per_window = 0
    probe_store.save(led)
    rep = probe_store.budget("zeroq")
    assert (rep["max"], rep["window_max"]) == (0, 0), f"0 必须报成 0，实际 {rep}"


def test_refund_rolls_back_the_cooldown_too() -> None:
    """refund 的语义是「这次不算数」。只退次数不退冷却的话，一次连 LLM 都
    没打的失败照样冻住后面 N 轮——间隔拉到 20 时就是白冻 20 轮。"""
    pid1 = probe_store.try_claim("cool2", "h", 3)
    probe_store.release("cool2", pid1)
    assert probe_store.load("cool2").last_probe_round == 3

    pid2 = probe_store.try_claim("cool2", "h", 9)
    assert probe_store.load("cool2").last_probe_round == 9
    probe_store.release("cool2", pid2, refund=True)
    led = probe_store.load("cool2")
    assert led.last_probe_round == 3, "白失败的那次要把冷却退回上一次真探的轮号"
    assert led.probe_calls == 1, "次数也只该剩真探过的那一次"


def test_orphan_window_leaves_a_real_margin() -> None:
    """余量固定 30 秒太紧：那 900 秒里还要塞书架扫盘、正文摘录、相似度计算，
    而且 timeout 传给 httpx 是 per-read 的——慢吐字节的中转能让单次尝试
    远超它。撞破窗口就是 v0.10.2 要修的那件事本身。"""
    for timeout in (30.0, 90.0, 150.0, 300.0):
        window = probe_store.orphan_window(timeout)
        worst = timeout * 3 * 2
        assert window >= worst * 1.4, f"超时 {timeout} 时窗口 {window} 余量不足（最坏 {worst}）"


def test_quota_status_reports_the_configured_max() -> None:
    """它以前读模块常量——正是同文件 budget() 的注释点名批评的那个错。"""
    from dataclasses import replace

    from pen.config import default_limits

    lim = replace(default_limits(), probe_max_per_window=7)
    assert probe_store.quota_status("qs1", lim)["max"] == 7
    assert probe_store.quota_status("qs1")["max"] == config.PROBE_MAX_PER_WINDOW
