"""记录里的时间戳长什么样。**全仓只在这里定。**

会话气泡、轨迹行、compact 的那条 note、会话文件的 `updated_at`——凡是落盘的
时刻都从 `now_iso()` 来。以前气泡根本没有时间，轨迹和会话文件各自写 UTC；
读者回看「哪个晚上在磁带上卡了 11 轮」时，一半记录没有钟，另一半的钟要他
自己换算八小时。

所以是**本地时间带时区偏移**：`2026-09-03T10:47:06+08:00`。它仍然是一个绝对
时刻——`datetime.fromisoformat` 一行读回，和历史行里的 `+00:00` 混着排序也
不会错——但读出来就是读者墙上那口钟的数字。

精确到秒。微秒对「读者什么时候问的」没有信息，只让文件难读。
"""

from __future__ import annotations

from datetime import datetime


def now_iso() -> str:
    """现在。本地时间、带偏移、到秒。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")
