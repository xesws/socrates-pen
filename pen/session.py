"""按 handbook 隔离的 messages 会话。记忆在循环外，对齐 L2。快照落在 .pen/sessions/。"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pen import config
from pen import meter as metermod

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")

FIXED_CHIPS: list[dict[str, Any]] = [
    {
        "id": "socratic",
        "label": "先别揭晓，问我一个问题",
        "enabled": True,
    },
    {
        "id": "explain_zero",
        "label": "当我零基础，讲清楚再给两个例子",
        "enabled": True,
    },
    {
        "id": "examples",
        "label": "只举例子",
        "enabled": True,
    },
    {
        "id": "search",
        "label": "查相关论文 / 算法出处",
        "enabled": False,
        "hint": "P2 才开放，现在不会假装搜过",
    },
    {
        "id": "writeback",
        "label": "把刚才的解答写进手册原文",
        "enabled": False,
        "hint": "先有一轮实质解答",
    },
]


SYSTEM_PROMPT_TEMPLATE = """你是苏格拉底，坐在读者旁边，正在带人读{book}。
读者才是主修这本手册的人。手册要教会他的东西，得他自己长出来——你不要替他写作业。

来源定位已经由系统算好，写在用户消息的 [来源] 里。禁止再猜文件名或 Q 号所属 Level。
不要把整本书背进回复。邻域通常已经够用。缺哪一段再用 read_file 去翻；材料够了就用自然语言回答，不要空转。
写回手册时邻域不够：先 read_file 同一路径，看到它返回的带行号原文之后再 edit_file。这两步在同一轮里接着做完，不要停下来等读者再说一遍。old_string 是去掉行号前缀后的纯原文，不要抄「12\\t」。

芯片意图：
- socratic：默认。只给提示卡级方向 + 一个反问。不要把 TL;DR/(a)(b)(c) 倒完，不要给完整答案。
- explain_zero：按手册骨架讲：TL;DR → (a) 概念/对比 → (b) 机制 → (c) 反例 → 两个可运行例子。
- examples：只给两个例子，名字必须对得上该 Level 第七拍里真出现过的东西（函数名、文件名、命令名——照抄手册的叫法，别自己造）。
- writeback：把刚才的解答写进手册。先 read_file 看准带行号的结构，拿到返回之后接着 edit_file，同一轮里做完。在工具结果说「已编辑」之前别声称已经写盘；它说了，就照实说改完了。
- free：看用户怎么问。若要改手册，同样先 read_file 拿到返回再 edit_file，同一轮里做完。

