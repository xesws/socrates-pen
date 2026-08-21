"""v0.12.5：`SessionStore` 的内存淘汰。

读者：「我需要你去设计一个内存更新的机制，不然用户用插件的时候，
迟早会把自己的电脑搞爆。」

此前 `_items` / `_locks` 都是纯 append 的普通 dict，sidecar 是常驻进程——
读者一天划几百次词就是几百个 `PenSession` 外加几百把 `Lock` 永远挂着。
"""

from __future__ import annotations

import threading

import pytest

from pen import config
from pen.session import SessionStore


def _fill(store: SessionStore, n: int) -> list[str]:
    return [store.create("demo").session_id for _ in range(n)]


def test_memory_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_LIVE_SESSIONS", 4)
    store = SessionStore()
    _fill(store, 40)
    assert len(store._items) <= 4


def test_locks_shrink_with_items(monkeypatch: pytest.MonkeyPatch) -> None:
    """只淘 `_items` 会留下 `_locks` 这条更小但同样无界的泄漏——
    每个 session id 一把 Lock，永不回收。"""
    monkeypatch.setattr(config, "MAX_LIVE_SESSIONS", 4)
    store = SessionStore()
    for _ in range(40):
        # 建一场、锁一场——生产里就是这个次序（`_try_lock_session` 紧跟在
        # `STORE.get()` 后面），所以锁永远是给「此刻还在 _items 里」的 sid 造的。
        store.lock_for(store.create("demo").session_id)
    assert len(store._locks) <= 4


def test_the_oldest_goes_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_LIVE_SESSIONS", 3)
    store = SessionStore()
    a, b, c = _fill(store, 3)
    store.create("demo")  # 挤掉一个
    assert a not in store._items
    assert b in store._items and c in store._items


def test_touching_a_session_moves_it_out_of_the_firing_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LRU 不是 FIFO：刚读过的那场不该因为「建得早」被淘汰。
    读者反复问同一篇笔记走的就是这条。"""
    monkeypatch.setattr(config, "MAX_LIVE_SESSIONS", 3)
    store = SessionStore()
    a, b, c = _fill(store, 3)
    store.get(a)  # a 重新变成最新
    store.create("demo")
    assert a in store._items
    assert b not in store._items


def test_a_running_session_is_never_evicted(monkeypatch: pytest.MonkeyPatch) -> None:
    """踢掉正在跑的那场 = 「对话中途重开侧栏，上一条回复消失」。

    `/v1/chat` 是先 `STORE.get()` 拿引用、再长期持锁改它。`_items[sid]` 一旦被
    踢掉，别的请求 `get()` 会从磁盘**重建出第二个实例**，收尾的 `save()` 再拿
    第一个实例覆盖回去。
    """
    monkeypatch.setattr(config, "MAX_LIVE_SESSIONS", 3)
    store = SessionStore()
    busy = store.create("demo")
    got = store.try_lock(busy)
    assert got is not None
    lock, busy = got
    try:
        _fill(store, 40)
        assert busy.session_id in store._items, "正在跑的被挤出去了"
        # 而且必须是**同一个对象实例**，不是从磁盘重建的等价副本。
        assert store.get(busy.session_id) is busy
    finally:
        lock.release()


def test_over_the_cap_is_allowed_when_too_many_are_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读者拍板的那条取舍：宁可暂时超上限，也不能把持着锁的那场挤出去。
    在跑的比上限还多时，`_items` 就该真的超出去。"""
    monkeypatch.setattr(config, "MAX_LIVE_SESSIONS", 2)
    store = SessionStore()
    locks = []
    for _ in range(5):
        got = store.try_lock(store.create("demo"))
        assert got is not None
        locks.append(got[0])
    try:
        _fill(store, 20)
        assert len(store._items) >= 5, "在跑的被挤掉了"
    finally:
        for lock in locks:
            lock.release()


