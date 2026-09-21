"""Cancellation is addressed to a run, never to whichever request started last."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
import threading
from typing import Any, Iterator
import uuid


class Cancelled(Exception):
    pass


class Run:
    def __init__(self, sid: str, rid: str, pending_id: str = "") -> None:
        self.sid, self.rid = sid, rid
        self.pending_id = pending_id
        self.started = False
        self.finished = False
        self.lock = threading.RLock()
        self.cancelled = False
        self.stream: Any = None

    def check(self) -> None:
        if self.cancelled:
            raise Cancelled()

    def attach(self, stream: Any) -> None:
        with self.lock:
            self.stream = stream
        if self.cancelled:
            self.close_stream()
            self.check()

    def close_stream(self) -> None:
        stream, self.stream = self.stream, None
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def cancel(self) -> None:
        # A disk commit already in progress finishes before cancellation wins.
        with self.lock:
            self.cancelled = True
        self.close_stream()

    @contextmanager
    def commit(self) -> Iterator[None]:
        with self.lock:
            self.check()
            yield


_lock = threading.RLock()
_active: dict[str, Run] = {}
_cancelled: OrderedDict[tuple[str, str], None] = OrderedDict()


def begin(sid: str, rid: str = "", pending_id: str = "") -> Run:
    run = Run(sid, rid or uuid.uuid4().hex, pending_id)
    with _lock:
        run.cancelled = (sid, run.rid) in _cancelled or (bool(pending_id) and (sid, "pending:" + pending_id) in _cancelled)
        _active[sid] = run
    return run


def finish(run: Run) -> None:
    run.finished = True
    run.close_stream()
    with _lock:
        if _active.get(run.sid) is run:
            del _active[run.sid]


def cancel(sid: str, rid: str) -> bool:
    with _lock:
        # Also covers Stop arriving before the streaming POST reaches begin().
        _cancelled[sid, rid] = None
        while len(_cancelled) > 1024:
            _cancelled.popitem(last=False)
        run = _active.get(sid)
    if run is None or run.rid != rid:
        return False
    run.cancel()
    return True


def cancel_pending(sid: str, pending_id: str) -> bool:
    with _lock:
        _cancelled[sid, "pending:" + pending_id] = None
        while len(_cancelled) > 1024:
            _cancelled.popitem(last=False)
        run = _active.get(sid)
    if run is None or run.pending_id != pending_id:
        return False
    run.cancel()
    return True


def check(ctx: dict[str, Any]) -> None:
    run = ctx.get("run")
    if run is not None:
        run.check()


def abandon_tools(session: Any) -> None:
    """Every issued call still needs a result before another model request."""
    answered = {m.get("tool_call_id") for m in session.messages if m.get("role") == "tool"}
    for message in list(session.messages):
        for call in message.get("tool_calls") or []:
            cid = call.get("id")
            if cid and cid not in answered:
                session.messages.append({"role": "tool", "tool_call_id": cid,
                                         "content": "CANCELLED: 用户已停止此任务。/ Task stopped by the reader."})
                answered.add(cid)
    session.pending = None
