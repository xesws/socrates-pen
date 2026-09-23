from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from pen import libraries
from pen.app import app
from pen.practice.contracts import fingerprint, scope_id, timestamp
from pen.practice.store import Store


BOOK_TEXT = """# Gateway Practice Book

# Level 0

## Fifth beat Meta Question gate

**Q1. shell and Bash?**
- **TL;DR:** shell is a category; Bash is one implementation.
- **Why:** Bash is one implementation.

〔回读：Third beat〕
"""

TWO_MQ_BOOK_TEXT = """# Gateway Practice Book

# Level 0

## Fifth beat Meta Question gate

**Q1. shell and Bash?**
- **TL;DR:** shell is a category; Bash is one implementation.

〔回读：Third beat〕

**Q2. venv?**
- **TL;DR:** venv isolates a Python environment.

〔回读：Third beat〕
"""


def _register(vault: Path, hid: str = "practice-gateway") -> Path:
    vault.mkdir(parents=True, exist_ok=True)
    book = vault / f"{hid}.md"
    book.write_text(BOOK_TEXT, encoding="utf-8")
    libraries.register(book, hid, extra_roots=[vault])
    return book


def _register_text(vault: Path, hid: str, text: str) -> Path:
    vault.mkdir(parents=True, exist_ok=True)
    book = vault / f"{hid}.md"
    book.write_text(text, encoding="utf-8")
    libraries.register(book, hid, extra_roots=[vault])
    return book


def _criterion(point_id: str = "pt_shell", cid: str = "crit_shell") -> dict[str, Any]:
    return {
        "id": cid,
        "point_id": point_id,
        "max_score": 1,
        "description": "states that shell is a category and Bash is one implementation",
        "partial_credit": "0.5 for only one side",
        "evidence": [{"mq_id": "mq", "quote": "shell is a category"}],
    }


def _question(
    qid: str,
    qtype: str,
    *,
    source_mq_id: str,
    purpose: str = "practice",
    exam_form: int | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": qid,
        "version": "v1",
        "source_mq_id": source_mq_id,
        "type": qtype,
        "purpose": purpose,
        "exam_form": exam_form,
        "prompt": f"{qid} prompt",
        "point_ids": ["pt_shell"],
        "status": "accepted",
        "estimated_seconds": 30,
        "reference_answer": "shell is a category; Bash is one implementation",
        "criteria": [_criterion()],
    }
    if qtype == "single_choice":
        base.update(
            {
                "choices": [
                    {"id": "a", "text": "category to implementation"},
                    {"id": "b", "text": "same thing"},
                ],
                "correct_choice_id": "a",
            }
        )
    if qtype == "fill_blank":
        base["blanks"] = [
            {
                "id": "b1",
                "label": "Bash is one ____ of shell",
                "answers": ["implementation"],
                "grading": "exact",
            }
        ]
    return base


def _compiled_part(mq: dict[str, Any]) -> dict[str, Any]:
    point = {
        "id": "pt_shell",
        "name": "Shell category",
        "definition": "shell is a category and Bash is one implementation",
        "source_mq_ids": [mq["id"]],
        "evidence": [{"mq_id": mq["id"], "quote": "shell is a category"}],
    }
    questions = [
        _question("q-practice-choice", "single_choice", source_mq_id=mq["id"]),
        _question("q-practice-fill", "fill_blank", source_mq_id=mq["id"]),
        _question("q-exam-choice", "single_choice", source_mq_id=mq["id"], purpose="exam", exam_form=0),
        _question("q-exam-fill", "fill_blank", source_mq_id=mq["id"], purpose="exam", exam_form=0),
    ]
    return {"points": [point], "edges": [], "questions": questions, "issues": [], "usage": {"prompt_tokens": 1}}


