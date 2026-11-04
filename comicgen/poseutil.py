# -*- coding: utf-8 -*-
"""姿态图工具：把真人照片转成 OpenPose 风格骨架。

只依赖 mediapipe（0.10.x）+ opencv；没有安装时抛 RuntimeError，
调用方回退为"直接把上传图当骨架"。
"""
from __future__ import annotations

from pathlib import Path


def _imread(path: str | Path):
    """cv2.imread 的 Unicode 安全版（Windows 中文路径）。"""
    import cv2
    import numpy as np
    from PIL import Image

    rgb = np.array(Image.open(path).convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _imwrite(path: str | Path, bgr) -> None:
    """cv2.imwrite 的 Unicode 安全版。"""
    import cv2
    from PIL import Image

    Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).save(path)


def photo_to_skeleton(src: str | Path, dst: str | Path,
                      min_visibility: float = 0.35) -> str:
    """真人照片 -> 黑底彩色骨架 PNG。

    生成的是 OpenPose 风格的身体线 + 关节点，用于 Z-Image Pose ControlNet。
    单张照片里有多个人的情况只取置信度最高的一个人。
    """
    try:
        import cv2
        import mediapipe as mp
        import numpy as np
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("未安装 mediapipe/opencv，无法把真人照片转骨架") from exc

    img = _imread(src)
    if img is None:
        raise ValueError("读不到上传的图片")
    h, w = img.shape[:2]
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    try:
        with mp.solutions.pose.Pose(
                static_image_mode=True,
                model_complexity=1,
                enable_segmentation=False,
                min_detection_confidence=0.4) as pose:
            result = pose.process(rgb)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"姿态检测失败：{exc}") from exc
    if not result.pose_landmarks:
        raise ValueError("没有检测到人体姿态，请换一张全身/半身更清晰的照片")

    canvas = np.zeros((h, w, 3), dtype=np.uint8)
    # 黑底 + 高对比骨架；ControlNet 只关心关节位置
    mp.solutions.drawing_utils.draw_landmarks(
        canvas, result.pose_landmarks, mp.solutions.pose.POSE_CONNECTIONS,
        landmark_drawing_spec=mp.solutions.drawing_styles.get_default_pose_landmarks_style())

    # 按检测到的关节范围裁紧，避免 4000px 宽图里人物只占一小块；
    # ControlNet 会再缩放，裁紧后姿态占比更合理。
    xs, ys = [], []
    for lm in result.pose_landmarks.landmark:
        if lm.visibility >= min_visibility:
            xs.append(lm.x * w)
            ys.append(lm.y * h)
    if xs and ys:
        pad_x = max(24, (max(xs) - min(xs)) * 0.12)
        pad_y = max(24, (max(ys) - min(ys)) * 0.08)
        x0 = max(0, int(min(xs) - pad_x))
        y0 = max(0, int(min(ys) - pad_y))
        x1 = min(w, int(max(xs) + pad_x))
        y1 = min(h, int(max(ys) + pad_y))
        if x1 - x0 > 32 and y1 - y0 > 64:
            canvas = canvas[y0:y1, x0:x1]

    out = Path(dst)
    out.parent.mkdir(parents=True, exist_ok=True)
    _imwrite(out, canvas)
    return str(out)


def looks_like_skeleton(src: str | Path) -> bool:
    """粗略判断上传图是否已经是黑底彩色骨架（避免重复检测）。"""
    try:
        import cv2
        import numpy as np
    except Exception:  # noqa: BLE001
        return False
    img = _imread(src)
    if img is None:
        return False
    h, w = img.shape[:2]
    if h < 64 or w < 64:
        return False
    # 骨架图通常 >70% 像素接近纯黑，且存在明显彩色线
    flat = img.reshape(-1, 3)
    dark = np.mean(np.max(flat, axis=1) < 18)
    colorful = np.mean(np.max(flat, axis=1) - np.min(flat, axis=1) > 35)
    return bool(dark > 0.55 and colorful > 0.008)


def white_background(image_path: str | Path, out_path: str | Path) -> str:
    """用 MediaPipe 人像分割把姿态图裁成白底单人格，去掉桌子/百叶窗等背景。"""
    import cv2
    import numpy as np
    import mediapipe as mp
    from PIL import Image

    src = _imread(image_path)
    if src is None:
        return ""
    rgb = cv2.cvtColor(src, cv2.COLOR_BGR2RGB)
    with mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=1) as seg:
        mask = seg.process(rgb).segmentation_mask
    if mask is None:
        return ""
    m = (mask > 0.5).astype(np.uint8) * 255
    m = cv2.GaussianBlur(m, (5, 5), 0)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n > 1:
        idx = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        m = (labels == idx).astype(np.uint8) * 255
    alpha = (m.astype(np.float32) / 255.0)[..., None]
    out = (src.astype(np.float32) * alpha + 255.0 * (1.0 - alpha)).astype(np.uint8)
    ys, xs = np.where(m > 0)
    if len(xs):
        out = out[max(0, ys.min() - 12):ys.max() + 12,
                  max(0, xs.min() - 12):xs.max() + 12]
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB)).save(out_path)
    return str(out_path)
