# -*- coding: utf-8 -*-
"""参考图去背景。

优先调用 ComfyUI 内置 BiRefNet 工作流（LoadBackgroundRemovalModel +
RemoveBackground + InvertMask + JoinImageWithAlpha），失败时回退到
MediaPipe 人像分割 / OpenCV 边缘漫水填充，保证上传流程不因 ComfyUI
不可用而中断。

用途：
- 角色俯视参考、道具参考、创作工坊参考图（姿态图除外）统一去背景；
- 表情包参考图 / 生成的 chibi 母本去背景，减少背景噪点对角色特征的干扰。
"""
from __future__ import annotations

import io
import time
import uuid
from pathlib import Path

import requests
from PIL import Image

import config

_BG_GRAPH_SAVE_NODE = "6"


def _host() -> str:
    return str(getattr(config, "COMFY_API_HOST", "http://127.0.0.1:8188")).rstrip("/")


def _model_name() -> str:
    return str(getattr(config, "BG_REMOVAL_MODEL", "birefnet.safetensors"))


def _timeout() -> float:
    return float(getattr(config, "BG_REMOVAL_TIMEOUT", 180) or 180)


def comfy_available() -> bool:
    try:
        r = requests.get(f"{_host()}/system_stats", timeout=3)
        return bool(r.ok)
    except Exception:  # noqa: BLE001
        return False


def _comfy_remove(src: str, dst: str, transparent: bool) -> str:
    """ComfyUI BiRefNet：输出 RGBA（或白底 RGB）到 dst。"""
    host = _host()
    session = requests.Session()
    with open(src, "rb") as f:
        up = session.post(
            f"{host}/upload/image",
            files={"image": (f"bg_{uuid.uuid4().hex[:8]}.png", f, "image/png")},
            data={"overwrite": "true"}, timeout=120)
    up.raise_for_status()
    name = up.json()["name"]

    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": name}},
        "2": {"class_type": "LoadBackgroundRemovalModel",
              "inputs": {"bg_removal_name": _model_name()}},
        "3": {"class_type": "RemoveBackground",
              "inputs": {"bg_removal_model": ["2", 0], "image": ["1", 0]}},
        # JoinImageWithAlpha 内部会做 1 - mask，所以先把前景 mask 反转一次
        "4": {"class_type": "InvertMask", "inputs": {"mask": ["3", 0]}},
        "5": {"class_type": "JoinImageWithAlpha",
              "inputs": {"image": ["1", 0], "alpha": ["4", 0]}},
        _BG_GRAPH_SAVE_NODE: {
            "class_type": "SaveImage",
            "inputs": {"images": ["5", 0], "filename_prefix": "bgremove"}},
    }
    q = session.post(f"{host}/prompt", json={"prompt": graph,
                                             "client_id": uuid.uuid4().hex},
                     timeout=60)
    q.raise_for_status()
    prompt_id = q.json()["prompt_id"]

    deadline = time.time() + _timeout()
    entry = None
    while time.time() < deadline:
        h = session.get(f"{host}/history/{prompt_id}", timeout=30)
        if h.ok:
            data = h.json()
            if prompt_id in data:
                entry = data[prompt_id]
                break
        time.sleep(0.8)
    if entry is None:
        raise TimeoutError("ComfyUI 去背景超时")
    status = entry.get("status") or {}
    if status.get("status_str") == "error":
        raise RuntimeError("ComfyUI 去背景执行失败")
    images = ((entry.get("outputs") or {})
              .get(_BG_GRAPH_SAVE_NODE, {}).get("images") or [])
    if not images:
        raise RuntimeError("ComfyUI 去背景没有返回图片")
    info = images[0]
    r = session.get(f"{host}/view", params={
        "filename": info.get("filename", ""),
        "subfolder": info.get("subfolder", ""),
        "type": info.get("type", "output"),
    }, timeout=120)
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content))
    im = im.convert("RGBA" if im.mode in ("RGBA", "LA") else "RGB")
    out = Path(dst)
    out.parent.mkdir(parents=True, exist_ok=True)
    if transparent:
        im.save(out, format="PNG")
    else:
        canvas = Image.new("RGB", im.size, (255, 255, 255))
        if im.mode == "RGBA":
            canvas.paste(im, (0, 0), im)
        else:
            canvas.paste(im.convert("RGB"), (0, 0))
        canvas.save(out, format="PNG")
    return str(out)