def _single_question_part(mq: dict[str, Any]) -> dict[str, Any]:
    suffix = mq["id"][-8:]
    point_id = f"pt_{suffix}"
    question = {
        "id": f"q_{suffix}",
        "version": "v1",
        "source_mq_id": mq["id"],
        "type": "fill_blank",
        "purpose": "practice",
        "exam_form": None,
        "prompt": f"{mq['title']} blank",
        "point_ids": [point_id],
        "status": "accepted",
        "estimated_seconds": 30,
        "reference_answer": "answer",
        "blanks": [{"id": "b1", "label": "blank", "answers": ["answer"], "grading": "exact"}],
        "criteria": [
            {
                "id": "c1",
                "point_id": point_id,
                "blank_id": "b1",
                "max_score": 1,
                "description": "mapped blank",
                "partial_credit": "none",
                "evidence": [{"mq_id": mq["id"], "quote": "TL;DR"}],
            }
        ],
    }
    point = {
        "id": point_id,
        "name": f"Point {suffix}",
        "definition": mq["title"],
        "source_mq_ids": [mq["id"]],
        "evidence": [{"mq_id": mq["id"], "quote": "TL;DR"}],
    }
    return {"points": [point], "edges": [], "questions": [question], "issues": [], "usage": {}}


def _enable(client: TestClient, vault: Path) -> None:
    response = client.put("/v1/practice/enable", json={"vault_root": str(vault), "enabled": True})
    assert response.status_code == 200, response.text


