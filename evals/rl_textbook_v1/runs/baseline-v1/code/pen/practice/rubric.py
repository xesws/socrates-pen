"""Executable score levels and cumulative interview achievement bands."""
from __future__ import annotations

import math
from typing import Any


def number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def validate_levels(criteria: list[dict], bands: Any = None) -> None:
    for c in criteria:
        levels = c.get("levels")
        if levels is None:
            if bands is not None:
                raise ValueError("Tiered rubrics require explicit score levels for every criterion")
            continue
        if not isinstance(levels, list) or len(levels) < 2:
            raise ValueError("Criterion levels require zero and full-credit outcomes")
        ids, scores = set(), set()
        for level in levels:
            if not isinstance(level, dict) or not isinstance(level.get("id"), str) or not level["id"] or level["id"] in ids:
                raise ValueError("Duplicate or invalid score level id")
            score = level.get("score")
            if not number(score) or not 0 <= score <= c["max_score"] or not isinstance(level.get("condition"), str) or not level["condition"].strip():
                raise ValueError("Score levels need a bounded score and explicit condition")
            ids.add(level["id"]); scores.add(score)
        if 0 not in scores or c["max_score"] not in scores:
            raise ValueError("Score levels must include zero and the criterion maximum")
    if bands is None:
        return
    if not isinstance(bands, list) or [b.get("id") for b in bands if isinstance(b, dict)] != ["basic", "advanced", "complete"]:
        raise ValueError("Rubric must have basic, advanced and complete bands in order")
    by_id = {c["id"]: c for c in criteria}
    previous: set[str] = set()
    for band in bands:
        required = band.get("requires")
        if not isinstance(required, list) or not required or not all(isinstance(cid, str) for cid in required):
            raise ValueError("Band requires must list criterion IDs")
        current = set(required)
        if len(current) != len(required) or not previous < current or not current <= by_id.keys():
            raise ValueError("Bands must strictly accumulate valid criteria without duplicates")
        expected = sum(by_id[cid]["max_score"] for cid in current)
        if not number(band.get("min_score")) or abs(band["min_score"] - expected) > 1e-6:
            raise ValueError("Band threshold must equal its cumulative full-credit criteria")
        if not isinstance(band.get("label"), str) or not band["label"].strip():
            raise ValueError("Band requires a label")
        previous = current
    if previous != set(by_id):
        raise ValueError("Complete band must cover every criterion")
    for c in criteria:
        expected_tier = next(b["id"] for b in bands if c["id"] in b["requires"])
        if c.get("tier") != expected_tier:
            raise ValueError("Criterion tier disagrees with cumulative bands")


def achievement(question: dict, rows: list[dict]) -> dict | None:
    by_id = {r["criterion_id"]: r for r in rows}
    achieved = None
    for band in question.get("rubric_levels", []):
        if all(abs(by_id[cid]["score"] - by_id[cid]["max_score"]) < 1e-6 for cid in band["requires"]):
            achieved = {"id": band["id"], "label": band["label"]}
    return achieved
