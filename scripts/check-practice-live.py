"""Live browser smoke for the experimental practice stack.

This uses the production PracticeView and the real FastAPI gateway with real
analysis and scheduling child workers.  The resource is pre-seeded into an
isolated temporary PEN_HOME so the run never calls the generation pipeline or an
external LLM provider.

Run: python scripts/check-practice-live.py [screenshot-directory]
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BOOK_TEXT = """# Practice Live Book

# Level 0

## Fifth beat Meta Question gate

**Q1. What is an invariant?**
- **TL;DR:** An invariant is a stable rule that remains true while steps change.
- **Why:** A stable rule lets us check reasoning after each step.

〔回读：Third beat〕

**Q2. How should you use an invariant?**
- **TL;DR:** Use the invariant as a reference point when deciding the next step.
- **Why:** The reference point prevents a random aside from replacing the goal.

〔回读：Third beat〕
"""


def _request_json(method: str, url: str, body: dict[str, Any] | None = None, *, timeout: float = 5) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept-Language": "en"},
    )
    with urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _ui_port() -> int:
    for port in (5173, 4173):
        if _port_is_free(port):
            return port
    raise RuntimeError("Neither localhost:5173 nor localhost:4173 is free for the CORS smoke")


def _wait_health(base_url: str, proc: subprocess.Popen[Any]) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    last = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        try:
            return _request_json("GET", f"{base_url}/v1/health", timeout=1)
        except Exception as exc:  # noqa: BLE001 - this is a short readiness loop
            last = str(exc)
            time.sleep(0.1)
    raise RuntimeError(f"sidecar did not become healthy: {last}")


def _handbook_id_from_path(abs_path: str) -> str:
    h = 0
    for ch in abs_path:
        h = ((h * 31) + ord(ch)) & 0xFFFFFFFF
    stem = Path(abs_path).name
    stem = re.sub(r"\.md$", "", stem, flags=re.IGNORECASE) or "note"
    slug = re.sub(r"[^a-z0-9._-]+", "-", stem.lower()).strip("-") or "note"
    short = slug[:48].rstrip("-") or "note"
    return f"{short}-{h:x}"


def _criterion(cid: str, point_id: str, mq_id: str, *, blank_id: str | None = None) -> dict[str, Any]:
    out = {
        "id": cid,
        "point_id": point_id,
        "max_score": 1,
        "description": f"Checks {point_id} from the source meta question.",
        "partial_credit": "No partial credit for this deterministic smoke item.",
        "evidence": [{"mq_id": mq_id, "quote": "stable rule"}],
    }
    if blank_id:
        out["blank_id"] = blank_id
    return out


def _single_choice(qid: str, mq_id: str, point_id: str, *, purpose: str, exam_form: int | None) -> dict[str, Any]:
    return {
        "id": qid,
        "version": "v1",
        "source_mq_id": mq_id,
        "type": "single_choice",
        "purpose": purpose,
        "exam_form": exam_form,
        "prompt": "Which option names the invariant?",
        "point_ids": [point_id],
        "status": "accepted",
        "estimated_seconds": 30,
        "choices": [
            {"id": "a", "text": "A stable rule"},
            {"id": "b", "text": "A random aside"},
        ],
        "correct_choice_id": "a",
        "reference_answer": "An invariant is a stable rule.",
        "criteria": [_criterion(f"c-{qid}", point_id, mq_id)],
    }


def _fill_blank(qid: str, mq_id: str, point_id: str, *, purpose: str, exam_form: int | None) -> dict[str, Any]:
    return {
        "id": qid,
        "version": "v1",
        "source_mq_id": mq_id,
        "type": "fill_blank",
        "purpose": purpose,
        "exam_form": exam_form,
        "prompt": "Complete the reference term.",
        "point_ids": [point_id],
        "status": "accepted",
        "estimated_seconds": 30,
        "blanks": [{"id": "term", "label": "Term", "answers": ["invariant"], "grading": "exact"}],
        "reference_answer": "invariant",
        "criteria": [_criterion(f"c-{qid}", point_id, mq_id, blank_id="term")],
    }


def _seed_practice(vault: Path, pen_home: Path) -> tuple[str, Path]:
    os.environ["PEN_HOME"] = str(pen_home)
    os.environ["PEN_ALLOW_ROOTS"] = str(vault)
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("DEEPSEEK_API_KEY", None)
    os.environ.pop("OPENAI_BASE_URL", None)
    os.environ.pop("MODEL_NAME", None)

    from pen import config

    config.apply_pen_home()
    from pen import libraries
    from pen.practice.contracts import fingerprint, scope_id, timestamp
    from pen.practice.resources import extract_meta_questions
    from pen.practice.runtime import set_enabled
    from pen.practice.store import Store

    vault.mkdir(parents=True, exist_ok=True)
    book = vault / "note.md"
    book.write_text(BOOK_TEXT, encoding="utf-8")
    hid = _handbook_id_from_path(str(book.resolve()))
    libraries.register(book, hid, extra_roots=[vault])
    snapshot = extract_meta_questions(book)
    mqs = snapshot["meta_questions"]
    if len(mqs) != 2:
        raise RuntimeError(f"expected 2 meta questions, got {len(mqs)}")

    now = timestamp()
    mq1, mq2 = mqs
    p1 = {
        "id": "p-invariant",
        "name": "Invariant",
        "definition": "A stable rule that remains true while steps change.",
        "source_mq_ids": [mq1["id"]],
        "evidence": [{"mq_id": mq1["id"], "quote": "stable rule"}],
    }
    p2 = {
        "id": "p-application",
        "name": "Application",
        "definition": "Using the invariant as the reference point for the next step.",
        "source_mq_ids": [mq2["id"]],
        "evidence": [{"mq_id": mq2["id"], "quote": "reference point"}],
    }
    resource_version = f"res-live-{fingerprint(snapshot['source_revision'])[:8]}"
    resource = {
        "resource_version": resource_version,
        "source_revision": snapshot["source_revision"],
        "meta_questions": mqs,
        "points": [p1, p2],
        "edges": [{"from": p1["id"], "to": p2["id"], "type": "requires", "evidence": [{"mq_id": mq2["id"], "quote": "reference point"}]}],
        "questions": [
            _single_choice("q-choice", mq1["id"], p1["id"], purpose="practice", exam_form=None),
            _fill_blank("q-rec-fill", mq2["id"], p2["id"], purpose="practice", exam_form=None),
            _single_choice("e-choice", mq1["id"], p1["id"], purpose="exam", exam_form=0),
            _fill_blank("e-fill", mq2["id"], p2["id"], purpose="exam", exam_form=0),
        ],
        "issues": [],
        "built_at": now,
        "handbook_id": hid,
    }
    blueprint = {
        "id": f"{hid}:live",
        "version": 1,
        "resource_version": resource_version,
        "point_weights": {p1["id"]: 1, p2["id"]: 1},
        "target_score": 80,
        "target_date": "2026-10-22",
        "daily_minutes": 20,
        "source_mq_ids": [mq1["id"], mq2["id"]],
        "stable_tolerance": 10,
        "updated_at": now,
    }
    scope = scope_id(str(vault.resolve()))
    set_enabled(scope, True)
    store = Store()
    store.put(scope, "resource", resource_version, hid, resource)
    store.put(scope, "active", hid, hid, {"resource_version": resource_version})
    store.put(scope, "blueprint", hid, hid, blueprint)
    return hid, book


def _stop(proc: subprocess.Popen[Any] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _log_tail(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-6000:]


def main() -> None:
    shots = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="sp-practice-live-") as tmp_raw:
        tmp = Path(tmp_raw)
        vault = tmp / "vault"
        pen_home = tmp / "pen-home"
        harness_dir = tmp / "harness"
        hid, book = _seed_practice(vault, pen_home)

        api_port = _free_port()
        ui_port = _ui_port()
        api_base = f"http://127.0.0.1:{api_port}"
        api_log_path = tmp / "sidecar.log"
        static_log_path = tmp / "static.log"
        env = {
            **os.environ,
            "PEN_HOME": str(pen_home),
            "PEN_ALLOW_ROOTS": str(vault),
            "PYTHONPATH": str(ROOT),
        }
        for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_BASE_URL", "MODEL_NAME"):
            env.pop(key, None)

        api_log = api_log_path.open("w", encoding="utf-8")
        static_log = static_log_path.open("w", encoding="utf-8")
        api_proc: subprocess.Popen[Any] | None = None
        static_proc: subprocess.Popen[Any] | None = None
        try:
            api_proc = subprocess.Popen(
                [sys.executable, "-m", "pen", "--host", "127.0.0.1", "--port", str(api_port)],
                cwd=ROOT,
                env=env,
                stdout=api_log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            _wait_health(api_base, api_proc)

            state_url = f"{api_base}/v1/practice/state?{urlencode({'vault_root': str(vault.resolve()), 'handbook_id': hid})}"
            state = _request_json("GET", state_url)
            assert state["enabled"] is True
            assert state["resource"]["points"][0]["id"] == "p-invariant"
            assert len(state["resource"]["points"]) == 2

            subprocess.run(["node", "scripts/build-practice-live-harness.mjs", str(harness_dir)], cwd=ROOT, check=True, env=env)
            (harness_dir / "config.js").write_text(
                "window.practiceLiveConfig = "
                + json.dumps(
                    {
                        "sidecarUrl": api_base,
                        "vaultRoot": str(vault.resolve()),
                        "notePath": book.relative_to(vault).as_posix(),
                    }
                )
                + ";\n",
                encoding="utf-8",
            )
            static_proc = subprocess.Popen(
                [sys.executable, "-m", "http.server", str(ui_port), "--bind", "127.0.0.1", "--directory", str(harness_dir)],
                cwd=ROOT,
                env=env,
                stdout=static_log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            _wait_static(ui_port, static_proc)

            with sync_playwright() as p:
                chrome = os.environ.get("CHROME_EXECUTABLE") or shutil.which("google-chrome")
                mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                if not chrome and Path(mac).exists():
                    chrome = mac
                browser = p.chromium.launch(headless=True, **({"executable_path": chrome} if chrome else {}))
                page = browser.new_page(viewport={"width": 1100, "height": 850})
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{ui_port}/index.html")
                page.wait_for_function("window.ready === true")
                page.wait_for_function("qa.view.practiceState?.resource?.points?.length === 2")

                page.get_by_role("button", name="Practice").click()
                page.get_by_role("button", name="Free practice").click()
                page.get_by_label("A stable rule").check()
                page.get_by_role("button", name="Submit answer").click()
                page.wait_for_function("qa.view.session?.attempts?.[0]?.grade?.score === 1")
                assert "1 / 1" in page.locator(".sp-practice-grade").inner_text()

                page.get_by_role("button", name="Get recommendations").click()
                page.wait_for_function("qa.view.recs?.items?.length > 0")
                rec_text = page.locator(".sp-practice-panel:not(.is-off)").inner_text()
                assert "Recommendation queue" in rec_text
                assert "[object Object]" not in rec_text
                if shots:
                    page.screenshot(path=str(shots / "practice-live-recommendations.png"))

                page.get_by_role("button", name="Start exam").click()
                page.wait_for_function("qa.view.session?.mode === 'exam' && qa.view.session?.questions?.[0]?.id === 'e-choice'")
                page.get_by_label("A stable rule").check()
                page.get_by_role("button", name="Submit answer").click()
                page.wait_for_function("qa.view.session?.mode === 'exam' && qa.view.session?.attempts?.length === 1")
                page.locator("button[title='Next question']").click()
                page.locator(".sp-practice-fill input").fill("invariant")
                page.get_by_role("button", name="Submit answer").click()
                page.wait_for_function("qa.view.session?.attempts?.length === 2")
                page.get_by_role("button", name="Finish exam").click()
                page.wait_for_function("qa.view.session?.status === 'completed'", timeout=15000)
                final_text = page.locator(".sp-practice-session").inner_text().lower()
                assert "reference answer" in final_text
                assert "coverage complete" in final_text
                assert "passed" in final_text
                assert "[object object]" not in final_text
                if shots:
                    page.screenshot(path=str(shots / "practice-live-exam.png"))

                page.get_by_role("button", name="Analysis").click()
                page.wait_for_function("qa.view.analysis?.points?.length === 2")
                analysis_text = page.locator(".sp-practice-panel:not(.is-off)").inner_text()
                assert "Mean" in analysis_text
                assert "Predicted" in analysis_text
                assert "Evidence" in analysis_text
                assert "Invariant" in analysis_text
                assert "Application" in analysis_text
                assert "[object Object]" not in analysis_text
                if shots:
                    page.screenshot(path=str(shots / "practice-live-analysis.png"))
                assert not errors, errors
                browser.close()

            health = _request_json("GET", f"{api_base}/v1/health")
            services = health.get("practice_services") or {}
            assert services.get("analysis", {}).get("status") == "running", services
            assert services.get("scheduling", {}).get("status") == "running", services
        except Exception:
            if shots and "page" in locals():
                try:
                    page.screenshot(path=str(shots / "practice-live-failure.png"))
                except Exception:
                    pass
            print("--- sidecar.log ---", file=sys.stderr)
            print(_log_tail(api_log_path), file=sys.stderr)
            print("--- static.log ---", file=sys.stderr)
            print(_log_tail(static_log_path), file=sys.stderr)
            raise
        finally:
            _stop(static_proc)
            _stop(api_proc)
            api_log.close()
            static_log.close()

    print("Practice live: real gateway, workers, recommendations, exam completion and analysis passed")


def _wait_static(port: int, proc: subprocess.Popen[Any]) -> None:
    deadline = time.monotonic() + 10
    url = f"http://127.0.0.1:{port}/index.html"
    last = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        try:
            with urlopen(url, timeout=1) as res:
                if res.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001 - readiness loop
            last = str(exc)
            time.sleep(0.1)
    raise RuntimeError(f"static harness did not become ready: {last}")


if __name__ == "__main__":
    main()
