from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pen.practice import models
from pen.practice.scheduler import recommend
from pen.practice.worker_store import WorkerStore


def _snapshot() -> dict[str, Any]:
    return {
        "resource_version": "rv1",
        "points": [
            {"id": "p-weak", "name": "Weak point"},
            {"id": "p-strong", "name": "Strong point"},
            {"id": "p-cold", "name": "Cold point"},
        ],
        "edges": [],
        "blueprint": {
            "id": "bp1",
            "version": "b1",
            "point_weights": {"p-weak": 2, "p-strong": 1, "p-cold": 1},
            "target_score": 80,
            "daily_minutes": 6,
        },
        "questions": [
            _question("q-weak", "p-weak", "single_choice", seconds=90),
            _question("q-weak-fill", "p-weak", "fill_blank", seconds=80),
            _question("q-strong", "p-strong", "short_answer", seconds=120),
            _question("q-cold", "p-cold", "short_answer", seconds=60),
            _question("q-exam", "p-weak", "short_answer", purpose="exam"),
            {**_question("q-retired", "p-cold", "single_choice"), "status": "retired"},
        ],
    }


def _question(qid: str, point_id: str, qtype: str, *, purpose: str = "practice", seconds: int = 60) -> dict[str, Any]:
    return {
        "id": qid,
        "version": "v1",
        "source_mq_id": "mq1",
        "type": qtype,
        "purpose": purpose,
        "exam_form": 0 if purpose == "exam" else None,
        "prompt": qid,
        "point_ids": [point_id],
        "status": "accepted",
        "estimated_seconds": seconds,
        "criteria": [{"id": "c1", "point_id": point_id, "max_score": 1}],
    }


def _attempt(
    seq: int,
    aid: str,
    q: dict[str, Any],
    score: float,
    at: datetime,
    *,
    status: str = "graded",
    purpose: str | None = None,
    decision_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": aid,
        "session_id": "s1",
        "question_id": q["id"],
        "question_version": q["version"],
        "source_version": "rv1",
        "purpose": purpose or q["purpose"],
        "answer": {},
        "status": status,
        "created_at": (at - timedelta(minutes=2)).isoformat(),
        "answered_at": at.isoformat(),
        "duration_seconds": 120,
        "question": q,
    }
    if decision_id:
        payload["decision_id"] = decision_id
    if status == "graded":
        payload["grade"] = {
            "criteria": [
                {
                    "criterion_id": "c1",
                    "point_id": q["point_ids"][0],
                    "score": score,
                    "max_score": 1,
                    "reason": "synthetic",
                    "evidence_quote": "",
                }
            ],
            "score": score,
            "max_score": 1,
            "model": "test",
        }
    return {"seq": seq, "type": "attempt_graded", "payload": payload, "created_at": at.isoformat()}


def test_analysis_uses_only_graded_criterion_scores_and_freezes_same_attempt_features() -> None:
    snapshot = _snapshot()
    q = snapshot["questions"][0]
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    multi = _attempt(1, "a1", q, 1.0, at)
    multi["payload"]["grade"]["criteria"].append(
        {
            "criterion_id": "c2",
            "point_id": "p-weak",
            "score": 0.5,
            "max_score": 1,
            "reason": "synthetic",
            "evidence_quote": "",
        }
    )
    multi["payload"]["grade"]["criteria"].append(
        {
            "criterion_id": "c3",
            "point_id": "p-strong",
            "score": 0.0,
            "max_score": 1,
            "reason": "synthetic",
            "evidence_quote": "",
        }
    )
    pending = _attempt(2, "a2", q, 0.0, at + timedelta(minutes=5), status="pending")
    examples = models.graded_examples(snapshot, [multi, pending])
    assert [(e.point_id, e.value) for e in examples] == [("p-weak", 0.75), ("p-strong", 0.0)]
    rows = models.build_learning_rows(examples)
    assert [r.prior_count for r in rows] == [0, 0]
    assert [round(r.prior_score, 2) for r in rows] == [0.5, 0.5]


