"""The gateway speaks to real independent processes, without model/provider calls."""
from __future__ import annotations

import os

from pen import config
from pen.practice import coordinator as core
from pen.practice.contracts import timestamp
from pen.practice.runtime import Supervisor, set_enabled
from pen.practice.store import Store


def test_real_workers_restart_and_replay(monkeypatch):
    processes = Supervisor()
    monkeypatch.setattr(core, "supervisor", processes)
    scope, hid = "a" * 32, "book"
    store = Store()
    point = {"id": "p", "name": "Value", "definition": "Expected future return", "source_mq_ids": ["mq"]}
    question = {"id": "q", "version": "1", "source_mq_id": "mq", "type": "single_choice",
                "purpose": "practice", "point_ids": ["p"], "prompt": "Which value?", "status": "accepted",
                "estimated_seconds": 45, "choices": [{"id": "a", "text": "Return"}],
                "criteria": [{"id": "c", "point_id": "p", "max_score": 1}]}
    resource = {"resource_version": "r", "meta_questions": [{"id": "mq"}], "points": [point],
                "edges": [], "questions": [question]}
    store.put(scope, "resource", "r", hid, resource)
    store.put(scope, "active", hid, hid, {"resource_version": "r"})
    store.put(scope, "blueprint", hid, hid, core.default_blueprint(resource))
    try:
        initial = core.analysis_report(scope, hid)
        assert initial["points"][0]["n"] == 0
        rec = core.recommendations(scope, hid, 20)
        assert rec["items"][0]["question_id"] == "q"
        statuses = processes.status()
        assert len({s["pid"] for s in statuses.values()} | {os.getpid()}) == 3
        decision_id = rec["items"][0]["decision_id"]
        attempt = {"id": "attempt", "status": "graded", "question_id": "q", "question": question,
                   "purpose": "practice", "answered_at": timestamp(), "created_at": timestamp(),
                   "decision_id": decision_id,
                   "grade": {"criteria": [{"criterion_id": "c", "point_id": "p", "score": .5, "max_score": 1}]}}
        with store.transaction() as db:
            store.emit(db, scope, hid, "attempt_graded", "grade:attempt", attempt)
        first = core.analysis_report(scope, hid)
        assert first["points"][0]["n"] == 1
        processes.children["analysis"]["process"].kill()
        processes.children["analysis"]["process"].wait(timeout=5)
        second = core.analysis_report(scope, hid)
        assert second["points"][0]["n"] == 1
        assert second["cursor"] == first["cursor"]
        assert core.recommendations(scope, hid, 20)["items"] == []  # same question on cooldown
        other = processes.call("analysis", "/v1/sync", {"scope": "b"*32, "handbook_id": hid,
                                "snapshot": {**resource, "blueprint": core.default_blueprint(resource)}, "events": []})
        assert other["cursor"] == 0
        clean = processes.call("analysis", "/v1/analysis", {"scope": "b"*32, "handbook_id": hid})
        assert clean["points"][0]["n"] == 0
    finally:
        processes.stop()
    assert processes.status() == {}


def test_no_databases_or_children_created_on_import():
    processes = Supervisor()
    assert processes.status() == {}
    assert not (config.PEN_DIR / "practice").exists()
