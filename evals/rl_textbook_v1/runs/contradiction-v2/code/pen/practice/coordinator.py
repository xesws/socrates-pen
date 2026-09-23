"""Practice orchestration: durable jobs, frozen sessions, grading and event delivery."""
from __future__ import annotations

import copy
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import threading
import uuid
from typing import Any

from pen.config import LLMConfig
from pen.practice.contracts import fingerprint, public_question, question_metadata, timestamp
from pen.practice.runtime import supervisor, enabled
from pen.practice.store import Store

_threads: dict[str, threading.Thread] = {}
_thread_lock = threading.RLock()
_model_slots = threading.BoundedSemaphore(3)


def launch(key: str, function: Any, *args: Any) -> None:
    with _thread_lock:
        if key in _threads and _threads[key].is_alive():
            return
        def run() -> None:
            try:
                function(*args)
            finally:
                with _thread_lock:
                    _threads.pop(key, None)
        thread = threading.Thread(target=run, name=f"practice-{key[:16]}", daemon=True)
        _threads[key] = thread
        thread.start()


def _llm(cfg: LLMConfig | None, lang: str = "zh") -> Any:
    if cfg is None:
        return None
    from pen.practice.llm import make_llm
    return make_llm(cfg, lang=lang)


def default_blueprint(resource: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex, "version": 1, "resource_version": resource["resource_version"],
        "point_weights": {p["id"]: 1.0 for p in resource["points"]},
        "target_score": 80, "target_date": (date.today() + timedelta(days=30)).isoformat(),
        "daily_minutes": 20, "source_mq_ids": [m["id"] for m in resource["meta_questions"]],
        "stable_tolerance": 10,
        "updated_at": timestamp(),
    }


def validate_blueprint(raw: dict[str, Any], resource: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    out = copy.deepcopy(raw)
    known_mq = {m["id"] for m in resource["meta_questions"]}
    selected = out.get("source_mq_ids")
    if not isinstance(selected, list) or not selected or not set(selected) <= known_mq:
        raise ValueError("Select at least one current meta question")
    known_points = {p["id"] for p in resource["points"] if set(p.get("source_mq_ids", [])) & set(selected)}
    weights = out.get("point_weights")
    if not isinstance(weights, dict) or not weights or not set(weights) <= known_points:
        raise ValueError("Blueprint contains missing or outdated knowledge points")
    for value in weights.values():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError("Weights must be finite non-negative numbers")
    if not any(v > 0 for v in weights.values()):
        raise ValueError("At least one knowledge point needs a positive weight")
    for name, lo, hi in (("target_score", 1, 100), ("daily_minutes", 1, 240), ("stable_tolerance", 0, 100)):
        value = out.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError(f"{name} must be between {lo} and {hi}")
    date.fromisoformat(out["target_date"])
    out["id"] = (previous or {}).get("id", uuid.uuid4().hex)
    out["version"] = (previous or {}).get("version", 0) + 1
    out["resource_version"] = resource["resource_version"]
    out["updated_at"] = timestamp()
    return out


def start_build(scope: str, hid: str, path: Path, cfg: LLMConfig,
                practice_per_type: int = 2, exam_forms: int = 3, lang: str = "zh",
                regenerate: bool = False) -> dict[str, Any]:
    from pen.practice.resources import extract_meta_questions
    snapshot = extract_meta_questions(path)
    revision = snapshot.get("source_revision", fingerprint(path.read_text(encoding="utf-8")))
    counts = Counter(mq["id"] for mq in snapshot["meta_questions"])
    indices = [i for i, mq in enumerate(snapshot["meta_questions"])
               if mq.get("answer", "").strip() and counts[mq["id"]] == 1]
    if any(issue.get("code") == "index_problem" and issue.get("severity") == "error" for issue in snapshot.get("issues", [])):
        indices = []
    store = Store()
    lock = f"build:{scope}:{hid}"
    owner = f"{os.getpid()}:{uuid.uuid4().hex}"
    if not store.acquire(lock, owner, 15):
        raise ValueError("Another build request is being created")
    try:
        for old in store.list(scope, "job", hid):
            if old["status"] in {"queued", "running"}:
                if store.leased(f"job:{scope}:{old['id']}") or old["id"] in _threads:
                    return old
                old.update(status="interrupted", error="Generation stopped; resume to continue")
                store.put(scope, "job", old["id"], hid, old)
        job = {"id": uuid.uuid4().hex, "handbook_id": hid, "status": "queued", "completed": 0,
               "total": len(indices), "mq_indices": indices, "source_revision": revision,
               "path": str(path), "snapshot": snapshot, "issues": snapshot.get("issues", []),
               "created_at": timestamp(), "practice_per_type": practice_per_type, "exam_forms": exam_forms,
               "usage": {}, "cancel_requested": False, "parts": [], "lang": lang,
               "regenerate": regenerate}
        if not job["total"]:
            job["status"] = "failed"
            job["error"] = "No supported meta questions with reference answers were found"
        store.put(scope, "job", job["id"], hid, job)
        if job["status"] != "failed":
            launch(job["id"], build_job, scope, job["id"], cfg)
        return job
    finally:
        store.release(lock, owner)


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in job.items() if k not in {"snapshot", "path", "parts", "mq_indices"}}