def test_same_exam_grades_are_unavailable_until_graded_at() -> None:
    snapshot = _snapshot()
    q = snapshot["questions"][0]
    start = datetime(2026, 1, 1, 9, tzinfo=timezone.utc)
    first = _attempt(1, "exam-1", q, 1.0, start, purpose="exam")
    second = _attempt(2, "exam-2", q, 0.0, start + timedelta(minutes=10), purpose="exam")
    for event in (first, second):
        event["payload"]["session_id"] = "exam-session"
        event["payload"]["graded_at"] = (start + timedelta(hours=2)).isoformat()
        event["created_at"] = event["payload"]["graded_at"]
    rows = models.build_learning_rows(models.graded_examples(snapshot, [first, second]))
    assert [r.prior_count for r in rows] == [0, 0]


def test_analysis_trains_fractional_logistic_after_temporal_gates() -> None:
    snapshot = {
        "resource_version": "rv1",
        "points": [{"id": "p-mixed", "name": "Mixed point"}],
        "edges": [],
        "blueprint": {"target_score": 80},
        "questions": [
            _question("q-low", "p-mixed", "single_choice"),
            _question("q-high", "p-mixed", "fill_blank"),
        ],
    }
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events: list[dict[str, Any]] = []
    questions = {q["id"]: q for q in snapshot["questions"]}
    seq = 1
    for i in range(130):
        at = start + timedelta(days=i * 2)
        events.append(_attempt(seq, f"lo{i}", questions["q-low"], 0.1, at))
        seq += 1
        events.append(_attempt(seq, f"hi{i}", questions["q-high"], 0.9, at + timedelta(days=1)))
        seq += 1
    examples = models.graded_examples(snapshot, events)
    artifact = models.fit_analysis_model(examples, previous_model=None, now=start + timedelta(days=140))
    assert artifact["status"] == "trained"
    assert artifact["metrics"]["mae"] <= artifact["metrics"]["baseline_mae"] * 0.95
    elapsed_idx = artifact["features"].index("elapsed_days")
    assert artifact["weights"][elapsed_idx] <= 0
    state = {"p-mixed": {"sum": 100.0, "count": 200, "last_seen": start}}
    low = models.expected_score_for_point("p-mixed", state, artifact, question_type="single_choice", at=start + timedelta(days=150))
    high = models.expected_score_for_point("p-mixed", state, artifact, question_type="fill_blank", at=start + timedelta(days=150))
    assert low is not None and high is not None and low < high


def test_analysis_report_marks_under_sampled_points_unknown() -> None:
    snapshot = _snapshot()
    now = datetime(2026, 1, 3, tzinfo=timezone.utc)
    events = [_attempt(1, "a1", snapshot["questions"][0], 0.3, now)]
    report, artifact = models.build_analysis_report(snapshot, events, previous_model=None, now=now, cursor=1)
    assert artifact["status"] == "baseline"
    by_id = {p["id"]: p for p in report["points"]}
    assert by_id["p-weak"]["status"] == "insufficient"
    assert by_id["p-cold"]["status"] == "unknown"
    assert by_id["p-cold"]["score"] is None


def test_repeating_one_question_does_not_make_a_point_stable() -> None:
    snapshot = _snapshot()
    now = datetime(2026, 2, 1, tzinfo=timezone.utc)
    q = snapshot["questions"][0]
    events = [
        _attempt(i + 1, f"a{i}", q, 1.0, now - timedelta(days=10 - i))
        for i in range(6)
    ]
    report, _artifact = models.build_analysis_report(snapshot, events, previous_model=None, now=now, cursor=6)
    point = {p["id"]: p for p in report["points"]}["p-weak"]
    assert point["n"] == 6
    assert point["distinct_questions"] == 1
    assert point["repeated_attempts"] == 5
    assert point["status"] == "insufficient"


