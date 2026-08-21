"""深挖问题的池子与投递游标。落在 .pen/probes/sessions/。

这里的所有写入都发生在**后台探索线程**里，所以它刻意不碰 PenSession——
那个对象由请求线程独占，两边都 save 会抢同一个 to_dict() 快照，后写的赢，
丢掉的是一整轮对话。深题只进这个 store，要拼给前端时在 app 层现拼。

投递语义是「至少一次」：服务端返回 seq > since 的全部成熟项，前端把 since
推到 max(seq)。丢一个响应不会丢问题，重复请求同一个 since 返回同样的东西。
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pen import config
from pen import meter
from pen.questions import normalize_qkey

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")

# 一条 later 问题等多少轮还没成熟就丢掉。真实会话 5~15 轮，
# 太短则总也等不到，太长则读者早走远了。
ITEM_TTL_TURNS = 6
# 进程被杀时 running 会留在盘上。不回收的话 try_claim 永远抢不到坑，
# 前端也会对着一个永远不会完成的幽灵轮询到超时。
#
# **这是下限，不是窗口本身**（v0.10.2）。真窗口由 orphan_window() 从本次的
# 超时派生：300 秒连一次正常的跨书探索都盖不住——deeppoll.ts 自己记着实测
# 351 秒，而最坏是 timeout 150 × SDK 重试 3 次 × explore 两次调用 = 900 秒。
# 窗口短于真实耗时 = 在真线程还活着时清空 running = try_claim 能再起一个
# = **同一场对话两份深挖并行，各花各的钱**。
ORPHAN_AFTER_SECONDS = 300.0
# 一次最多放出几条。深题是「跳出来」的，一轮蹦两条就成噪音了。
MAX_RELEASE_PER_TURN = 1
# 同时可见几条 —— 这是**前端**的显示上限（PenView.mergeDeep 执行）。
# 别拿它在服务端当放行闸门：visible_count 只增不减，抛满之后 room 恒为 0，
# 后面每一条 pending 都会在进 _ripe 之前被跳过，池子再也不衰减。
MAX_VISIBLE = 2

_LOCK = threading.RLock()


def probes_dir() -> Path:
    dest = config.PEN_DIR / "probes" / "sessions"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _path(session_id: str) -> Path:
    if not _SAFE_ID.match(session_id):
        raise ValueError(f"非法 session_id：{session_id!r}")
    return probes_dir() / f"{session_id}.json"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 临时名要带进程和随机后缀。固定用 <path>.tmp 的话，两个 sidecar 指同一个
    # PEN_HOME（同一 checkout 起两次，或旧进程没死透）会互相把对方的临时文件
    # replace 掉，抛出未捕获的 FileNotFoundError。
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DeepQuestion:
    id: str
    text: str
    why: str = ""
    axis: str = ""
    grounding: str = "book"          # book | open
    anchors: list[dict[str, Any]] = field(default_factory=list)
    timing: str = "later"            # now | later（确定性闸门只能把 now 降成 later）
    target: str = ""                 # later 时挂在哪一关
    depth: int = 0
    atom: str = ""                   # 探索那一刻的 atom_key
    born_round: int = 0
    seq: int = 0
    state: str = "pending"           # pending | shown | clicked | dropped
    created_at: str = field(default_factory=_now)

    def to_chip(self) -> dict[str, Any]:
        return {"id": self.id, "kind": "deep", "text": self.text, "why": self.why}


@dataclass
class SessionLedger:
    session_id: str
    handbook_id: str = ""
    seq: int = 0
    probe_calls: int = 0
    last_probe_round: int = -99
    pool: list[DeepQuestion] = field(default_factory=list)
    running: list[str] = field(default_factory=list)
    running_since: str = ""
    asked_qkeys: list[str] = field(default_factory=list)
    # 本会话深挖累计花掉的 token。**这是读者唯一看不见的花钱路径**，
    # 在 v0.10.0 之前一个 token 都没记。它落在账本上而不是 PenSession 上，
    # 理由见模块开头那段红线。
    spend: dict[str, int] = field(default_factory=meter.blank)
    # try_claim 那一刻生效的上限，用来回答两个问题：
    #   running_timeout → 孤儿窗口该多大（见 orphan_window）
    #   max_*           → GET /deep 该报多少（那个端点没有请求体，读不到设置）
    # 老账本没有这三个字段（=0）→ 全部退回 config 现值 → 行为与 v0.10.1 一致。
    # **哨兵是 -1 不是 0**：夹紧表明文允许把配额设成 0（「本场不探」/
    # 「本小时不探」），而 `x or 默认值` 会把合法的 0 吃掉——报表于是永远
    # 报不出 0，读者调完只看到「深题突然不来了」，零解释。
    # 上限恒 ≥ 0，所以 -1 是干净的「还没探过」。
    # try_claim 覆盖 last_probe_round 之前的旧值。refund 要退冷却就得知道
    # 「改之前是多少」——只有账本记得住。
    prev_probe_round: int = -99
    running_timeout: float = -1.0
    max_per_session: int = -1
    max_per_window: int = -1

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "handbook_id": self.handbook_id,
            "seq": self.seq,
            "probe_calls": self.probe_calls,
            "last_probe_round": self.last_probe_round,
            "pool": [asdict(q) for q in self.pool],
            "running": list(self.running),
            "running_since": self.running_since,
            "asked_qkeys": list(self.asked_qkeys)[-60:],
            "spend": dict(self.spend),
            "prev_probe_round": self.prev_probe_round,
            "running_timeout": self.running_timeout,
            "max_per_session": self.max_per_session,
            "max_per_window": self.max_per_window,
            "updated_at": _now(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SessionLedger:
        pool = []
        for q in raw.get("pool") or []:
            if not isinstance(q, dict):
                continue
            known = {k: v for k, v in q.items() if k in DeepQuestion.__dataclass_fields__}
            try:
                pool.append(DeepQuestion(**known))
            except TypeError:
                continue
        return cls(
            session_id=str(raw.get("session_id") or ""),
            handbook_id=str(raw.get("handbook_id") or ""),
            seq=int(raw.get("seq") or 0),
            probe_calls=int(raw.get("probe_calls") or 0),
            # `x or -99` 在 x == 0 时会得 -99——第 0 轮（也就是一场对话的第一轮）
            # 探过之后，落盘再读回来就变成「从没探过」。字段是死的时候无害，
            # v0.10.4 开始冷却要读它，就成了「第一轮之后冷却失效一次」。
            last_probe_round=(
                int(raw["last_probe_round"])
                if isinstance(raw.get("last_probe_round"), (int, float))
                else -99
            ),
            pool=pool,
            spend=meter.coerce(raw.get("spend")),
            prev_probe_round=(
                int(raw["prev_probe_round"])
                if isinstance(raw.get("prev_probe_round"), (int, float))
                else -99
            ),
            running_timeout=_snap(raw.get("running_timeout")),
            max_per_session=int(_snap(raw.get("max_per_session"))),
            max_per_window=int(_snap(raw.get("max_per_window"))),
            running=[str(x) for x in raw.get("running") or []],
            running_since=str(raw.get("running_since") or ""),
            asked_qkeys=[str(x) for x in raw.get("asked_qkeys") or []],
        )

    def pending_count(self) -> int:
        return sum(1 for q in self.pool if q.state == "pending")

    def visible_count(self) -> int:
        return sum(1 for q in self.pool if q.state in ("shown", "clicked"))


def load(session_id: str, handbook_id: str = "") -> SessionLedger:
    try:
        dest = _path(session_id)
    except ValueError:
        return SessionLedger(session_id=session_id, handbook_id=handbook_id)
    if not dest.is_file():
        return SessionLedger(session_id=session_id, handbook_id=handbook_id)
    try:
        raw = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SessionLedger(session_id=session_id, handbook_id=handbook_id)
    if not isinstance(raw, dict):
        return SessionLedger(session_id=session_id, handbook_id=handbook_id)
    led = SessionLedger.from_dict(raw)
    led.session_id = session_id
    if handbook_id:
        led.handbook_id = handbook_id
    _reap_orphan(led)
    return led


def _num(v: Any) -> float:
    """盘上读回来的数字。脏数据退化成 0，不让一份坏 JSON 顶掉整个账本。"""
    try:
        n = float(v)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return n if n > 0 and n == n and n != float("inf") else 0.0


def _snap(v: Any) -> float:
    """上限快照。**0 是合法值**（「本场不探」），所以缺失的哨兵是 -1。
    脏数据、负数、NaN、inf 一律退回 -1 = 还没探过。"""
    try:
        n = float(v)
    except (TypeError, ValueError, OverflowError):
        return -1.0
    return n if n >= 0 and n == n and n != float("inf") else -1.0


def orphan_window(timeout_s: float = 0.0) -> float:
    """孤儿回收的窗口。**必须大于一次探索的最坏耗时。**

    最坏耗时 = 单次超时 × (1 + SDK 默认 max_retries=2) × explore 的两次调用。
    窗口短于它 = 在真线程还活着时清空 running = try_claim 能再起一个 =
    同一场对话两份深挖并行。这不是理论：ORPHAN_AFTER_SECONDS=300 而
    deeppoll.ts 记着实测 351 秒，v0.10.2 之前一直在发生。

    timeout_s=0（老账本没有这个字段）时退回常量，行为与 v0.10.1 一致。
    """
    if timeout_s <= 0:
        return ORPHAN_AFTER_SECONDS
    # 余量取「最坏耗时的一半」，不是固定 30 秒。900 秒里还要塞书架扫盘
    # （登记表 + 逐本读 400 行）、正文摘录、相似度计算；更要紧的是那个
    # timeout 传给 httpx 是 **per-read** 的——一个慢吐字节的中转能让单次
    # 尝试远超它。撞破窗口 = 真线程还活着就清空 running = 同一场对话两份
    # 深挖并行，也就是 v0.10.2 要修的那件事本身。
    worst = timeout_s * 3 * 2
    return max(ORPHAN_AFTER_SECONDS, worst * 1.5)


def _reap_orphan(led: SessionLedger) -> None:
    """就地清掉进程被杀时留下的 running。不清的话前端会白轮询到超时。"""
    if not led.running or not led.running_since:
        return
    try:
        started = datetime.fromisoformat(led.running_since)
    except ValueError:
        led.running = []
        led.running_since = ""
        return
    if (datetime.now(timezone.utc) - started).total_seconds() > orphan_window(
        led.running_timeout
    ):
        led.running = []
        led.running_since = ""


def save(led: SessionLedger) -> None:
    config.ensure_pen_dirs()
    _atomic_write(_path(led.session_id), json.dumps(led.to_dict(), ensure_ascii=False))


def _quota_path(handbook_id: str) -> Path:
    if not _SAFE_ID.match(handbook_id):
        raise ValueError(f"非法 handbook_id：{handbook_id!r}")
    dest = config.PEN_DIR / "probes" / handbook_id
    dest.mkdir(parents=True, exist_ok=True)
    return dest / "quota.json"


def _window() -> str:
    """配额窗口。读者选的是「每小时 40 次」——先前实现成了每天，严了 24 倍。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")