def test_the_running_session_is_evictable_again_once_it_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超上限只是**暂时**的。锁一放，下一次淘汰就该把它收回去，
    否则「绝不碰正在跑的」会变成一条永久豁免，泄漏照旧。"""
    monkeypatch.setattr(config, "MAX_LIVE_SESSIONS", 3)
    store = SessionStore()
    busy = store.create("demo")
    got = store.try_lock(busy)
    assert got is not None
    lock, busy = got
    _fill(store, 40)
    assert busy.session_id in store._items
    lock.release()
    _fill(store, 5)
    assert busy.session_id not in store._items
    assert len(store._items) <= 3


def test_get_returns_the_same_instance_not_a_disk_copy() -> None:
    store = SessionStore()
    sess = store.create("demo")
    assert store.get(sess.session_id) is sess


def test_two_threads_racing_on_a_cold_session_get_one_instance() -> None:
    """读盘不持锁，所以两条请求可能同时 miss。**必须返回先到的那个**——
    同一个 sid 有两个实例的话，两条请求各改各的，收尾的 save 互相覆盖。
    （这条竞态在加淘汰之前就存在。）"""
    store = SessionStore()
    sid = store.create("demo").session_id
    store._items.clear()  # 装成冷启动：磁盘上有，内存里没有
    got: list[object] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        got.append(store.get(sid))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(got) == 8
    assert all(x is got[0] for x in got), "同一个 sid 冒出了两个实例"


def test_concurrent_get_save_and_eviction_never_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「查长度 → 挑最旧 → pop」是 read-modify-write，不整体加锁会踩空 KeyError。
    FastAPI 的 def 端点跑在 40 线程的 threadpool 上。"""
    monkeypatch.setattr(config, "MAX_LIVE_SESSIONS", 4)
    store = SessionStore()
    seeds = _fill(store, 8)
    errors: list[BaseException] = []
    stop = threading.Event()

    def churn() -> None:
        try:
            while not stop.is_set():
                s = store.create("demo")
                store.save(s)
                for sid in seeds:
                    try:
                        store.save(store.get(sid))
                    except KeyError:
                        pass  # 被淘汰又读不回来是合法结局，不是崩
        except BaseException as exc:  # noqa: BLE001 — 就是要抓住任何一种
            errors.append(exc)

    threads = [threading.Thread(target=churn) for _ in range(8)]
    for t in threads:
        t.start()
    threading.Event().wait(0.4)
    stop.set()
    for t in threads:
        t.join(5)
    assert not errors, errors
    assert len(store._items) <= 4 + 8  # 上限 + 最多 8 条在飞


def test_try_lock_refuses_a_busy_session() -> None:
    store = SessionStore()
    sess = store.create("demo")
    first = store.try_lock(sess)
    assert first is not None
    assert store.try_lock(sess) is None, "同一场不许被两条请求同时改"
    first[0].release()
    second = store.try_lock(sess)
    assert second is not None
    second[0].release()


