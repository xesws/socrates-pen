"""Resource boundaries and complete-assessment invariants at the gateway."""
from pathlib import Path

import pytest

from pen.practice import coordinator as core
from pen.practice.resources import extract_meta_questions
from pen.practice.store import Store
from pen.practice.runtime import set_enabled


TEXT = """# Book
# Level 0
## Meta Question gate
**Q1. Why?**
Answer inside MQ.
〔回读：Teaching〕
## Teaching
PRIVATE_TEACHING_MARKER
"""


def test_resource_index_and_excerpts_use_the_same_single_read(tmp_path, monkeypatch):
    path = tmp_path / "book.md"
    path.write_text(TEXT)
    original = Path.read_text
    reads = []
    def racing_read(self, *args, **kwargs):
        if self == path:
            reads.append(1)
            return TEXT if len(reads) == 1 else "\n" * 4 + "PRIVATE_TEACHING_MARKER\n" * 12
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", racing_read)
    snapshot = extract_meta_questions(path)
    assert len(reads) == 1
    assert len(snapshot["meta_questions"]) == 1
    assert "PRIVATE_TEACHING_MARKER" not in snapshot["meta_questions"][0]["text"]
    assert "Answer inside MQ." in snapshot["meta_questions"][0]["answer"]


def test_missing_reference_answer_is_rejected_before_a_model_call(tmp_path, monkeypatch):
    path = tmp_path / "book.md"
    path.write_text("# Book\n# Level 0\n## Meta Question gate\n**Q1. Why?**\n")
    monkeypatch.setattr(core, "launch", lambda *a: pytest.fail("invalid format launched a paid job"))
    result = core.start_build("scope", "book", path, object())
    assert result["status"] == "failed"
    assert result["total"] == 0


def test_failed_mq_is_not_silently_omitted_from_full_exam_scope():
    store = Store()
    resource = {"resource_version": "r", "meta_questions": [{"id": "ready"}, {"id": "quarantined"}],
                "points": [{"id": "p", "name": "Point", "source_mq_ids": ["ready"]}], "edges": [],
                "questions": [{"id": "q", "source_mq_id": "ready", "purpose": "exam", "exam_form": 0,
                               "status": "accepted", "criteria": [{"point_id": "p", "max_score": 1}]}]}
    store.put("scope", "resource", "r", "book", resource)
    store.put("scope", "active", "book", "book", {"resource_version": "r"})
    store.put("scope", "blueprint", "book", "book", core.default_blueprint(resource))
    with pytest.raises(ValueError, match="do not cover"):
        core.new_session("scope", "book", "exam")
    assert store.list("scope", "session", "book") == []


def test_cancel_stops_before_a_second_model_call(tmp_path, monkeypatch):
    from pen.practice import resources
    path = tmp_path / "book.md"
    path.write_text(TEXT)
    set_enabled("scope", True)
    calls = []
    def provider(system, payload):
        calls.append(system)
        store = Store()
        job = store.list("scope", "job", "book")[0]
        job["cancel_requested"] = True
        store.put("scope", "job", job["id"], "book", job)
        return {}
    def compile(mq, llm, **kwargs):
        llm("generate", {})
        llm("audit", {})
        pytest.fail("cancelled build reached a second call")
    monkeypatch.setattr(core, "_llm", lambda *a: provider)
    monkeypatch.setattr(core, "launch", lambda key, fn, *args: fn(*args))
    monkeypatch.setattr(resources, "compile_meta_question", compile)
    created = core.start_build("scope", "book", path, object())
    result = Store().get("scope", "job", created["id"])
    assert result["status"] == "cancelled"
    assert calls == ["generate"]
