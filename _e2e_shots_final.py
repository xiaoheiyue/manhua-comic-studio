import os, pathlib
from playwright.sync_api import sync_playwright
OUT=pathlib.Path("screenshots/run_20261031"); OUT.mkdir(parents=True,exist_ok=True)
exe=os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\chromium-1243\chrome-win64\chrome.exe")
errors=[]
with sync_playwright() as p:
    b=p.chromium.launch(executable_path=exe)
    pg=b.new_page(viewport={"width":1600,"height":1000},device_scale_factor=1)
    pg.on("pageerror",lambda e: errors.append(str(e)))
    pg.goto("http://127.0.0.1:7870/",wait_until="networkidle",timeout=60000); pg.wait_for_timeout(2500)
    def nav(s): pg.click(f'.nav-item[data-stage="{s}"]'); pg.wait_for_timeout(1500)
    def snap(n): pg.screenshot(path=str(OUT/f"{n}.png")); print("shot",n,flush=True)
    nav("4"); pg.wait_for_timeout(800); snap("05b_阶段四_分镜形态与版式")
    nav("5"); pg.wait_for_timeout(1500); snap("06_阶段五_25格分镜")
    nav("6"); pg.wait_for_timeout(1500); snap("07_阶段六_姿态图")
    nav("7"); pg.wait_for_timeout(1500); snap("08_阶段七_出图与预览")
    pg.eval_on_selector("#page-grid","e=>e.scrollIntoView({block:'center'})"); pg.wait_for_timeout(800); snap("09_分格页")
    nav("projects"); pg.wait_for_timeout(1500); snap("13_工程与产物")
    nav("logs"); pg.wait_for_timeout(1200); snap("14_运行日志")
    b.close()
print("errors",errors[:5])
