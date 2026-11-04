import os, pathlib
from playwright.sync_api import sync_playwright
OUT=pathlib.Path("screenshots/round2")
exe=os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\chromium-1243\chrome-win64\chrome.exe")
with sync_playwright() as p:
    b=p.chromium.launch(executable_path=exe); pg=b.new_page(viewport={"width":1600,"height":1000})
    pg.goto("http://127.0.0.1:7870/",wait_until="networkidle",timeout=60000); pg.wait_for_timeout(2000)
    pg.click('.nav-item[data-stage="3"]'); pg.wait_for_timeout(1500)
    pg.screenshot(path=str(OUT/"04_角色参考图.png"))
    print("ok")
    b.close()
