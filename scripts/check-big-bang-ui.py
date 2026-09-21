"""Browser integration checks. Uses real view/layout/API code with a fake host/model.
Run: python scripts/check-big-bang-ui.py [screenshot-directory]
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
with tempfile.TemporaryDirectory(prefix="sp-big-bang-") as tmp:
    subprocess.run(["node", "scripts/build-big-bang-harness.mjs", tmp], cwd=ROOT, check=True)
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
        page.evaluate("qa.capture()")
        assert page.locator(".sp-agent-cell").count() == 1
        page.evaluate("qa.history(0)")
        page.locator(".sp-input").fill("Draft stays in its own agent")
        page.evaluate("window.originalInput=document.querySelector('.sp-input'); document.querySelector('.sp-log').scrollTop=80")
        page.evaluate("""()=>{const log=document.querySelector('.sp-log');window.readingAnchor=[...log.children].find(e=>e.getBoundingClientRect().bottom>log.getBoundingClientRect().top+8)}""")
        for count in [2, 3, 4]:
            page.locator(".sp-add-agent:visible").click()
            page.wait_for_function("(n)=>document.querySelectorAll('.sp-agent-cell').length===n && !qa.view.adding", arg=count)
        assert page.locator(".sp-add-agent:visible").is_disabled()
        assert page.evaluate("new Set(qa.view.panes.map(p=>p.pane.session)).size") == 4
        assert page.evaluate("savedData.agentPanels.length") == 4
        assert page.evaluate("originalInput===document.querySelector('.sp-input')")
        assert page.evaluate("()=>{const r=readingAnchor.getBoundingClientRect(),l=document.querySelector('.sp-log').getBoundingClientRect();return r.bottom>l.top&&r.top<l.bottom}")
        assert page.locator(".sp-input").first.input_value() == "Draft stays in its own agent"
        for width, height, layout in [(1440, 900, "grid"), (700, 1800, "column-4"), (320, 900, "stack"), (1100, 850, "grid")]:
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_function("(kind)=>document.querySelector('.sp-agent-grid').dataset.layout===kind", arg=layout)
            assert page.evaluate("originalInput===document.querySelector('.sp-input')")
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            # The form stays inside its pane, including the small overflow cards.
            assert page.locator(".sp-agent-cell").evaluate_all("els=>els.every(e=>{const f=e.querySelector('.sp-form').getBoundingClientRect(),p=e.getBoundingClientRect();return f.bottom<=p.bottom+1&&f.left>=p.left-1&&f.right<=p.right+1})")
            if shots:
                page.screenshot(path=str(shots / f"{width}x{height}.png"))
        # Four independent streaming requests; no selected-tab routing shortcuts.
        for index in range(4):
            pane = page.locator(".sp-agent-cell").nth(index)
            pane.locator(".sp-input").fill(f"Question for agent {index}")
            pane.locator(".sp-input").press("Enter")
            page.wait_for_function("(n)=>qa.streams.size===n", arg=index+1)
            page.evaluate("i=>qa.emit(i,{type:'token',text:`Only agent ${i}. `.repeat(40)})", index)
        page.wait_for_function("qa.view.panes.every((p,i)=>p.pane.msgs.at(-1).text.includes(`Only agent ${i}.`))")
        assert page.evaluate("qa.view.panes.every((p,i)=>!p.pane.msgs.at(-1).text.includes(`Only agent ${(i+1)%4}.`))")
        page.locator(".sp-log").first.evaluate("el=>el.scrollTop=50")
        page.evaluate("qa.emit(0,{type:'token',text:' must be ignored',run_id:'wrong-run'})")
        assert page.evaluate("!qa.view.panes[0].pane.msgs.at(-1).text.includes('must be ignored')")
        page.evaluate("qa.emit(0,{type:'token',text:' continues'})")
        page.wait_for_function("qa.view.panes[0].pane.msgs.at(-1).text.endsWith('continues')")
        assert page.locator(".sp-log").first.evaluate("el=>el.scrollTop") == 50
        page.locator(".sp-agent-cell").nth(0).locator(".sp-stop-agent").click()
        page.wait_for_function("qa.streams.size===3 && !qa.view.panes[0].pane.busy")
        assert page.locator(".sp-agent-cell").nth(0).locator(".sp-input").is_enabled()
        page.evaluate("qa.emit(3,{type:'token',text:'still running'})")
        page.wait_for_function("qa.view.panes[3].pane.msgs.at(-1).text.endsWith('still running')")
        # One pending approval does not disable the other agents.
        page.evaluate("""()=>{qa.emit(1,{type:'approval',pending_id:'old-p',name:'edit_file',args:{path:'/vault/note.md',old_string:'before',new_string:'after'}});
          const sid=qa.view.panes[1].pane.session;qa.streams.get(sid).controller.close();qa.streams.delete(sid);} """)
        page.wait_for_function("qa.view.panes[1].pane.pending && !qa.view.panes[1].pane.controller")
        assert page.locator(".sp-agent-cell").nth(1).locator(".sp-panel").is_visible()
        page.locator(".sp-agent-cell").nth(1).locator(".sp-panel .mod-cta").click()
        page.wait_for_function("qa.view.panes[1].pane.approving && qa.streams.has(qa.view.panes[1].pane.session)")
        page.evaluate("""()=>{qa.emit(1,{type:'tool',name:'edit_file',ok:false,code:'FILE_CHANGED',detail:'/vault/note.md'});
          qa.emit(1,{type:'approval',pending_id:'fresh-p',name:'edit_file',args:{path:'/vault/note.md',old_string:'before v2',new_string:'after v2'}});
          const sid=qa.view.panes[1].pane.session;qa.streams.get(sid).controller.close();qa.streams.delete(sid);} """)
        page.wait_for_function("qa.view.panes[1].pane.pending?.pending_id==='fresh-p' && !qa.view.panes[1].pane.controller")
        assert page.locator(".sp-agent-cell").nth(1).locator(".sp-panel").inner_text().find("before v2") >= 0
        assert page.evaluate("qa.streams.size") == 2
        page.set_viewport_size({"width": 700, "height": 1800})
        page.wait_for_function("document.querySelector('.sp-agent-grid').dataset.layout==='column-4'")
        assert page.locator(".sp-agent-cell").nth(1).locator(".sp-panel").is_visible()
        page.evaluate("qa.finish(2);qa.finish(3)")
        page.wait_for_function("!qa.view.panes[2].pane.busy && !qa.view.panes[3].pane.busy")
        page.locator(".sp-agent-cell").nth(1).locator(".sp-close-agent").click()
        page.wait_for_function("qa.view.paneCount===3 && !qa.view.adding")
        for count in [2, 1]:
            page.locator(".sp-agent-cell").last.locator(".sp-close-agent").click()
            page.wait_for_function("(n)=>qa.view.paneCount===n && !qa.view.adding", arg=count)
        page.wait_for_function("document.querySelector('.sp-agent-grid').dataset.layout==='single'")
        assert page.evaluate("originalInput===document.querySelector('.sp-input')")
        # Return to 3 panes to verify both asymmetric orientations and dark styling.
        for _ in range(2):
            page.locator(".sp-add-agent:visible").click()
            page.wait_for_function("!qa.view.adding")
        for width,height,kind in [(1100,800,'primary-left'),(800,1000,'primary-top')]:
            page.set_viewport_size({"width":width,"height":height})
            page.wait_for_function("k=>document.querySelector('.sp-agent-grid').dataset.layout===k",arg=kind)
        page.evaluate("document.body.classList.add('dark');qa.language('zh')")
        page.wait_for_function("document.querySelector('.sp-big-bang-title').textContent.includes('实验')")
        if shots:
            page.screenshot(path=str(shots / "portrait-three-dark.png"))
        identities = page.evaluate("qa.view.panes.map(p=>p.pane.session)")
        page.evaluate("qa.reopen()")
        assert page.evaluate("qa.view.panes.map(p=>p.pane.session)") == identities
        assert page.locator(".sp-agent-cell").count() == 3
        assert not errors, errors
        browser.close()
print("Big Bang UI: split/reshape, DOM identity, drafts, four streams, stop, pending approval and close passed")