def quota_count(handbook_id: str) -> int:
    """本小时这本书已经探了几次。跨会话累计——每会话 8 次挡不住
    「开一堆新会话」这种用法，窗口配额才是真正的成本上限。"""
    try:
        dest = _quota_path(handbook_id)
    except ValueError:
        return 0
    if not dest.is_file():
        return 0
    try:
        raw = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(raw, dict) or raw.get("hour") != _window():
        return 0
    try:
        return int(raw.get("count") or 0)
    except (TypeError, ValueError):
        return 0


def _write_quota(handbook_id: str, count: int) -> None:
    try:
        dest = _quota_path(handbook_id)
    except ValueError:
        return
    _atomic_write(dest, json.dumps({"hour": _window(), "count": max(0, count)}, ensure_ascii=False))


def _bump_quota(handbook_id: str) -> None:
    _write_quota(handbook_id, quota_count(handbook_id) + 1)


def quota_status(
    handbook_id: str, limits: config.RuntimeLimits | None = None
) -> dict[str, Any]:
    """给设置页看的：本小时用了多少。读者以前只能看到「深题突然不来了」。

    以前它读模块常量——正是同文件 budget() 的注释点名批评的那个错，
    而且一直没有生产调用方（写的时候设置页还没有统计块）。v0.10.10 的
    设置页统计要用它，所以修对而不是删掉。
    """
    lim = limits or config.default_limits()
    return {
        "used": quota_count(handbook_id),
        "max": lim.probe_max_per_window,
        "window": "hour",
    }