def test_try_lock_and_eviction_cannot_interleave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """抢锁必须和淘汰互斥。`lock_for()` 再 `acquire(False)` 的写法中间有一条缝，
    淘汰挤进来把 `_locks[sid]` 换掉，两个线程就各拿一把不同的锁进临界区。"""
    monkeypatch.setattr(config, "MAX_LIVE_SESSIONS", 2)
    store = SessionStore()
    sess = store.create("demo")
    held: list[threading.Lock] = []
    errors: list[str] = []

    def grab() -> None:
        got = store.try_lock(sess)
        if got is None:
            return
        lock, _live = got
        if held:
            errors.append("两个线程同时拿到了同一场的锁")
        held.append(lock)
        _fill(store, 6)  # 在临界区里制造淘汰压力
        held.pop()
        lock.release()

    threads = [threading.Thread(target=grab) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert not errors, errors


def test_a_lock_with_no_session_behind_it_is_collected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`lock_for()` 可以给一个 `_items` 里没有的 sid 造锁。那种孤儿锁
    也得收，否则 `_locks` 仍然是一条（更小的）无界泄漏。"""
    monkeypatch.setattr(config, "MAX_LIVE_SESSIONS", 2)
    store = SessionStore()
    for i in range(40):
        store.lock_for(f"ghost{i:04d}")
    _fill(store, 6)
    assert len(store._locks) <= 2 + 1


def test_eviction_does_not_touch_the_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """淘汰只放掉内存。磁盘上的快照是「7 天保留期」那一层的事，
    两件事各管各的——淘汰顺手删盘 = 读者关一次侧栏历史就没了。"""
    from pen.session import sessions_dir

    monkeypatch.setattr(config, "MAX_LIVE_SESSIONS", 2)
    store = SessionStore()
    sids = _fill(store, 20)
    assert len(store._items) <= 2
    on_disk = {p.stem for p in sessions_dir().glob("*.json")}
    assert set(sids) <= on_disk, "淘汰把磁盘上的会话也带走了"
    gone = sids[0]
    assert store.get(gone).session_id == gone, "淘汰之后该能从磁盘读回来"


def test_try_lock_pins_the_instance_back_into_the_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_evict()` 从最旧往最新扫、跳过在跑的——**在跑的多于上限时，
    连刚 `get()` 出来的那条都会被扫到**。

    生产里的次序是 `STORE.get(sid)` → `_try_lock_session(sess)`，两步之间
    别的线程完全可能触发一次淘汰。抢到锁的这一刻不把手里的实例按回 `_items`，
    下一个 `get(sid)` 就会从磁盘重建出**第二个实例**，两条请求各改各的，
    收尾的 `save()` 互相覆盖——一整轮对话凭空消失。
    """
    monkeypatch.setattr(config, "MAX_LIVE_SESSIONS", 2)
    store = SessionStore()
    sess = store.create("demo")
    busy = [store.try_lock(store.create("demo")) for _ in range(4)]
    assert all(b is not None for b in busy)
    try:
        # 在跑的（4 把）已经多于上限（2），这一场又是唯一没锁的 → 会被扫掉
        store.get(sess.session_id)
        store.create("demo")
        assert sess.session_id not in store._items, "前提不成立，这条测不到东西"
        got = store.try_lock(sess)
        assert got is not None
        lock, live = got
        try:
            assert store.get(sess.session_id) is live, "冒出了第二个实例"
        finally:
            lock.release()
    finally:
        for b in busy:
            if b is not None:
                b[0].release()


def test_try_lock_hands_back_the_live_instance_not_the_stale_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """光「钉回去」只关掉了一半（v0.12.6 补上另一半）。

    生产次序是 `get()` → `_meta_or_404` / `load_index`（真磁盘 I/O，毫秒级）
    → `try_lock()`。中间那几毫秒里手上这个实例可能已经过时了：

        T1 get(S) 拿到 X → 淘汰把 X 扫出表 → T2 get(S) 从盘上重建 Y，
        锁上、跑完**一整轮**、save(Y)、放锁 → T1 这才 try_lock(X)，
        锁是空的，抢到 → 收尾 save(X) 把 T2 那一轮整个盖掉。

    「一整轮对话凭空消失」——v0.12.5 的 commit message 说修了它，实际只修了
    「同时进临界区」那一半。
    """
    monkeypatch.setattr(config, "MAX_LIVE_SESSIONS", 2)
    store = SessionStore()
    sid = store.create("demo").session_id
    busy = [store.try_lock(store.create("demo")) for _ in range(4)]
    try:
        stale = store.get(sid)  # T1 手里这个
        store.create("demo")  # 在跑的多于上限 → 淘汰连它一起扫
        assert sid not in store._items, "前提不成立，这条测不到东西"
        fresh = store.get(sid)  # T2 从盘上重建
        assert fresh is not stale, "前提不成立：这俩该是两个实例"
        fresh.messages.append({"role": "assistant", "content": "T2 这一整轮"})
        store.save(fresh)
        got = store.try_lock(stale)  # T1 这才抢到锁
        assert got is not None
        lock, live = got
        try:
            assert any(
                m.get("content") == "T2 这一整轮" for m in live.messages
            ), "拿到的是陈旧实例，收尾 save 会把 T2 那一轮整个盖掉"
        finally:
            lock.release()
    finally:
        for b in busy:
            if b is not None:
                b[0].release()


def test_try_lock_falls_back_to_disk_when_the_table_has_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """更窄的一档：别人跑完那一轮之后，那个实例**也**被淘汰扫走了。

    表里没得认，就得回盘上现读。此刻锁在我们手上，谁也不能再改这一场，
    所以磁盘那份就是权威——不读盘的话又退回去用陈旧实例了。
    """
    monkeypatch.setattr(config, "MAX_LIVE_SESSIONS", 2)
    store = SessionStore()
    sid = store.create("demo").session_id
    busy = [store.try_lock(store.create("demo")) for _ in range(4)]
    try:
        stale = store.get(sid)
        fresh = store.get(sid)
        fresh.messages.append({"role": "assistant", "content": "别人那一轮"})
        store.save(fresh)
        store._items.pop(sid, None)  # 收工之后它也被扫走了
        got = store.try_lock(stale)
        assert got is not None
        lock, live = got
        try:
            assert any(m.get("content") == "别人那一轮" for m in live.messages)
        finally:
            lock.release()
    finally:
        for b in busy:
            if b is not None:
                b[0].release()


def test_try_lock_still_works_when_the_file_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保留期清理正好插在 `get()` 和 `try_lock()` 之间。

    盘上没了就用手上这个，让读者这一枪照样能跑完——强过甩他一个 404。
    """
    from pen.session import sessions_dir

    monkeypatch.setattr(config, "MAX_LIVE_SESSIONS", 2)
    store = SessionStore()
    sess = store.create("demo")
    sid = sess.session_id
    store._items.pop(sid, None)
    (sessions_dir() / f"{sid}.json").unlink()
    got = store.try_lock(sess)
    assert got is not None
    lock, live = got
    try:
        assert live is sess
    finally:
        lock.release()


def test_the_endpoint_helper_goes_through_try_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端点用的是 `_try_lock_session`，它必须走 `STORE.try_lock`。

    退回 `lock_for()` + `acquire(False)` 的老写法就会红：那两步之间淘汰能挤进来，
    而且不钉回实例——`/v1/chat` 抢到锁之后，别的请求 `get()` 拿到的就是从磁盘
    重建的第二个实例。上面那条 `pins_the_instance_back` 只测 store 本身，
    测不到端点这一层用没用对方法。
    """
    from pen.app import _try_lock_session
    from pen.session import STORE

    monkeypatch.setattr(config, "MAX_LIVE_SESSIONS", 2)
    sess = STORE.create("demo")
    busy = [STORE.try_lock(STORE.create("demo")) for _ in range(4)]
    assert all(b is not None for b in busy)
    try:
        STORE.create("demo")  # 触发淘汰，把没锁的 sess 扫掉
        assert sess.session_id not in STORE._items
        lock, live = _try_lock_session(sess)
        try:
            assert STORE.get(sess.session_id) is live, "端点这一层冒出了第二个实例"
        finally:
            lock.release()
    finally:
        for b in busy:
            if b is not None:
                b[0].release()
