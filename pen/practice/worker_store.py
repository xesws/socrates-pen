"""SQLite persistence for independent practice workers."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pen.practice.contracts import fingerprint, timestamp

SERVICES = ("analysis", "scheduling")


class SyncConflict(ValueError):
    pass


@dataclass(frozen=True)
class SyncResult:
    cursor: int
    inserted: int
    duplicates: int


def db_path_for(pen_home: Path, service: str) -> Path:
    if service not in SERVICES:
        raise ValueError(f"unknown practice service: {service}")
    return Path(pen_home).expanduser().resolve() / "practice" / f"{service}.sqlite"


class WorkerStore:
    def __init__(self, path: Path, *, service: str) -> None:
        if service not in SERVICES:
            raise ValueError(f"unknown practice service: {service}")
        self.path = Path(path)
        self.service = service
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init()

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self._lock:
            yield

    @classmethod
    def open(cls, pen_home: Path, *, service: str) -> "WorkerStore":
        return cls(db_path_for(pen_home, service), service=service)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def sync(
        self,
        *,
        scope: str,
        handbook_id: str,
        snapshot: dict[str, Any] | None,
        events: list[dict[str, Any]],
    ) -> SyncResult:
        if not scope or not handbook_id:
            raise ValueError("scope and handbook_id are required")
        with self._lock:
            now = timestamp()
            inserted = 0
            duplicates = 0
            with self._conn:
                if snapshot is not None:
                    resource_version = str(snapshot.get("resource_version") or "")
                    snapshot_revision = _snapshot_revision(snapshot)
                    existing = self._conn.execute(
                        "SELECT snapshot_revision FROM snapshots WHERE scope=? AND handbook_id=?",
                        (scope, handbook_id),
                    ).fetchone()
                    existing_revision = int(existing["snapshot_revision"] or 0) if existing else 0
                    if not (snapshot_revision > 0 and existing_revision > snapshot_revision):
                        self._conn.execute(
                            """
                            INSERT INTO snapshots(scope, handbook_id, resource_version, snapshot_revision, snapshot_json, updated_at)
                            VALUES(?, ?, ?, ?, ?, ?)
                            ON CONFLICT(scope, handbook_id) DO UPDATE SET
                              resource_version=excluded.resource_version,
                              snapshot_revision=excluded.snapshot_revision,
                              snapshot_json=excluded.snapshot_json,
                              updated_at=excluded.updated_at
                            """,
                            (scope, handbook_id, resource_version, snapshot_revision, _json(snapshot), now),
                        )
                for event in events or []:
                    seq = _event_seq(event)
                    body = _json(event)
                    h = fingerprint(event)
                    found = self._conn.execute(
                        "SELECT event_hash FROM events WHERE scope=? AND handbook_id=? AND seq=?",
                        (scope, handbook_id, seq),
                    ).fetchone()
                    if found:
                        if found["event_hash"] != h:
                            raise SyncConflict(f"seq {seq} already exists with different payload")
                        duplicates += 1
                        continue
                    self._conn.execute(
                        """
                        INSERT INTO events(scope, handbook_id, seq, event_type, created_at, event_hash, event_json)
                        VALUES(?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            scope,
                            handbook_id,
                            seq,
                            str(event.get("type") or ""),
                            str(event.get("created_at") or ""),
                            h,
                            body,
                        ),
                    )
                    inserted += 1
                cursor = self.cursor(scope, handbook_id)
                self._conn.execute(
                    """
                    INSERT INTO cursors(scope, handbook_id, cursor, updated_at)
                    VALUES(?, ?, ?, ?)
                    ON CONFLICT(scope, handbook_id) DO UPDATE SET
                      cursor=max(cursors.cursor, excluded.cursor),
                      updated_at=excluded.updated_at
                    """,
                    (scope, handbook_id, cursor, now),
                )
            return SyncResult(cursor=self.cursor(scope, handbook_id), inserted=inserted, duplicates=duplicates)

    def cursor(self, scope: str, handbook_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT max(seq) AS cursor FROM events WHERE scope=? AND handbook_id=?",
                (scope, handbook_id),
            ).fetchone()
            return int(row["cursor"] or 0) if row else 0

    def snapshot(self, scope: str, handbook_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT snapshot_json FROM snapshots WHERE scope=? AND handbook_id=?",
                (scope, handbook_id),
            ).fetchone()
            if not row:
                return None
            return _loads(row["snapshot_json"])

    def events(self, scope: str, handbook_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_json FROM events WHERE scope=? AND handbook_id=? ORDER BY seq",
                (scope, handbook_id),
            ).fetchall()
            return [_loads(r["event_json"]) for r in rows]

    def artifact(self, scope: str, handbook_id: str, kind: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT artifact_json FROM artifacts WHERE scope=? AND handbook_id=? AND kind=?",
                (scope, handbook_id, kind),
            ).fetchone()
            return _loads(row["artifact_json"]) if row else None

    def put_artifact(self, scope: str, handbook_id: str, kind: str, artifact: dict[str, Any]) -> None:
        with self._lock:
            now = timestamp()
            version = str(artifact.get("version") or fingerprint(artifact)[:16])
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO artifacts(scope, handbook_id, kind, version, artifact_json, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scope, handbook_id, kind) DO UPDATE SET
                      version=excluded.version,
                      artifact_json=excluded.artifact_json,
                      updated_at=excluded.updated_at
                    """,
                    (scope, handbook_id, kind, version, _json(artifact), now),
                )

    def decisions(self, scope: str, handbook_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT decision_id, created_at, status, point_id, question_id, question_type,
                       propensity, context_json, candidates_json, analysis_json, cursor
                FROM decisions
                WHERE scope=? AND handbook_id=?
                ORDER BY created_at, decision_id
                """,
                (scope, handbook_id),
            ).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                out.append(
                    {
                        "decision_id": row["decision_id"],
                        "created_at": row["created_at"],
                        "status": row["status"],
                        "point_id": row["point_id"],
                        "question_id": row["question_id"],
                        "question_type": row["question_type"],
                        "propensity": row["propensity"],
                        "context": _loads(row["context_json"]),
                        "candidates": _loads(row["candidates_json"]),
                        "analysis": _loads(row["analysis_json"]),
                        "cursor": row["cursor"],
                    }
                )
            return out

    def put_decisions(
        self,
        *,
        scope: str,
        handbook_id: str,
        items: list[dict[str, Any]],
        analysis: dict[str, Any],
        cursor: int,
    ) -> None:
        with self._lock:
            with self._conn:
                for item in items:
                    decision_id = str(item.get("decision_id") or "")
                    if not decision_id:
                        continue
                    self._conn.execute(
                        """
                        INSERT INTO decisions(
                          scope, handbook_id, decision_id, created_at, status,
                          point_id, question_id, question_type, propensity,
                          context_json, candidates_json, analysis_json, cursor
                        )
                        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(scope, handbook_id, decision_id) DO UPDATE SET
                          status=excluded.status,
                          propensity=excluded.propensity,
                          context_json=excluded.context_json,
                          candidates_json=excluded.candidates_json,
                          analysis_json=excluded.analysis_json,
                          cursor=excluded.cursor
                        """,
                        (
                            scope,
                            handbook_id,
                            decision_id,
                            str(item.get("created_at") or timestamp()),
                            str(item.get("status") or "pending"),
                            str(item.get("point_id") or ""),
                            str(item.get("question_id") or ""),
                            str(item.get("question_type") or ""),
                            float(item.get("propensity") or item.get("probability") or 0.0),
                            _json(item.get("context") or {}),
                            _json(item.get("candidates") or []),
                            _json(analysis or {}),
                            cursor,
                        ),
                    )

    def _init(self) -> None:
        with self._lock:
            with self._conn:
                self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshots(
                  scope TEXT NOT NULL,
                  handbook_id TEXT NOT NULL,
                  resource_version TEXT NOT NULL,
                  snapshot_revision INTEGER NOT NULL DEFAULT 0,
                  snapshot_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY(scope, handbook_id)
                );
                CREATE TABLE IF NOT EXISTS events(
                  scope TEXT NOT NULL,
                  handbook_id TEXT NOT NULL,
                  seq INTEGER NOT NULL,
                  event_type TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  event_hash TEXT NOT NULL,
                  event_json TEXT NOT NULL,
                  PRIMARY KEY(scope, handbook_id, seq)
                );
                CREATE TABLE IF NOT EXISTS cursors(
                  scope TEXT NOT NULL,
                  handbook_id TEXT NOT NULL,
                  cursor INTEGER NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY(scope, handbook_id)
                );
                CREATE TABLE IF NOT EXISTS artifacts(
                  scope TEXT NOT NULL,
                  handbook_id TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  version TEXT NOT NULL,
                  artifact_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY(scope, handbook_id, kind)
                );
                CREATE TABLE IF NOT EXISTS decisions(
                  scope TEXT NOT NULL,
                  handbook_id TEXT NOT NULL,
                  decision_id TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  status TEXT NOT NULL,
                  point_id TEXT NOT NULL,
                  question_id TEXT NOT NULL,
                  question_type TEXT NOT NULL,
                  propensity REAL NOT NULL,
                  context_json TEXT NOT NULL,
                  candidates_json TEXT NOT NULL,
                  analysis_json TEXT NOT NULL,
                  cursor INTEGER NOT NULL,
                  PRIMARY KEY(scope, handbook_id, decision_id)
                );
                CREATE INDEX IF NOT EXISTS idx_worker_events_scope_seq
                  ON events(scope, handbook_id, seq);
                CREATE INDEX IF NOT EXISTS idx_worker_decisions_scope_created
                  ON decisions(scope, handbook_id, created_at);
                """
                )
                cols = {
                    row["name"]
                    for row in self._conn.execute("PRAGMA table_info(snapshots)").fetchall()
                }
                if "snapshot_revision" not in cols:
                    self._conn.execute("ALTER TABLE snapshots ADD COLUMN snapshot_revision INTEGER NOT NULL DEFAULT 0")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str) -> Any:
    return json.loads(value)


def _event_seq(event: dict[str, Any]) -> int:
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    try:
        seq = int(event.get("seq"))
    except (TypeError, ValueError):
        raise ValueError("event seq must be an integer") from None
    if seq <= 0:
        raise ValueError("event seq must be positive")
    return seq


def _snapshot_revision(snapshot: dict[str, Any]) -> int:
    for key in ("epoch", "revision", "seq"):
        raw = snapshot.get(key)
        if raw is None:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = _iso_revision(raw)
        if value and value > 0:
            return value
    return 0


def _iso_revision(value: Any) -> int:
    if not isinstance(value, str) or not value.strip():
        return 0
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return int(dt.timestamp() * 1_000_000)


__all__ = ["SERVICES", "SyncConflict", "SyncResult", "WorkerStore", "db_path_for"]