def test_review_due_interval_advances_one_step_and_resets_on_wrong_answer() -> None:
    snapshot = _snapshot()
    q = snapshot["questions"][0]
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)

    first = [_attempt(1, "first", q, 1.0, start)]
    report, _artifact = models.build_analysis_report(snapshot, first, previous_model=None, now=start, cursor=1)
    point = {p["id"]: p for p in report["points"]}["p-weak"]
    assert models.parse_time(point["due_at"]) == start + timedelta(days=3)

    second_at = start + timedelta(days=1)
    second = first + [_attempt(2, "second", q, 1.0, second_at)]
    report, _artifact = models.build_analysis_report(snapshot, second, previous_model=None, now=second_at, cursor=2)
    point = {p["id"]: p for p in report["points"]}["p-weak"]
    assert models.parse_time(point["due_at"]) == second_at + timedelta(days=7)

    third_at = start + timedelta(days=2)
    third = second + [_attempt(3, "third", q, 0.0, third_at)]
    report, _artifact = models.build_analysis_report(snapshot, third, previous_model=None, now=third_at, cursor=3)
    point = {p["id"]: p for p in report["points"]}["p-weak"]
    assert models.parse_time(point["due_at"]) == third_at + timedelta(days=1)


def test_recommendation_budget_respects_subminute_request() -> None:
    snapshot = _snapshot()
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    analysis = {
        "points": [
            {"id": "p-weak", "name": "Weak point", "n": 0, "score": None, "expected_score": None, "status": "unknown", "due_at": now.isoformat()},
            {"id": "p-cold", "name": "Cold point", "n": 0, "score": None, "expected_score": None, "status": "unknown", "due_at": now.isoformat()},
            {"id": "p-strong", "name": "Strong point", "n": 0, "score": None, "expected_score": None, "status": "unknown", "due_at": now.isoformat()},
        ],
        "cursor": 0,
    }
    response, _model, chosen = models.build_recommendations(
        snapshot,
        [],
        analysis,
        [],
        previous_model=None,
        now=now,
        cursor=0,
        minutes=0.5,
        scope="scope1",
        handbook_id="book1",
    )
    assert response["items"] == []
    assert chosen == []


def test_scheduler_excludes_exam_retired_recent_questions_and_persists_decisions(tmp_path: Path) -> None:
    store = WorkerStore.open(tmp_path, service="scheduling")
    snapshot = _snapshot()
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    recent = _attempt(11, "recent", snapshot["questions"][0], 0.2, now - timedelta(hours=2))
    old = _attempt(25, "old", snapshot["questions"][2], 1.0, now - timedelta(days=10))
    result = store.sync(scope="scope1", handbook_id="book1", snapshot=snapshot, events=[recent, old])
    assert result.cursor == 25
    analysis = {
        "points": [
            {"id": "p-weak", "name": "Weak point", "n": 4, "score": 0.2, "expected_score": 0.2, "status": "weak", "due_at": now.isoformat()},
            {"id": "p-cold", "name": "Cold point", "n": 0, "score": None, "expected_score": None, "status": "unknown", "due_at": now.isoformat()},
            {"id": "p-strong", "name": "Strong point", "n": 8, "score": 0.95, "expected_score": 0.95, "status": "stable", "due_at": (now + timedelta(days=14)).isoformat()},
        ],
        "cursor": 25,
    }
    out = recommend(store, scope="scope1", handbook_id="book1", analysis=analysis, now=now.isoformat(), minutes=3)
    ids = {item["question_id"] for item in out["items"]}
    assert "q-weak" not in ids
    assert "q-exam" not in ids
    assert "q-retired" not in ids
    assert ids
    assert all(0 < item["probability"] <= 1 for item in out["items"])
    persisted = store.decisions("scope1", "book1")
    assert {d["decision_id"] for d in persisted} == {item["decision_id"] for item in out["items"]}
    assert all(d["candidates"] for d in persisted)
    store.close()


