"""Analysis service entry points."""

from __future__ import annotations

from typing import Any

from pen.practice import models
from pen.practice.worker_store import WorkerStore


def analyze(
    store: WorkerStore,
    *,
    scope: str,
    handbook_id: str,
    now: Any = None,
) -> dict[str, Any]:
    with store.locked():
        snapshot = store.snapshot(scope, handbook_id)
        events = store.events(scope, handbook_id)
        cursor = store.cursor(scope, handbook_id)
        previous = store.artifact(scope, handbook_id, models.ANALYSIS_MODEL_KIND)
        report, artifact = models.build_analysis_report(
            snapshot,
            events,
            previous_model=previous,
            now=models.parse_now(now),
            cursor=cursor,
        )
        store.put_artifact(scope, handbook_id, models.ANALYSIS_MODEL_KIND, artifact)
        return report


__all__ = ["analyze"]
