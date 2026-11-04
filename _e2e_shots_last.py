import os, pathlib
from playwright.sync_api import sync_playwright
OUT=pathlib.Path("screenshots/run_20261031")
exe=os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\chromium-1243\chrome-win64\chrome.exe")
with sync_playwright() as p:
    b=p.chromium.launch(executable_path=exe); pg=b.new_page(viewport={"width":1600,"height":1000})
    pg.goto("http://127.0.0.1:7870/",wait_until="networkidle",timeout=60000); pg.wait_for_timeout(2000)
    pg.click('.nav-item[data-stage="2"]'); pg.wait_for_timeout(1200)
    pg.click("#btn-style-browse"); pg.wait_for_timeout(2000); pg.screenshot(path=str(OUT/"03b_风格库浏览.png")); print("style modal")
    pg.keyboard.press("Escape"); pg.wait_for_timeout(500)
    pg.click('.nav-item[data-stage="5"]'); pg.wait_for_timeout(1200); pg.screenshot(path=str(OUT/"06_阶段五_25格分镜.png")); print("grid")
    pg.click('.nav-item[data-stage="7"]'); pg.wait_for_timeout(1200); pg.screenshot(path=str(OUT/"08_阶段七_出图与预览.png")); print("stage7")
    pg.eval_on_selector("#page-grid","e=>e.scrollIntoView({block:'center'})"); pg.wait_for_timeout(700); pg.screenshot(path=str(OUT/"09_分格页.png")); print("pages")
    b.close()
