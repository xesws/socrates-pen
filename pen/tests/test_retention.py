"""v0.12.4：会话按时间清理。

读者：「日志最多保存最近 7 天的会话吧，每次启动插件的时候都要自动清理一下，
不然用户的电脑就直接爆炸了。」

实测背景：`.pen/sessions/` 攒到 3389 个文件 / 10.4 MB，3371 个是空的。
**纯 7 天规则一个都删不掉**（最老的才几天），所以「空会话 1 天」那一档
才是唯一管用的扫帚——下面每条都盯着这个形状。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from pen import config, probe_store, retention
from pen.session import sessions_dir

DAY = 86400.0


def _write(sid: str, *, age_days: float, messages: int = 1, pending: bool = False) -> Path:
    """造一场指定年龄的假会话。messages=1 → 只有 system prompt，即「空」。"""
    dest = sessions_dir() / f"{sid}.json"
    data = {
        "session_id": sid,
        "handbook_id": "demo",
        "messages": [{"role": "system", "content": "x"}]
        + [{"role": "user", "content": "问"} for _ in range(messages - 1)],
        "pending": {"id": "p1", "name": "edit_file", "args": {}} if pending else None,
    }
    dest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    old = time.time() - age_days * DAY
    os.utime(dest, (old, old))
    return dest


def test_empty_session_older_than_a_day_is_swept() -> None:
    """3371 个空会话就是靠这一条清掉的。"""
    doomed = _write("a" * 32, age_days=1.5)
    assert retention.purge_expired_sessions()["removed"] == 1
    assert not doomed.exists()


def test_a_freshly_opened_note_is_never_swept() -> None:
    """读者刚打开一篇新笔记 → 建会话 → 还没提问，这时它就是「空」的。
    实测最新的空会话建于 6.9 分钟前——一小时档会把正在用的那场删掉。"""
    live = _write("b" * 32, age_days=0.2)
    assert retention.purge_expired_sessions()["removed"] == 0
    assert live.exists()


def test_chatted_session_keeps_a_full_week() -> None:
    kept = _write("c" * 32, age_days=6.5, messages=9)
    doomed = _write("d" * 32, age_days=7.5, messages=9)
    assert retention.purge_expired_sessions()["removed"] == 1
    assert kept.exists() and not doomed.exists()


def test_chatted_session_is_not_judged_by_the_empty_rule() -> None:
    """两档要真的分开。聊过的会话第 2 天不许被当成空的删掉——
    只有一个 KEEP_DAYS 的话这条会红。"""
    kept = _write("e" * 32, age_days=2.0, messages=5)
    retention.purge_expired_sessions()
    assert kept.exists()


def test_pending_approval_gets_a_much_longer_grace_period() -> None:
    """挂着审批的那场删掉，读者点「同意」就撞 404，手里的提案当场作废。
    所以给它 30 天，而不是聊过的那 7 天。"""
    kept = _write("f" * 32, age_days=20, messages=9, pending=True)
    assert retention.purge_expired_sessions()["removed"] == 0
    assert kept.exists()


def test_but_pending_is_not_a_forever_pass() -> None:
    """**不能是无限。** 读者在审批面板开着时关掉 Obsidian、之后点
    「新开会话」，那一场就再也没人碰它了——一条没有天花板的豁免
    就是一条谁都清不掉的泄漏，正是这次要治的病本身。"""
    doomed = _write("2" * 32, age_days=400, messages=9, pending=True)
    assert retention.purge_expired_sessions()["removed"] == 1
    assert not doomed.exists()


def test_pending_on_an_empty_session_does_not_buy_immortality() -> None:
    """「有 pending 就永不删」曾是一条**无界**豁免。

    实测真实 `.pen`（2026-08-21）3219 个文件里有 551 个是「pending + messages == 1」，
    全是测试污染留下的假数据，而按第一版规则它们永远删不掉——清理跑一万遍
    也清不掉的东西，就等于没有上限。

    而真 pending 不可能长在空会话上：`tutor.py` 设 pending 之前，这一轮的
    user 和带 tool_calls 的 assistant 早就进了 messages（最少 3 条）。

    年龄取 5 天是**故意**的：它同时越过了空会话那 1 天档、没越过 pending
    那 30 天档。取 400 天的话，判断顺序写反了这条也照样绿——那就成了空转。
    """
    fake = _write("0" * 32, age_days=5, pending=True)
    assert retention.purge_expired_sessions()["removed"] == 1
    assert not fake.exists()


def test_but_a_real_pending_still_outlives_the_seven_day_rule() -> None:
    """收紧的是「空 + pending」，不是 pending 本身。这条守住另一边：
    真 pending 撑过了聊过的那 7 天档。"""
    real = _write("1" * 32, age_days=20, messages=3, pending=True)
    assert retention.purge_expired_sessions()["removed"] == 0
    assert real.exists()


def test_unparseable_file_gets_the_longer_grace_period() -> None:
    """读不出来的按 7 天档：宁可多留一周，也不要因为一次解析失败
    就把读者聊过的东西删了。"""
    dest = sessions_dir() / f"{'g' * 32}.json"
    dest.write_text("{截断的坏 JSON", encoding="utf-8")
    old = time.time() - 3 * DAY
    os.utime(dest, (old, old))
    retention.purge_expired_sessions()
    assert dest.exists(), "坏文件不该按 1 天档删"
    older = time.time() - 9 * DAY
    os.utime(dest, (older, older))
    retention.purge_expired_sessions()
    assert not dest.exists()


def test_probe_ledger_is_deleted_in_the_same_breath() -> None:
    """深挖账本按会话索引，会话没了它就是孤儿。"""
    sid = "h" * 32
    _write(sid, age_days=9, messages=9)
    ledger = probe_store.probes_dir() / f"{sid}.json"
    ledger.write_text("{}", encoding="utf-8")
    retention.purge_expired_sessions()
    assert not ledger.exists()


def test_hourly_quota_is_never_touched() -> None:
    """`.pen/probes/<hid>/quota.json` 是按 handbook 索引的每小时配额，
    和会话没关系。跟着删 = 把成本闸门清零。"""
    _write("i" * 32, age_days=9, messages=9)
    quota = probe_store._quota_path("demo")
    quota.write_text('{"2026-08-21T00": 40}', encoding="utf-8")
    retention.purge_expired_sessions()
    assert quota.exists()
    assert json.loads(quota.read_text(encoding="utf-8")) == {"2026-08-21T00": 40}


def test_another_process_mid_write_temp_file_is_not_globbed() -> None:
    """`_atomic_write` 的临时名是 `<sid>.json.<pid>.<hex>.tmp`。
    glob 写成 `*.json*` 就会命中另一个 sidecar **正在写**的那个文件。"""
    tmp = sessions_dir() / f"{'j' * 32}.json.4242.deadbeef.tmp"
    tmp.write_text("{}", encoding="utf-8")
    old = time.time() - 30 * DAY
    os.utime(tmp, (old, old))
    got = retention.purge_expired_sessions()
    assert got["scanned"] == 0 and got["removed"] == 0
    assert tmp.exists()


def test_purge_is_silent_when_there_is_nothing_there() -> None:
    """它挂在 lifespan 上：没有会话目录不该让 sidecar 起不来。"""
    assert not config.PEN_DIR.exists()
    assert retention.purge_expired_sessions() == {"scanned": 0, "removed": 0}


def test_purge_is_silent_when_the_directory_cannot_even_be_made() -> None:
    """上面那条其实**摸不到** `except OSError`：`sessions_dir()` 自己会
    `mkdir(exist_ok=True)`，所以 glob 永远有个目录可扫，那个分支一次都没跑过
    ——docstring 声称的「目录不存在不该让 sidecar 起不来」并没有被守住。

    真要让 `sessions_dir()` 抛，得让 `.pen` 那个位置**是个文件**
    （读者手贱、同步工具塞了个占位文件），`mkdir` 就会 `NotADirectoryError`。
    """
    config.PEN_DIR.parent.mkdir(parents=True, exist_ok=True)
    config.PEN_DIR.write_text("我不是目录", encoding="utf-8")
    assert retention.purge_expired_sessions() == {"scanned": 0, "removed": 0}
    retention.touch("z" * 32)  # 同一个坑，touch 也不许抛


def test_touch_makes_mtime_mean_last_seen_not_last_written() -> None:
    """`GET /v1/sessions/{sid}` 那条路径不 save。不推 mtime 的话，
    读者天天开面板却不发消息的会话会在第 7 天被静默删掉，历史全丢。"""
    sid = "k" * 32
    path = _write(sid, age_days=8, messages=9)
    retention.touch(sid)
    assert time.time() - path.stat().st_mtime < 5
    assert retention.purge_expired_sessions()["removed"] == 0


def test_touch_on_a_missing_session_leaves_no_trace() -> None:
    """「不抛就算过」不算断言。真正要守的是：touch 一个不存在的 sid
    **不许把文件创出来**——`Path.touch()` 默认就是 create，少了那道
    `is_file()` 就会在会话目录里凭空长出空文件，下一次清理再把它删掉。"""
    sid = "l" * 32
    retention.touch(sid)
    assert not (sessions_dir() / f"{sid}.json").exists()
    assert retention.purge_expired_sessions()["scanned"] == 0


def test_get_session_pushes_mtime_forward() -> None:
    """端到端：真走一遍 HTTP，确认读一次就不会被清理带走。"""
    from pen.app import app

    with TestClient(app) as client:
        created = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"})
        sid = created.json()["session_id"]
        path = sessions_dir() / f"{sid}.json"
        old = time.time() - 30 * DAY
        os.utime(path, (old, old))
        assert client.get(f"/v1/sessions/{sid}").status_code == 200
        assert time.time() - path.stat().st_mtime < 5


def test_reusing_a_session_id_pushes_mtime_forward() -> None:
    """`POST /v1/sessions` 的恢复分支同理——插件每次划词走的就是它。"""
    from pen.app import app

    with TestClient(app) as client:
        sid = client.post("/v1/sessions", json={"handbook_id": "swe-agent-v2"}).json()[
            "session_id"
        ]
        path = sessions_dir() / f"{sid}.json"
        old = time.time() - 30 * DAY
        os.utime(path, (old, old))
        again = client.post(
            "/v1/sessions", json={"handbook_id": "swe-agent-v2", "session_id": sid}
        )
        assert again.json()["session_id"] == sid, "该走恢复分支，不该新建"
        assert time.time() - path.stat().st_mtime < 5


def test_lifespan_sweeps_on_startup() -> None:
    """「每次启动都自动清理一下」的服务端那一半。"""
    from pen.app import app

    doomed = _write("m" * 32, age_days=30)
    with TestClient(app):
        pass
    assert not doomed.exists()


def test_maintenance_endpoint_is_the_plugin_onload_half() -> None:
    """插件不拉起 sidecar——sidecar 可能已经跑了好几天，lifespan 早跑完了。
    读者说的「每次启动插件的时候」只能靠这一枪。"""
    from pen.app import app

    with TestClient(app) as client:
        doomed = _write("n" * 32, age_days=30)
        got = client.post("/v1/maintenance/purge")
        assert got.status_code == 200
        assert got.json()["removed"] >= 1
        assert not doomed.exists()
