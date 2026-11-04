"""角色参考资产：面部特写、单视图、俯视质量。

这些资产由主视图/三视图/俯视图派生，**不重新生成角色**：
- 面部特写：从主视图裁头肩，给特写/近景当身份参考；
- 单视图：把三视图切成 front/side/back，给侧身/背身镜头；
- 俯视图：复制到 views/top.png，并用“人脸检测=0”判断是不是真顶视。

用法：
    from comicgen import charrefs
    charrefs.ensure_character_assets(character, look_dir)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

import config


def _read_rgb(path: str | Path) -> Image.Image | None:
    try:
        return Image.open(path).convert("RGB")
    except Exception:
        return None


def detect_faces_in_image(img: Image.Image) -> list[tuple[int, int, int, int]]:
    """对 PIL 图片做人脸检测（用于出图后的“多画了一张脸”检查）。"""
    try:
        import cv2
        arr = np.array(img.convert("RGB"))
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        cas = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cas.detectMultiScale(gray, 1.08, 5, minSize=(32, 32))
        return sorted([tuple(map(int, f)) for f in faces],
                      key=lambda r: r[2] * r[3], reverse=True)
    except Exception:
        return []


def detect_faces(path: str | Path) -> list[tuple[int, int, int, int]]:
    """Haar 人脸检测；只用于“脸大概在哪/有没有脸”，不用于身份判断。"""
    img = _read_rgb(path)
    if img is None:
        return []
    return detect_faces_in_image(img)


def _trim_white(img: Image.Image, tolerance: int = 245) -> Image.Image:
    """裁掉四周白边（三视图单格常带大片留白）。"""
    arr = np.array(img.convert("RGB"))
    mask = (arr < tolerance).any(axis=2)
    ys, xs = np.where(mask)
    if len(xs) == 0 or len(ys) == 0:
        return img
    box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        return img
    return img.crop(box)


def _fit_white(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """等比缩放到画布内，白底居中；不裁人物。"""
    canvas = Image.new("RGB", size, (255, 255, 255))
    img = img.copy()
    img.thumbnail(size, Image.LANCZOS)
    canvas.paste(img, ((size[0] - img.width) // 2,
                       max(0, (size[1] - img.height) // 2)))
    return canvas


def face_crop(main_path: str | Path, out_path: str | Path,
              margin: float = 2.4) -> bool:
    """从主视图裁一个头肩特写；检测不到脸时退回“顶部居中头肩框”。

    Haar 对风格化/低对比脸会漏检；此时不能直接放弃面部特写，
    否则该角色在特写/近景就没有 face 参考。顶部居中框只用于主视图
    （单人正面全身），不会像三视图那样裁进第二个人。
    """
    img = _read_rgb(main_path)
    if img is None:
        return False
    faces = detect_faces(main_path)
    if faces:
        x, y, w, h = faces[0]
        side = max(8, int(max(w, h) * float(margin)))
        cx = x + w // 2
        cy = y + h // 2 + int(h * 0.15)
        left = cx - side // 2
        top = cy - side // 2
    else:
        side = max(64, int(min(img.width, img.height) * 0.42))
        left = (img.width - side) // 2
        top = max(0, int(img.height * 0.015))
    # 先在白底上放一个 side×side 的画布，再贴图，避免越界时裁掉脸。
    canvas = Image.new("RGB", (side, side), (255, 255, 255))
    src_box = (max(0, left), max(0, top),
               min(img.width, left + side), min(img.height, top + side))
    crop = img.crop(src_box)
    canvas.paste(crop, (max(0, -left), max(0, -top)))
    canvas = _fit_white(canvas, (512, 512))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG")
    return True


def split_triple(triple_path: str | Path, look_dir: str | Path) -> dict[str, str]:
    """把三视图切成 front/side/back 三张单视图。

    三视图顺序按本项目约定：正面 / 侧面 / 背面。
    正面由调用方用主视图覆盖（保证 100% 同脸）。
    """
    img = _read_rgb(triple_path)
    out: dict[str, str] = {}
    if img is None:
        return out
    view_dir = Path(look_dir) / str(getattr(config, "CHARACTER_VIEW_DIR", "views"))
    view_dir.mkdir(parents=True, exist_ok=True)
    third = img.width // 3
    if third < 32:
        return out
    for name, box in (("side", (third, 0, third * 2, img.height)),
                      ("back", (third * 2, 0, img.width, img.height))):
        panel = _trim_white(img.crop(box))
        panel = _fit_white(panel, tuple(getattr(config, "CHARACTER_VIEW_SIZE", (512, 768))))
        path = view_dir / f"{name}.png"
        panel.save(path, format="PNG")
        out[name] = str(path)
    return out


def copy_front(main_path: str | Path, look_dir: str | Path) -> str:
    """正面单视图 = 主视图（同一张脸），只做白底/尺寸归一。"""
    img = _read_rgb(main_path)
    if img is None:
        return ""
    view_dir = Path(look_dir) / str(getattr(config, "CHARACTER_VIEW_DIR", "views"))
    view_dir.mkdir(parents=True, exist_ok=True)
    panel = _fit_white(_trim_white(img), tuple(getattr(config, "CHARACTER_VIEW_SIZE", (512, 768))))
    path = view_dir / "front.png"
    panel.save(path, format="PNG")
    return str(path)


def top_quality(top_path: str | Path) -> str:
    """真顶视 = 看不到脸；能看到脸就是高角度俯视。"""
    if not Path(top_path).is_file():
        return ""
    return "high_angle" if detect_faces(top_path) else "top_down"


def copy_top(top_path: str | Path, look_dir: str | Path) -> str:
    if not Path(top_path).is_file():
        return ""
    view_dir = Path(look_dir) / str(getattr(config, "CHARACTER_VIEW_DIR", "views"))
    view_dir.mkdir(parents=True, exist_ok=True)
    dest = view_dir / "top.png"
    shutil.copyfile(top_path, dest)
    return str(dest)


def ensure_character_assets(character, look_dir: str | Path) -> dict:
    """生成/刷新一个角色的 face_ref、views、view_quality。

    返回摘要，便于日志/测试；任何一项失败都不抛异常（不阻断主流程）。
    """
    look_dir = Path(look_dir)
    look_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, object] = {"name": getattr(character, "name", ""),
                                  "face": False, "views": [], "top": ""}
    main = str(getattr(character, "main_visual", "") or "")
    if not main or not Path(main).is_file():
        ref = getattr(character, "reference_image", lambda: "")()
        main = str(ref or "")
    if not main or not Path(main).is_file():
        summary["error"] = "没有主视图"
        return summary

    face_name = Path(str(getattr(config, "CHARACTER_FACE_FILENAME", "面部特写"))).stem + ".png"
    face_path = look_dir / face_name
    try:
        if face_crop(main, face_path):
            character.face_ref = str(face_path)
            summary["face"] = True
        elif Path(character.face_ref or "").is_file():
            summary["face"] = True
    except Exception:
        pass

    try:
        front = copy_front(main, look_dir)
        if front:
            character.views = dict(getattr(character, "views", {}) or {})
            character.views["front"] = front
            summary["views"].append("front")
    except Exception:
        pass

    triple = str(getattr(character, "triple_view", "") or "")
    if triple and Path(triple).is_file():
        try:
            got = split_triple(triple, look_dir)
            character.views = dict(getattr(character, "views", {}) or {})
            character.views.update(got)
            summary["views"].extend(got.keys())
        except Exception:
            pass

    top = str(getattr(character, "top_view", "") or "")
    if top and Path(top).is_file():
        try:
            quality = top_quality(top)
            dest = copy_top(top, look_dir)
            if dest:
                character.views = dict(getattr(character, "views", {}) or {})
                character.views["top"] = dest
                character.view_quality = dict(getattr(character, "view_quality", {}) or {})
                character.view_quality["top"] = quality
                summary["top"] = quality
                summary["views"].append("top")
        except Exception:
            pass

    summary["views"] = sorted(set(summary["views"]))
    return summary