def _fallback_person(src: str, dst: str) -> str:
    """MediaPipe 自拍分割：适合人物参考图，失败返回空字符串。"""
    try:
        from . import poseutil
        made = poseutil.white_background(src, dst)
        if made and Path(made).exists():
            return str(made)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _fallback_flood(src: str, dst: str) -> str:
    """OpenCV 从四边漫水填充成白色，适合纯色/简单背景的物件图。"""
    try:
        import cv2
        import numpy as np
        im = cv2.imdecode(np.fromfile(src, dtype=np.uint8), cv2.IMREAD_COLOR)
        if im is None:
            return ""
        h, w = im.shape[:2]
        mask = np.zeros((h + 2, w + 2), np.uint8)
        for seed in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
                     (w // 2, 0), (w // 2, h - 1)):
            cv2.floodFill(im, mask, seed, (255, 255, 255),
                          loDiff=(24, 24, 24), upDiff=(24, 24, 24), flags=8)
        out = Path(dst)
        out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imencode(".png", im)[1].tofile(str(out))
        return str(out)
    except Exception:  # noqa: BLE001
        return ""


def _fallback_alpha_flood(src: str, dst: str) -> str:
    """ComfyUI 不可用时的透明兜底：从四边漫水，背景 alpha=0。

    适合表情包这类纯白/纯色背景贴纸；复杂背景会留下残边，所以只作为兜底。
    """
    try:
        import cv2
        import numpy as np
        bgr = cv2.imdecode(np.fromfile(src, dtype=np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            return ""
        h, w = bgr.shape[:2]
        work = bgr.copy()
        mask = np.zeros((h + 2, w + 2), np.uint8)
        for seed in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
                     (w // 2, 0), (w // 2, h - 1)):
            cv2.floodFill(work, mask, seed, (255, 255, 255),
                          loDiff=(18, 18, 18), upDiff=(18, 18, 18), flags=8)
        alpha = np.where(mask[1:-1, 1:-1] > 0, 0, 255).astype(np.uint8)
        # 边缘轻微羽化，避免白边
        alpha = cv2.GaussianBlur(alpha, (3, 3), 0)
        rgba = np.dstack([cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), alpha])
        out = Path(dst)
        out.parent.mkdir(parents=True, exist_ok=True)
        cv2.imencode(".png", cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))[1].tofile(str(out))
        return str(out)
    except Exception:  # noqa: BLE001
        return ""


def remove_background(src: str | Path, dst: str | Path | None = None,
                      mode: str = "white") -> str:
    """去背景并保存。

    mode="white" 输出白底 RGB（推荐给图像模型当参考图）；
    mode="transparent" 输出透明 PNG（给贴纸/表情包）。
    返回输出路径；全部失败时返回原图路径。
    """
    src_p = Path(src)
    if not src_p.exists():
        return str(src_p)
    dst_p = Path(dst) if dst else src_p.with_name(src_p.stem + "_nobg.png")
    if dst_p.exists() and dst_p.stat().st_mtime >= src_p.stat().st_mtime:
        return str(dst_p)
    transparent = str(mode).lower() in ("transparent", "alpha", "rgba")

    if bool(getattr(config, "BG_REMOVAL_ENABLED", True)) and comfy_available():
        try:
            return _comfy_remove(str(src_p), str(dst_p), transparent=transparent)
        except Exception as exc:  # noqa: BLE001
            try:
                from .logger import get_logger
                get_logger().warning("ComfyUI 去背景失败，回退本地：%s", exc)
            except Exception:
                pass

    # 透明模式本地没有可靠的通用 alpha 分割；先用漫水 alpha 兜底，
    # 复杂背景仍然失败时退回白底（至少不带场景噪点）。
    if transparent:
        fb = _fallback_alpha_flood(str(src_p), str(dst_p))
        if fb:
            return fb

    fb = _fallback_person(str(src_p), str(dst_p))
    if fb:
        return fb
    fb = _fallback_flood(str(src_p), str(dst_p))
    if fb:
        return fb
    return str(src_p)
