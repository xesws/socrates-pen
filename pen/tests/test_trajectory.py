"""轨迹行：本地时间、一行不丢、approve 那半截不算一轮。"""

from __future__ import annotations

from datetime import datetime

from pen import trajectory
from pen.clock import now_iso


def test_stamp_is_local_time_with_offset_to_the_second() -> None:
    """读者要按自己墙上的钟回看。UTC 要他自己换算，微秒只让文件难读。"""
    dt = datetime.fromisoformat(now_iso())
    assert dt.tzinfo is not None and dt.utcoffset() is not None
    assert dt.utcoffset() == datetime.now().astimezone().utcoffset()
    assert dt.microsecond == 0


def test_append_stamps_ts_and_load_keeps_every_row() -> None:
    """以前没有 anchor 的行在读的时候被静默扔掉——读者的数据不该在读时被筛。"""
    trajectory.append_turn("hb-x", {"session_id": "s", "phase": "approve", "anchor": None, "ok": True})
    trajectory.append_turn("hb-x", {"session_id": "s", "anchor": {"level": "Level 1"}, "ok": True})
    rows = trajectory.load_turns("hb-x")
    assert len(rows) == 2
    assert datetime.fromisoformat(rows[0]["ts"]).utcoffset() is not None
    assert rows[1]["handbook_id"] == "hb-x"


def test_is_turn_treats_old_rows_as_chat_and_approve_as_half() -> None:
    assert trajectory.is_turn({"anchor": {}})
    assert trajectory.is_turn({"phase": "chat"})
    assert not trajectory.is_turn({"phase": "approve"})
    assert not trajectory.is_turn("not a row")