def test_scheduler_reward_requires_prior_exam_baseline_and_observed_followup() -> None:
    snapshot = _snapshot()
    questions = {q["id"]: q for q in snapshot["questions"]}
    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    prior_exam = _attempt(1, "exam-before", questions["q-exam"], 0.4, start, purpose="exam")
    practice = _attempt(2, "practice", questions["q-weak"], 1.0, start + timedelta(days=1), decision_id="d1")
    followup = _attempt(3, "exam-after", questions["q-exam"], 0.8, start + timedelta(days=5), purpose="exam")
    followup["payload"]["graded_at"] = (start + timedelta(days=5, hours=2)).isoformat()
    no_baseline = _attempt(4, "practice-cold", questions["q-cold"], 1.0, start + timedelta(days=1), decision_id="d2")
    decisions = [
        {
            "decision_id": "d1",
            "created_at": (start + timedelta(days=1, minutes=-5)).isoformat(),
            "point_id": "p-weak",
            "question_id": "q-weak",
            "question_type": "single_choice",
            "propensity": 0.5,
            "context": {"gap": 0.5},
            "candidates": [{"point_id": "p-weak", "question_type": "single_choice", "context": {"gap": 0.5}}],
        },
        {
            "decision_id": "d2",
            "created_at": (start + timedelta(days=1, minutes=-5)).isoformat(),
            "point_id": "p-cold",
            "question_id": "q-cold",
            "question_type": "short_answer",
            "propensity": 0.5,
            "context": {"gap": 0.5},
            "candidates": [{"point_id": "p-cold", "question_type": "short_answer", "context": {"gap": 0.5}}],
        },
    ]
    rewards, completed = models.scheduler_rewards(
        decisions,
        models.graded_examples(snapshot, [prior_exam, practice, followup, no_baseline]),
    )
    assert completed == 2
    assert [(r.decision_id, round(r.reward, 2), r.observed_at) for r in rewards] == [
        ("d1", 0.4, models.parse_time(followup["payload"]["graded_at"]))
    ]


def test_scheduler_model_trains_and_promotes_better_action_from_delayed_exam_rewards() -> None:
    decisions, examples = _scheduler_training_fixture(reverse_holdout=False)
    rewards, completed = models.scheduler_rewards(decisions, examples)
    assert completed == 260 and len(rewards) == 260
    rewards.sort(key=lambda r: (r.observed_at, r.decision_id))
    holdout = rewards[-52:]
    train = rewards[:-52]
    assert max(r.observed_at for r in train) < min(r.observed_at for r in holdout)

    artifact = models.fit_scheduler_model(
        decisions,
        examples,
        previous_model=None,
        now=datetime(2042, 1, 1, tzinfo=timezone.utc),
        resource_version="rv-scheduler",
    )
    assert artifact["status"] == "trained"
    assert artifact["metrics"]["ci_low"] > artifact["metrics"]["baseline_reward"]
    ranked = models.rank_candidates(_scheduler_rank_candidates(), model=artifact)
    assert [item["question_id"] for item in ranked[:2]] == ["q-good", "q-bad"]


def test_scheduler_model_falls_back_when_holdout_rejects_training_policy() -> None:
    decisions, examples = _scheduler_training_fixture(reverse_holdout=True)
    artifact = models.fit_scheduler_model(
        decisions,
        examples,
        previous_model=None,
        now=datetime(2042, 1, 1, tzinfo=timezone.utc),
        resource_version="rv-scheduler",
    )
    assert artifact["status"] == "baseline"
    assert artifact["reason"] == "ope-gate-failed"
    assert artifact["metrics"]["ci_low"] <= artifact["metrics"]["baseline_reward"]