def try_claim(
    session_id: str,
    handbook_id: str,
    now_round: int,
    limits: config.RuntimeLimits | None = None,
) -> str | None:
    """占坑。同一会话同时只允许一个 probe 在跑——抢不到就跳过，不排队：
    排队意味着上下文已经过期了还要再花一次钱。"""
    lim = limits or config.default_limits()
    with _LOCK:
        led = load(session_id, handbook_id)
        if led.running:
            return None
        if handbook_id and quota_count(handbook_id) >= lim.probe_max_per_window:
            return None
        pid = uuid.uuid4().hex[:12]
        led.running = [pid]
        led.running_since = _now()
        # 上限快照：孤儿窗口和 GET /deep 的报表都靠它。
        led.running_timeout = float(lim.probe_timeout_s)
        led.max_per_session = int(lim.probe_max_per_session)
        led.max_per_window = int(lim.probe_max_per_window)
        led.probe_calls += 1
        led.prev_probe_round = led.last_probe_round
        led.last_probe_round = now_round
        save(led)
        if handbook_id:
            _bump_quota(handbook_id)
        return pid


def release(
    session_id: str,
    probe_id: str,
    *,
    refund: bool = False,
    spend: dict[str, int] | None = None,
) -> None:
    """放掉坑位。refund=True 时连记账一起退——一次 LLM 都没打就失败了
    （起线程失败、抢不到信号量、书架读盘炸了），不该扣读者的配额。
    失败越多能用的次数越少，那是反的。

    spend 是**已经花掉**的 token：探索炸在第二枪上时，第一枪的钱早花了，
    不记就永远对不上账。它和 refund 不矛盾——refund 退的是「次数配额」，
    spend 记的是「已发生的花销」，refund 那条路上 spend 恒为空。
    """
    with _LOCK:
        led = load(session_id)
        if spend:
            led.spend = meter.merge(led.spend, spend)
        led.running = [x for x in led.running if x != probe_id]
        if not led.running:
            led.running_since = ""
        if refund:
            # refund 的语义是「这次不算数」，那就退干净。只退次数不退冷却的话，
            # 一次连 LLM 都没打的失败照样冻住后面 N 轮——probe_every_n_rounds
            # 拉到 20 时就是冻 20 轮。
            led.last_probe_round = led.prev_probe_round
            if led.probe_calls > 0:
                led.probe_calls -= 1
                if led.handbook_id:
                    _write_quota(led.handbook_id, quota_count(led.handbook_id) - 1)
        save(led)