def build_job(scope: str, jid: str, cfg: LLMConfig) -> None:
    from pen.practice.resources import compile_meta_question
    store, owner = Store(), f"{os.getpid()}:{uuid.uuid4().hex}"
    if not store.acquire(f"job:{scope}:{jid}", owner, 600):
        return
    heartbeat = store.heartbeat(f"job:{scope}:{jid}", owner)
    caller = None
    try:
        job = store.get(scope, "job", jid)
        if not job:
            return
        hid = job["handbook_id"]
        job.update(status="running", error="", cancel_requested=False)
        store.put(scope, "job", jid, hid, job)
        caller = _llm(cfg, job.get("lang", "zh"))
        if hasattr(caller, "usage"):
            caller.usage = copy.deepcopy(job.get("usage", {}))
        def checked_call(system: str, payload: dict[str, Any]) -> dict[str, Any]:
            latest = store.get(scope, "job", jid)
            if not enabled(scope) or (latest and latest.get("cancel_requested")):
                raise ValueError("Generation cancelled before the next model call")
            return caller(system, payload)
        previous_resource = active_resource(scope, hid)
        current_versions = {m["id"]: m["version"] for m in job["snapshot"]["meta_questions"]}
        unchanged = {m["id"] for m in (previous_resource or {}).get("meta_questions", [])
                     if current_versions.get(m["id"]) == m["version"]}
        previous_points = [p for p in (previous_resource or {}).get("points", [])
                           if set(p.get("source_mq_ids", [])) & unchanged]
        old_questions = [q for r in store.list(scope, "resource", hid) for q in r.get("questions", [])] if job.get("regenerate") else []
        for i in range(job["completed"], job["total"]):
            fresh = store.get(scope, "job", jid) or job
            if fresh.get("cancel_requested") or not enabled(scope):
                job.update(status="cancelled", cancel_requested=True)
                store.put(scope, "job", jid, hid, job)
                return
            mq_index = job.get("mq_indices", list(range(job["total"])))[i]
            mq = job["snapshot"]["meta_questions"][mq_index]
            from pen.practice.importer import RUBRIC_SYSTEM
            part_key = fingerprint([mq["id"], mq.get("version"), job["practice_per_type"], job["exam_forms"], job["snapshot"].get("format","legacy"), fingerprint(RUBRIC_SYSTEM)])
            part = None if job.get("regenerate") else store.get(scope, "compiled", part_key)
            if part is None:
                try:
                    known = list({p["id"]: p for p in previous_points + [p for chunk in job["parts"] for p in chunk.get("points", [])]}.values())
                    with _model_slots:
                        if not enabled(scope):
                            raise ValueError("Practice disabled before generation started")
                        if job["snapshot"].get("format") == "mq_h3_v1" and not job.get("regenerate"):
                            from pen.practice.importer import derive_rubric, question_images
                            from pen import config
                            import shutil
                            original = copy.deepcopy(mq["question"])
                            question_images(original)  # verify source bytes before freezing
                            for asset in original.get("assets", []):
                                destination = config.PEN_DIR / "practice" / "assets" / (asset["sha256"] + Path(asset["relative_path"]).suffix)
                                destination.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copyfile(Path(original["asset_root"]) / asset["relative_path"], destination)
                                asset["storage_path"] = str(destination)
                            part = derive_rubric(original, checked_call)
                        else:
                            part = compile_meta_question(mq, checked_call, practice_per_type=job["practice_per_type"],
                                                         exam_forms=job["exam_forms"], existing_points=known,
                                                         previous_questions=[public_question(q) for q in old_questions if q["source_mq_id"] == mq["id"]])
                    if part.get("questions"):
                        store.put(scope, "compiled", part_key, hid, part)
                except Exception as exc:
                    part = {"points": [], "edges": [], "questions": [],
                            "issues": [{"mq_id": mq["id"], "message": str(exc)}]}
            job["parts"].append(part)
            job["completed"] = i + 1
            job["issues"].extend(part.get("issues", []))
            job["usage"] = getattr(caller, "usage", {})
            with store.transaction() as db:
                fresh = store.read(db, scope, "job", jid)
                job["cancel_requested"] = bool(fresh and fresh.get("cancel_requested"))
                store.write(db, scope, "job", jid, hid, job)
            store.acquire(f"job:{scope}:{jid}", owner, 600)
        # Reading the snapshot and checking the current file are intentionally separate:
        # compiled content is never swapped for newly edited text inside a running job.
        from pen.practice.resources import extract_meta_questions
        current = extract_meta_questions(Path(job["path"]))
        if current.get("source_revision") != job["source_revision"]:
            job.update(status="stale", error="Meta questions changed during generation; build the current revision")
            store.put(scope, "job", jid, hid, job)
            return
        points: dict[str, Any] = {}
        questions, edges = [], []
        for part in job["parts"]:
            for point in part.get("points", []):
                prior = points.get(point["id"])
                if prior:
                    point = {**point, "source_mq_ids": sorted(set(prior.get("source_mq_ids", []) + point.get("source_mq_ids", []))),
                             "evidence": prior.get("evidence", []) + point.get("evidence", [])}
                points[point["id"]] = point
            questions.extend(part.get("questions", []))
            edges.extend(part.get("edges", []))
        question_ids = [q["id"] for q in questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("Question IDs collided across meta questions; bank was not published")
        resource = {"resource_version": fingerprint([job["source_revision"], [(q["version"],fingerprint(q.get("criteria",[]))) for q in questions]]),
                    "source_revision": job["source_revision"], "meta_questions": job["snapshot"]["meta_questions"],
                    "points": list(points.values()), "edges": edges, "questions": questions,
                    "issues": job["issues"], "built_at": timestamp(), "handbook_id": hid}
        resource["schema_version"] = job["snapshot"].get("schema_version", 0)
        resource["format"] = job["snapshot"].get("format", "legacy")
        resource["units"] = job["snapshot"].get("units", [])
        job["status"] = "completed_with_issues" if job["issues"] else "completed"
        if not questions:
            job.update(status="failed", error="No questions passed validation")
        with store.transaction() as db:
            fresh = store.read(db, scope, "job", jid)
            if not enabled(scope) or (fresh and fresh.get("cancel_requested")):
                job["status"] = "cancelled"
            elif questions:
                store.write(db, scope, "resource", resource["resource_version"], hid, resource)
                store.write(db, scope, "active", hid, hid, {"resource_version": resource["resource_version"]})
                old_blueprint = store.read(db, scope, "blueprint", hid)
                if old_blueprint is None:
                    store.write(db, scope, "blueprint", hid, hid, default_blueprint(resource))
                elif set(old_blueprint["point_weights"]) <= set(points) and set(old_blueprint["source_mq_ids"]) <= {m["id"] for m in resource["meta_questions"]}:
                    old_blueprint["resource_version"] = resource["resource_version"]
                    store.write(db, scope, "blueprint", hid, hid, old_blueprint)
            store.write(db, scope, "job", jid, hid, job)
    except Exception as exc:
        job = store.get(scope, "job", jid)
        if job:
            job.update(status="failed", error=str(exc))
            if caller is not None:
                job["usage"] = getattr(caller, "usage", {})
            store.put(scope, "job", jid, job["handbook_id"], job)
    finally:
        heartbeat.set()
        store.release(f"job:{scope}:{jid}", owner)


def active_resource(scope: str, hid: str) -> dict[str, Any] | None:
    store = Store()
    active = store.get(scope, "active", hid)
    return store.get(scope, "resource", active["resource_version"]) if active else None


def resource_is_stale(resource: dict[str, Any] | None, path: Path) -> bool:
    if resource is None:
        return False
    from pen.practice.resources import extract_meta_questions
    return extract_meta_questions(path)["source_revision"] != resource["source_revision"]


def public_resource(resource: dict[str, Any] | None) -> dict[str, Any] | None:
    if resource is None:
        return None
    result = {k: v for k, v in resource.items() if k not in {"questions", "meta_questions"}}
    result["meta_questions"] = [{k: m[k] for k in ("id", "version", "title", "source", "chapter") if k in m}
                                for m in resource["meta_questions"]]
    result["question_count"] = len(resource["questions"])
    return result


def sync_worker(service: str, scope: str, hid: str) -> dict[str, Any]:
    store = Store()
    with store.transaction() as db:
        active = store.read(db, scope, "active", hid)
        resource = store.read(db, scope, "resource", active["resource_version"]) if active else None
        blueprint = store.read(db, scope, "blueprint", hid)
    if resource is None:
        raise ValueError("Build a question bank first")
    blueprint = blueprint or default_blueprint(resource)
    snapshot = {"resource_version": resource["resource_version"], "points": resource["points"],
                "edges": resource["edges"], "questions": [question_metadata(q) for q in resource["questions"]],
                "blueprint": blueprint,
                "revision": max(resource.get("built_at", ""), blueprint.get("updated_at", ""))}
    base = {"scope": scope, "handbook_id": hid, "snapshot": snapshot}
    response = supervisor.call(service, "/v1/sync", {**base, "events": []})
    cursor = response.get("cursor", 0)
    while events := store.events(scope, hid, cursor):
        response = supervisor.call(service, "/v1/sync", {**base, "events": events})
        advanced = response.get("cursor", cursor)
        if advanced <= cursor:
            raise RuntimeError("Worker cursor did not advance")
        cursor = advanced
    return response


def analysis_report(scope: str, hid: str) -> dict[str, Any]:
    sync_worker("analysis", scope, hid)
    report = supervisor.call("analysis", "/v1/analysis", {"scope": scope, "handbook_id": hid})
    report["exams"] = exam_summary(scope, hid)
    return report


def recommendations(scope: str, hid: str, minutes: float | None = None) -> dict[str, Any]:
    resource = active_resource(scope, hid)
    blueprint = Store().get(scope, "blueprint", hid)
    if resource and blueprint and blueprint.get("resource_version") != resource["resource_version"]:
        raise ValueError("Update the blueprint for the current knowledge graph before requesting recommendations")
    analysis = analysis_report(scope, hid)
    sync_worker("scheduling", scope, hid)
    body = {"scope": scope, "handbook_id": hid, "analysis": analysis}
    if minutes is not None:
        body["minutes"] = minutes
    return supervisor.call("scheduling", "/v1/recommendations", body)


def new_session(scope: str, hid: str, mode: str, question_ids: list[str] | None = None,
                minutes: float | None = None) -> dict[str, Any]:
    store = Store()
    resource = active_resource(scope, hid)
    if resource is None:
        raise ValueError("Build a question bank first")
    blueprint = store.get(scope, "blueprint", hid) or default_blueprint(resource)
    if blueprint.get("resource_version") != resource["resource_version"]:
        raise ValueError("The textbook changed. Update the blueprint before starting a new session")
    with store.transaction() as db:
        rows = db.execute("SELECT data FROM documents WHERE scope=? AND kind='session' AND handbook_id=?",
                          (scope, hid))
        prior = [json.loads(r[0]) for r in rows]
    for old in prior:
        if old["mode"] == mode and old["status"] != "completed":
            return public_session(scope, old)
    selected_mqs = set(blueprint["source_mq_ids"])
    candidates = [q for q in resource["questions"] if q.get("status") == "accepted"
                  and q["source_mq_id"] in selected_mqs]
    recs: list[dict[str, Any]] = []
    exam_form = None
    if mode == "exam":
        used = {q["id"] for old in prior if old["mode"] == "exam" for q in old["questions"]}
        available = [q for q in candidates if q["purpose"] == "exam" and q["id"] not in used]
        forms = sorted({q["exam_form"] for q in available if q.get("exam_form") is not None})
        if not forms:
            raise ValueError("Reserved exam questions are exhausted; generate a new bank explicitly")
        needed = {p for p, w in blueprint["point_weights"].items() if w > 0}
        questions = []
        for form in forms:
            batch = [q for q in available if q["exam_form"] == form]
            covered = {c["point_id"] for q in batch for c in q["criteria"]}
            covered_mqs = {q["source_mq_id"] for q in batch}
            if needed <= covered and selected_mqs <= covered_mqs:
                questions, exam_form = batch, form
                break
        if not questions:
            raise ValueError("Reserved exam questions do not cover the blueprint; adjust scope or rebuild")
    elif mode == "recommended":
        rec = recommendations(scope, hid, minutes)
        recs = rec["items"]
        by_id = {q["id"]: q for q in candidates if q["purpose"] == "practice"}
        questions = [by_id[r["question_id"]] for r in recs if r["question_id"] in by_id]
    elif mode == "practice":
        questions = [q for q in candidates if q["purpose"] == "practice"]
        if question_ids:
            by_id = {q["id"]: q for q in questions}
            if not set(question_ids) <= set(by_id):
                raise ValueError("Only current practice questions can be selected")
            questions = [by_id[qid] for qid in dict.fromkeys(question_ids)]
        else:
            budget = (minutes or blueprint["daily_minutes"]) * 60
            chosen, total = [], 0
            for q in questions:
                duration = q.get("estimated_seconds", 90)
                if total + duration <= budget:
                    chosen.append(q)
                    total += duration
            questions = chosen
    else:
        raise ValueError("Unknown session mode")
    if not questions:
        raise ValueError("No eligible questions fit this practice request")
    session = {"id": uuid.uuid4().hex, "handbook_id": hid, "mode": mode, "status": "active",
               "resource_version": resource["resource_version"], "questions": copy.deepcopy(questions),
               "attempt_ids": [], "drafts": {}, "recommendations": recs, "blueprint": blueprint,
               "exam_form": exam_form, "created_at": timestamp()}
    # Serialize creation with a second check: two tabs cannot consume the same reserved form.
    with store.transaction() as db:
        rows = db.execute("SELECT data FROM documents WHERE scope=? AND kind='session' AND handbook_id=?", (scope, hid))
        reserved: set[str] = set()
        for row in rows:
            old = json.loads(row[0])
            if old["mode"] == "exam":
                reserved.update(q["id"] for q in old["questions"])
            if old["mode"] == mode and old["status"] != "completed":
                session = old
                break
        else:
            if mode == "exam" and reserved & {q["id"] for q in questions}:
                raise ValueError("Another session reserved these exam questions; retry to select a fresh form")
            store.write(db, scope, "session", session["id"], hid, session)
    return public_session(scope, session)


def public_session(scope: str, session: dict[str, Any]) -> dict[str, Any]:
    store = Store()
    out = {k: v for k, v in session.items() if k not in {"questions", "attempt_ids"}}
    out["questions"] = [public_question(q) for q in session["questions"]]
    by_id = {q["id"]: q for q in session["questions"]}
    attempts = []
    for aid in session["attempt_ids"]:
        attempt = store.get(scope, "attempt", aid)
        if attempt is None:
            continue
        if (attempt["status"] == "pending" and (session["mode"] != "exam" or session["status"] != "active")
                and aid not in _threads and not store.leased(f"grade:{scope}:{aid}")):
            # A GET never starts a paid model request. Expose recovery explicitly.
            attempt = {**attempt, "status": "failed", "error": "Grading was interrupted; retry to continue"}
        if session["mode"] == "exam" and session["status"] == "active":
            attempt = {k: v for k, v in attempt.items() if k not in {"grade", "error"}}
        elif attempt.get("status") == "graded":
            q = by_id[attempt["question_id"]]
            attempt = {**attempt, "reference_answer": q.get("reference_answer", ""), "solution_markdown": q.get("solution_markdown", ""), "criteria": q["criteria"]}
        attempts.append(attempt)
    out["attempts"] = attempts
    return out


def submit(scope: str, sid: str, qid: str, answer: dict[str, Any], key: str,
           seconds: float, cfg: LLMConfig | None, skip: bool = False, lang: str = "zh") -> dict[str, Any]:
    store = Store()
    should_grade = False
    if not key or len(key) > 200:
        raise ValueError("A bounded idempotency_key is required")
    aid = fingerprint([scope, sid, key])[:32]
    with store.transaction() as db:
        session = store.read(db, scope, "session", sid)
        if not session:
            raise KeyError("session")
        old = store.read(db, scope, "attempt", aid)
        if old:
            if old["question_id"] != qid or old["answer"] != answer or (old["status"] == "skipped") != skip:
                raise ValueError("Idempotency key already used with another answer")
        else:
            if session["status"] != "active":
                raise ValueError("This session has already been submitted")
            questions = {q["id"]: q for q in session["questions"]}
            if qid not in questions:
                raise ValueError("Question does not belong to this session")
            for existing_id in session["attempt_ids"]:
                existing = store.read(db, scope, "attempt", existing_id)
                if existing and existing["question_id"] == qid:
                    raise ValueError("This question already has an answer")
            q = questions[qid]
            if not skip:
                from pen.practice.grading import validate_answer
                validate_answer(q, answer)
            attempt = {"id": aid, "session_id": sid, "handbook_id": session["handbook_id"],
                       "question_id": qid, "question_version": q["version"], "source_version": session["resource_version"],
                       "rubric_version": q.get("rubric_version", fingerprint(q["criteria"])),
                       "purpose": q["purpose"], "answer": answer, "status": "skipped" if skip else "pending",
                       "created_at": timestamp(), "answered_at": timestamp(), "duration_seconds": seconds,
                       "lang": lang}
            for rec in session["recommendations"]:
                if rec["question_id"] == qid:
                    attempt["decision_id"] = rec["decision_id"]
                    attempt["point_id"] = rec.get("point_id")
            store.write(db, scope, "attempt", aid, session["handbook_id"], attempt)
            session["attempt_ids"].append(aid)
            session["drafts"].pop(qid, None)
            store.write(db, scope, "session", sid, session["handbook_id"], session)
            should_grade = not skip and session["mode"] != "exam"
    if should_grade:
        launch(aid, grade_attempt, scope, aid, cfg)
    return public_session(scope, session)


def grade_attempt(scope: str, aid: str, cfg: LLMConfig | None) -> None:
    from pen.practice.grading import grade_answer
    store, owner = Store(), f"{os.getpid()}:{uuid.uuid4().hex}"
    lease = f"grade:{scope}:{aid}"
    if not store.acquire(lease, owner, 1800):
        return
    heartbeat = store.heartbeat(lease, owner, 1800)
    try:
        attempt = store.get(scope, "attempt", aid)
        if not attempt or attempt["status"] in {"graded", "skipped"}:
            return
        session = store.get(scope, "session", attempt["session_id"])
        if not session or (session["mode"] == "exam" and session["status"] == "active"):
            return
        question = next(q for q in session["questions"] if q["id"] == attempt["question_id"])
        try:
            caller = _llm(cfg, attempt.get("lang", "zh"))
            with _model_slots:
                if not enabled(scope):
                    raise ValueError("Practice disabled before grading started; enable it before retrying")
                grade = grade_answer(question, attempt["answer"], llm=caller)
            criteria = {c["id"]: c for c in question["criteria"]}
            if {c["criterion_id"] for c in grade["criteria"]} != set(criteria) or len(grade["criteria"]) != len(criteria):
                raise ValueError("Reviewer returned a mismatched rubric")
            for result in grade["criteria"]:
                canonical = criteria[result["criterion_id"]]
                value = result["score"]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= canonical["max_score"]:
                    raise ValueError("Reviewer returned a score outside the rubric")
                result.update(point_id=canonical["point_id"], max_score=canonical["max_score"])
            grade["score"] = sum(c["score"] for c in grade["criteria"])
            grade["max_score"] = sum(c["max_score"] for c in grade["criteria"])
            grade["usage"] = getattr(caller, "usage", {})
            attempt.update(status="graded", grade=grade, graded_at=timestamp(), error="", error_code="")
        except Exception as exc:
            attempt.update(status="failed", error=str(exc), error_code=getattr(exc, "code", "grading_failed"))
        with store.transaction() as db:
            current = store.read(db, scope, "attempt", aid)
            if current and current["status"] == "graded":
                return
            store.write(db, scope, "attempt", aid, attempt["handbook_id"], attempt)
            if attempt["status"] == "graded":
                payload = {**attempt, "question": question_metadata(question), "blueprint": session["blueprint"]}
                store.emit(db, scope, attempt["handbook_id"], "attempt_graded", f"grade:{aid}", payload)
        complete_if_ready(scope, session["id"])
    finally:
        heartbeat.set()
        store.release(lease, owner)


def finish(scope: str, sid: str, cfg: LLMConfig | None) -> dict[str, Any]:
    store = Store()
    pending = []
    with store.transaction() as db:
        session = store.read(db, scope, "session", sid)
        if session is None:
            raise KeyError("session")
        if session["status"] == "completed":
            pass
        else:
            answered = set()
            for aid in session["attempt_ids"]:
                attempt = store.read(db, scope, "attempt", aid)
                if attempt:
                    answered.add(attempt["question_id"])
                    if attempt["status"] in {"pending", "failed"}:
                        pending.append(aid)
            for q in session["questions"]:
                if q["id"] not in answered:
                    aid = uuid.uuid4().hex
                    attempt = {"id": aid, "session_id": sid, "handbook_id": session["handbook_id"],
                               "question_id": q["id"], "question_version": q["version"],
                               "source_version": session["resource_version"], "purpose": q["purpose"],
                               "answer": {}, "status": "skipped", "created_at": timestamp(),
                               "answered_at": timestamp(), "duration_seconds": 0}
                    store.write(db, scope, "attempt", aid, session["handbook_id"], attempt)
                    session["attempt_ids"].append(aid)
            session.update(status="grading", submitted_at=timestamp())
            store.write(db, scope, "session", sid, session["handbook_id"], session)
    for aid in pending:
        launch(aid, grade_attempt, scope, aid, cfg)
    complete_if_ready(scope, sid)
    return public_session(scope, store.get(scope, "session", sid) or session)


def complete_if_ready(scope: str, sid: str) -> None:
    store = Store()
    with store.transaction() as db:
        session = store.read(db, scope, "session", sid)
        if not session or session["status"] == "completed":
            return
        attempts = [store.read(db, scope, "attempt", aid) for aid in session["attempt_ids"]]
        if len(attempts) != len(session["questions"]) or any(a is None or a["status"] in {"pending", "failed"} for a in attempts):
            return
        if session["mode"] == "exam" and session["status"] == "active":
            return
        totals: dict[str, list[float]] = {}
        for a in attempts:
            for c in (a or {}).get("grade", {}).get("criteria", []):
                pair = totals.setdefault(c["point_id"], [0, 0])
                pair[0] += c["score"]
                pair[1] += c["max_score"]
        weights = {p: w for p, w in session["blueprint"]["point_weights"].items() if w > 0}
        complete = bool(weights) and set(weights) <= set(totals) and all(a and a["status"] == "graded" for a in attempts)
        grade = sum(w * totals[p][0] / totals[p][1] for p, w in weights.items() if p in totals) / sum(weights.values()) * 100 if weights else None
        session.update(status="completed", completed_at=timestamp(),
                       result={"score": grade if complete else None, "provisional_score": grade,
                               "complete_coverage": complete, "point_scores": {p: v[0]/v[1] for p, v in totals.items()},
                               "skipped": sum(a and a["status"] == "skipped" for a in attempts)})
        if session["mode"] != "exam":
            raw_earned = sum((a or {}).get("grade", {}).get("score", 0) for a in attempts)
            raw_max = sum((a or {}).get("grade", {}).get("max_score", 0) for a in attempts)
            session["result"]["score"] = 100 * raw_earned / raw_max if raw_max else None
        store.write(db, scope, "session", sid, session["handbook_id"], session)
        store.emit(db, scope, session["handbook_id"], "session_completed", f"session:{sid}",
                   {k: session[k] for k in ("id", "mode", "blueprint", "result", "completed_at")})


def exam_summary(scope: str, hid: str) -> dict[str, Any]:
    store = Store()
    blueprint = store.get(scope, "blueprint", hid)
    exams = [s for s in store.list(scope, "session", hid) if s["mode"] == "exam" and s["status"] == "completed"]
    def comparable(b: dict[str, Any] | None) -> Any:
        return {k: v for k, v in (b or {}).items() if k not in {"resource_version", "updated_at"}}
    eligible = [s for s in exams if s.get("result", {}).get("complete_coverage") and comparable(s["blueprint"]) == comparable(blueprint)]
    eligible.sort(key=lambda s: s["completed_at"])
    last = eligible[-3:]
    stable = False
    if len(last) == 3 and blueprint:
        scores = [s["result"]["score"] for s in last]
        times = [datetime.fromisoformat(s["completed_at"]) for s in last]
        stable = min(scores) >= blueprint["target_score"] and max(scores)-min(scores) <= blueprint["stable_tolerance"] and all(
            (b-a).total_seconds() >= 86400 for a, b in zip(times, times[1:]))
    return {"stable": stable, "comparable_count": len(eligible),
            "history": [{"id": s["id"], "completed_at": s["completed_at"], "result": s["result"],
                         "blueprint_version": s["blueprint"]["version"]} for s in exams]}
