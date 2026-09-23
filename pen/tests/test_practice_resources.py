from __future__ import annotations

from pathlib import Path
from typing import Any
import copy

from pen.practice.resources import compile_meta_question, extract_meta_questions


def _book(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "book.md"
    path.write_text(body, encoding="utf-8")
    return path


def _minimal_candidate(mq_id: str) -> dict[str, Any]:
    points = [
        {
            "id": "pt_shell",
            "name": "Shell category",
            "definition": "shell is a category and Bash is one implementation",
            "source_mq_ids": [mq_id],
            "evidence": [{"mq_id": mq_id, "quote": "shell is a category"}],
        }
    ]
    criterion = {
        "id": "crit_shell",
        "point_id": "pt_shell",
        "max_score": 1,
        "description": "states the category and implementation relation",
        "partial_credit": "0.5 for only naming one side",
        "evidence": [{"mq_id": mq_id, "quote": "Bash is one implementation"}],
    }
    questions: list[dict[str, Any]] = []
    for n in range(2):
        questions.append(
            {
                "type": "single_choice",
                "purpose": "practice",
                "prompt": f"Which relation describes shell and Bash? {n}",
                "point_ids": ["pt_shell"],
                "choices": [{"id": "a", "text": "category to implementation"}, {"id": "b", "text": "same thing"}],
                "correct_choice_id": "a",
                "reference_answer": "shell is a category; Bash is one implementation",
                "criteria": [criterion],
            }
        )
        questions.append(
            {
                "type": "fill_blank",
                "purpose": "practice",
                "prompt": f"Bash is one ____ of shell. {n}",
                "point_ids": ["pt_shell"],
                "blanks": [{"id": "b1", "label": "blank", "answers": ["implementation"], "grading": "exact"}],
                "reference_answer": "implementation",
                "criteria": [criterion],
            }
        )
        questions.append(
            {
                "type": "short_answer",
                "purpose": "practice",
                "prompt": f"Explain the relation between shell and Bash. {n}",
                "point_ids": ["pt_shell"],
                "reference_answer": "shell is a category; Bash is one implementation",
                "criteria": [criterion],
            }
        )
    for form in range(3):
        questions.append(
            {
                "type": "short_answer",
                "purpose": "exam",
                "exam_form": form,
                "prompt": f"Exam explain shell and Bash. {form}",
                "point_ids": ["pt_shell"],
                "reference_answer": "shell is a category; Bash is one implementation",
                "criteria": [criterion],
            }
        )
        qtype = "single_choice" if form % 2 == 0 else "fill_blank"
        q: dict[str, Any] = {
            "type": qtype,
            "purpose": "exam",
            "exam_form": form,
            "prompt": f"Exam check shell and Bash. {form}",
            "point_ids": ["pt_shell"],
            "reference_answer": "shell is a category; Bash is one implementation",
            "criteria": [criterion],
        }
        if qtype == "single_choice":
            q.update(
                {
                    "choices": [{"id": "a", "text": "category to implementation"}, {"id": "b", "text": "same thing"}],
                    "correct_choice_id": "a",
                }
            )
        else:
            q["blanks"] = [{"id": "b1", "label": "blank", "answers": ["implementation"], "grading": "exact"}]
        questions.append(q)
    return {"points": points, "edges": [], "questions": questions}


def test_extract_meta_questions_uses_only_q_blocks_inside_meta_section(tmp_path: Path) -> None:
    path = _book(
        tmp_path,
        """# Demo

# Level 0

## First beat

BODY_SENTINEL must never enter the MQ.

## Fifth beat Meta Question gate

**Q1. shell and Bash?**
- **TL;DR:** shell is a category; Bash is one implementation.
- **Why:** Bash is one implementation.

〔回读：First beat〕
""",
    )

    snapshot = extract_meta_questions(path)

    assert snapshot["issues"] == []
    assert len(snapshot["meta_questions"]) == 1
    mq = snapshot["meta_questions"][0]
    assert "BODY_SENTINEL" not in mq["text"]
    assert "〔回读" not in mq["text"]
    assert "shell is a category" in mq["answer"]
    moved = _book(tmp_path, "\n\n" + path.read_text(encoding="utf-8"))
    assert extract_meta_questions(moved)["source_revision"] == snapshot["source_revision"]


def test_compile_meta_question_generates_valid_bundle_and_never_sends_body_text(tmp_path: Path) -> None:
    path = _book(
        tmp_path,
        """# Demo

# Level 0

## Teaching

BODY_SENTINEL

## Fifth beat Meta Question gate

**Q1. shell and Bash?**
- **TL;DR:** shell is a category; Bash is one implementation.
- **Why:** Bash is one implementation.

〔回读：Teaching〕
""",
    )
    mq = extract_meta_questions(path)["meta_questions"][0]
    calls: list[tuple[str, dict[str, Any]]] = []

    def llm(system: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((system, payload))
        assert "BODY_SENTINEL" not in system
        assert "BODY_SENTINEL" not in repr(payload)
        if "independent reviewer" in system:
            return {"accepted": True, "issues": [], "usage": {"prompt_tokens": 1}}
        return {**_minimal_candidate(mq["id"]), "usage": {"prompt_tokens": 2, "completion_tokens": 3}}

    result = compile_meta_question(mq, llm)

    assert result["issues"] == []
    assert len(result["points"]) == 1
    assert len(result["questions"]) == 12
    fill_questions = [q for q in result["questions"] if q["type"] == "fill_blank"]
    assert fill_questions
    assert all(q["criteria"][0]["blank_id"] == "b1" for q in fill_questions)
    assert result["usage"]["calls"] == 2
    assert result["usage"]["prompt_tokens"] == 3
    assert any("reviewer" in call[0] for call in calls)



def test_compile_meta_question_uses_canonical_question_ids_not_model_labels(tmp_path: Path) -> None:
    path = _book(
        tmp_path,
        """# Demo
# Level 0
## Fifth beat Meta Question gate
**Q1. shell and Bash?**
- **TL;DR:** shell is a category; Bash is one implementation.
- **Why:** Bash is one implementation.
〔回读：x〕
""",
    )
    mq = extract_meta_questions(path)["meta_questions"][0]

    def compile_with_raw_labels(target_mq: dict[str, Any], raw_id: str, raw_version: str) -> dict[str, Any]:
        candidate = copy.deepcopy(_minimal_candidate(target_mq["id"]))
        for question in candidate["questions"]:
            question["id"] = raw_id
            question["version"] = raw_version

        def llm(system: str, payload: dict[str, Any]) -> dict[str, Any]:
            if "independent reviewer" in system:
                return {"accepted": True, "issues": []}
            return candidate

        return compile_meta_question(target_mq, llm)

    first = compile_with_raw_labels(mq, "q1", "model-v1")
    same_content_different_raw = compile_with_raw_labels(mq, "q-model-other", "model-v2")
    other_mq = {**mq, "id": mq["id"] + "-other", "source": {**mq["source"], "start_line": 99, "end_line": 105}}
    second = compile_with_raw_labels(other_mq, "q1", "model-v1")

    assert first["issues"] == []
    assert same_content_different_raw["issues"] == []
    assert second["issues"] == []

    first_ids = [q["id"] for q in first["questions"]]
    assert all(qid.startswith("q_") for qid in first_ids)
    assert "q1" not in first_ids
    assert len(first_ids) == len(set(first_ids))
    assert [q["id"] for q in same_content_different_raw["questions"]] == first_ids
    assert [q["version"] for q in same_content_different_raw["questions"]] == [q["version"] for q in first["questions"]]
    assert set(first_ids).isdisjoint({q["id"] for q in second["questions"]})

def test_compile_meta_question_repairs_once_after_audit_rejection(tmp_path: Path) -> None:
    path = _book(
        tmp_path,
        """# Demo
# Level 0
## Fifth beat Meta Question gate
**Q1. shell and Bash?**
- **TL;DR:** shell is a category; Bash is one implementation.
- **Why:** Bash is one implementation.
〔回读：x〕
""",
    )
    mq = extract_meta_questions(path)["meta_questions"][0]
    calls = 0

    def llm(system: str, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if "independent reviewer" in system and calls < 4:
            return {"accepted": False, "issues": [{"code": "ambiguous", "message": "ambiguous"}]}
        return _minimal_candidate(mq["id"]) | ({"accepted": True, "issues": []} if "independent reviewer" in system else {})

    result = compile_meta_question(mq, llm)

    assert result["issues"] == []
    assert calls == 4


def test_compile_meta_question_reports_ungrounded_duplicate_and_bad_choice(tmp_path: Path) -> None:
    path = _book(
        tmp_path,
        """# Demo
# Level 0
## Fifth beat Meta Question gate
**Q1. shell and Bash?**
- **TL;DR:** shell is a category; Bash is one implementation.
〔回读：x〕
""",
    )
    mq = extract_meta_questions(path)["meta_questions"][0]
    bad = _minimal_candidate(mq["id"])
    bad["points"][0]["evidence"] = [{"mq_id": mq["id"], "quote": "not in source"}]
    bad["questions"] = bad["questions"][:]
    bad["questions"][0] = {
        **bad["questions"][0],
        "prompt": bad["questions"][1]["prompt"],
        "correct_choice_id": "missing",
    }

    def llm(system: str, payload: dict[str, Any]) -> dict[str, Any]:
        return bad

    result = compile_meta_question(mq, llm)

    codes = {issue["code"] for issue in result["issues"]}
    assert "ungrounded_evidence" in codes
    assert "duplicate_question" in codes
    assert "bad_correct_choice" in codes
    assert result["questions"] == []


def test_compile_meta_question_requires_reusing_existing_point_id(tmp_path: Path) -> None:
    path = _book(
        tmp_path,
        """# Demo
# Level 0
## Fifth beat Meta Question gate
**Q1. shell and Bash?**
- **TL;DR:** shell is a category; Bash is one implementation.
- **Why:** Bash is one implementation.
〔回读：x〕
""",
    )
    mq = extract_meta_questions(path)["meta_questions"][0]
    candidate = _minimal_candidate(mq["id"])
    candidate["points"][0]["id"] = "pt_new_duplicate"
    for question in candidate["questions"]:
        question["point_ids"] = ["pt_new_duplicate"]
        for criterion in question["criteria"]:
            criterion["point_id"] = "pt_new_duplicate"
    existing = [
        {
            "id": "pt_existing",
            "name": "Shell category",
            "definition": "shell is a category and Bash is one implementation",
        }
    ]

    def llm(system: str, payload: dict[str, Any]) -> dict[str, Any]:
        if "independent reviewer" in system:
            return {"accepted": True, "issues": []}
        assert payload["existing_points"] == existing
        return candidate

    result = compile_meta_question(mq, llm, existing_points=existing)

    assert {issue["code"] for issue in result["issues"]} == {"point_should_reuse_existing"}


def test_compile_meta_question_rejects_mixed_blank_grading(tmp_path: Path) -> None:
    path = _book(
        tmp_path,
        """# Demo
# Level 0
## Fifth beat Meta Question gate
**Q1. shell and Bash?**
- **TL;DR:** shell is a category; Bash is one implementation.
- **Why:** Bash is one implementation.
〔回读：x〕
""",
    )
    mq = extract_meta_questions(path)["meta_questions"][0]
    candidate = _minimal_candidate(mq["id"])
    fill = next(q for q in candidate["questions"] if q["type"] == "fill_blank")
    fill["blanks"] = [
        {"id": "b1", "label": "one", "answers": ["category"], "grading": "exact"},
        {"id": "b2", "label": "two", "answers": [], "grading": "semantic"},
    ]
    fill["criteria"] = [
        {**fill["criteria"][0], "id": "c1", "blank_id": "b1"},
        {**fill["criteria"][0], "id": "c2", "blank_id": "b2"},
    ]

    def llm(system: str, payload: dict[str, Any]) -> dict[str, Any]:
        return candidate

    result = compile_meta_question(mq, llm)

    assert "mixed_blank_grading" in {issue["code"] for issue in result["issues"]}
    assert result["questions"] == []


def test_compile_meta_question_rejects_previous_prompt_reuse(tmp_path: Path) -> None:
    path = _book(
        tmp_path,
        """# Demo
# Level 0
## Fifth beat Meta Question gate
**Q1. shell and Bash?**
- **TL;DR:** shell is a category; Bash is one implementation.
- **Why:** Bash is one implementation.
〔回读：x〕
""",
    )
    mq = extract_meta_questions(path)["meta_questions"][0]
    candidate = _minimal_candidate(mq["id"])
    previous = [{"id": "old-q", "type": "single_choice", "purpose": "practice", "prompt": candidate["questions"][0]["prompt"]}]

    def llm(system: str, payload: dict[str, Any]) -> dict[str, Any]:
        if "independent reviewer" not in system:
            assert payload["previous_questions"] == previous
        return candidate

    result = compile_meta_question(mq, llm, previous_questions=previous)

    assert "previous_question_duplicate" in {issue["code"] for issue in result["issues"]}
    assert result["questions"] == []
