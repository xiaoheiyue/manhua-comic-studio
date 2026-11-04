# -*- coding: utf-8 -*-
"""日志模块：同时写入控制台、日志文件，并保留环形缓冲供界面日志窗口读取。"""

from __future__ import annotations

import logging
import sys
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

import config

_LOCK = threading.Lock()
_BUFFER: deque[str] = deque(maxlen=max(100, config.LOG_MAX_LINES))
_INITIALIZED = False


class _BufferHandler(logging.Handler):
    """把日志行放进内存环形缓冲，供界面"日志窗口"滚动显示。"""

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - 简单转发
        try:
            msg = self.format(record)
        except Exception:  # noqa: BLE001
            return
        with _LOCK:
            _BUFFER.append(msg)


def _build_formatter() -> logging.Formatter:
    return logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def init_logging(level: int = logging.INFO) -> logging.Logger:
    """初始化根日志器，幂等。"""
    global _INITIALIZED
    root = logging.getLogger("comicgen")
    if _INITIALIZED:
        return root

    root.setLevel(level)
    root.propagate = False
    fmt = _build_formatter()

    # 控制台
    stream = logging.StreamHandler(stream=sys.stdout)
    stream.setFormatter(fmt)
    stream.setLevel(level)
    root.addHandler(stream)

    # 文件
    try:
        Path(config.LOG_DIR).mkdir(parents=True, exist_ok=True)
        log_file = Path(config.LOG_DIR) / f"comicgen_{datetime.now():%Y%m%d}.log"
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.setLevel(logging.DEBUG)
        root.addHandler(fh)
    except Exception as exc:  # noqa: BLE001 - 日志文件写不了也不能影响主流程
        root.warning("日志文件创建失败：%s", exc)

    # 内存缓冲
    bh = _BufferHandler()
    bh.setFormatter(logging.Formatter(fmt="%(asctime)s [%(levelname)s] %(message)s",
                                      datefmt="%H:%M:%S"))
    bh.setLevel(logging.INFO)
    root.addHandler(bh)

    _INITIALIZED = True
    return root


def get_logger(name: str = "comicgen") -> logging.Logger:
    """取得子日志器。"""
    init_logging()
    return logging.getLogger(name)


def log_lines(limit: int | None = None) -> str:
    """返回最近若干行日志（给界面用）。"""
    with _LOCK:
        items = list(_BUFFER)
    if limit is not None and limit > 0:
        items = items[-limit:]
    return "\n".join(items)


def clear_log_buffer() -> None:
    """清空界面日志缓冲（不影响文件日志）。"""
    with _LOCK:
        _BUFFER.clear()


def info(msg: str, *args) -> None:
    get_logger().info(msg, *args)


def warn(msg: str, *args) -> None:
    get_logger().warning(msg, *args)


def error(msg: str, *args) -> None:
    get_logger().error(msg, *args)
