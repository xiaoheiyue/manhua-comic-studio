import json, os
from playwright.sync_api import sync_playwright
exe=os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\chromium-1243\chrome-win64\chrome.exe")
out={"console_errors":[],"page_errors":[],"nav":[],"buttons":[]}
with sync_playwright() as p:
    b=p.chromium.launch(executable_path=exe)
    pg=b.new_page(viewport={"width":1600,"height":1000})
    pg.on("console", lambda m: out["console_errors"].append(m.text) if m.type=="error" else None)
    pg.on("pageerror", lambda e: out["page_errors"].append(str(e)))
    pg.goto("http://127.0.0.1:7870/", wait_until="networkidle", timeout=60000)
    pg.wait_for_timeout(2500)
    out["title"]=pg.title()
    out["nav"]=[{"stage":x.get_attribute("data-stage"),"text":x.inner_text().strip()} for x in pg.query_selector_all(".nav-item")]
    out["buttons"]=[{"id":x.get_attribute("id"),"text":x.inner_text().strip()[:40],"disabled":x.is_disabled()} for x in pg.query_selector_all("button") if x.get_attribute("id")]
    out["text_len"]=len(pg.input_value("#novel-text"))
    out["active_panel"]=pg.get_attribute(".stage-panel.active","data-panel")
    b.close()
open("outputs/_e2e_ui_probe.json","w",encoding="utf-8").write(json.dumps(out,ensure_ascii=False,indent=2))
print("errors",len(out["console_errors"]),len(out["page_errors"]),"buttons",len(out["buttons"]))
