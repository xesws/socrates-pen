"""Learning models for the experimental practice workers.

The worker layer deliberately passes plain dictionaries across HTTP.  This
module keeps the math and contract normalization independent from FastAPI and
SQLite so the gateway can exercise it without starting child processes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from pen.practice.contracts import fingerprint, timestamp

ANALYSIS_MODEL_KIND = "analysis_model"
SCHEDULER_MODEL_KIND = "scheduler_model"
ANALYSIS_MODEL_SCHEMA = 1
SCHEDULER_MODEL_SCHEMA = 1
QUESTION_TYPES = ("single_choice", "fill_blank", "short_answer")
DUE_INTERVAL_DAYS = (1, 3, 7, 14, 30)


@dataclass(frozen=True)
class ScoreExample:
    attempt_id: str
    session_id: str
    question_id: str
    question_type: str
    point_id: str
    criterion_id: str
    score: float
    max_score: float
    answered_at: datetime
    available_at: datetime
    seq: int
    purpose: str
    decision_id: str | None = None
    created_at: datetime | None = None

    @property
    def value(self) -> float:
        if self.max_score <= 0:
            return 0.0
        return _clamp(self.score / self.max_score, 0.0, 1.0)


@dataclass(frozen=True)
class LearningRow:
    attempt_id: str
    point_id: str
    question_type: str
    question_id: str
    answered_at: datetime
    target: float
    prior_score: float
    prior_count: int
    repeat_count: int
    elapsed_days: float


@dataclass(frozen=True)
class SchedulerReward:
    decision_id: str
    point_id: str
    question_type: str
    reward: float
    propensity: float
    created_at: datetime
    observed_at: datetime
    context: dict[str, Any]
    candidates: list[dict[str, Any]]
    action_key: str


def parse_time(value: Any, *, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            dt = fallback or datetime.now(timezone.utc)
    else:
        dt = fallback or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_now(value: Any = None) -> datetime:
    return parse_time(value, fallback=datetime.now(timezone.utc))


def normalize_score(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return _clamp(f, 0.0, 1.0)


def extract_points(snapshot: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    points: dict[str, dict[str, Any]] = {}
    for raw in (snapshot or {}).get("points") or []:
        if not isinstance(raw, dict):
            continue
        pid = str(raw.get("id") or "").strip()
        if pid:
            points[pid] = raw
    return points


def extract_questions(snapshot: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    questions: dict[str, dict[str, Any]] = {}
    for raw in (snapshot or {}).get("questions") or []:
        if not isinstance(raw, dict):
            continue
        qid = str(raw.get("id") or "").strip()
        if qid:
            questions[qid] = raw
    return questions


def graded_examples(snapshot: dict[str, Any] | None, events: Iterable[dict[str, Any]]) -> list[ScoreExample]:
    """Turn enriched attempt events into per-criterion point scores.

    Only graded attempts with bounded numeric scores are used.  Pending, failed,
    skipped, and malformed attempts stay in the event log but do not become
    evidence for the analysis model.
    """

    questions = extract_questions(snapshot)
    out: list[ScoreExample] = []
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "attempt_graded":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("status") != "graded":
            continue
        attempt_id = str(payload.get("id") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        question_id = str(payload.get("question_id") or "").strip()
        if not attempt_id or not question_id:
            continue
        q = payload.get("question")
        if not isinstance(q, dict):
            q = questions.get(question_id, {})
        grade = payload.get("grade")
        if not isinstance(grade, dict):
            continue
        answered_at = parse_time(payload.get("answered_at") or payload.get("created_at") or event.get("created_at"))
        available_at = parse_time(
            payload.get("graded_at") or event.get("created_at") or payload.get("answered_at"),
            fallback=answered_at,
        )
        seq = _as_int(event.get("seq"), default=0)
        qtype = str(q.get("type") or payload.get("question_type") or "").strip()
        if qtype not in QUESTION_TYPES:
            qtype = "short_answer"
        purpose = str(payload.get("purpose") or q.get("purpose") or "practice")
        criteria_meta = _criteria_by_id(q)
        rows = grade.get("criteria")
        if isinstance(rows, list) and rows:
            grouped: dict[str, dict[str, Any]] = {}
            for idx, criterion in enumerate(rows):
                if not isinstance(criterion, dict):
                    continue
                cid = str(criterion.get("criterion_id") or criterion.get("id") or f"c{idx}")
                meta = criteria_meta.get(cid, {})
                point_id = str(criterion.get("point_id") or meta.get("point_id") or "").strip()
                if not point_id:
                    point_id = _first_point(q)
                score = _as_float(criterion.get("score"))
                max_score = _as_float(criterion.get("max_score"))
                if max_score is None:
                    max_score = _as_float(meta.get("max_score"))
                if not point_id or score is None or max_score is None or max_score <= 0:
                    continue
                acc = grouped.setdefault(point_id, {"score": 0.0, "max_score": 0.0, "criteria": []})
                acc["score"] += score
                acc["max_score"] += max_score
                acc["criteria"].append(cid)
            for point_id, acc in grouped.items():
                out.append(
                    ScoreExample(
                        attempt_id=attempt_id,
                        session_id=session_id,
                        question_id=question_id,
                        question_type=qtype,
                        point_id=point_id,
                        criterion_id="+".join(sorted(acc["criteria"])),
                        score=float(acc["score"]),
                        max_score=float(acc["max_score"]),
                        answered_at=answered_at,
                        available_at=available_at,
                        seq=seq,
                        purpose=purpose,
                        decision_id=_optional_str(payload.get("decision_id")),
                        created_at=parse_time(payload.get("created_at"), fallback=answered_at),
                    )
                )
            continue

    out.sort(key=lambda e: (e.answered_at, e.seq, e.attempt_id, e.criterion_id))
    return out


def build_learning_rows(examples: list[ScoreExample]) -> list[LearningRow]:
    """Create pre-event features, freezing all criteria from one attempt together."""

    by_attempt: dict[str, list[ScoreExample]] = {}
    order: list[str] = []
    for ex in examples:
        if ex.attempt_id not in by_attempt:
            by_attempt[ex.attempt_id] = []
            order.append(ex.attempt_id)
        by_attempt[ex.attempt_id].append(ex)
    order.sort(key=lambda aid: (by_attempt[aid][0].answered_at, by_attempt[aid][0].seq, aid))
    available = sorted(examples, key=lambda e: (e.available_at, e.answered_at, e.seq, e.attempt_id, e.point_id))

    point_sum: dict[str, float] = {}
    point_count: dict[str, int] = {}
    last_seen: dict[str, datetime] = {}
    question_count: dict[str, int] = {}
    ingested: set[tuple[str, str]] = set()
    available_idx = 0
    rows: list[LearningRow] = []

    def ingest(ex: ScoreExample) -> None:
        key = (ex.attempt_id, ex.point_id)
        if key in ingested:
            return
        point_sum[ex.point_id] = point_sum.get(ex.point_id, 0.0) + ex.value
        point_count[ex.point_id] = point_count.get(ex.point_id, 0) + 1
        last_seen[ex.point_id] = max(ex.answered_at, last_seen.get(ex.point_id, ex.answered_at))
        question_count[ex.question_id] = question_count.get(ex.question_id, 0) + 1
        ingested.add(key)

    for aid in order:
        group = sorted(by_attempt[aid], key=lambda e: e.criterion_id)
        first_time = group[0].answered_at
        current_keys = {(ex.attempt_id, ex.point_id) for ex in group}
        while available_idx < len(available) and available[available_idx].available_at < first_time:
            candidate = available[available_idx]
            available_idx += 1
            if (candidate.attempt_id, candidate.point_id) not in current_keys:
                ingest(candidate)
        frozen: list[LearningRow] = []
        for ex in group:
            n = point_count.get(ex.point_id, 0)
            total = point_sum.get(ex.point_id, 0.0)
            prior = (total + 1.5) / (n + 3.0)
            prev = last_seen.get(ex.point_id)
            elapsed = 30.0 if prev is None else max(0.0, (first_time - prev).total_seconds() / 86400.0)
            frozen.append(
                LearningRow(
                    attempt_id=aid,
                    point_id=ex.point_id,
                    question_type=ex.question_type,
                    question_id=ex.question_id,
                    answered_at=first_time,
                    target=ex.value,
                    prior_score=prior,
                    prior_count=n,
                    repeat_count=question_count.get(ex.question_id, 0),
                    elapsed_days=min(elapsed, 365.0),
                )
            )
        rows.extend(frozen)
    return rows


def point_summaries(
    snapshot: dict[str, Any] | None,
    examples: list[ScoreExample],
    *,
    now: datetime,
    model: dict[str, Any] | None = None,
    target: float = 0.8,
) -> list[dict[str, Any]]:
    points = extract_points(snapshot)
    values: dict[str, list[ScoreExample]] = {pid: [] for pid in points}
    for ex in examples:
        if points and ex.point_id not in points:
            continue
        values.setdefault(ex.point_id, []).append(ex)

    summaries: list[dict[str, Any]] = []
    state = _history_state(examples)
    goal_date = _target_date(snapshot)
    for pid in sorted(values or points):
        raw_point = points.get(pid, {})
        point_examples = sorted(values.get(pid, []), key=lambda e: (e.answered_at, e.seq))
        n = len(point_examples)
        score = None
        last_seen = None
        evidence: list[dict[str, Any]] = []
        if n:
            score = sum(e.value for e in point_examples) / n
            last_seen = iso(point_examples[-1].answered_at)
            evidence = [
                {
                    "attempt_id": e.attempt_id,
                    "question_id": e.question_id,
                    "score": round(e.value, 4),
                    "at": iso(e.answered_at),
                }
                for e in point_examples[-3:]
            ]
        distinct_questions = len({e.question_id for e in point_examples})
        expected = expected_score_for_point(pid, state, model, at=now)
        status = point_status(n, score, expected, distinct_questions=distinct_questions)
        due = due_at_for_status(
            now,
            point_examples[-1].answered_at if point_examples else None,
            score,
            expected,
            n,
            interval_days=_review_interval_days(point_examples),
            point_id=pid,
            state=state,
            model=model,
            target=target,
            target_date=goal_date,
        )
        summaries.append(
            {
                "id": pid,
                "name": raw_point.get("name") or pid,
                "n": n,
                "score": None if score is None else round(score, 4),
                "expected_score": None if expected is None else round(expected, 4),
                "status": status,
                "distinct_questions": distinct_questions,
                "repeated_attempts": max(0, n - distinct_questions),
                "evidence": evidence,
                "last_seen": last_seen,
                "due_at": iso(due) if due else None,
            }
        )
    return summaries


def build_analysis_report(
    snapshot: dict[str, Any] | None,
    events: list[dict[str, Any]],
    *,
    previous_model: dict[str, Any] | None,
    now: datetime,
    cursor: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    examples = graded_examples(snapshot, events)
    current_points = set(extract_points(snapshot))
    if current_points:
        examples = [e for e in examples if e.point_id in current_points]
    examples = [e for e in examples if e.available_at <= now]
    resource_version = (snapshot or {}).get("resource_version")
    model = fit_analysis_model(examples, previous_model=previous_model, now=now, resource_version=resource_version)
    target = _target_score(snapshot)
    points = point_summaries(snapshot, examples, now=now, model=model, target=target)
    directions = [
        {
            "point_id": p["id"],
            "name": p["name"],
            "reason": p["status"],
            "gap": round(max(0.0, target - _analysis_value_for_gap(p)), 4),
            "n": p["n"],
        }
        for p in points
        if p["status"] in ("unknown", "weak", "insufficient")
    ]
    directions.sort(key=lambda d: (-d["gap"], d["n"], d["point_id"]))
    report = {
        "points": points,
        "directions": directions[:10],
        "model": public_model(model),
        "resource_version": (snapshot or {}).get("resource_version"),
        "cursor": cursor,
    }
    return report, model


def fit_analysis_model(
    examples: list[ScoreExample],
    *,
    previous_model: dict[str, Any] | None,
    now: datetime,
    resource_version: Any = None,
) -> dict[str, Any]:
    attempts = _attempt_times(examples)
    attempt_count = len(attempts)
    delayed = _delayed_point_gap_count(examples)
    resource = str(resource_version or "")
    if _trained_enough_to_reuse(previous_model, attempt_count, step=50, resource_version=resource):
        return dict(previous_model or {})

    gates = {"attempts": attempt_count, "delayed_attempts": delayed, "holdout_attempts": 0}
    if attempt_count < 100 or delayed < 30:
        return _fallback_analysis_model(
            examples,
            now=now,
            reason="insufficient-training-data",
            gates=gates,
            previous_model=previous_model,
            resource_version=resource,
        )

    rows = build_learning_rows(examples)
    holdout_count = max(20, math.ceil(attempt_count * 0.2))
    if attempt_count <= holdout_count:
        return _fallback_analysis_model(
            examples,
            now=now,
            reason="insufficient-holdout",
            gates=gates,
            previous_model=previous_model,
            resource_version=resource,
        )
    attempt_order = [aid for aid, _t in attempts]
    holdout_ids = set(attempt_order[-holdout_count:])
    train_rows = [r for r in rows if r.attempt_id not in holdout_ids]
    holdout_rows = [r for r in rows if r.attempt_id in holdout_ids]
    gates["holdout_attempts"] = holdout_count
    if not train_rows or not holdout_rows:
        return _fallback_analysis_model(
            examples,
            now=now,
            reason="empty-train-or-holdout",
            gates=gates,
            previous_model=previous_model,
            resource_version=resource,
        )

    features = _analysis_feature_names(train_rows)
    weights = _train_logistic(train_rows, features)
    preds = [_predict_logistic(weights, features, r) for r in holdout_rows]
    ys = [r.target for r in holdout_rows]
    base_preds = [r.prior_score for r in holdout_rows]
    mae = _mae(preds, ys)
    rmse = _rmse(preds, ys)
    base_mae = _mae(base_preds, ys)
    base_rmse = _rmse(base_preds, ys)
    elapsed_weight = weights[features.index("elapsed_days")] if "elapsed_days" in features else 0.0
    pass_quality = mae <= base_mae * 0.95 and rmse <= base_rmse and elapsed_weight <= 1e-9
    metrics = {
        "mae": round(mae, 6),
        "rmse": round(rmse, 6),
        "baseline_mae": round(base_mae, 6),
        "baseline_rmse": round(base_rmse, 6),
        "elapsed_weight": round(elapsed_weight, 8),
    }
    if not pass_quality:
        return _fallback_analysis_model(
            examples,
            now=now,
            reason="validation-gate-failed",
            gates=gates,
            previous_model=previous_model,
            resource_version=resource,
            metrics=metrics,
        )

    artifact = {
        "kind": ANALYSIS_MODEL_KIND,
        "schema": ANALYSIS_MODEL_SCHEMA,
        "status": "trained",
        "trained_at": iso(now),
        "training_attempts": attempt_count,
        "training_rows": len(rows),
        "features": features,
        "weights": [round(w, 12) for w in weights],
        "metrics": metrics,
        "gates": gates,
        "resource_version": resource,
    }
    artifact["version"] = fingerprint({"kind": ANALYSIS_MODEL_KIND, "schema": ANALYSIS_MODEL_SCHEMA, "resource_version": resource, "features": features, "weights": artifact["weights"], "metrics": metrics})[:16]
    return artifact


def expected_score_for_point(
    point_id: str,
    state: dict[str, dict[str, Any]],
    model: dict[str, Any] | None,
    *,
    question_type: str = "short_answer",
    question_id: str = "",
    at: datetime | None = None,
) -> float | None:
    point = state.get(point_id, {})
    n = int(point.get("count") or 0)
    prior = (float(point.get("sum") or 0.0) + 1.5) / (n + 3.0)
    last = point.get("last_seen")
    now = at or datetime.now(timezone.utc)
    elapsed = 30.0
    if isinstance(last, datetime):
        elapsed = max(0.0, (now - last).total_seconds() / 86400.0)
    row = LearningRow(
        attempt_id="prediction",
        point_id=point_id,
        question_type=question_type,
        question_id=question_id,
        answered_at=now,
        target=prior,
        prior_score=prior,
        prior_count=n,
        repeat_count=0,
        elapsed_days=elapsed,
    )
    if model and model.get("status") == "trained":
        features = list(model.get("features") or [])
        weights = [float(x) for x in model.get("weights") or []]
        if len(features) == len(weights):
            return _predict_logistic(weights, features, row)
    if n == 0:
        return None
    return _clamp(prior, 0.0, 1.0)


def point_status(n: int, score: float | None, expected: float | None, *, distinct_questions: int = 0) -> str:
    if n == 0:
        return "unknown"
    if n < 3:
        return "insufficient"
    value = expected if expected is not None else score
    if value is None:
        return "unknown"
    if value < 0.6:
        return "weak"
    if n < 5 or distinct_questions < 3:
        return "insufficient"
    if value >= 0.8:
        return "stable"
    return "insufficient"


def _review_interval_days(point_examples: list[ScoreExample]) -> int:
    interval = DUE_INTERVAL_DAYS[0]
    for ex in sorted(point_examples, key=lambda e: (e.answered_at, e.seq, e.attempt_id)):
        if ex.value < 0.6:
            interval = DUE_INTERVAL_DAYS[0]
            continue
        if ex.value >= 0.9:
            try:
                idx = DUE_INTERVAL_DAYS.index(interval)
            except ValueError:
                idx = 0
            interval = DUE_INTERVAL_DAYS[min(idx + 1, len(DUE_INTERVAL_DAYS) - 1)]
    return interval


def due_at_for_status(
    now: datetime,
    last_seen: datetime | None,
    score: float | None,
    expected: float | None,
    n: int,
    *,
    interval_days: int | None = None,
    point_id: str | None = None,
    state: dict[str, dict[str, Any]] | None = None,
    model: dict[str, Any] | None = None,
    target: float = 0.8,
    target_date: datetime | None = None,
) -> datetime:
    if last_seen is None or n == 0:
        return now
    if point_id and state and model and model.get("status") == "trained":
        due = _model_due_at(point_id, state, model, last_seen=last_seen, now=now, target=target)
        if target_date is not None and due > target_date:
            due = target_date
        return max(due, now) if due < now else due
    interval = interval_days if interval_days in DUE_INTERVAL_DAYS else DUE_INTERVAL_DAYS[0]
    due = last_seen + timedelta(days=interval)
    if target_date is not None and due > target_date:
        due = target_date
    return due


def public_model(model: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(model or {})
    return {
        "status": raw.get("status") or "baseline",
        "version": raw.get("version"),
        "trained_at": raw.get("trained_at"),
        "training_attempts": raw.get("training_attempts", 0),
        "training_rows": raw.get("training_rows", 0),
        "metrics": raw.get("metrics", {}),
        "gates": raw.get("gates", {}),
        "reason": raw.get("reason"),
        "reused": bool(raw.get("reused")),
        "resource_version": raw.get("resource_version"),
    }


def build_recommendations(
    snapshot: dict[str, Any] | None,
    events: list[dict[str, Any]],
    analysis: dict[str, Any] | None,
    decisions: list[dict[str, Any]],
    *,
    previous_model: dict[str, Any] | None,
    now: datetime,
    cursor: int,
    minutes: float | None = None,
    scope: str = "",
    handbook_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    examples = graded_examples(snapshot, events)
    current_points = set(extract_points(snapshot))
    if current_points:
        examples = [e for e in examples if e.point_id in current_points]
    examples = [e for e in examples if e.available_at <= now]
    resource_version = str((snapshot or {}).get("resource_version") or "")
    model = fit_scheduler_model(decisions, examples, previous_model=previous_model, now=now, resource_version=resource_version)
    candidates = recommendation_candidates(snapshot, examples, analysis or {}, now=now)
    if not candidates:
        return {"items": [], "model": public_model(model), "cursor": cursor}, model, []

    budget_minutes = float(minutes) if minutes is not None else float(_blueprint_minutes(snapshot))
    budget = max(1, int(budget_minutes * 60))
    ranked = rank_candidates(candidates, model=model)
    chosen = _choose_recommendation_items(
        ranked,
        now=now,
        budget_seconds=budget,
        scope=scope,
        handbook_id=handbook_id,
        cursor=cursor,
    )
    report_items = [
        {
            "question_id": item["question_id"],
            "decision_id": item["decision_id"],
            "point_id": item["point_id"],
            "reason": item["reason"],
            "estimated_seconds": item["estimated_seconds"],
            "due_at": item["due_at"],
            "probability": item["probability"],
        }
        for item in chosen
    ]
    response = {"items": report_items, "model": public_model(model), "cursor": cursor}
    return response, model, chosen


def recommendation_candidates(
    snapshot: dict[str, Any] | None,
    examples: list[ScoreExample],
    analysis: dict[str, Any],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    snapshot = snapshot or {}
    questions = extract_questions(snapshot)
    recent_questions = _recent_question_ids(examples, now=now)
    analysis_points = {str(p.get("id")): p for p in analysis.get("points") or [] if isinstance(p, dict) and p.get("id")}
    blueprint = snapshot.get("blueprint") if isinstance(snapshot.get("blueprint"), dict) else {}
    weights = _normalized_weights(blueprint.get("point_weights") if isinstance(blueprint, dict) else {})
    target = _target_score(snapshot)
    candidates: list[dict[str, Any]] = []
    for qid, q in questions.items():
        if q.get("purpose") == "exam" or q.get("status", "accepted") != "accepted" or qid in recent_questions:
            continue
        if q.get("type") not in QUESTION_TYPES:
            continue
        point_ids = [str(p) for p in q.get("point_ids") or [] if str(p)]
        if not point_ids:
            for c in q.get("criteria") or []:
                if isinstance(c, dict) and c.get("point_id"):
                    point_ids.append(str(c["point_id"]))
        if not point_ids:
            continue
        for pid in point_ids:
            ap = analysis_points.get(pid, {})
            n = _as_int(ap.get("n"), default=0)
            score = normalize_score(ap.get("expected_score"))
            if score is None:
                score = normalize_score(ap.get("score"))
            gap = _clamp(target - (score if score is not None else 0.5), 0.0, 1.0)
            due_at = parse_time(ap.get("due_at"), fallback=now) if ap.get("due_at") else now
            due = 1.0 if due_at <= now else _clamp(1.0 - (due_at - now).total_seconds() / (30.0 * 86400.0), 0.0, 1.0)
            evidence_lack = _clamp(1.0 - min(n, 3) / 3.0, 0.0, 1.0)
            coverage_gap = weights.get(pid, 0.0) if n > 0 else max(weights.get(pid, 0.0), 0.5)
            novelty = _question_novelty(qid, examples, now)
            cold_score = 0.35 * gap + 0.30 * due + 0.20 * evidence_lack + 0.10 * coverage_gap + 0.05 * novelty
            candidates.append(
                {
                    "question_id": qid,
                    "point_id": pid,
                    "question_type": q["type"],
                    "estimated_seconds": int(q.get("estimated_seconds") or 60),
                    "due_at": iso(due_at),
                    "reason": _candidate_reason(ap, gap, due, evidence_lack),
                    "context": {
                        "gap": round(gap, 6),
                        "due": round(due, 6),
                        "evidence_lack": round(evidence_lack, 6),
                        "coverage_gap": round(coverage_gap, 6),
                        "novelty": round(novelty, 6),
                        "cold_score": round(cold_score, 6),
                        "estimated_seconds": int(q.get("estimated_seconds") or 60),
                    },
                    "cold_score": cold_score,
                }
            )
    return candidates


def rank_candidates(candidates: list[dict[str, Any]], *, model: dict[str, Any] | None) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for c in candidates:
        item = dict(c)
        score = float(c.get("cold_score") or 0.0)
        if model and model.get("status") == "trained":
            score += _predict_scheduler_reward(model, c)
        item["rank_score"] = score
        ranked.append(item)
    ranked.sort(key=lambda x: (-float(x["rank_score"]), x["point_id"], x["question_type"], x["question_id"]))
    return ranked


def fit_scheduler_model(
    decisions: list[dict[str, Any]],
    examples: list[ScoreExample],
    *,
    previous_model: dict[str, Any] | None,
    now: datetime,
    resource_version: str = "",
) -> dict[str, Any]:
    rewards, completed = scheduler_rewards(decisions, examples)
    reward_count = len(rewards)
    gates = {"completed_recommendations": completed, "rewards": reward_count, "holdout_rewards": 0}
    if _trained_enough_to_reuse(previous_model, completed, step=50, resource_version=resource_version):
        return dict(previous_model or {})
    if completed < 200 or reward_count < 50:
        return _fallback_scheduler_model(
            now=now,
            reason="insufficient-training-data",
            gates=gates,
            previous_model=previous_model,
            resource_version=resource_version,
        )

    rewards.sort(key=lambda r: (r.observed_at, r.decision_id))
    holdout_count = max(10, math.ceil(reward_count * 0.2))
    if reward_count <= holdout_count:
        return _fallback_scheduler_model(
            now=now,
            reason="insufficient-holdout",
            gates=gates,
            previous_model=previous_model,
            resource_version=resource_version,
        )
    train = rewards[:-holdout_count]
    holdout = rewards[-holdout_count:]
    gates["holdout_rewards"] = holdout_count
    features = _scheduler_feature_names(train)
    weights = _train_linear(train, features)
    ess, policy_reward, ci_low, baseline_reward = _ope_gate(holdout, features, weights)
    gates["ess"] = round(ess, 4)
    metrics = {
        "policy_reward": round(policy_reward, 6) if policy_reward is not None else None,
        "ci_low": round(ci_low, 6) if ci_low is not None else None,
        "baseline_reward": round(baseline_reward, 6),
    }
    if policy_reward is None or ess < max(10.0, holdout_count * 0.10) or ci_low <= baseline_reward:
        return _fallback_scheduler_model(
            now=now,
            reason="ope-gate-failed",
            gates=gates,
            previous_model=previous_model,
            resource_version=resource_version,
            metrics=metrics,
        )
    artifact = {
        "kind": SCHEDULER_MODEL_KIND,
        "schema": SCHEDULER_MODEL_SCHEMA,
        "status": "trained",
        "trained_at": iso(now),
        "training_attempts": completed,
        "training_rows": reward_count,
        "features": features,
        "weights": [round(w, 12) for w in weights],
        "metrics": metrics,
        "gates": gates,
        "resource_version": resource_version,
    }
    artifact["version"] = fingerprint({"kind": SCHEDULER_MODEL_KIND, "resource_version": resource_version, "features": features, "weights": artifact["weights"], "metrics": metrics})[:16]
    return artifact


def scheduler_rewards(decisions: list[dict[str, Any]], examples: list[ScoreExample]) -> tuple[list[SchedulerReward], int]:
    decision_by_id: dict[str, dict[str, Any]] = {
        str(d.get("decision_id")): d for d in decisions if isinstance(d, dict) and d.get("decision_id")
    }
    completion: dict[str, datetime] = {}
    for ex in examples:
        if ex.decision_id and ex.decision_id in decision_by_id and ex.purpose != "exam":
            current = completion.get(ex.decision_id)
            completion[ex.decision_id] = ex.available_at if current is None else min(current, ex.available_at)
    completed = len(completion)
    examples_sorted = sorted(examples, key=lambda e: (e.available_at, e.answered_at, e.seq))
    rewards: list[SchedulerReward] = []
    for did, completed_at in completion.items():
        dec = decision_by_id[did]
        point_id = str(dec.get("point_id") or "")
        if not point_id:
            continue
        baseline = _latest_exam_score_before(examples_sorted, point_id, parse_time(dec.get("created_at"), fallback=completed_at))
        if baseline is None:
            continue
        followup = _followup_exam_score(decisions, completion, examples_sorted, did, point_id, completed_at)
        if followup is None:
            continue
        followup_score, observed_at = followup
        reward = _clamp(followup_score - baseline, -1.0, 1.0)
        propensity = float(dec.get("propensity") or dec.get("probability") or 0.0)
        if propensity <= 0.0:
            continue
        qtype = str(dec.get("question_type") or "short_answer")
        rewards.append(
            SchedulerReward(
                decision_id=did,
                point_id=point_id,
                question_type=qtype,
                reward=reward,
                propensity=min(propensity, 1.0),
                created_at=parse_time(dec.get("created_at"), fallback=completed_at),
                observed_at=observed_at,
                context=dict(dec.get("context") or {}),
                candidates=list(dec.get("candidates") or []),
                action_key=f"{point_id}:{qtype}",
            )
        )
    return rewards, completed


def _choose_recommendation_items(
    ranked: list[dict[str, Any]],
    *,
    now: datetime,
    budget_seconds: int,
    scope: str,
    handbook_id: str,
    cursor: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    used_questions: set[str] = set()
    used_seconds = 0
    slot = 0
    while used_seconds < budget_seconds:
        remaining = budget_seconds - used_seconds
        eligible = [
            c
            for c in ranked
            if c["question_id"] not in used_questions and max(30, int(c.get("estimated_seconds") or 60)) <= remaining
        ]
        if not eligible:
            break
        groups: dict[str, list[dict[str, Any]]] = {}
        for c in eligible:
            groups.setdefault(_candidate_action_key(c), []).append(c)
        group_order = sorted(groups, key=lambda k: (-float(groups[k][0]["rank_score"]), k))
        if not group_order:
            break
        seed = fingerprint(
            {
                "scope": scope,
                "handbook_id": handbook_id,
                "cursor": cursor,
                "now": iso(now),
                "slot": slot,
                "remaining": remaining,
                "used_questions": sorted(used_questions),
                "n": len(group_order),
            }
        )
        explore = (int(seed[:8], 16) / 0xFFFFFFFF) >= 0.90 and len(group_order) > 1
        chosen_group = group_order[int(seed[8:16], 16) % len(group_order)] if explore else group_order[0]
        probability = (0.10 / len(group_order)) + (0.90 if chosen_group == group_order[0] else 0.0)
        question = groups[chosen_group][0]
        seconds = max(30, int(question.get("estimated_seconds") or 60))
        decision_id = fingerprint(
            {
                "scope": scope,
                "handbook_id": handbook_id,
                "cursor": cursor,
                "now": iso(now),
                "question_id": question["question_id"],
                "point_id": question["point_id"],
                "slot": slot,
            }
        )[:32]
        candidates = [
            {
                "question_id": c["question_id"],
                "point_id": c["point_id"],
                "question_type": c["question_type"],
                "rank_score": round(float(c["rank_score"]), 6),
                "context": c["context"],
            }
            for c in eligible
        ]
        item = dict(question)
        item.update(
            {
                "decision_id": decision_id,
                "probability": round(probability, 6),
                "propensity": round(probability, 6),
                "created_at": iso(now),
                "candidates": candidates,
                "status": "pending",
            }
        )
        out.append(item)
        used_questions.add(question["question_id"])
        used_seconds += seconds
        slot += 1
    return out


def _analysis_feature_names(rows: list[LearningRow]) -> list[str]:
    points = sorted({r.point_id for r in rows})
    qtypes = sorted({r.question_type for r in rows})
    return [
        "intercept",
        "prior_score",
        "prior_count_log",
        "repeat_count_log",
        "elapsed_days",
        *[f"point:{p}" for p in points],
        *[f"type:{t}" for t in qtypes],
    ]


def _analysis_vector(features: list[str], row: LearningRow) -> list[float]:
    values: list[float] = []
    for f in features:
        if f == "intercept":
            values.append(1.0)
        elif f == "prior_score":
            values.append(row.prior_score)
        elif f == "prior_count_log":
            values.append(math.log1p(row.prior_count) / math.log(101.0))
        elif f == "repeat_count_log":
            values.append(math.log1p(row.repeat_count) / math.log(101.0))
        elif f == "elapsed_days":
            values.append(min(row.elapsed_days, 60.0) / 60.0)
        elif f.startswith("point:"):
            values.append(1.0 if row.point_id == f[6:] else 0.0)
        elif f.startswith("type:"):
            values.append(1.0 if row.question_type == f[5:] else 0.0)
        else:
            values.append(0.0)
    return values


def _train_logistic(rows: list[LearningRow], features: list[str]) -> list[float]:
    mean = _clamp(sum(r.target for r in rows) / len(rows), 1e-4, 1 - 1e-4)
    weights = [0.0] * len(features)
    if "intercept" in features:
        weights[features.index("intercept")] = math.log(mean / (1.0 - mean))
    elapsed_idx = features.index("elapsed_days") if "elapsed_days" in features else -1
    l2 = 0.015
    lr = 0.12
    vectors = [_analysis_vector(features, r) for r in rows]
    for epoch in range(420):
        grad = [0.0] * len(weights)
        for x, row in zip(vectors, rows):
            p = _sigmoid(_dot(weights, x))
            err = p - row.target
            for i, xi in enumerate(x):
                grad[i] += err * xi
        scale = 1.0 / max(1, len(rows))
        step = lr / math.sqrt(1.0 + epoch / 40.0)
        for i in range(len(weights)):
            penalty = 0.0 if features[i] == "intercept" else l2 * weights[i]
            weights[i] -= step * (grad[i] * scale + penalty)
        if elapsed_idx >= 0 and weights[elapsed_idx] > 0.0:
            weights[elapsed_idx] = 0.0
    return weights


def _predict_logistic(weights: list[float], features: list[str], row: LearningRow) -> float:
    return _clamp(_sigmoid(_dot(weights, _analysis_vector(features, row))), 0.0, 1.0)


def _fallback_analysis_model(
    examples: list[ScoreExample],
    *,
    now: datetime,
    reason: str,
    gates: dict[str, Any],
    previous_model: dict[str, Any] | None,
    resource_version: str = "",
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if previous_model and previous_model.get("status") == "trained" and str(previous_model.get("resource_version") or "") == resource_version:
        reused = dict(previous_model)
        reused["reused"] = True
        reused["reason"] = reason
        return reused
    values = [e.value for e in examples]
    baseline = sum(values) / len(values) if values else 0.5
    by_point = _history_state(examples)
    point_means = {
        pid: round((float(st["sum"]) + 1.5) / (int(st["count"]) + 3.0), 6)
        for pid, st in sorted(by_point.items())
    }
    artifact = {
        "kind": ANALYSIS_MODEL_KIND,
        "schema": ANALYSIS_MODEL_SCHEMA,
        "status": "baseline",
        "trained_at": iso(now),
        "training_attempts": len(_attempt_times(examples)),
        "training_rows": len(examples),
        "baseline": round(baseline, 6),
        "point_means": point_means,
        "metrics": metrics or {},
        "gates": gates,
        "reason": reason,
        "resource_version": resource_version,
    }
    artifact["version"] = fingerprint({"kind": ANALYSIS_MODEL_KIND, "schema": ANALYSIS_MODEL_SCHEMA, "resource_version": resource_version, "baseline": artifact["baseline"], "points": point_means, "reason": reason})[:16]
    return artifact


def _fallback_scheduler_model(
    *,
    now: datetime,
    reason: str,
    gates: dict[str, Any],
    previous_model: dict[str, Any] | None,
    resource_version: str = "",
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if previous_model and previous_model.get("status") == "trained" and str(previous_model.get("resource_version") or "") == resource_version:
        reused = dict(previous_model)
        reused["reused"] = True
        reused["reason"] = reason
        return reused
    artifact = {
        "kind": SCHEDULER_MODEL_KIND,
        "schema": SCHEDULER_MODEL_SCHEMA,
        "status": "baseline",
        "trained_at": iso(now),
        "training_attempts": int(gates.get("completed_recommendations") or 0),
        "training_rows": int(gates.get("rewards") or 0),
        "metrics": metrics or {},
        "gates": gates,
        "reason": reason,
        "resource_version": resource_version,
    }
    artifact["version"] = fingerprint({"kind": SCHEDULER_MODEL_KIND, "schema": SCHEDULER_MODEL_SCHEMA, "resource_version": resource_version, "reason": reason, "gates": gates})[:16]
    return artifact


def _scheduler_feature_names(rows: list[SchedulerReward]) -> list[str]:
    points = sorted({r.point_id for r in rows})
    qtypes = sorted({r.question_type for r in rows})
    return [
        "intercept",
        "gap",
        "due",
        "evidence_lack",
        "coverage_gap",
        "novelty",
        "cold_score",
        "estimated_seconds",
        *[f"point:{p}" for p in points],
        *[f"type:{t}" for t in qtypes],
    ]


def _scheduler_vector_from_parts(features: list[str], point_id: str, qtype: str, context: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for f in features:
        if f == "intercept":
            values.append(1.0)
        elif f == "estimated_seconds":
            values.append(min(float(context.get(f) or 60.0), 1800.0) / 1800.0)
        elif f in {"gap", "due", "evidence_lack", "coverage_gap", "novelty", "cold_score"}:
            values.append(_clamp(float(context.get(f) or 0.0), 0.0, 1.0))
        elif f.startswith("point:"):
            values.append(1.0 if point_id == f[6:] else 0.0)
        elif f.startswith("type:"):
            values.append(1.0 if qtype == f[5:] else 0.0)
        else:
            values.append(0.0)
    return values


def _scheduler_vector(features: list[str], row: SchedulerReward) -> list[float]:
    return _scheduler_vector_from_parts(features, row.point_id, row.question_type, row.context)


def _train_linear(rows: list[SchedulerReward], features: list[str]) -> list[float]:
    dim = len(features)
    xtx = [[0.0 for _ in range(dim)] for _ in range(dim)]
    xty = [0.0 for _ in range(dim)]
    l2 = 0.05
    for row in rows:
        x = _scheduler_vector(features, row)
        y = row.reward
        for i in range(dim):
            xty[i] += x[i] * y
            for j in range(dim):
                xtx[i][j] += x[i] * x[j]
    for i, f in enumerate(features):
        if f != "intercept":
            xtx[i][i] += l2
    return _solve_linear(xtx, xty)


def _predict_scheduler_reward(model: dict[str, Any], candidate: dict[str, Any]) -> float:
    features = list(model.get("features") or [])
    weights = [float(w) for w in model.get("weights") or []]
    if not features or len(features) != len(weights):
        return 0.0
    x = _scheduler_vector_from_parts(features, str(candidate.get("point_id") or ""), str(candidate.get("question_type") or ""), dict(candidate.get("context") or {}))
    return _clamp(_dot(weights, x), -1.0, 1.0)


def _ope_gate(rows: list[SchedulerReward], features: list[str], weights: list[float]) -> tuple[float, float | None, float | None, float]:
    baseline = sum(r.reward for r in rows) / len(rows)
    weighted: list[tuple[float, float]] = []
    for row in rows:
        best = _policy_best_action(row.candidates, features, weights)
        if best and best == row.action_key:
            w = 1.0 / max(row.propensity, 1e-6)
            weighted.append((w, row.reward))
    if not weighted:
        return 0.0, None, None, baseline
    weight_sum = sum(w for w, _r in weighted)
    weight_sq = sum(w * w for w, _r in weighted)
    ess = (weight_sum * weight_sum) / weight_sq if weight_sq else 0.0
    mean = sum(w * r for w, r in weighted) / weight_sum
    variance = sum(w * ((r - mean) ** 2) for w, r in weighted) / weight_sum
    stderr = math.sqrt(max(variance, 0.0) / max(ess, 1.0))
    return ess, mean, mean - 1.96 * stderr, baseline


def _policy_best_action(candidates: list[dict[str, Any]], features: list[str], weights: list[float]) -> str | None:
    best_key = None
    best_score = -1e9
    for c in candidates:
        if not isinstance(c, dict):
            continue
        point_id = str(c.get("point_id") or "")
        qtype = str(c.get("question_type") or "")
        if not point_id or not qtype:
            continue
        context = dict(c.get("context") or {})
        x = _scheduler_vector_from_parts(features, point_id, qtype, context)
        score = _dot(weights, x)
        if score > best_score:
            best_score = score
            best_key = f"{point_id}:{qtype}"
    return best_key


def _history_state(examples: list[ScoreExample]) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    for ex in sorted(examples, key=lambda e: (e.answered_at, e.seq)):
        st = state.setdefault(ex.point_id, {"sum": 0.0, "count": 0, "last_seen": ex.answered_at})
        st["sum"] = float(st["sum"]) + ex.value
        st["count"] = int(st["count"]) + 1
        st["last_seen"] = max(st["last_seen"], ex.answered_at)
    return state


def _attempt_times(examples: list[ScoreExample]) -> list[tuple[str, datetime]]:
    seen: dict[str, datetime] = {}
    for ex in examples:
        if ex.attempt_id not in seen or ex.answered_at < seen[ex.attempt_id]:
            seen[ex.attempt_id] = ex.answered_at
    return sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))


def _delayed_point_gap_count(examples: list[ScoreExample]) -> int:
    by_point: dict[str, dict[str, datetime]] = {}
    for ex in examples:
        current = by_point.setdefault(ex.point_id, {})
        if ex.attempt_id not in current or ex.answered_at < current[ex.attempt_id]:
            current[ex.attempt_id] = ex.answered_at
    count = 0
    for attempts in by_point.values():
        prev: datetime | None = None
        for _aid, at in sorted(attempts.items(), key=lambda kv: (kv[1], kv[0])):
            if prev is not None and (at - prev).total_seconds() >= 86400:
                count += 1
            prev = at
    return count


def _trained_enough_to_reuse(
    model: dict[str, Any] | None,
    count: int,
    *,
    step: int,
    resource_version: str = "",
) -> bool:
    if not model or model.get("status") != "trained":
        return False
    if resource_version and str(model.get("resource_version") or "") != resource_version:
        return False
    trained_on = _as_int(model.get("training_attempts"), default=0)
    return count < trained_on + step


def _target_score(snapshot: dict[str, Any] | None) -> float:
    bp = (snapshot or {}).get("blueprint")
    raw = bp.get("target_score") if isinstance(bp, dict) else None
    val = _as_float(raw)
    if val is None:
        return 0.8
    if val > 1.0:
        val /= 100.0
    return _clamp(val, 0.0, 1.0)


def _target_date(snapshot: dict[str, Any] | None) -> datetime | None:
    bp = (snapshot or {}).get("blueprint")
    raw = bp.get("target_date") if isinstance(bp, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _analysis_value_for_gap(point: dict[str, Any]) -> float:
    raw = point.get("expected_score")
    if raw is None:
        raw = point.get("score")
    if raw is None:
        return 0.5
    return _clamp(float(raw), 0.0, 1.0)


def _model_due_at(
    point_id: str,
    state: dict[str, dict[str, Any]],
    model: dict[str, Any],
    *,
    last_seen: datetime,
    now: datetime,
    target: float,
) -> datetime:
    selected = DUE_INTERVAL_DAYS[0]
    for days in DUE_INTERVAL_DAYS:
        # Due dates are point-level; recommendation ranking later scores concrete question types.
        forecast = expected_score_for_point(
            point_id,
            state,
            model,
            at=last_seen + timedelta(days=days),
        )
        if forecast is not None and forecast >= target:
            selected = days
        else:
            break
    due = last_seen + timedelta(days=selected)
    return due if due >= now else now


def _blueprint_minutes(snapshot: dict[str, Any] | None) -> int:
    bp = (snapshot or {}).get("blueprint")
    raw = bp.get("daily_minutes") if isinstance(bp, dict) else None
    return max(1, _as_int(raw, default=20))


def _normalized_weights(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    vals: dict[str, float] = {}
    for k, v in raw.items():
        f = _as_float(v)
        if f is not None and f > 0:
            vals[str(k)] = f
    total = sum(vals.values())
    if total <= 0:
        return vals
    return {k: v / total for k, v in vals.items()}


def _recent_question_ids(examples: list[ScoreExample], *, now: datetime) -> set[str]:
    cutoff = now - timedelta(hours=24)
    return {ex.question_id for ex in examples if ex.answered_at >= cutoff}


def _question_novelty(question_id: str, examples: list[ScoreExample], now: datetime) -> float:
    last = None
    for ex in examples:
        if ex.question_id == question_id and (last is None or ex.answered_at > last):
            last = ex.answered_at
    if last is None:
        return 1.0
    return _clamp((now - last).total_seconds() / (30.0 * 86400.0), 0.0, 1.0)


def _candidate_reason(ap: dict[str, Any], gap: float, due: float, evidence_lack: float) -> str:
    name = ap.get("name") or ap.get("id") or "point"
    if evidence_lack >= 0.99:
        return f"{name}: needs first evidence"
    if gap >= 0.2:
        return f"{name}: target gap {gap:.2f}"
    if due >= 1.0:
        return f"{name}: review due"
    return f"{name}: reinforce"


def _candidate_action_key(c: dict[str, Any]) -> str:
    return f"{c.get('point_id')}:{c.get('question_type')}"


def _criteria_by_id(q: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for raw in q.get("criteria") or []:
        if isinstance(raw, dict) and raw.get("id"):
            out[str(raw["id"])] = raw
    return out


def _first_point(q: dict[str, Any]) -> str:
    point_ids = q.get("point_ids")
    if isinstance(point_ids, list) and point_ids:
        return str(point_ids[0])
    criteria = q.get("criteria")
    if isinstance(criteria, list):
        for c in criteria:
            if isinstance(c, dict) and c.get("point_id"):
                return str(c["point_id"])
    return ""


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-min(x, 700.0))
        return 1.0 / (1.0 + z)
    z = math.exp(max(x, -700.0))
    return z / (1.0 + z)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _mae(pred: list[float], y: list[float]) -> float:
    return sum(abs(a - b) for a, b in zip(pred, y)) / max(1, len(y))


def _rmse(pred: list[float], y: list[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(pred, y)) / max(1, len(y)))


def _solve_linear(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    aug = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-10:
            aug[col][col] += 1e-6
            pivot = col
        aug[col], aug[pivot] = aug[pivot], aug[col]
        div = aug[col][col]
        if abs(div) < 1e-12:
            continue
        for j in range(col, n + 1):
            aug[col][j] /= div
        for r in range(n):
            if r == col:
                continue
            factor = aug[r][col]
            if factor == 0:
                continue
            for j in range(col, n + 1):
                aug[r][j] -= factor * aug[col][j]
    return [aug[i][n] for i in range(n)]


def _latest_exam_score_before(examples: list[ScoreExample], point_id: str, at: datetime) -> float | None:
    by_attempt: dict[str, list[ScoreExample]] = {}
    for ex in examples:
        if ex.purpose == "exam" and ex.point_id == point_id and ex.available_at < at:
            by_attempt.setdefault(ex.attempt_id, []).append(ex)
    if not by_attempt:
        return None
    latest = max(by_attempt.values(), key=lambda group: max(ex.available_at for ex in group))
    return sum(ex.value for ex in latest) / len(latest)


def _followup_exam_score(
    decisions: list[dict[str, Any]],
    completion: dict[str, datetime],
    examples: list[ScoreExample],
    decision_id: str,
    point_id: str,
    completed_at: datetime,
) -> tuple[float, datetime] | None:
    decision_points = {
        str(d.get("decision_id")): str(d.get("point_id") or "")
        for d in decisions
        if isinstance(d, dict) and d.get("decision_id")
    }
    start = completed_at + timedelta(days=1)
    end = completed_at + timedelta(days=14)
    for ex in examples:
        if ex.purpose != "exam" or ex.point_id != point_id or ex.answered_at < start or ex.answered_at > end:
            continue
        latest_id = None
        latest_time = None
        for did, done_at in completion.items():
            if decision_points.get(did) != point_id or done_at >= ex.answered_at:
                continue
            if latest_time is None or done_at > latest_time:
                latest_time = done_at
                latest_id = did
        if latest_id == decision_id:
            exam = [
                e
                for e in examples
                if e.attempt_id == ex.attempt_id and e.point_id == point_id and e.purpose == "exam"
            ]
            if not exam:
                return ex.value, ex.available_at
            score = sum(e.value for e in exam) / len(exam)
            observed_at = max(e.available_at for e in exam)
            return score, observed_at
    return None


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


__all__ = [
    "ANALYSIS_MODEL_KIND",
    "SCHEDULER_MODEL_KIND",
    "ScoreExample",
    "build_analysis_report",
    "build_learning_rows",
    "build_recommendations",
    "fit_analysis_model",
    "fit_scheduler_model",
    "graded_examples",
    "parse_now",
    "parse_time",
    "point_summaries",
    "public_model",
    "recommendation_candidates",
    "scheduler_rewards",
]
