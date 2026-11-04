from playwright.sync_api import sync_playwright
import pathlib, os
url = "https://www.163.com/dy/article/HFATARTD0514R9P4.html"
out = pathlib.Path("data/uploads/孔乙己_网络文本.txt")
out.parent.mkdir(parents=True, exist_ok=True)
exe = os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\chromium-1243\chrome-win64\chrome.exe")
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=exe)
    pg = b.new_page()
    pg.goto(url, wait_until="domcontentloaded", timeout=60000)
    pg.wait_for_timeout(3000)
    txt = pg.inner_text(".post_body")
    b.close()
lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
print("lines", len(lines), "chars", sum(len(x) for x in lines))
print("\n".join(lines[:8]))
out.write_text("\n\n".join(lines), encoding="utf-8")
print("saved", out, out.stat().st_size)