def _scheduler_training_fixture(*, reverse_holdout: bool) -> tuple[list[dict[str, Any]], list[models.ScoreExample]]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    decisions: list[dict[str, Any]] = []
    examples: list[models.ScoreExample] = []
    seq = 1
    for i in range(260):
        is_good_action = i % 2 == 0
        point_id = "p-good" if is_good_action else "p-bad"
        qtype = "single_choice" if is_good_action else "short_answer"
        question_id = "q-good" if is_good_action else "q-bad"
        holdout = i >= 208
        good_is_better = not (reverse_holdout and holdout)
        reward = 0.4 if (is_good_action == good_is_better) else -0.2
        baseline = 0.4 if reward > 0 else 0.6
        followup = baseline + reward
        created_at = start + timedelta(days=i * 20 + 1)
        completed_at = created_at + timedelta(hours=1)
        exam_before = created_at - timedelta(days=1)
        exam_after = completed_at + timedelta(days=3)
        observed_after = exam_after + timedelta(hours=4)
        did = f"d{i}"
        decisions.append(
            {
                "decision_id": did,
                "created_at": created_at.isoformat(),
                "point_id": point_id,
                "question_id": question_id,
                "question_type": qtype,
                "propensity": 0.5,
                "context": _scheduler_context(point_id),
                "candidates": _scheduler_rank_candidates(),
            }
        )
        examples.extend(
            [
                _score_example(
                    seq,
                    attempt_id=f"before-{i}",
                    point_id=point_id,
                    question_id=f"exam-before-{point_id}",
                    qtype="short_answer",
                    score=baseline,
                    answered_at=exam_before,
                    available_at=exam_before + timedelta(hours=2),
                    purpose="exam",
                ),
                _score_example(
                    seq + 1,
                    attempt_id=f"practice-{i}",
                    point_id=point_id,
                    question_id=question_id,
                    qtype=qtype,
                    score=1.0,
                    answered_at=completed_at,
                    available_at=completed_at,
                    purpose="practice",
                    decision_id=did,
                ),
                _score_example(
                    seq + 2,
                    attempt_id=f"after-{i}",
                    point_id=point_id,
                    question_id=f"exam-after-{point_id}",
                    qtype="short_answer",
                    score=followup,
                    answered_at=exam_after,
                    available_at=observed_after,
                    purpose="exam",
                ),
            ]
        )
        seq += 3
    return decisions, examples


def _score_example(
    seq: int,
    *,
    attempt_id: str,
    point_id: str,
    question_id: str,
    qtype: str,
    score: float,
    answered_at: datetime,
    available_at: datetime,
    purpose: str,
    decision_id: str | None = None,
) -> models.ScoreExample:
    return models.ScoreExample(
        attempt_id=attempt_id,
        session_id=f"session-{attempt_id}",
        question_id=question_id,
        question_type=qtype,
        point_id=point_id,
        criterion_id="c1",
        score=score,
        max_score=1.0,
        answered_at=answered_at,
        available_at=available_at,
        seq=seq,
        purpose=purpose,
        decision_id=decision_id,
        created_at=answered_at - timedelta(minutes=2),
    )


def _scheduler_rank_candidates() -> list[dict[str, Any]]:
    return [
        {
            "question_id": "q-good",
            "point_id": "p-good",
            "question_type": "single_choice",
            "estimated_seconds": 60,
            "due_at": "2026-01-01T00:00:00+00:00",
            "reason": "synthetic good",
            "context": _scheduler_context("p-good"),
            "cold_score": 0.5,
        },
        {
            "question_id": "q-bad",
            "point_id": "p-bad",
            "question_type": "short_answer",
            "estimated_seconds": 60,
            "due_at": "2026-01-01T00:00:00+00:00",
            "reason": "synthetic bad",
            "context": _scheduler_context("p-bad"),
            "cold_score": 0.5,
        },
    ]


def _scheduler_context(point_id: str) -> dict[str, float]:
    if point_id == "p-good":
        return {
            "gap": 0.8,
            "due": 1.0,
            "evidence_lack": 0.2,
            "coverage_gap": 0.7,
            "novelty": 0.5,
            "cold_score": 0.5,
            "estimated_seconds": 60,
        }
    return {
        "gap": 0.2,
        "due": 0.4,
        "evidence_lack": 0.8,
        "coverage_gap": 0.2,
        "novelty": 0.5,
        "cold_score": 0.5,
        "estimated_seconds": 60,
    }