def _patch_sync_build(monkeypatch, *, mutate_source: Path | None = None) -> None:
    from pen.practice import coordinator as core
    from pen.practice import resources

    class FakeLLM:
        usage = {"prompt_tokens": 7}

        def __call__(self, system: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {}

    def compile_stub(mq: dict[str, Any], llm: Any, **kwargs: Any) -> dict[str, Any]:
        if mutate_source is not None:
            mutate_source.write_text(
                mutate_source.read_text(encoding="utf-8").replace(
                    "Bash is one implementation.",
                    "Bash is one implementation. This edit changes the MQ answer.",
                ),
                encoding="utf-8",
            )
        return _compiled_part(mq)

    monkeypatch.setattr(core, "_llm", lambda cfg, lang="zh": FakeLLM())
    monkeypatch.setattr(core, "launch", lambda key, function, *args: function(*args))
    monkeypatch.setattr(resources, "compile_meta_question", compile_stub)


def _build(client: TestClient, vault: Path, hid: str = "practice-gateway") -> dict[str, Any]:
    response = client.post(
        "/v1/practice/build",
        json={"vault_root": str(vault), "handbook_id": hid, "model": "fake-model"},
    )
    assert response.status_code == 200, response.text
    state = client.get("/v1/practice/state", params={"vault_root": str(vault), "handbook_id": hid})
    assert state.status_code == 200, state.text
    job = state.json()["job"]
    assert job["status"] == "completed", job
    return state.json()


def test_build_practice_score_and_exam_feedback_is_withheld_until_finish(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    vault = tmp_path / "vault"
    _register(vault)
    _patch_sync_build(monkeypatch)

    with TestClient(app) as client:
        _enable(client, vault)
        state = _build(client, vault)
        assert state["resource"]["question_count"] == 4

        public_questions = client.get(
            "/v1/practice/questions",
            params={"vault_root": str(vault), "handbook_id": "practice-gateway"},
        ).json()["questions"]
        by_id = {q["id"]: q for q in public_questions}
        assert set(by_id) == {"q-practice-choice", "q-practice-fill"}
        assert "correct_choice_id" not in by_id["q-practice-choice"]
        assert "reference_answer" not in by_id["q-practice-fill"]
        assert "criteria" not in by_id["q-practice-fill"]

        practice = client.post(
            "/v1/practice/sessions",
            json={
                "vault_root": str(vault),
                "handbook_id": "practice-gateway",
                "mode": "practice",
                "question_ids": ["q-practice-fill"],
            },
        ).json()
        graded = client.post(
            f"/v1/practice/sessions/{practice['id']}/answers",
            json={
                "vault_root": str(vault),
                "question_id": "q-practice-fill",
                "answer": {"blanks": {"b1": " implementation "}},
                "idempotency_key": "practice-fill",
                "duration_seconds": 4,
            },
        ).json()
        attempt = graded["attempts"][0]
        assert attempt["status"] == "graded"
        assert attempt["grade"]["score"] == 1
        assert attempt["reference_answer"]
        assert attempt["criteria"]

        exam = client.post(
            "/v1/practice/sessions",
            json={"vault_root": str(vault), "handbook_id": "practice-gateway", "mode": "exam"},
        ).json()
        exam_submit = client.post(
            f"/v1/practice/sessions/{exam['id']}/answers",
            json={
                "vault_root": str(vault),
                "question_id": "q-exam-fill",
                "answer": {"blanks": {"b1": "implementation"}},
                "idempotency_key": "exam-fill",
                "duration_seconds": 6,
            },
        ).json()
        exam_attempt = exam_submit["attempts"][0]
        assert exam_submit["status"] == "active"
        assert exam_attempt["status"] == "pending"
        assert "grade" not in exam_attempt
        assert "reference_answer" not in exam_attempt
        assert "criteria" not in exam_attempt

        finished = client.post(
            f"/v1/practice/sessions/{exam['id']}/finish",
            json={"vault_root": str(vault)},
        ).json()
        finished_attempt = next(a for a in finished["attempts"] if a["question_id"] == "q-exam-fill")
        assert finished["status"] == "completed"
        assert finished_attempt["grade"]["score"] == 1
        assert finished_attempt["reference_answer"]
        assert finished_attempt["criteria"]


def test_submit_answer_is_idempotent_and_rejects_conflicting_reuse(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    vault = tmp_path / "vault"
    _register(vault)
    _patch_sync_build(monkeypatch)

    with TestClient(app) as client:
        _enable(client, vault)
        _build(client, vault)
        session = client.post(
            "/v1/practice/sessions",
            json={
                "vault_root": str(vault),
                "handbook_id": "practice-gateway",
                "mode": "practice",
                "question_ids": ["q-practice-fill"],
            },
        ).json()
        body = {
            "vault_root": str(vault),
            "question_id": "q-practice-fill",
            "answer": {"blanks": {"b1": "implementation"}},
            "idempotency_key": "same-key",
            "duration_seconds": 1,
        }

        first = client.post(f"/v1/practice/sessions/{session['id']}/answers", json=body)
        second = client.post(f"/v1/practice/sessions/{session['id']}/answers", json=body)
        conflict = client.post(
            f"/v1/practice/sessions/{session['id']}/answers",
            json={**body, "answer": {"blanks": {"b1": "wrong"}}},
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert len(second.json()["attempts"]) == 1
        assert conflict.status_code == 409
        attempts = Store().list(scope_id(str(vault)), "attempt", "practice-gateway")
        assert len(attempts) == 1


def test_vault_scope_blocks_other_vault_from_registered_book(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    owner = tmp_path / "owner"
    other = tmp_path / "other"
    other.mkdir()
    _register(owner)
    _patch_sync_build(monkeypatch)

    with TestClient(app) as client:
        _enable(client, owner)
        _enable(client, other)
        _build(client, owner)
        forbidden = client.get(
            "/v1/practice/state",
            params={"vault_root": str(other), "handbook_id": "practice-gateway"},
        )
        assert forbidden.status_code == 403


def test_build_goes_stale_if_meta_questions_change_during_generation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    vault = tmp_path / "vault"
    book = _register(vault)
    _patch_sync_build(monkeypatch, mutate_source=book)

    with TestClient(app) as client:
        _enable(client, vault)
        response = client.post(
            "/v1/practice/build",
            json={"vault_root": str(vault), "handbook_id": "practice-gateway", "model": "fake-model"},
        )
        assert response.status_code == 200, response.text
        state = client.get(
            "/v1/practice/state",
            params={"vault_root": str(vault), "handbook_id": "practice-gateway"},
        ).json()
        assert state["job"]["status"] == "stale"
        assert state["resource"] is None


def test_cancelled_queued_build_can_resume_inline(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    vault = tmp_path / "vault"
    _register(vault)

    from pen.practice import coordinator as core
    from pen.practice import resources

    class FakeLLM:
        usage = {}

        def __call__(self, system: str, payload: dict[str, Any]) -> dict[str, Any]:
            return {}

    monkeypatch.setattr(core, "_llm", lambda cfg, lang="zh": FakeLLM())
    monkeypatch.setattr(resources, "compile_meta_question", lambda mq, llm, **kwargs: _compiled_part(mq))

    with TestClient(app) as client:
        _enable(client, vault)
        monkeypatch.setattr(core, "launch", lambda key, function, *args: None)
        created = client.post(
            "/v1/practice/build",
            json={"vault_root": str(vault), "handbook_id": "practice-gateway", "model": "fake-model"},
        ).json()
        assert created["status"] == "queued"
        cancelled = client.post(
            f"/v1/practice/jobs/{created['id']}/cancel",
            json={"vault_root": str(vault)},
        ).json()
        assert cancelled["cancel_requested"] is True

        monkeypatch.setattr(core, "launch", lambda key, function, *args: function(*args))
        resumed = client.post(
            f"/v1/practice/jobs/{created['id']}/resume",
            json={"vault_root": str(vault), "model": "fake-model"},
        )
        assert resumed.status_code == 200, resumed.text
        job = client.get(
            f"/v1/practice/jobs/{created['id']}",
            params={"vault_root": str(vault)},
        ).json()
        assert job["status"] == "completed"
        assert job["cancel_requested"] is False


def test_malformed_answer_is_rejected_before_attempt_persistence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    vault = tmp_path / "vault"
    _register(vault)
    _patch_sync_build(monkeypatch)

    with TestClient(app) as client:
        _enable(client, vault)
        _build(client, vault)
        session = client.post(
            "/v1/practice/sessions",
            json={
                "vault_root": str(vault),
                "handbook_id": "practice-gateway",
                "mode": "practice",
                "question_ids": ["q-practice-fill"],
            },
        ).json()

        bad = client.post(
            f"/v1/practice/sessions/{session['id']}/answers",
            json={
                "vault_root": str(vault),
                "question_id": "q-practice-fill",
                "answer": {"blanks": []},
                "idempotency_key": "bad-answer",
                "duration_seconds": 1,
            },
        )

        assert bad.status_code == 409
        assert Store().list(scope_id(str(vault)), "attempt", "practice-gateway") == []
        after = client.get(
            f"/v1/practice/sessions/{session['id']}",
            params={"vault_root": str(vault)},
        ).json()
        assert after["attempts"] == []


def test_idempotency_key_conflicts_when_skip_flag_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    vault = tmp_path / "vault"
    _register(vault)
    _patch_sync_build(monkeypatch)

    with TestClient(app) as client:
        _enable(client, vault)
        _build(client, vault)
        session = client.post(
            "/v1/practice/sessions",
            json={
                "vault_root": str(vault),
                "handbook_id": "practice-gateway",
                "mode": "practice",
                "question_ids": ["q-practice-fill"],
            },
        ).json()
        body = {
            "vault_root": str(vault),
            "question_id": "q-practice-fill",
            "answer": {"blanks": {"b1": "implementation"}},
            "idempotency_key": "skip-flip",
            "duration_seconds": 1,
        }

        skipped = client.post(f"/v1/practice/sessions/{session['id']}/answers", json={**body, "skip": True})
        replayed = client.post(f"/v1/practice/sessions/{session['id']}/answers", json={**body, "skip": False})

        assert skipped.status_code == 200, skipped.text
        assert skipped.json()["attempts"][0]["status"] == "skipped"
        assert replayed.status_code == 409


def test_retry_does_not_regrade_or_emit_duplicate_event_for_graded_attempt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    vault = tmp_path / "vault"
    _register(vault)
    _patch_sync_build(monkeypatch)

    from pen.practice import grading

    original = grading.grade_answer
    calls = 0

    def counting_grade(question: dict[str, Any], answer: dict[str, Any], llm: Any = None) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return original(question, answer, llm=llm)

    monkeypatch.setattr(grading, "grade_answer", counting_grade)

    with TestClient(app) as client:
        _enable(client, vault)
        _build(client, vault)
        session = client.post(
            "/v1/practice/sessions",
            json={
                "vault_root": str(vault),
                "handbook_id": "practice-gateway",
                "mode": "practice",
                "question_ids": ["q-practice-fill"],
            },
        ).json()
        graded = client.post(
            f"/v1/practice/sessions/{session['id']}/answers",
            json={
                "vault_root": str(vault),
                "question_id": "q-practice-fill",
                "answer": {"blanks": {"b1": "implementation"}},
                "idempotency_key": "one-grade",
                "duration_seconds": 1,
            },
        ).json()
        aid = graded["attempts"][0]["id"]
        retry = client.post(f"/v1/practice/attempts/{aid}/retry", json={"vault_root": str(vault)})

        assert retry.status_code == 200, retry.text
        assert calls == 1
        events = Store().events(scope_id(str(vault)), "practice-gateway")
        assert [event["type"] for event in events] == ["attempt_graded", "session_completed"]
        assert [event["payload"]["id"] for event in events if event["type"] == "attempt_graded"] == [aid]


def test_resume_interrupted_job_retries_failed_part_before_advancing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    hid = "practice-two-mq"
    vault = tmp_path / "vault"
    book = _register_text(vault, hid, TWO_MQ_BOOK_TEXT)

    from pen.practice import coordinator as core
    from pen.practice import resources

    snapshot = resources.extract_meta_questions(book)
    first_mq, second_mq = snapshot["meta_questions"]
    sid = scope_id(str(vault))
    jid = "interrupted-job"
    job = {
        "id": jid,
        "handbook_id": hid,
        "status": "interrupted",
        "completed": 1,
        "total": 2,
        "source_revision": snapshot["source_revision"],
        "path": str(book),
        "snapshot": snapshot,
        "issues": [{"mq_id": first_mq["id"], "message": "first attempt failed"}],
        "created_at": timestamp(),
        "practice_per_type": 1,
        "exam_forms": 1,
        "usage": {},
        "cancel_requested": False,
        "parts": [{"points": [], "edges": [], "questions": [], "issues": [{"mq_id": first_mq["id"], "message": "failed"}]}],
        "lang": "zh",
        "regenerate": False,
    }
    Store().put(sid, "job", jid, hid, job)
    calls: list[str] = []

    class FakeLLM:
        usage: dict[str, Any] = {}

    def compile_stub(mq: dict[str, Any], llm: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(mq["id"])
        return _single_question_part(mq)

    monkeypatch.setattr(core, "_llm", lambda cfg, lang="zh": FakeLLM())
    monkeypatch.setattr(core, "launch", lambda key, function, *args: function(*args))
    monkeypatch.setattr(resources, "compile_meta_question", compile_stub)

    with TestClient(app) as client:
        _enable(client, vault)
        resumed = client.post(
            f"/v1/practice/jobs/{jid}/resume",
            json={"vault_root": str(vault), "model": "fake-model"},
        )
        assert resumed.status_code == 200, resumed.text

    assert calls == [first_mq["id"], second_mq["id"]]
    resource = Store().get(sid, "resource", Store().get(sid, "active", hid)["resource_version"])
    assert {q["source_mq_id"] for q in resource["questions"]} == {first_mq["id"], second_mq["id"]}