def add_questions(
    session_id: str,
    probe_id: str,
    items: list[DeepQuestion],
    spend: dict[str, int] | None = None,
) -> None:
    """探索线程唯一的写入口。

    深挖的账**搭这次 save 一起写，不新开写入口**：多一个 load-modify-save
    就多一个和这里的竞态窗口（_LOCK 是 RLock，两次调用之间别的线程能插进来）。
    于是探索线程的终态仍然是**恰好一次 save**。

    顺带一个前端依赖的保证：spend 的更新和 running 的清空落在同一次 save 里，
    所以轮询看到 running: [] 的那一拍，读到的 spend 必然是终值，
    不会「停早了拿到半截数」。
    """
    with _LOCK:
        led = load(session_id)
        if spend:
            led.spend = meter.merge(led.spend, spend)
        known = {normalize_qkey(q.text) for q in led.pool} | set(led.asked_qkeys)
        for q in items:
            key = normalize_qkey(q.text)
            if not key or key in known:
                continue
            known.add(key)
            led.seq += 1
            q.seq = led.seq
            led.pool.append(q)
        led.running = [x for x in led.running if x != probe_id]
        if not led.running:
            led.running_since = ""
        save(led)


def budget(session_id: str) -> dict[str, int]:
    led = load(session_id)
    return {
        "used": led.probe_calls,
        # 报的必须是**执行时用的那个数**。读模块常量的话，设置页把每小时配额
        # 调成 7 之后，界面会一直显示 40——两个来源，正是本仓踩过三次的形状。
        # 用 `>= 0` 判而不是 `or`：0 是合法配额，`or` 会把它吃掉。
        "max": led.max_per_session if led.max_per_session >= 0 else config.PROBE_MAX_PER_SESSION,
        "window_used": quota_count(led.handbook_id) if led.handbook_id else 0,
        "window_max": led.max_per_window if led.max_per_window >= 0 else config.PROBE_MAX_PER_WINDOW,
    }


