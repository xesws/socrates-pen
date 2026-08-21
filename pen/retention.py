"""会话按时间清理。读者的电脑不该被几千个空会话撑爆。

实测（2026-08-20，清理上线前）：`.pen/sessions/` 攒到 3389 个文件 / 10.4 MB，
其中 **3371 个是空的**——
每划一次词就建一场，大部分没等到第一句话就被下一次划词换掉了。此前**完全没有**
任何清理机制：快照有 SNAPSHOT_KEEP、深挖配额按小时窗口滚、写回提案是内存的，
只有会话是纯 append，进程活多久涨多久。

三条规则（读者拍板的两档 + 一条硬保护）：

| 类型            | 判据                 | 保留   |
| --------------- | -------------------- | ------ |
| 空会话          | `len(messages) <= 1` | 1 天   |
| 聊过的          | 其余                 | 7 天   |
| 挂着 pending 的 | `pending.id` 非空 **且不是空会话** | 30 天 |

`pending` 是「读者点了写回、审批弹窗还没结果」的状态。那一场删掉，读者点
「同意」就会撞 404，手里已生成的提案当场作废——所以给它长得多的一档。

**但不是「永不删」**（v0.12.6 收紧的第二处）。读者在审批面板开着的时候
关掉 Obsidian、之后点「新开会话」，那一场就再也没人碰它了。一条没有天花板
的豁免，就是一条谁都清不掉的泄漏——正是这次要治的病本身。

**但这条豁免必须夹在「聊过」里面**（v0.12.6 收紧）。第一版写成「有 pending
就永不删」，那是一条**无界**豁免：任何带 `pending` 键的文件从此占着盘，
清理再怎么跑都动不了它。实测真实 `.pen`（2026-08-21）3219 个文件里有
**551 个**是「pending + messages == 1」——全是测试污染留下的假数据
（id 只有 `pend1` / `pend2` / `pid1` 三种），而它们永远删不掉。
（这个数会随清理跑动往下漂，下次再量别指望逐字对上。）

而真实的 pending **不可能**长在空会话上：`tutor.py:701` 设 pending 之前，
这一轮的 user 和带 tool_calls 的 assistant 早就 append 进 `session.messages`
了（`:544`），最少 3 条。所以「空 + pending」只可能是假数据或坏文件，
按空会话那一档处理不会伤到任何真东西。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pen import config, probe_store, session as sessionmod

_DAY = 86400.0


def _is_empty(data: dict) -> bool:
    """只有 system prompt = 从没说过话。

    `PenSession.__post_init__` 保证 messages 至少有那一条，所以判据是 `<= 1`
    而不是 `== 0`。
    """
    msgs = data.get("messages")
    return not isinstance(msgs, list) or len(msgs) <= 1


def _has_pending(data: dict) -> bool:
    pending = data.get("pending")
    return isinstance(pending, dict) and bool(pending.get("id"))


def _keep_seconds(path: Path) -> float:
    """这个文件该留多久（秒）。

    读不出来的（截断、手改坏了、正在写）按**长**的那档算：宁可多留 7 天，
    也不要因为一次解析失败就把读者聊过的东西删了。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return config.SESSION_KEEP_DAYS_CHATTED * _DAY
    if not isinstance(data, dict):
        return config.SESSION_KEEP_DAYS_CHATTED * _DAY
    # 顺序要紧：先判空。空会话上的 pending 是假的（见模块头），让它走短的那档，
    # 否则「有 pending 就免死」会盖住空会话那一档，而空会话正是 3371 个文件的来源。
    if _is_empty(data):
        return float(config.SESSION_KEEP_DAYS_EMPTY) * _DAY
    if _has_pending(data):
        return float(config.SESSION_KEEP_DAYS_PENDING) * _DAY
    return float(config.SESSION_KEEP_DAYS_CHATTED) * _DAY


def purge_expired_sessions(*, now: float | None = None) -> dict[str, int]:
    """扫一遍会话目录，删过期的。返回 `{"scanned", "removed"}`。

    **绝不抛异常。** 它挂在 lifespan 上，也挂在插件 onload 打的那个端点上——
    目录不存在、文件被并发删、权限不对，任何一样都不该让 sidecar 起不来。

    glob 用 `*.json` **不要用 `*.json*`**：`_atomic_write` 的临时文件叫
    `<sid>.json.<pid>.<hex>.tmp`，后者会命中另一个进程**正在写**的那个。
    """
    stamp = time.time() if now is None else now
    scanned = removed = 0
    try:
        files = sorted(sessionmod.sessions_dir().glob("*.json"))
    except OSError:
        return {"scanned": 0, "removed": 0}
    for f in files:
        scanned += 1
        try:
            age = stamp - f.stat().st_mtime
        except OSError:
            continue  # 被别人删掉了，正好
        if age <= _keep_seconds(f):
            continue
        sid = f.stem
        try:
            f.unlink(missing_ok=True)
        except OSError:
            continue
        removed += 1
        # 成对删。深挖账本是懒创建的子集（实测 3389 ↔ 3），missing_ok 兜住。
        # **只删这一个**——`.pen/probes/<hid>/quota.json` 是按 handbook 索引的
        # 每小时配额，和会话没关系，碰它等于把成本闸门清零。
        try:
            (probe_store.probes_dir() / f"{sid}.json").unlink(missing_ok=True)
        except OSError:
            pass
    return {"scanned": scanned, "removed": removed}


def touch(session_id: str) -> None:
    """把 mtime 推到现在，语义从「最后写过」改成「最后碰过」。

    为什么需要：`GET /v1/sessions/{sid}` 和 `POST /v1/sessions` 的恢复分支
    **都不 save**——而这两条正是读者每次开面板、每次划词必走的路。只要不真发
    一轮对话，mtime 就冻在最后一次 chat 上，7 天不聊天就被清掉，下次划词静默
    换成新会话，历史全丢。所以 `pen/app.py` 只在这两处调 `touch()`。

    （`GET /deep`、`retarget`、`apply` 同样不 save，但都被上游的 save/touch
    兜住了：读者要走到它们，必然先经过上面那两条中的一条。v0.12.5 这段注释
    把它们也列进来了，像是在说那三条端点也调了 touch——并没有。）

    静默失败：它只是让清理更保守，不该成为读会话的失败原因。
    """
    try:
        path = sessionmod.sessions_dir() / f"{session_id}.json"
        if path.is_file():
            path.touch()
    except (OSError, ValueError):
        pass
