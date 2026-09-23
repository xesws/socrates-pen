"""Opt-in gateway API; the existing chat/profile routes remain independent."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pen import config, libraries
from pen.practice import coordinator as core
from pen.practice.contracts import public_question, scope_id
from pen.practice.runtime import ServiceUnavailable, enabled, set_enabled, supervisor
from pen.practice.store import Store

router = APIRouter(prefix="/v1/practice", tags=["experimental-practice"])


class ScopeBody(BaseModel):
    vault_root: str


class LlmBody(ScopeBody):
    base_url: str | None = None
    model: str | None = None
    thinking: str | None = None
    vision: bool | None = None
    provider: str | None = None
    lang: str = "zh"

    def config(self) -> config.LLMConfig | None:
        return config.merge_llm(base_url=self.base_url, model=self.model, thinking=self.thinking,
                                vision=self.vision, provider=self.provider)


class EnableBody(ScopeBody):
    enabled: bool


class BuildBody(LlmBody):
    handbook_id: str
    practice_per_type: int = Field(default=2, ge=1, le=10)
    exam_forms: int = Field(default=3, ge=1, le=10)
    regenerate: bool = False


class BlueprintBody(ScopeBody):
    handbook_id: str
    blueprint: dict[str, Any]


class SessionBody(LlmBody):
    handbook_id: str
    mode: Literal["practice", "recommended", "exam"] = "practice"
    question_ids: list[str] | None = None
    minutes: float | None = Field(default=None, gt=0, le=240, allow_inf_nan=False)


class DraftBody(ScopeBody):
    question_id: str
    answer: dict[str, Any]


class AnswerBody(LlmBody):
    question_id: str
    answer: dict[str, Any]
    idempotency_key: str = Field(min_length=1, max_length=200)
    duration_seconds: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False)
    skip: bool = False


class RecommendBody(ScopeBody):
    handbook_id: str
    minutes: float | None = Field(default=None, gt=0, le=240, allow_inf_nan=False)


def scope(root: str, require_enabled: bool = True) -> str:
    try:
        result = scope_id(root)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if require_enabled and not enabled(result):
        raise HTTPException(409, {"code": "practice_disabled", "message": "Enable experimental practice first"})
    return result


def book(root: str, hid: str) -> Path:
    meta = libraries.get(hid)
    if meta is None:
        raise HTTPException(404, "Import this handbook first")
    path = Path(meta.original_path).resolve()
    if not path.is_relative_to(Path(root).expanduser().resolve()):
        raise HTTPException(403, "The handbook does not belong to this vault")
    if not path.is_file():
        raise HTTPException(404, "Handbook file no longer exists")
    return path


def get_record(s: str, kind: str, ident: str) -> dict[str, Any]:
    row = Store().get(s, kind, ident)
    if row is None:
        raise HTTPException(404, f"Unknown {kind}")
    return row


def require_current(s: str, hid: str, path: Path) -> None:
    if core.resource_is_stale(core.active_resource(s, hid), path):
        raise HTTPException(409, {"code": "practice_resource_stale", "message": "Meta questions changed; rebuild the question bank"})


def translate_error(function: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return function(*args, **kwargs)
    except ServiceUnavailable as exc:
        raise HTTPException(503, {"code": "practice_service_unavailable", "message": str(exc)}) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(409, {"code": "practice_conflict", "message": str(exc)}) from exc


@router.put("/enable")
def enable(body: EnableBody) -> dict[str, Any]:
    sid = scope(body.vault_root, False)
    set_enabled(sid, body.enabled)
    return {"enabled": body.enabled, "services": supervisor.status()}


@router.get("/state")
def state(vault_root: str, handbook_id: str) -> dict[str, Any]:
    sid = scope(vault_root, False)
    if not enabled(sid):
        return {"enabled": False, "resource": None, "job": None, "blueprint": None,
                "sessions": [], "services": supervisor.status(), "stats": {}}
    path = book(vault_root, handbook_id)
    store = Store()
    jobs = store.list(sid, "job", handbook_id)
    sessions = store.list(sid, "session", handbook_id)
    attempts = store.list(sid, "attempt", handbook_id)
    resource = core.active_resource(sid, handbook_id)
    public = core.public_resource(resource)
    if public is not None:
        public["stale"] = core.resource_is_stale(resource, path)
    for old in jobs:
        if old["status"] in {"queued", "running"} and old["id"] not in core._threads and not store.leased(f"job:{sid}:{old['id']}"):
            old["status"] = "interrupted"
    return {"enabled": True, "resource": public,
            "job": core.public_job(jobs[0]) if jobs else None,
            "blueprint": store.get(sid, "blueprint", handbook_id),
            "sessions": [{k: row[k] for k in ("id", "mode", "status", "created_at", "result") if k in row}
                         for row in sessions],
            "services": supervisor.status(),
            "stats": {"attempts": len(attempts), "graded": sum(a["status"] == "graded" for a in attempts),
                      "exams": core.exam_summary(sid, handbook_id)}}


@router.post("/build")
def build(body: BuildBody) -> dict[str, Any]:
    sid = scope(body.vault_root)
    path = book(body.vault_root, body.handbook_id)
    cfg = body.config()
    if cfg is None:
        raise HTTPException(400, "Configure a model before generating questions")
    return core.public_job(translate_error(core.start_build, sid, body.handbook_id, path, cfg,
                                          body.practice_per_type, body.exam_forms, body.lang, body.regenerate))


@router.get("/jobs/{ident}")
def job(ident: str, vault_root: str) -> dict[str, Any]:
    sid = scope(vault_root)
    row = get_record(sid, "job", ident)
    if row["status"] in {"queued", "running"} and ident not in core._threads and not Store().leased(f"job:{sid}:{ident}"):
        row["status"] = "interrupted"
    return core.public_job(row)


@router.post("/jobs/{ident}/cancel")
def cancel(ident: str, body: ScopeBody) -> dict[str, Any]:
    sid, store = scope(body.vault_root), Store()
    with store.transaction() as db:
        row = store.read(db, sid, "job", ident)
        if row is None:
            raise HTTPException(404, "Unknown job")
        if row["status"] in {"queued", "running"}:
            row["cancel_requested"] = True
            store.write(db, sid, "job", ident, row["handbook_id"], row)
    return core.public_job(row)


@router.post("/jobs/{ident}/resume")
def resume(ident: str, body: LlmBody) -> dict[str, Any]:
    sid = scope(body.vault_root)
    row = get_record(sid, "job", ident)
    path = book(body.vault_root, row["handbook_id"])
    from pen.practice.resources import extract_meta_questions
    if extract_meta_questions(path).get("source_revision") != row["source_revision"]:
        raise HTTPException(409, "Meta questions changed; create a new build")
    cfg = body.config()
    if cfg is None:
        raise HTTPException(400, "Configure a model before generating questions")
    if row["status"] not in {"completed", "stale"}:
        if row["status"] in {"failed", "completed_with_issues"} or any(not p.get("questions") for p in row.get("parts", [])):
            row.update(completed=0, parts=[], issues=row["snapshot"].get("issues", []))
            Store().put(sid, "job", ident, row["handbook_id"], row)
        core.launch(ident, core.build_job, sid, ident, cfg)
    return core.public_job(row)


@router.put("/blueprint")
def blueprint(body: BlueprintBody) -> dict[str, Any]:
    sid = scope(body.vault_root)
    book(body.vault_root, body.handbook_id)
    resource = core.active_resource(sid, body.handbook_id)
    if resource is None:
        raise HTTPException(409, "Build a question bank first")
    store = Store()
    previous = store.get(sid, "blueprint", body.handbook_id)
    result = translate_error(core.validate_blueprint, body.blueprint, resource, previous)
    store.put(sid, "blueprint", body.handbook_id, body.handbook_id, result)
    return result


@router.get("/questions")
def questions(vault_root: str, handbook_id: str) -> dict[str, Any]:
    sid = scope(vault_root)
    book(vault_root, handbook_id)
    resource = core.active_resource(sid, handbook_id)
    return {"questions": [public_question(q) for q in (resource or {}).get("questions", []) if q["purpose"] == "practice"]}


@router.post("/sessions")
def create_session(body: SessionBody) -> dict[str, Any]:
    sid = scope(body.vault_root)
    path = book(body.vault_root, body.handbook_id)
    require_current(sid, body.handbook_id, path)
    return translate_error(core.new_session, sid, body.handbook_id, body.mode, body.question_ids, body.minutes)


@router.get("/sessions/{ident}")
def session(ident: str, vault_root: str) -> dict[str, Any]:
    sid = scope(vault_root)
    return core.public_session(sid, get_record(sid, "session", ident))


@router.put("/sessions/{ident}/draft")
def draft(ident: str, body: DraftBody) -> dict[str, bool]:
    sid, store = scope(body.vault_root), Store()
    with store.transaction() as db:
        row = store.read(db, sid, "session", ident)
        if not row:
            raise HTTPException(404, "Unknown session")
        if row["status"] != "active" or body.question_id not in {q["id"] for q in row["questions"]}:
            raise HTTPException(409, "Draft is not for an active question")
        # Validate attachments on draft writes as well as final submissions.
        from pen.practice.grading import validate_answer
        question = next(q for q in row["questions"] if q["id"] == body.question_id)
        try:
            validate_answer(question, body.answer)
        except ValueError as exc:
            raise HTTPException(400, {"code": "bad_answer", "message": str(exc)}) from exc
        row["drafts"][body.question_id] = body.answer
        store.write(db, sid, "session", ident, row["handbook_id"], row)
    return {"ok": True}


@router.post("/sessions/{ident}/answers")
def answer(ident: str, body: AnswerBody) -> dict[str, Any]:
    return translate_error(core.submit, scope(body.vault_root), ident, body.question_id,
                           body.answer, body.idempotency_key, body.duration_seconds, body.config(), body.skip, body.lang)


@router.post("/sessions/{ident}/finish")
def finish(ident: str, body: LlmBody) -> dict[str, Any]:
    return translate_error(core.finish, scope(body.vault_root), ident, body.config())


@router.post("/attempts/{ident}/retry")
def retry(ident: str, body: LlmBody) -> dict[str, Any]:
    sid = scope(body.vault_root)
    attempt = get_record(sid, "attempt", ident)
    if attempt["status"] in {"pending", "failed"}:
        core.launch(ident, core.grade_attempt, sid, ident, body.config())
    return attempt


@router.get("/analysis")
def analysis(vault_root: str, handbook_id: str) -> dict[str, Any]:
    sid = scope(vault_root)
    book(vault_root, handbook_id)
    return translate_error(core.analysis_report, sid, handbook_id)


@router.post("/recommendations")
def recommend(body: RecommendBody) -> dict[str, Any]:
    sid = scope(body.vault_root)
    path = book(body.vault_root, body.handbook_id)
    require_current(sid, body.handbook_id, path)
    return translate_error(core.recommendations, sid, body.handbook_id, body.minutes)