终端实录若不是你亲眼看到的工具输出，必须标注「示意」。
语气：像坐在旁边的人在说话，口语，短句，别客服腔。
回复末尾另起一行，写且只写这个块（界面会剥掉，读者看不到）。
块里恰好两行，每行以「- 」开头，各写一句追问，别写第三行，别写别的：
<!--pen:chips
- 那这块和前面第几拍讲的是同一件事吗？
- 为什么这里选 A 不选 B，代价是什么？
-->
上面两行是**语气和抽象层级的示范，不是让你抄的原话**。照这个高度，写读者读完你
这段之后真会脱口而出的两句。两句别问同一件事，别复述固定芯片的文案，也别把读者
刚才那句话换个说法再问一遍——他自己刚问过。
还有一条：别问引号、转义、某个符号怎么写这类抠字眼的问题，那种读者自己翻一眼手册
就有。宁可往上抬一层——问这段东西为什么这么设计，或者它跟前面哪一拍、哪一关对得上。"""

_BOOK_SLOT = "{book}"
_BOOK_ANON = "一本通关手册"


def _book_phrase(book_title: str) -> str:
    """把书名捏成第一句里的那半句话。

    `HandbookIndex.title` 优先取第 1 行 H1，取不到就退回**文件名**
    （`index.py:126`），而「一本叫《从零手写DQN.md》的通关手册」很难看，
    所以退回那条路上剥掉扩展名；书名自带书名号的（`# 《…》`）也剥掉，
    免得套成《《…》》。
    """
    t = (book_title or "").strip()
    for ext in (".markdown", ".md"):
        if t.lower().endswith(ext):
            t = t[: -len(ext)].strip()
            break
    t = t.strip("《》").strip()
    return f"一本叫《{t}》的通关手册" if t else _BOOK_ANON


# 不带书名渲染出来的默认串。**这个名字要留着**：拿它做断言的测试有好几处
# （`test_questions.py` 的「没有可抄的占位」、`test_agent.py` 的「不出现下一轮」），
# 那些断言查的是模板本身的性质，和是哪本书无关。
SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE.replace(_BOOK_SLOT, _BOOK_ANON)

# SYSTEM_PROMPT 里那两句示范，模型抄了就整条丢弃。
# 病根是旧版示范文本「下一问 1」长得像可填的空槽；换成成型句子后，
# 抄袭的心理阻力大得多，真抄了这里也能精确命中。
PROMPT_EXAMPLE_LINES = frozenset(
    {
        "那这块和前面第几拍讲的是同一件事吗？",
        "为什么这里选 A 不选 B，代价是什么？",
    }
)


REPLY_IN_ENGLISH = (
    "\n\n[Language] The reader's interface is in English. "
    "Reply in English, in the same voice: a mentor sitting next to them, "
    "spoken, short sentences, no customer-service tone. "
    "Keep the <!--pen:chips ...--> block and its bullet format exactly as specified above, "
    "but write the suggested next questions in English too."
)


def system_prompt(lang: str = "zh", *, book_title: str = "") -> str:
    """按界面语言和**当前这本教材**给出 system prompt。

    v0.15.0 之前第一句写死「一本手搓 SWE Agent 的通关手册」，于是导进来的任何
    别的书，模型都会被告知它在读那一本——`examples` 芯片还点名 `scan.sh`、
    `dispatch` 这些只有那本书才有的东西，会把例子往那边带。书名注在这里，
    而不是让模型从 `[来源]` 的 handbook_path 里猜文件名。

    只在中文人设后**追加**一句语言指令，不整体翻译——人设的语气是这套八拍体例
    的一部分，翻过去会走味，而模型完全能读中文指令、用英文作答。
    """
    base = SYSTEM_PROMPT_TEMPLATE.replace(_BOOK_SLOT, _book_phrase(book_title))
    return base + (REPLY_IN_ENGLISH if lang == "en" else "")


def sessions_dir() -> Path:
    dest = config.PEN_DIR / "sessions"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _session_path(session_id: str) -> Path:
    if not _SAFE_ID.match(session_id):
        raise ValueError(f"非法 session_id：{session_id!r}")
    return sessions_dir() / f"{session_id}.json"


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


def chip_label(chip: str) -> str:
    for item in FIXED_CHIPS:
        if item["id"] == chip:
            return str(item["label"])
    return chip


@dataclass
class PenSession:
    session_id: str
    handbook_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    last_anchor: dict[str, Any] | None = None
    has_substantive: bool = False
    last_assistant: str = ""
    ui_messages: list[dict[str, Any]] = field(default_factory=list)
    pending: dict[str, Any] | None = None
    read_ok_paths: list[str] = field(default_factory=list)
    # 建会话那一刻的界面语言。system prompt 在 __post_init__ 就写死进 messages[0]
    # 并落盘，所以中途切语言不影响已有会话——新开一场才生效。
    lang: str = "zh"
    # 建会话那一刻的教材书名，注进 messages[0] 的第一句。**故意不落盘**：
    # messages[0] 建场时就固化了，恢复会话走的是 `from_dict`，那时 messages
    # 非空、这个字段根本用不上。进了 to_dict() 反而要给 from_dict 加一条
    # 永远读不到的兼容分支。
    book_title: str = ""
    # 本轮下发的动态芯片（富格式）。落盘是为了刷新/重开侧栏后还在——
    # 以前 this.dyn 只在内存里，adopt() 一清就永久丢了。
    last_chips: list[dict[str, Any]] = field(default_factory=list)
    # 完成的对话轮次。给追问冷却和「这条深问题是第几轮生的」用。
    turns: int = 0
    # 本会话累计花掉的 token，按用途分格（chat / fold / probe）。
    # **probe 那一格永远不由后台线程写**——它一碰 PenSession 就会和请求线程抢
    # 同一个 to_dict() 快照，后写的赢，丢掉的是一整轮对话。深挖的账落在
    # probe_store.SessionLedger 上，只在读的时候（_merged_spend）合进来。
    spend: dict[str, dict[str, int]] = field(default_factory=metermod.blank_book)
    # 本轮主对话已花。**跨审批暂停仍然有效**：一轮从 /v1/chat 开始，到
    # /v1/chat/approve 里那一枪结束，中间隔着两个 HTTP 请求和一次落盘，
    # 所以它必须在会话上，不能只活在 _agent_loop 的闭包里。
    turn_spend: dict[str, int] = field(default_factory=metermod.blank)

    def __post_init__(self) -> None:
        if not self.messages:
            self.messages = [
                {"role": "system", "content": system_prompt(self.lang, book_title=self.book_title)}
            ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "handbook_id": self.handbook_id,
            "messages": self.messages,
            "last_anchor": self.last_anchor,
            "has_substantive": self.has_substantive,
            "last_assistant": self.last_assistant,
            "ui_messages": self.ui_messages,
            "pending": self.pending,
            "read_ok_paths": list(self.read_ok_paths),
            "lang": self.lang,
            "last_chips": list(self.last_chips),
            "turns": self.turns,
            "spend": {k: dict(v) for k, v in self.spend.items()},
            "turn_spend": dict(self.turn_spend),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def to_public(self) -> dict[str, Any]:
        pending = None
        if isinstance(self.pending, dict) and self.pending.get("id"):
            pending = {
                "pending_id": self.pending.get("id"),
                "name": self.pending.get("name") or "edit_file",
                "args": dict(self.pending.get("args") or {}),
            }
        return {
            "session_id": self.session_id,
            "handbook_id": self.handbook_id,
            "chips": FIXED_CHIPS,
            "has_substantive": self.has_substantive,
            "last_anchor": self.last_anchor,
            "ui_messages": self.ui_messages,
            "last_assistant": self.last_assistant,
            "pending": pending,
            "dyn_chips": list(self.last_chips),
            # probe 那一格在这里恒为 0；app._public_session 会从账本补上。
            "spend": {k: dict(v) for k, v in self.spend.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PenSession:
        return cls(
            session_id=str(data["session_id"]),
            handbook_id=str(data["handbook_id"]),
            messages=list(data.get("messages") or []),
            last_anchor=data.get("last_anchor") if isinstance(data.get("last_anchor"), dict) else None,
            has_substantive=bool(data.get("has_substantive")),
            last_assistant=str(data.get("last_assistant") or ""),
            ui_messages=list(data.get("ui_messages") or []),
            pending=data.get("pending") if isinstance(data.get("pending"), dict) else None,
            read_ok_paths=[str(p) for p in data.get("read_ok_paths") or []]
            if isinstance(data.get("read_ok_paths"), list)
            else [],
            lang=str(data.get("lang") or "zh"),
            last_chips=[c for c in (data.get("last_chips") or []) if isinstance(c, dict)],
            turns=int(data.get("turns") or 0),
            # coerce_* 吃任何脏输入。旧快照里根本没这两个键 → 全 0，不需要迁移。
            spend=metermod.coerce_book(data.get("spend")),
            turn_spend=metermod.coerce(data.get("turn_spend")),
        )


def load_session(session_id: str) -> PenSession | None:
    try:
        dest = _session_path(session_id)
    except ValueError:
        return None
    if not dest.is_file():
        return None
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("session_id"):
        return None
    return PenSession.from_dict(data)


def save_session(sess: PenSession) -> Path:
    config.ensure_pen_dirs()
    dest = _session_path(sess.session_id)
    _atomic_write(dest, json.dumps(sess.to_dict(), ensure_ascii=False))
    return dest


class SessionStore:
    """内存里的会话表，按 LRU 淘汰。

    此前**完全没有淘汰**：`_items` 和 `_locks` 都是纯 append 的普通 dict，
    sidecar 是常驻进程，读者一天划几百次词就是几百个 `PenSession`（外加
    几百把 `Lock`）永远挂在那儿。

    **`_items` 的读写此前也完全裸奔**——`_lock_meta` 只护 `_locks`。单次
    getitem/setitem 在 GIL 下是原子的，所以以前不炸；但淘汰是「查长度 → 挑最旧
    → pop」这种 read-modify-write，不整体加锁会踩空 `KeyError`。所以这一版起
    `_meta` 同时护两个字典。

    磁盘上的快照一个字没动：淘汰只是把它从内存里放掉，下次 `get()` 现读回来。
    """

    def __init__(self) -> None:
        # OrderedDict 而不是 dict：要的是 move_to_end。最左边 = 最久没碰过。
        self._items: OrderedDict[str, PenSession] = OrderedDict()
        self._locks: dict[str, threading.Lock] = {}
        # 名字保持 _lock_meta：现在它护的是**两个**字典，不只是 _locks。
        self._lock_meta = threading.RLock()

    def lock_for(self, session_id: str) -> threading.Lock:
        with self._lock_meta:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[session_id] = lock
            if session_id in self._items:
                self._items.move_to_end(session_id)
            return lock

    def try_lock(
        self, sess: PenSession
    ) -> tuple[threading.Lock, PenSession] | None:
        """抢这一场的锁（非阻塞）。返回 `(锁, 当家实例)`，抢不到返回 None。

        为什么必须是**一个**方法而不是 `lock_for()` 再 `acquire(False)`：
        那两步之间有一条缝，淘汰正好挤进来就会把 `_locks[sid]` 换掉，于是两个
        线程各拿一把不同的锁同时进临界区，各自 `save()` 一个从磁盘重建的实例，
        后写的赢——表现就是「一整轮对话凭空消失」。
        acquire 发生在 `_meta` 之内，淘汰也要 `_meta`，缝就没了。

        为什么参数是 `PenSession` 而不是 `session_id`：`_evict()` 从最旧往最新
        扫，在跑的那些会被跳过——**在跑的多于上限时，连刚 `get()` 出来的那条
        都会被扫到**。所以抢到锁的这一刻必须把手里这个实例按回 `_items`，
        否则别的请求 `get()` 会从磁盘重建出第二个实例。

        **为什么还要把当家实例还给调用方**（v0.12.6）：光「钉回去」只关掉了
        一半。生产次序是 `get()` → 一堆磁盘 I/O（`_meta_or_404` / `load_index`）
        → `try_lock()`，中间那几毫秒里手上这个实例可能已经过时了：

            T1 get(S) 拿到 X → 淘汰把 X 扫出表 → T2 get(S) 从盘上重建 Y，
            锁上、跑完**一整轮**、save(Y)、放锁 → T1 这才 try_lock(X)，
            锁是空的，抢到，还把陈旧的 X 钉回表里 → 收尾 save(X)
            把 T2 那一轮整个盖掉。

        所以抢到锁之后要认表里那个（或者回盘上现读）。**此刻锁在我们手上，
        谁也不能再改这一场，磁盘那份就是权威**——现读是安全的。
        """
        sid = sess.session_id
        with self._lock_meta:
            lock = self._locks.get(sid)
            if lock is None:
                lock = threading.Lock()
                self._locks[sid] = lock
            if not lock.acquire(blocking=False):
                return None
            incumbent = self._items.get(sid)
            if incumbent is not None:
                self._items.move_to_end(sid)
                return lock, incumbent
        # 表里没有 = 手上这个在 get() 之后被淘汰扫走了，而它离表期间别人可能
        # 跑完了一整轮。读盘不持 `_meta`（那是每个请求都要过的窄门）。
        try:
            fresh = load_session(sid)
        except Exception:
            fresh = None
        # 盘上也没了（保留期清理正好插在中间）就用手上这个，让读者这一枪照样能跑，
        # 强过甩他一个 404。
        live = fresh if fresh is not None else sess
        with self._lock_meta:
            self._items[sid] = live
            self._items.move_to_end(sid)
        return lock, live

    def _evict(self) -> None:
        """把最久没碰过的放掉。**调用方必须已持有 `_meta`。**

        **绝不碰 `lock.locked()` 为真的那场**——宁可暂时超上限。
        `/v1/chat`、`/approve`、`/propose` 三处都是先 `STORE.get()` 拿到 `sess`
        引用，再长时间持锁改它。把 `_items[sid]` 踢掉之后，别的请求
        `STORE.get(sid)` 会从磁盘**重建出第二个实例**，而收尾的
        `STORE.save(sess)` 又拿第一个实例覆盖回去——「对话中途重开侧栏，
        上一条回复消失」就是这么来的。
        """
        cap = max(1, int(config.MAX_LIVE_SESSIONS))
        if len(self._items) <= cap:
            return
        for sid in list(self._items):
            if len(self._items) <= cap:
                break
            lock = self._locks.get(sid)
            if lock is not None and lock.locked():
                continue  # 正在跑，跳过
            self._items.pop(sid, None)
            self._locks.pop(sid, None)
        # 顺手收孤儿锁：`_items` 里没有、也没人持着的。只在超限时扫——
        # 平时两个字典一一对应，每次都扫是白花钱。
        for sid in [s for s in self._locks if s not in self._items]:
            if not self._locks[sid].locked():
                self._locks.pop(sid, None)

    def create(self, handbook_id: str, lang: str = "zh", book_title: str = "") -> PenSession:
        sid = uuid.uuid4().hex
        sess = PenSession(
            session_id=sid, handbook_id=handbook_id, lang=lang, book_title=book_title
        )
        with self._lock_meta:
            self._items[sid] = sess
            self._evict()
        save_session(sess)  # 落盘不持锁
        return sess

    def get(self, session_id: str) -> PenSession:
        with self._lock_meta:
            hit = self._items.get(session_id)
            if hit is not None:
                self._items.move_to_end(session_id)
                return hit
        # 读盘不持锁：`_meta` 是每个请求都要过的窄门，别把磁盘 I/O 关在里面。
        loaded = load_session(session_id)
        if loaded is None:
            raise KeyError(session_id)
        with self._lock_meta:
            # 双检。读盘那会儿别人可能已经放进来了，**必须返回先到的那个**：
            # 同一个 sid 有两个实例的话，两条请求各改各的，收尾的 save 互相覆盖。
            # （这条竞态在加淘汰之前就存在，顺手关掉。）
            cur = self._items.get(session_id)
            if cur is not None:
                self._items.move_to_end(session_id)
                return cur
            self._items[session_id] = loaded
            self._evict()
        return loaded

    def save(self, sess: PenSession) -> None:
        with self._lock_meta:
            self._items[sess.session_id] = sess
            self._items.move_to_end(sess.session_id)
            self._evict()
        save_session(sess)


STORE = SessionStore()
