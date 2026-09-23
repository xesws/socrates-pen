"""Scheduling service entry points."""

from __future__ import annotations

from typing import Any

from pen.practice import models
from pen.practice.worker_store import WorkerStore


def recommend(
    store: WorkerStore,
    *,
    scope: str,
    handbook_id: str,
    analysis: dict[str, Any],
    now: Any = None,
    minutes: float | None = None,
) -> dict[str, Any]:
    with store.locked():
        snapshot = store.snapshot(scope, handbook_id)
        events = store.events(scope, handbook_id)
        decisions = store.decisions(scope, handbook_id)
        cursor = store.cursor(scope, handbook_id)
        response, artifact, emitted = models.build_recommendations(
            snapshot,
            events,
            analysis,
            decisions,
            previous_model=store.artifact(scope, handbook_id, models.SCHEDULER_MODEL_KIND),
            now=models.parse_now(now),
            cursor=cursor,
            minutes=minutes,
            scope=scope,
            handbook_id=handbook_id,
        )
        store.put_artifact(scope, handbook_id, models.SCHEDULER_MODEL_KIND, artifact)
        if emitted:
            store.put_decisions(
                scope=scope,
                handbook_id=handbook_id,
                items=emitted,
                analysis=analysis,
                cursor=cursor,
            )
        return response


__all__ = ["recommend"]
