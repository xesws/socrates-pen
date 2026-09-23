"""HTTP worker for the experimental practice analysis and scheduling services."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from pen.practice import analysis as analysis_service
from pen.practice import scheduler as scheduler_service
from pen.practice.contracts import PROTOCOL
from pen.practice.worker_store import SERVICES, SyncConflict, WorkerStore


class SyncBody(BaseModel):
    scope: str
    handbook_id: str
    snapshot: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)


class AnalysisBody(BaseModel):
    scope: str
    handbook_id: str
    now: str | None = None


class RecommendationBody(BaseModel):
    scope: str
    handbook_id: str
    analysis: dict[str, Any] = Field(default_factory=dict)
    snapshot: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    now: str | None = None
    minutes: float | None = None


def create_app(*, service: str, store: WorkerStore, token: str) -> FastAPI:
    if service not in SERVICES:
        raise ValueError(f"unknown practice service: {service}")
    if not token:
        raise ValueError("practice worker token is required")
    app = FastAPI(title=f"Socratic Pen Practice {service}", version=str(PROTOCOL))

    def require_auth(authorization: str | None = Header(default=None)) -> None:
        prefix = "Bearer "
        if not authorization or not authorization.startswith(prefix):
            raise HTTPException(status_code=401, detail="missing bearer token")
        supplied = authorization[len(prefix) :]
        if not secrets.compare_digest(supplied, token):
            raise HTTPException(status_code=401, detail="bad bearer token")

    @app.get("/v1/health", dependencies=[Depends(require_auth)])
    def health() -> dict[str, Any]:
        return {"status": "ok", "protocol": PROTOCOL, "service": service, "pid": os.getpid()}

    @app.post("/v1/sync", dependencies=[Depends(require_auth)])
    def sync(body: SyncBody) -> dict[str, Any]:
        try:
            result = store.sync(
                scope=body.scope,
                handbook_id=body.handbook_id,
                snapshot=body.snapshot,
                events=body.events,
            )
        except SyncConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"cursor": result.cursor}

    @app.post("/v1/analysis", dependencies=[Depends(require_auth)])
    def analysis(body: AnalysisBody) -> dict[str, Any]:
        if service != "analysis":
            raise HTTPException(status_code=404, detail="analysis endpoint is not mounted on this worker")
        return analysis_service.analyze(store, scope=body.scope, handbook_id=body.handbook_id, now=body.now)

    @app.post("/v1/recommendations", dependencies=[Depends(require_auth)])
    def recommendations(body: RecommendationBody) -> dict[str, Any]:
        if service != "scheduling":
            raise HTTPException(status_code=404, detail="recommendations endpoint is not mounted on this worker")
        with store.locked():
            if body.snapshot is not None or body.events:
                try:
                    store.sync(
                        scope=body.scope,
                        handbook_id=body.handbook_id,
                        snapshot=body.snapshot,
                        events=body.events,
                    )
                except SyncConflict as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
            return scheduler_service.recommend(
                store,
                scope=body.scope,
                handbook_id=body.handbook_id,
                analysis=body.analysis,
                now=body.now,
                minutes=body.minutes,
            )

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an experimental practice worker.")
    parser.add_argument("--service", choices=SERVICES, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--pen-home", type=Path, required=True)
    args = parser.parse_args(argv)

    token = os.environ.get("PEN_PRACTICE_TOKEN", "")
    if not token:
        raise SystemExit("PEN_PRACTICE_TOKEN is required")

    store = WorkerStore.open(args.pen_home, service=args.service)
    app = create_app(service=args.service, store=store, token=token)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = int(sock.getsockname()[1])
    _write_ready(args.ready_file, {"port": port, "pid": os.getpid(), "service": args.service, "protocol": PROTOCOL})
    _start_parent_watchdog(args.parent_pid)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    try:
        server.run(sockets=[sock])
    finally:
        store.close()
    return 0


def _write_ready(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _start_parent_watchdog(parent_pid: int) -> None:
    def run() -> None:
        while True:
            if not _pid_alive(parent_pid):
                os._exit(0)
            time.sleep(0.5)

    thread = threading.Thread(target=run, name="practice-parent-watchdog", daemon=True)
    thread.start()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["create_app", "main"]
