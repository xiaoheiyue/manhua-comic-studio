"""Qwen-Image-2.1 漫画出图后端：有参考图走 Edit，无参考图走 T2I。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import config
from .imageclient import GenResult
from .qwenedit import QwenEditClient


class QwenComicClient:
    supports_reference_images = True
    color_matched = False

    def __init__(self) -> None:
        self.workflow_name = "qwen_image_2.1_turbo"
        self._qwen = QwenEditClient()

    def style_reference(self, *_args: Any, **_kwargs: Any) -> list[str]:
        # Qwen 的风格靠提示词，不把画风图当参考图；否则会把画风图内容也编辑进来。
        return []

    # ---- 服务状态（给 /api/service/status 用，别再去查 Z-Image 工作流）----
    def health(self) -> tuple[bool, str]:
        ok = self._qwen.available()
        if ok:
            return True, "Qwen-Image-2.1 Turbo：ComfyUI 就绪"
        return False, "Qwen-Image-2.1 Turbo：无法连接 ComfyUI（127.0.0.1:8188）"

    def image_model_status(self) -> dict[str, Any]:
        ok = self._qwen.available()
        return {
            "loaded": ok,
            "workflow": "qwen_image_2.1_turbo",
            "source": "ComfyUI",
            "engine": "Qwen-Image-2.1 Turbo",
            "host": self._qwen.host,
            "supports_reference_images": True,
            "reference_slots": int(getattr(config, "QWEN_STORYBOARD_REF_MAX", 3) or 3),
            "color_matched": False,
            "supports_negative_prompt": True,
            "negative_enabled": True,
            "negative_effective": True,
            "models": {},
        }

    @staticmethod
    def _face_safe_crop(img):
        """单人格后处理：检测到多张脸时，以最大脸为中心裁切，避免双头/克隆。"""
        try:
            import cv2, numpy as np
            arr = np.array(img.convert("RGB"))
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
            cas = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            faces = cas.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48))
            if len(faces) <= 1:
                return img
            x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
            cx, cy = x + w / 2, y + h / 2
            side = max(w, h) * 2.6
            x0 = max(0, int(cx - side / 2)); y0 = max(0, int(cy - side / 2))
            x1 = min(arr.shape[1], int(cx + side / 2)); y1 = min(arr.shape[0], int(cy + side / 2))
            crop = img.crop((x0, y0, x1, y1))
            return crop.resize(img.size)
        except Exception:
            return img

    def generate(self, prompt: str, *, reference_paths: list[str] | None = None,
                 negative_prompt: str = "", size: tuple[int, int] | None = None,
                 seed: int | None = None, pose_image: str | None = None,
                 color_transfer_strength: float | None = None,
                 single_subject: bool = False,
                 **kwargs: Any) -> GenResult:
        size = tuple(size or config.IMAGE_SIZE)
        refs = [p for p in (reference_paths or []) if p and Path(p).exists()]
        seed_i = int(seed or 888001)
        # Qwen Image Edit 支持多参考图：按镜头路由送 1–2 张（主视图/面部特写/
        # 侧面/背面/俯视）。参考图先 pad 到分镜格比例，避免输出被参考图比例绑架。
        if pose_image and Path(pose_image).exists():
            # 姿态格：ControlNet 锁姿势 + 多参考图锁身份。
            img = self._qwen.generate_control(
                prompt, refs, pose_image,
                negative=negative_prompt, size=size, seed=seed_i, steps=8,
                strength=0.8)
            wf = "qwen_image_2.1_turbo_control"
        elif refs:
            img = self._qwen.generate(
                prompt, refs, negative=negative_prompt, size=size,
                seed=seed_i, steps=8)
            wf = "qwen_image_2.1_turbo_edit_local"
        else:
            img = self._qwen.generate_t2i(
                prompt, negative=negative_prompt, size=size, seed=seed_i, steps=8)
            wf = "qwen_image_2.1_turbo_t2i_local"
        if single_subject:
            img = self._face_safe_crop(img)
        return GenResult(image=img, seed=seed_i, size=img.size,
                         reference_paths=refs, workflow=wf,
                         prompt_id="", protocol="comfyui")
