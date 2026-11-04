import os, pathlib
from playwright.sync_api import sync_playwright
OUT=pathlib.Path("screenshots/round2"); OUT.mkdir(parents=True,exist_ok=True)
exe=os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\chromium-1243\chrome-win64\chrome.exe")
with sync_playwright() as p:
    b=p.chromium.launch(executable_path=exe); pg=b.new_page(viewport={"width":1600,"height":1000})
    errs=[]; pg.on("pageerror",lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:7870/",wait_until="networkidle",timeout=60000); pg.wait_for_timeout(2000)
    pg.screenshot(path=str(OUT/"01_输入文本.png"))
    pg.click('.nav-item[data-stage="refine"]'); pg.wait_for_timeout(1500)
    pg.screenshot(path=str(OUT/"02_小说精修_25格.png"))
    print("errors",errs)
    b.close()
