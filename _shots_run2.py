# -*- coding: utf-8 -*-
"""对 Web 界面做全流程截图（使用已准备好的真实工程数据）。

用法：
    env\\Scripts\\python.exe shots.py [目标地址] [输出目录]
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

# 控制台编码兜底：GBK 终端输出 ✅ 会直接抛 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

TARGET = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:7870/"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "screenshots")
OUT.mkdir(parents=True, exist_ok=True)
VIEWPORT = {"width": 1600, "height": 1000}


def snap(page, name: str) -> None:
    page.screenshot(path=str(OUT / f"{name}.png"))
    print(f"  已截图 {name}.png", flush=True)


def nav(page, stage: str) -> None:
    page.click(f'.nav-item[data-stage="{stage}"]')
    page.wait_for_timeout(700)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright\chromium-1243\chrome-win64\chrome.exe"))
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())

        page.goto(TARGET, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)
        snap(page, "01_阶段一_输入文本")

        # 设置弹窗（双密钥状态）
        page.click("#btn-settings")
        page.wait_for_timeout(2000)
        snap(page, "02_设置_密钥与模型状态")
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)

        nav(page, "2")
        page.wait_for_timeout(1200)
        snap(page, "03_阶段二_内容分析")

        nav(page, "3")
        page.wait_for_timeout(1200)
        snap(page, "04_阶段三_角色人设与三视图")

        nav(page, "4")
        page.wait_for_timeout(1200)
        snap(page, "05_阶段四_分镜脚本表格")

        nav(page, "7")
        page.wait_for_timeout(1000)
        # 选中第一格有图的分镜
        cards = page.query_selector_all(".shot-card")
        for c in cards:
            if c.query_selector("img"):
                c.click()
                break
        page.wait_for_timeout(1200)
        snap(page, "06_阶段五_出图与预览")

        # 气泡编辑器
        page.click("#btn-edit-bubble")
        page.wait_for_timeout(2500)
        snap(page, "07_气泡编辑器")
        page.keyboard.press("Escape")
        page.wait_for_timeout(600)

        # 缩略图区域
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1000)
        snap(page, "08_阶段五_缩略图网格")

        nav(page, "logs")
        page.wait_for_timeout(1500)
        snap(page, "09_运行日志")

        nav(page, "projects")
        page.wait_for_timeout(2000)
        snap(page, "10_工程与产物")

        browser.close()

    print("-" * 62)
    if errors:
        print(f"⚠️ 控制台报错 {len(errors)} 条：")
        for e in errors[:12]:
            print("   ", e[:190])
    else:
        print("✅ 无控制台报错")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
