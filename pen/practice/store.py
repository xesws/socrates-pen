"""Durable gateway state. Short SQLite transactions never include a model call."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from pen import config
from pen.practice.contracts import timestamp


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or config.PEN_DIR / "practice" / "practice.sqlite"

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=15000")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    scope TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                    handbook_id TEXT NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(scope,kind,id));
                CREATE INDEX IF NOT EXISTS documents_book ON documents(scope,kind,handbook_id);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
                    handbook_id TEXT NOT NULL, type TEXT NOT NULL, event_key TEXT NOT NULL,
                    data TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(scope,event_key));
                CREATE INDEX IF NOT EXISTS events_book ON events(scope,handbook_id,seq);
                CREATE TABLE IF NOT EXISTS leases (
                    key TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL);
            """)
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def read(db: sqlite3.Connection, scope: str, kind: str, ident: str) -> dict[str, Any] | None:
        row = db.execute("SELECT data FROM documents WHERE scope=? AND kind=? AND id=?",
                         (scope, kind, ident)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def write(db: sqlite3.Connection, scope: str, kind: str, ident: str,
              handbook_id: str, data: dict[str, Any]) -> None:
        db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?) ON CONFLICT(scope,kind,id) "
                   "DO UPDATE SET handbook_id=excluded.handbook_id,data=excluded.data,updated_at=excluded.updated_at",
                   (scope, kind, ident, handbook_id,
                    json.dumps(data, ensure_ascii=False, allow_nan=False), timestamp()))

    def get(self, scope: str, kind: str, ident: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        with self.transaction() as db:
            return self.read(db, scope, kind, ident)

    def put(self, scope: str, kind: str, ident: str, handbook_id: str, data: dict[str, Any]) -> None:
        with self.transaction() as db:
            self.write(db, scope, kind, ident, handbook_id, data)

    def list(self, scope: str, kind: str, handbook_id: str | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.transaction() as db:
            sql, args = "SELECT data FROM documents WHERE scope=? AND kind=?", [scope, kind]
            if handbook_id is not None:
                sql += " AND handbook_id=?"
                args.append(handbook_id)
            sql += " ORDER BY updated_at DESC"
            return [json.loads(row[0]) for row in db.execute(sql, args)]

    @staticmethod
    def emit(db: sqlite3.Connection, scope: str, handbook_id: str,
             event_type: str, event_key: str, payload: dict[str, Any]) -> None:
        db.execute("INSERT OR IGNORE INTO events(scope,handbook_id,type,event_key,data,created_at) "
                   "VALUES(?,?,?,?,?,?)", (scope, handbook_id, event_type, event_key,
                                          json.dumps(payload, ensure_ascii=False, allow_nan=False), timestamp()))

    def events(self, scope: str, handbook_id: str, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.transaction() as db:
            rows = db.execute("SELECT seq,type,data,created_at FROM events WHERE scope=? "
                              "AND handbook_id=? AND seq>? ORDER BY seq LIMIT ?",
                              (scope, handbook_id, after, limit))
            return [{"seq": r[0], "type": r[1], "payload": json.loads(r[2]), "created_at": r[3]}
                    for r in rows]

    def acquire(self, key: str, owner: str, seconds: float = 300) -> bool:
        with self.transaction() as db:
            now = time.time()
            row = db.execute("SELECT owner,expires FROM leases WHERE key=?", (key,)).fetchone()
            if row and row[1] > now and row[0] != owner and self.owner_alive(row[0]):
                return False
            db.execute("INSERT INTO leases VALUES(?,?,?) ON CONFLICT(key) DO UPDATE "
                       "SET owner=excluded.owner,expires=excluded.expires", (key, owner, now + seconds))
            return True

    @staticmethod
    def owner_alive(owner: str) -> bool:
        try:
            pid = int(owner.split(":", 1)[0])
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except (ValueError, PermissionError):
            return True

    def leased(self, key: str) -> bool:
        if not self.path.exists():
            return False
        with self.transaction() as db:
            row = db.execute("SELECT owner,expires FROM leases WHERE key=?", (key,)).fetchone()
            return bool(row and row[1] > time.time() and self.owner_alive(row[0]))

    def release(self, key: str, owner: str) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM leases WHERE key=? AND owner=?", (key, owner))

    def heartbeat(self, key: str, owner: str, seconds: float = 600) -> threading.Event:
        """Keep queued/in-flight work owned while a slow provider call is in progress."""
        stop = threading.Event()
        def renew() -> None:
            while not stop.wait(min(20, seconds / 3)):
                try:
                    if not self.acquire(key, owner, seconds):
                        return
                except sqlite3.Error:
                    # A short busy interval is retried before the lease expires.
                    continue
        threading.Thread(target=renew, name="practice-lease", daemon=True).start()
        return stop
