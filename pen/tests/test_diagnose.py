from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from pen import diagnose, trajectory
from pen.app import app
from pen.config import DEFAULT_HANDBOOK


def _q(
    *,
    level: str,
    title: str,
    selected: str = "",
    chip: str = "explain_zero",
    user: str = "",
    kind: str = "q",
    beat: str = "第五拍 · 📝 Meta Question 门禁",
    line: int = 10,
) -> dict:
    return {
        "chip": chip,
        "user_text": user,
        "anchor": {
            "kind": kind,
            "level": level,
            "beat": beat,
            "q_title": title,
            "start_line": line,
            "end_line": line,
            "selected_text": selected,
        },
        "ok": True,
    }


def test_empty_history() -> None:
    report = diagnose.aggregate([])
    assert report["n_turns"] == 0
    assert report["n_curriculum"] == 0
    assert report["weak"] == []
    assert report["footprints"] == []
    assert report["levels"] == []


def test_cover_and_one_shot_are_not_weak() -> None:
    turns = [
        _q(level="封面", title="", kind="other", selected="封面闲话"),
        _q(
            level="Level 2",
            title="**Q4. 为什么说 LLM 没有记忆？**",
            selected="messages.append",
            user="少 append 哪一行",
        ),
    ]
    report = diagnose.aggregate(turns)
    assert report["n_curriculum"] == 1
    assert report["weak"] == []
    assert len(report["footprints"]) == 1
    assert report["footprints"][0]["level"] == "Level 2"
    assert "append" in report["footprints"][0]["keywords"]


def test_repeat_same_q_is_weak_and_levels_sum() -> None:
    t = _q(
        level="Level 0",
        title="**Q3. `chmod +x hello.sh` 和 `bash hello.sh` 两种运行方式的本质区别是什么？**",
        selected="source ~/.bashrc 之后 export 才看得到",
        user="source 和 export 到底谁先",
    )
    report = diagnose.aggregate([t, dict(t), dict(t)])
    assert report["n_curriculum"] == 3
    assert len(report["weak"]) == 1
    spot = report["weak"][0]
    assert spot["hits"] == 3
    assert spot["pct"] == 100.0
    assert spot["level"] == "Level 0"
    assert "chmod" in " ".join(spot["keywords"]) or "export" in spot["keywords"]
    assert report["levels"][0]["level"] == "Level 0"
    assert report["levels"][0]["pct"] == 100.0


def test_same_q_number_different_level_do_not_merge() -> None:
    a = _q(level="Level 0", title="**Q1. shell 和 Bash 是什么关系？**")
    b = _q(level="Level 1", title="**Q1. 为什么要用虚拟环境？**")
    report = diagnose.aggregate([a, a, b])
    labels = {s["label"] for s in report["weak"] + report["footprints"]}
    assert any("shell 和 Bash" in x for x in labels)
    assert any("虚拟环境" in x for x in labels)
    assert len(report["weak"]) == 1
    assert report["weak"][0]["level"] == "Level 0"


def test_jsonl_roundtrip(tmp_path: Path, monkeypatch) -> None:
    from pen import config

    monkeypatch.setattr(config, "PEN_DIR", tmp_path / ".pen")
    hid = "mini-book"
    trajectory.append_turn(hid, _q(level="Level 0", title="**Q2. heredoc 的引号？**"))
    trajectory.append_turn(hid, _q(level="Level 0", title="**Q2. heredoc 的引号？**"))
    turns = trajectory.load_turns(hid)
    assert len(turns) == 2
    report = diagnose.aggregate(turns)
    assert report["weak"][0]["hits"] == 2


def test_corrupt_jsonl_skipped(tmp_path: Path, monkeypatch) -> None:
    from pen import config

    monkeypatch.setattr(config, "PEN_DIR", tmp_path / ".pen")
    dest = tmp_path / ".pen" / "trajectories"
    dest.mkdir(parents=True)
    (dest / "bad.jsonl").write_text("{no\n{}\n", encoding="utf-8")
    assert trajectory.load_turns("bad") == []


def test_diagnosis_endpoint_empty_and_no_handbook_write(tmp_path: Path, monkeypatch) -> None:
    from pen import config

    monkeypatch.setattr(config, "PEN_DIR", tmp_path / ".pen")
    before = DEFAULT_HANDBOOK.read_bytes()
    mtime = DEFAULT_HANDBOOK.stat().st_mtime
    with TestClient(app) as client:
        r = client.get("/v1/handbooks/swe-agent-v2/diagnosis")
        assert r.status_code == 200
        body = r.json()
        assert body["handbook_id"] == "swe-agent-v2"
        assert "weak" in body
        assert "footprints" in body
        missing = client.get("/v1/handbooks/no-such-book/diagnosis")
        assert missing.status_code == 404
    assert DEFAULT_HANDBOOK.read_bytes() == before
    assert DEFAULT_HANDBOOK.stat().st_mtime == mtime


def test_start_line_keeps_first_hit() -> None:
    a = _q(
        level="Level 2",
        title="**Q4. 为什么说 LLM 没有记忆？**",
        selected="messages.append",
        line=3994,
    )
    b = _q(
        level="Level 2",
        title="**Q4. 为什么说 LLM 没有记忆？**",
        selected="再 append 一次",
        line=4051,
    )
    report = diagnose.aggregate([a, b])
    assert report["weak"][0]["start_line"] == 3994


def test_junk_keywords_filtered() -> None:
    report = diagnose.aggregate(
        [
            _q(
                level="Level 2",
                title="**Q4. 为什么说 LLM 没有记忆？**",
                selected="if mode == plan then input prompt",
                user="system prompt 呢",
            ),
            _q(
                level="Level 2",
                title="**Q4. 为什么说 LLM 没有记忆？**",
                selected="if mode",
            ),
        ]
    )
    keys = report["weak"][0]["keywords"]
    assert "Q4." not in keys
    assert "if" not in keys
    assert "mode" not in keys
    assert "input" not in keys
    assert "system" not in keys
    assert "prompt" not in keys


def test_keyword_provenance() -> None:
    report = diagnose.aggregate(
        [
            _q(
                level="Level 0",
                title="**Q3. `chmod +x hello.sh` 和 `bash hello.sh`**",
                selected="source ~/.bashrc 之后 export 才看得到",
                user="export 写进谁",
            ),
            _q(
                level="Level 0",
                title="**Q3. `chmod +x hello.sh` 和 `bash hello.sh`**",
                selected="source",
            ),
        ]
    )
    srcs = {k["token"]: k["src"] for k in report["weak"][0]["keyword_src"]}
    assert srcs["chmod +x hello.sh"] == "title"
    assert srcs["source"] == "selected"
    assert srcs["export"] in {"selected", "user"}


def test_narrate_prompt_excludes_user_text() -> None:
    report = diagnose.aggregate(
        [
            _q(
                level="Level 0",
                title="**Q3. chmod**",
                user="我的密码是 hunter2 请看 source",
            ),
            _q(
                level="Level 0",
                title="**Q3. chmod**",
                user="我的密码是 hunter2 请看 source",
            ),
        ]
    )
    _system, user = diagnose.narrate_prompt(report)
    assert "hunter2" not in user
    assert "Q3" in user or "chmod" in user
