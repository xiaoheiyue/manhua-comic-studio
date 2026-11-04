import os, pathlib
from playwright.sync_api import sync_playwright
OUT=pathlib.Path("screenshots/run_20261031"); OUT.mkdir(parents=True,exist_ok=True)
exe=os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\chromium-1243\chrome-win64\chrome.exe")
errors=[]
with sync_playwright() as p:
    b=p.chromium.launch(executable_path=exe)
    pg=b.new_page(viewport={"width":1600,"height":1000},device_scale_factor=1)
    pg.on("pageerror",lambda e: errors.append(str(e)))
    pg.goto("http://127.0.0.1:7870/",wait_until="networkidle",timeout=60000)
    pg.wait_for_timeout(2500)
    def nav(stage):
        pg.click(f'.nav-item[data-stage="{stage}"]'); pg.wait_for_timeout(1500)
    def snap(name):
        pg.screenshot(path=str(OUT/f"{name}.png")); print("shot",name,flush=True)
    # 阶段4 版式
    nav("4"); pg.wait_for_timeout(1000); snap("05b_阶段四_分镜形态与版式")
    # 阶段5 25格
    nav("5"); pg.wait_for_timeout(1200); snap("06_阶段五_25格分镜")
    # 阶段6 姿态
    nav("6"); pg.wait_for_timeout(1200); snap("07_阶段六_姿态图")
    # 阶段7
    nav("7"); pg.wait_for_timeout(2000); snap("08_阶段七_出图与预览")
    pg.eval_on_selector("#page-grid","e=>e.scrollIntoView({block:'center'})"); pg.wait_for_timeout(800); snap("09_分格页")
    # 选第一格
    cards=pg.query_selector_all("#shot-grid .shot-card")
    print("cards",len(cards),flush=True)
    clicked=False
    for c in cards:
        if c.query_selector("img"):
            c.scroll_into_view_if_needed(); pg.wait_for_timeout(300); c.click(); clicked=True; break
    pg.wait_for_timeout(1000)
    if clicked:
        pg.eval_on_selector("#preview-box","e=>e.scrollIntoView({block:'center'})"); pg.wait_for_timeout(600); snap("10_单格大图")
        try:
            pg.click("#btn-edit-bubble"); pg.wait_for_timeout(1800); snap("11_气泡编辑器")
            pg.keyboard.press("Escape"); pg.wait_for_timeout(500)
        except Exception as e:
            print("bubble fail",e,flush=True)
    # 其他页面
    nav("studio"); pg.wait_for_timeout(1200); snap("12_创作工坊")
    nav("projects"); pg.wait_for_timeout(1500); snap("13_工程与产物")
    nav("logs"); pg.wait_for_timeout(1200); snap("14_运行日志")
    pg.click("#btn-settings"); pg.wait_for_timeout(1500); snap("15_设置与服务状态")
    b.close()
print("errors",errors[:5])
