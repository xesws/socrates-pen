"""Browser integration checks for the experimental PracticeView.

Uses the production view/API client/CSS with a fake Obsidian host and in-memory
practice service. Run: python scripts/check-practice-ui.py [screenshot-directory]
Requires the optional Python playwright package and Chrome/Chromium.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
shots = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
if shots:
    shots.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory(prefix="sp-practice-") as tmp:
    subprocess.run(["node", "scripts/build-practice-harness.mjs", tmp], cwd=ROOT, check=True)
    with sync_playwright() as p:
        chrome = os.environ.get("CHROME_EXECUTABLE") or shutil.which("google-chrome")
        mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if not chrome and Path(mac).exists():
            chrome = mac
        browser = p.chromium.launch(headless=True, **({"executable_path": chrome} if chrome else {}))
        page = browser.new_page(viewport={"width": 1100, "height": 850})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(Path(tmp, "index.html").as_uri())
        page.wait_for_function("window.ready === true")
        page.wait_for_function("qa.serverEnabled === true && qa.view.state === 'ready'")
        enable_requests = page.evaluate("requests.filter(r => r.path === '/v1/practice/enable').map(r => r.body.enabled)")
        assert True in enable_requests
        page.get_by_role("button", name="Build / rebuild").click()
        page.wait_for_function("qa.view.practiceState?.resource?.meta_questions?.length===2")
        source_text = page.locator(".sp-practice-panel:not(.is-off)").inner_text()
        assert "completed_with_issues" not in source_text
        assert "completed with issues" in source_text
        assert "ready" in source_text
        assert page.get_by_role("button", name="Resume generation").is_visible()
        assert page.get_by_role("button", name="Generate new variants").is_visible()
        assert "One generated distractor was quarantined." in "\n".join(page.locator(".sp-practice-issues").all_inner_texts())
        page.evaluate("qa.togglePractice(false)")
        page.wait_for_function("qa.serverEnabled === false && qa.view.state === 'disabled'")
        assert page.evaluate("qa.view.job === null && qa.view.session === null")
        assert page.evaluate("requests.filter(r => r.path === '/v1/practice/enable').at(-1).body.enabled === false")
        assert page.get_by_role("button", name="Turn on experimental practice").is_visible()
        page.evaluate("qa.togglePractice(true)")
        page.wait_for_function("qa.serverEnabled === true && qa.view.state === 'ready'")
        assert page.evaluate("requests.filter(r => r.path === '/v1/practice/enable').at(-1).body.enabled === true")
        if shots:
            page.screenshot(path=str(shots / "practice-source.png"))

        page.get_by_role("button", name="Practice").click()
        page.get_by_role("button", name="Free practice").click()
        assert page.locator('input[type="file"]').count() == 0
        page.get_by_label("A stable rule").check()
        page.get_by_role("button", name="Submit answer").click()
        page.wait_for_function("qa.view.session?.attempts?.[0]?.grade?.score===1")
        assert "1 / 1" in page.locator(".sp-practice-grade").inner_text()
        page.evaluate("qa.setStale(true)")
        page.wait_for_function("qa.view.practiceState?.resource?.stale===true")
        assert page.get_by_role("button", name="Free practice").is_disabled()
        assert page.get_by_role("button", name="Resume practice · active").is_visible()
        assert "Rebuild the question bank before starting new practice" in page.locator(".sp-practice-panel:not(.is-off)").inner_text()
        page.evaluate("qa.setStale(false)")
        page.wait_for_function("qa.view.practiceState?.resource?.stale===false")

        page.locator("button[title='Next question']").click()
        page.locator(".sp-practice-fill input").fill("drafted invariant")
        page.get_by_label("Upload answer images").set_input_files(str(ROOT / "evals/answer_images_v2/assets/fill.png"))
        page.wait_for_function("qa.view.answer.images?.length===1")
        page.wait_for_function("qa.sessions.get(qa.view.session.id).drafts['q-fill']?.blanks?.term==='drafted invariant'")
        page.wait_for_function("qa.sessions.get(qa.view.session.id).drafts['q-fill']?.images?.length===1")
        page.evaluate("qa.reopen()")
        page.wait_for_function("window.ready === true")
        page.get_by_role("button", name="Practice").click()
        page.get_by_role("button", name="Resume practice · active").click()
        page.wait_for_selector(".sp-practice-fill input")
        assert page.locator(".sp-practice-fill input").input_value() == "drafted invariant"
        assert page.locator('.sp-practice-answer-image img').count() == 1
        page.get_by_role("button", name="Remove image").click()
        assert page.locator('.sp-practice-answer-image img').count() == 0
        assert page.locator(".sp-practice-fill input").input_value() == "drafted invariant"
        page.get_by_role("button", name="Submit answer").click()
        page.wait_for_function("qa.view.session?.attempts?.some(a=>a.question_id==='q-fill'&&a.status==='graded')")

        page.locator("button[title='Next question']").click()
        # Paste an image-only answer. No text transcript is supplied by the client.
        import base64
        pasted = base64.b64encode((ROOT / "evals/answer_images_v2/assets/basic.png").read_bytes()).decode()
        page.locator("textarea.sp-practice-text").evaluate("""(el, data) => {
            const bytes = Uint8Array.from(atob(data), c => c.charCodeAt(0));
            const clipboardData = new DataTransfer();
            clipboardData.items.add(new File([bytes], 'answer.png', {type:'image/png'}));
            el.dispatchEvent(new ClipboardEvent('paste', {clipboardData, bubbles:true, cancelable:true}));
        }""", pasted)
        page.wait_for_function("qa.view.answer.images?.length===1")
        assert page.locator("textarea.sp-practice-text").input_value() == ""
        if shots:
            page.screenshot(path=str(shots / "practice-uploaded-answer.png"))
        page.get_by_role("button", name="Submit answer").click()
        page.wait_for_function("qa.view.session?.attempts?.some(a=>a.question_id==='q-short'&&a.status==='pending')")
        assert "waiting for grading" in page.locator(".sp-practice-grade").inner_text()
        page.wait_for_function("qa.view.session?.attempts?.some(a=>a.question_id==='q-short'&&a.status==='graded')")
        assert page.evaluate("qa.view.session.attempts.find(a=>a.question_id==='q-short').answer.images.length===1")
        assert "0.5 / 1" in page.locator(".sp-practice-grade").inner_text()

        page.get_by_role("button", name="Start exam").click()
        page.get_by_label("A stable rule").check()
        page.get_by_role("button", name="Submit answer").click()
        page.wait_for_function("qa.view.session?.mode==='exam' && qa.view.session?.attempts?.length===1")
        assert "reference answer" not in page.locator(".sp-practice-session").inner_text().lower()
        assert "graded · 1 / 1" not in page.locator(".sp-practice-session").inner_text()
        page.get_by_role("button", name="Finish exam").click()
        page.wait_for_function("qa.view.session?.status==='completed'")
        final_text = page.locator(".sp-practice-session").inner_text().lower()
        assert "reference answer" in final_text
        assert "graded · 1 / 1" in final_text
        assert "total 83 / 100 · target 80 · passed" in final_text
        assert "coverage complete" in final_text
        if shots:
            page.screenshot(path=str(shots / "practice-exam-finished.png"))

        page.get_by_role("button", name="Analysis").click()
        page.wait_for_function("qa.view.analysis?.points?.length===2")
        analysis_text = page.locator(".sp-practice-panel:not(.is-off)").inner_text()
        assert "Analysis model: cold start" in analysis_text
        assert "Mean" in analysis_text and "Predicted" in analysis_text and "Evidence" in analysis_text
        assert "Application · not measured yet · gap 80% · 0 evidence" in analysis_text
        assert "[object Object]" not in analysis_text
        assert "not measured yet" in analysis_text
        page.locator(".sp-axes details").first.click()
        opened_analysis_text = page.locator(".sp-practice-panel:not(.is-off)").inner_text()
        assert "q-choice · score 100%" in opened_analysis_text
        assert "[object Object]" not in opened_analysis_text
        assert "not stable · 2 comparable exams · target 80 · date 2026-10-22" in analysis_text
        assert "83 / target 80 · passed" in analysis_text
        if shots:
            page.screenshot(path=str(shots / "practice-analysis.png"))
        assert not errors, errors
        browser.close()

print("Practice UI: build poll, draft restore, three question types, pending grade and exam reveal passed")