def _ripe(q: DeepQuestion, *, atom: str, level: str, now_round: int) -> bool:
    """成熟度闸门。纯确定性，零 LLM。"""
    if q.state != "pending":
        return False
    if now_round - q.born_round > ITEM_TTL_TURNS:
        q.state = "dropped"
        return False
    if q.timing == "now":
        # 读者还在生它的那块地上，或者就是刚生出来的这一轮
        return q.atom == atom or now_round <= q.born_round
    # later：等读者自己走到那一关，或者等够两轮
    if q.target and level and q.target == level:
        # target 只是模型填的一个字符串，别的书也有「Level 1」。跨书题里它可能
        # 抄的是别本的关名，于是读者走到**当前书**的 Level 1 时，一条讲别人家
        # Level 1 的题就弹出来了。有跨书锚时要求本册也有一条锚落在这一关，
        # 才算实锤。没有跨书锚的题行为完全不变。
        cross = any(str(a.get("book") or "").strip() for a in q.anchors)
        if not cross:
            return True
        return any(
            not str(a.get("book") or "").strip() and str(a.get("level") or "") == level
            for a in q.anchors
        )
    return now_round - q.born_round >= 2


def inbox(
    session_id: str,
    *,
    since: int = 0,
    atom: str = "",
    level: str = "",
    now_round: int = 0,
) -> dict[str, Any]:
    """给前端的收件箱。只读，不碰会话锁。"""
    with _LOCK:
        led = load(session_id)
        out: list[dict[str, Any]] = []
        seqs: list[int] = []
        touched = False
        fresh = 0
        for q in sorted(led.pool, key=lambda x: x.seq):
            if q.state in ("clicked", "dropped"):
                continue
            # 过期判定必须无条件跑在最前面。以前它藏在 _ripe 里，而放行名额
            # 用完后根本走不到 _ripe——池子于是永远不会衰减，pending 一路堆到
            # PROBE_PENDING_CAP，从第四轮起 should_probe 永久返回 backlog-full。
            if now_round - q.born_round > ITEM_TTL_TURNS:
                if q.state == "pending":
                    q.state = "dropped"
                    touched = True
                    continue
            if q.seq <= since:
                continue
            if q.state == "shown":
                # 已经放行过、但前端还没把游标推过去：照样再给一遍。
                # 投递必须是「至少一次」——丢一个响应不该丢掉一条问题。
                out.append(q.to_chip())
                seqs.append(q.seq)
                continue
            if fresh >= MAX_RELEASE_PER_TURN:
                continue
            was = q.state
            if not _ripe(q, atom=atom, level=level, now_round=now_round):
                touched = touched or q.state != was
                continue
            out.append(q.to_chip())
            seqs.append(q.seq)
            q.state = "shown"
            fresh += 1
            touched = True
        # 只能推到**本次真正投递出去**的最大 seq。推到池子最大 seq 会把没投递的
        # 高 seq 项永久吞掉：一次探索产两条、每轮只放一条，第二条就再也够不着了，
        # later 通道整条死掉。
        cursor = max([since, *seqs]) if seqs else since
        if touched:
            save(led)
        return {
            "session_id": session_id,
            "items": out,
            "cursor": cursor,
            "running": list(led.running),
            # 深挖累计花掉的 token。和 budget 是两件事：budget 数「还能探几次」，
            # 这里数「已经烧了多少」。前端把它加进状态行第三格。
            "spend": dict(led.spend),
            "budget": {
                "used": led.probe_calls,
                # 和 budget() 一样，报执行时用的那个数，不是模块常量；
                # 也一样用 `>= 0` 判，0 是合法配额。
                "max": (
                    led.max_per_session
                    if led.max_per_session >= 0
                    else config.PROBE_MAX_PER_SESSION
                ),
                # 窗口配额是跨会话的那道闸。不报出去的话，读者只会看到
                # 「深题突然不来了」，没有任何解释。
                "window_used": quota_count(led.handbook_id) if led.handbook_id else 0,
                "window_max": (
                    led.max_per_window
                    if led.max_per_window >= 0
                    else config.PROBE_MAX_PER_WINDOW
                ),
            },
        }


def mark_clicked(session_id: str, text: str) -> DeepQuestion | None:
    """读者点了幽灵按钮——前端以 chip="free" 把原文当 user_text 发回来，
    在这里精确匹配即可。这是整个功能唯一的真实质量反馈信号。
    顺带认出 open 题，好在回答时注入诚实指令。"""
    key = normalize_qkey(text)
    if not key:
        return None
    with _LOCK:
        led = load(session_id)
        for q in led.pool:
            if normalize_qkey(q.text) == key:
                q.state = "clicked"
                if key not in led.asked_qkeys:
                    led.asked_qkeys.append(key)
                save(led)
                return q
    return None


def asked(session_id: str) -> list[str]:
    led = load(session_id)
    return [q.text for q in led.pool if q.state in ("shown", "clicked")][-20:]
