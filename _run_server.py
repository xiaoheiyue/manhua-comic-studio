# -*- coding: utf-8 -*-
"""本轮检测专用启动器：不自动开浏览器，固定 7870，日志落 outputs/_run_server.log。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402

config.WEB_OPEN_BROWSER = False

import uvicorn  # noqa: E402
import server  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(server.app, host=config.WEB_HOST, port=config.WEB_PORT,
                log_level="info", access_log=False)
