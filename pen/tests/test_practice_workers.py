from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from pen.practice.contracts import PROTOCOL
from pen.practice.worker import create_app
from pen.practice.worker_store import SyncConflict, WorkerStore, db_path_for


def _snapshot() -> dict[str, Any]:
    return {
        "resource_version": "rv1",
        "points": [{"id": "p1", "name": "Point one"}],
        "edges": [],
        "blueprint": {"id": "bp1", "version": "b1", "point_weights": {"p1": 1}, "target_score": 80, "daily_minutes": 5},
        "questions": [
            {
                "id": "q1",
                "version": "v1",
                "source_mq_id": "mq1",
                "type": "short_answer",
                "purpose": "practice",
                "exam_form": None,
                "prompt": "Why?",
                "point_ids": ["p1"],
                "status": "accepted",
                "estimated_seconds": 60,
                "criteria": [{"id": "c1", "point_id": "p1", "max_score": 1}],
            }
        ],
    }


def _event(seq: int, score: float = 1.0) -> dict[str, Any]:
    at = (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=seq - 1)).isoformat()
    q = _snapshot()["questions"][0]
    return {
        "seq": seq,
        "type": "attempt_graded",
        "created_at": at,
        "payload": {
            "id": f"a{seq}",
            "session_id": "s1",
            "question_id": "q1",
            "question_version": "v1",
            "source_version": "rv1",
            "purpose": "practice",
            "answer": {"text": "x"},
            "status": "graded",
            "created_at": at,
            "answered_at": at,
            "duration_seconds": 30,
            "question": q,
            "grade": {
                "criteria": [{"criterion_id": "c1", "point_id": "p1", "score": score, "max_score": 1, "reason": "", "evidence_quote": ""}],
                "score": score,
                "max_score": 1,
                "model": "test",
            },
        },
    }


def test_worker_store_sync_is_idempotent_allows_scoped_gaps_and_rejects_conflicts(tmp_path: Path) -> None:
    store = WorkerStore.open(tmp_path, service="analysis")
    first = store.sync(scope="scope", handbook_id="book", snapshot=_snapshot(), events=[_event(3, 0.4), _event(9, 0.8)])
    assert first.cursor == 9
    repeat = store.sync(scope="scope", handbook_id="book", snapshot=_snapshot(), events=[_event(3, 0.4)])
    assert repeat.cursor == 9
    assert repeat.duplicates == 1
    assert [e["seq"] for e in store.events("scope", "book")] == [3, 9]
    with pytest.raises(SyncConflict):
        store.sync(scope="scope", handbook_id="book", snapshot=None, events=[_event(3, 0.7)])
    other = store.sync(scope="other", handbook_id="book", snapshot=None, events=[_event(40, 1.0)])
    assert other.cursor == 40
    assert store.cursor("scope", "book") == 9
    store.close()


def test_worker_store_older_snapshot_revision_does_not_replace_events_still_ingest(tmp_path: Path) -> None:
    store = WorkerStore.open(tmp_path, service="analysis")
    newer = {**_snapshot(), "resource_version": "new", "revision": "2026-02-01T00:00:00.000000+00:00"}
    older = {**_snapshot(), "resource_version": "old", "revision": "2026-01-01T00:00:00.000000+00:00"}
    store.sync(scope="scope", handbook_id="book", snapshot=newer, events=[])
    result = store.sync(scope="scope", handbook_id="book", snapshot=older, events=[_event(7, 0.5)])
    assert result.cursor == 7
    assert store.snapshot("scope", "book")["resource_version"] == "new"
    assert [e["seq"] for e in store.events("scope", "book")] == [7]
    store.close()


def test_worker_http_auth_sync_and_analysis(tmp_path: Path) -> None:
    store = WorkerStore.open(tmp_path, service="analysis")
    app = create_app(service="analysis", store=store, token="secret")
    with TestClient(app) as client:
        assert client.get("/v1/health").status_code == 401
        assert client.get("/v1/health", headers={"Authorization": "Bearer bad"}).status_code == 401
        ok = client.get("/v1/health", headers={"Authorization": "Bearer secret"})
        assert ok.status_code == 200
        assert ok.json()["protocol"] == PROTOCOL
        synced = client.post(
            "/v1/sync",
            headers={"Authorization": "Bearer secret"},
            json={"scope": "scope", "handbook_id": "book", "snapshot": _snapshot(), "events": [_event(3, 0.25)]},
        )
        assert synced.status_code == 200
        assert synced.json() == {"cursor": 3}
        report = client.post(
            "/v1/analysis",
            headers={"Authorization": "Bearer secret"},
            json={"scope": "scope", "handbook_id": "book", "now": "2026-02-01T00:00:00+00:00"},
        )
        assert report.status_code == 200
        body = report.json()
        assert body["cursor"] == 3
        assert body["points"][0]["score"] == 0.25
    store.close()


def test_worker_sqlite_files_are_service_isolated(tmp_path: Path) -> None:
    analysis = WorkerStore.open(tmp_path, service="analysis")
    scheduling = WorkerStore.open(tmp_path, service="scheduling")
    analysis.sync(scope="scope", handbook_id="book", snapshot=_snapshot(), events=[_event(1, 1.0)])
    assert analysis.cursor("scope", "book") == 1
    assert scheduling.cursor("scope", "book") == 0
    assert scheduling.snapshot("scope", "book") is None
    assert db_path_for(tmp_path, "analysis").name == "analysis.sqlite"
    assert db_path_for(tmp_path, "scheduling").name == "scheduling.sqlite"
    analysis.close()
    scheduling.close()


def test_worker_cli_writes_ready_file_and_serves_authenticated_health(tmp_path: Path) -> None:
    ready = tmp_path / "ready.json"
    proc = _start_worker(tmp_path, ready, service="analysis", parent_pid=os.getpid())
    try:
        data = _wait_ready(proc, ready)
        assert data["service"] == "analysis"
        assert data["protocol"] == PROTOCOL
        url = f"http://127.0.0.1:{data['port']}/v1/health"
        with httpx.Client(trust_env=False, timeout=5.0) as client:
            assert client.get(url).status_code == 401
            good = client.get(url, headers={"Authorization": "Bearer tok"})
            assert good.status_code == 200
            assert good.json()["pid"] == data["pid"]
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_worker_exits_when_parent_pid_is_already_dead(tmp_path: Path) -> None:
    parent = subprocess.Popen([sys.executable, "-c", "pass"])
    parent.wait(timeout=5)
    ready = tmp_path / "dead-ready.json"
    proc = _start_worker(tmp_path, ready, service="analysis", parent_pid=parent.pid)
    try:
        deadline = time.time() + 5
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        assert proc.poll() == 0, proc.stderr.read() if proc.stderr else ""
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5)


def _start_worker(tmp_path: Path, ready: Path, *, service: str, parent_pid: int) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["PEN_PRACTICE_TOKEN"] = "tok"
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pen.practice.worker",
            "--service",
            service,
            "--parent-pid",
            str(parent_pid),
            "--ready-file",
            str(ready),
            "--pen-home",
            str(tmp_path),
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wait_ready(proc: subprocess.Popen[str], ready: Path) -> dict[str, Any]:
    deadline = time.time() + 5
    while time.time() < deadline:
        if ready.exists():
            return json.loads(ready.read_text(encoding="utf-8"))
        if proc.poll() is not None:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise AssertionError(f"worker exited before ready: {proc.returncode} {stderr}")
        time.sleep(0.05)
    stderr = proc.stderr.read() if proc.stderr and proc.poll() is not None else ""
    raise AssertionError(f"worker did not become ready {stderr}")
