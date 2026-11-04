# -*- coding: utf-8 -*-
"""《第七十二小时，他没有自杀》封面排版。

用法：
    env\\Scripts\\python.exe tools\\cover_72h_compose.py
    env\\Scripts\\python.exe tools\\cover_72h_compose.py --base outputs/cover_72h/xxx.png

底图由 tools/cover_72h_v3.py 通过项目内 ComfyUI 流程生成；本脚本只做：
裁切 → 冷调压暗 → 标题排版 → 颗粒/扫描线 → 输出 1280×1640 / 640×820 / 缩略图。
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "cover_72h"

SERIF = r"C:\Windows\Fonts\NotoSerifSC-VF.ttf"
SANS = r"C:\Windows\Fonts\NotoSansSC-VF.ttf"
MONO = r"C:\Windows\Fonts\consola.ttf"
GEORGIA = r"C:\Windows\Fonts\georgia.ttf"

W, H = 1280, 1640


def font(path: str, size: int, weight: str | None = None) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(path, size)
    if weight:
        try:
            f.set_variation_by_name(weight)
        except Exception:
            pass
    return f


def text_mask(text: str, f: ImageFont.FreeTypeFont, tracking: int = 0,
              pad: int = 40) -> Image.Image:
    """把一行字画成灰度蒙版，按实际像素裁紧。"""
    tmp = Image.new("L", (4200, 900), 0)
    d = ImageDraw.Draw(tmp)
    x = pad
    for ch in text:
        d.text((x, pad), ch, font=f, fill=255, anchor="lt")
        x += d.textlength(ch, font=f) + tracking
    bbox = tmp.getbbox()
    if not bbox:
        return Image.new("L", (1, 1), 0)
    return tmp.crop(bbox)


def gradient(size: tuple[int, int], top: tuple[int, int, int],
             bottom: tuple[int, int, int]) -> Image.Image:
    w, h = size
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for i, (a, b) in enumerate(zip(top, bottom)):
        arr[:, :, i] = np.linspace(a, b, h, dtype=np.uint8)[:, None]
    return Image.fromarray(arr, "RGB")


def colored_text(text: str, f: ImageFont.FreeTypeFont, tracking: int,
                 top: tuple[int, int, int], bottom: tuple[int, int, int],
                 glow: tuple[int, int, int] | None = None,
                 glow_radius: int = 26, glow_alpha: int = 90) -> Image.Image:
    mask = text_mask(text, f, tracking)
    rgb = gradient(mask.size, top, bottom)
    layer = Image.new("RGBA", mask.size, (0, 0, 0, 0))
    layer.paste(rgb, (0, 0), mask)
    if not glow:
        return layer
    # 外发光：把蒙版放大模糊，铺一层低透明度色
    pad = glow_radius * 3
    big = Image.new("L", (mask.width + pad * 2, mask.height + pad * 2), 0)
    big.paste(mask, (pad, pad))
    halo = big.filter(ImageFilter.GaussianBlur(glow_radius))
    halo = halo.point(lambda v: int(v * glow_alpha / 255))
    glow_layer = Image.new("RGBA", big.size, glow + (0,))
    glow_layer.putalpha(halo)
    out = Image.new("RGBA", big.size, (0, 0, 0, 0))
    out.alpha_composite(glow_layer)
    out.alpha_composite(layer, (pad, pad))
    return out


def draw_tracked(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
                 f: ImageFont.FreeTypeFont, fill, tracking: int = 0) -> None:
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=f, fill=fill, anchor="lt")
        x += draw.textlength(ch, font=f) + tracking


def tracked_width(text: str, f: ImageFont.FreeTypeFont, tracking: int = 0) -> float:
    return sum(f.getlength(ch) for ch in text) + tracking * max(0, len(text) - 1)


def fit_width(text: str, path: str, weight: str | None, target: int,
              tracking_ratio: float = 0.02) -> tuple[ImageFont.FreeTypeFont, int]:
    size = 40
    while size < 900:
        f = font(path, size, weight)
        tr = max(0, int(size * tracking_ratio))
        if tracked_width(text, f, tr) >= target:
            return f, tr
        size += 2
    return font(path, size, weight), max(0, int(size * tracking_ratio))


def add_grain(img: Image.Image, strength: float = 0.055) -> Image.Image:
    noise = Image.effect_noise((img.width, img.height), 28).convert("L")
    noise = noise.filter(ImageFilter.GaussianBlur(0.35))
    noise_rgb = Image.merge("RGB", (noise, noise, noise))
    return Image.blend(img.convert("RGB"), noise_rgb, strength)


def add_scanlines(img: Image.Image, alpha: int = 9) -> Image.Image:
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for y in range(0, img.height, 4):
        d.line([(0, y), (img.width, y)], fill=(6, 18, 24, alpha), width=1)
    return Image.alpha_composite(img.convert("RGBA"), overlay)


def lift_region(img: Image.Image, center: tuple[int, int], radius: int,
                factor: float = 1.3) -> Image.Image:
    """把右下角桌面/U 盘/手表轻轻提亮，保证缩略图里还能看见关键道具。"""
    w, h = img.size
    yy, xx = np.mgrid[0:h, 0:w]
    dist = np.sqrt((xx - center[0]) ** 2 + (yy - center[1]) ** 2) / radius
    mask = np.clip(1 - dist, 0, 1) ** 1.7
    bright = ImageEnhance.Brightness(img).enhance(factor)
    mask_img = Image.fromarray((mask * 255).astype(np.uint8), "L")
    return Image.composite(bright, img, mask_img)


def soften_left(img: Image.Image, edge: int = 800, radius: float = 6.0) -> Image.Image:
    """左侧文件柜退到景深之外，给 72 和标题让出一块干净的底。"""
    w, h = img.size
    blurred = img.filter(ImageFilter.GaussianBlur(radius))
    xx = np.arange(w, dtype=np.float32)[None, :]
    mask = np.clip((edge - xx) / edge, 0, 1) ** 1.25
    mask = np.repeat(mask, h, axis=0)
    mask_img = Image.fromarray((mask * 255).astype(np.uint8), "L")
    return Image.composite(blurred, img, mask_img)


def add_vignette(img: Image.Image) -> Image.Image:
    w, h = img.size
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w * 0.56, h * 0.44
    dist = np.sqrt(((xx - cx) / (w * 0.78)) ** 2 + ((yy - cy) / (h * 0.78)) ** 2)
    v = np.clip(1.06 - 0.42 * np.clip(dist - 0.28, 0, 2) ** 1.5, 0.42, 1.0)
    arr = np.asarray(img.convert("RGB")).astype(np.float32) * v[:, :, None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def build(base_path: Path, out_stem: str) -> Path:
    base = Image.open(base_path).convert("RGB")
    img = ImageOps.fit(base, (W, H), method=Image.LANCZOS, centering=(0.5, 0.38))
    img = ImageEnhance_contrast(img)

    # 冷调压暗：左侧与底部压出可读的暗部，右侧保留数据屏冷光
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(shade)
    for x in range(0, 980, 4):
        a = int(218 * (1 - x / 980) ** 1.25)
        d.rectangle([x, 0, x + 4, H], fill=(2, 6, 9, a))
    for y in range(620, H, 4):
        a = int(205 * ((y - 620) / (H - 620)) ** 1.5)
        d.rectangle([0, y, W, y + 4], fill=(1, 4, 6, a))
    for y in range(0, 340, 4):
        a = int(120 * (1 - y / 340) ** 1.4)
        d.rectangle([0, y, W, y + 4], fill=(1, 5, 8, a))
    img = Image.alpha_composite(img.convert("RGBA"), shade).convert("RGB")

    # 标题与引语下面垫一层极软的暗雾，压住文件柜标签，保证字能读出来
    bed = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bed)
    bd.rectangle([0, 900, 930, 1370], fill=(0, 0, 0, 108))
    bd.rectangle([30, 720, 700, 900], fill=(0, 0, 0, 76))
    bd.rectangle([20, 120, 700, 675], fill=(0, 0, 0, 112))
    bed = bed.filter(ImageFilter.GaussianBlur(55))
    img = Image.alpha_composite(img.convert("RGBA"), bed).convert("RGB")

    # 细扫描线与胶片颗粒
    img = add_scanlines(img, 8)
    img = add_grain(img, 0.06)
    img = add_vignette(img)
    # 右下角道具补光：U 盘红灯与停表是封面必须被看见的两个物件
    img = lift_region(img, (1110, 1450), 300, 1.34)
    # 左侧文件柜退到景深之外，压掉小标签的噪点
    img = soften_left(img, edge=820, radius=6.5)
    img = img.convert("RGBA")

    # ---- 顶部技术信息 ----
    d = ImageDraw.Draw(img)
    f_mono = font(MONO, 17)
    d.text((58, 54), "02:41:29  /  87%  /  INTERRUPTED", font=f_mono,
           fill=(128, 168, 178, 205))
    right = "CASE 072   ·   QINGSHIMEN"
    d.text((W - 58 - d.textlength(right, font=f_mono), 54), right, font=f_mono,
           fill=(128, 168, 178, 170))
    d.line([(58, 92), (W - 58, 92)], fill=(150, 185, 192, 46), width=1)

    # ---- 巨大 72 ----
    f72, tr72 = fit_width("72", SERIF, "Black", 590, tracking_ratio=0.0)
    art72 = colored_text("72", f72, tr72, (214, 44, 54), (92, 8, 14),
                         glow=(196, 18, 30), glow_radius=34, glow_alpha=110)
    # 让数字吃进一点雨夜颗粒，避免像一块纯色贴纸
    a72 = art72.getchannel("A")
    grain_l = Image.effect_noise(art72.size, 46).convert("L")
    grime = Image.new("RGBA", art72.size, (8, 0, 2, 0))
    grime.putalpha(ImageChops.multiply(grain_l, a72.point(lambda v: int(v * 0.28))))
    art72.alpha_composite(grime)
    art72.putalpha(a72.point(lambda v: int(v * 0.972)))
    # 轻微色差 / 错位，让数字像一块被复写过的数据
    shadow72 = Image.new("RGBA", art72.size, (0, 0, 0, 0))
    shadow72.putalpha(art72.getchannel("A").point(lambda v: int(v * 0.62)))
    shadow72 = shadow72.filter(ImageFilter.GaussianBlur(12))
    img.alpha_composite(shadow72, (62, 166))
    chroma = Image.new("RGBA", art72.size, (0, 0, 0, 0))
    tint = Image.new("RGBA", art72.size, (64, 208, 220, 0))
    tint.putalpha(art72.getchannel("A").point(lambda v: int(v * 0.10)))
    chroma.alpha_composite(tint, (4, 0))
    img.alpha_composite(chroma, (54, 154))
    img.alpha_composite(art72, (54, 150))

    # ---- HOURS + 细分隔线 ----
    f_hours = font(SANS, 20, "Medium")
    draw_tracked(d, (62, 646), "H O U R S", f_hours, (190, 34, 42, 235), 3)
    d.line([(240, 660), (590, 660)], fill=(196, 36, 44, 110), width=1)
    f_small = font(MONO, 15)
    d.text((62, 686), "DATA RECOVERY / 47 CASES / 6 YEARS", font=f_small,
           fill=(120, 150, 158, 165))

    # ---- 引语 ----
    quote = ["一个人最后七十二小时的数据，全的。", "他没有自杀。我要你证明给我看。"]
    f_q = font(SANS, 25, "Light")
    qy = 764
    d.rectangle([62, qy + 4, 65, qy + 106], fill=(186, 26, 34, 230))
    for line in quote:
        d.text((92, qy), line, font=f_q, fill=(158, 181, 185, 232))
        qy += 44

    # ---- 书名 ----
    f_kick = font(SANS, 34, "Light")
    kick = "第 七 十 二 小 时"
    draw_tracked(d, (66, 1010), kick, f_kick, (206, 199, 184, 245), 16)

    title = "他没有自杀"
    f_title, tr_title = fit_width(title, SERIF, "Black", 846, tracking_ratio=0.035)
    t = colored_text(title, f_title, tr_title, (247, 240, 224), (201, 192, 173))
    # 标题投影，压住底图的亮部
    shadow = Image.new("RGBA", (t.width + 80, t.height + 90), (0, 0, 0, 0))
    sm = t.getchannel("A").point(lambda v: int(v * 0.72))
    sh = Image.new("RGBA", shadow.size, (0, 0, 0, 0))
    sh.paste((0, 0, 0, 230), (40, 52), sm)
    sh = sh.filter(ImageFilter.GaussianBlur(18))
    img.alpha_composite(sh, (52, 1052))
    img.alpha_composite(t, (58, 1054))

    # ---- 副标题 ----
    f_tag = font(SANS, 27, "Light")
    tag = "痕迹不会消失，它只是等一个会读的人"
    d = ImageDraw.Draw(img)
    d.text((66, 1252), tag, font=f_tag, fill=(166, 184, 186, 238))
    d.line([(66, 1312), (176, 1312)], fill=(190, 30, 38, 235), width=3)

    # ---- 底部信息 ----
    f_credit = font(SANS, 19, "Regular")
    draw_tracked(d, (66, 1516), "大女主 · 豪门复仇 · 数据取证", f_credit,
                 (134, 158, 163, 210), 3)
    f_author = font(SANS, 21, "Medium")
    d.text((66, 1562), "著 / MUKURO", font=f_author, fill=(196, 192, 180, 225))
    f_geo = font(GEORGIA, 16)
    f_cn = font(SANS, 15, "Light")
    right2 = "60 CHAPTERS"
    tail = "  ·  22 万字"
    rw = d.textlength(right2, font=f_geo) + d.textlength(tail, font=f_cn)
    rx = W - 66 - rw
    d.text((rx, 1566), right2, font=f_geo, fill=(130, 152, 158, 195))
    d.text((rx + d.textlength(right2, font=f_geo), 1567), tail, font=f_cn,
           fill=(130, 152, 158, 195))

    # ---- 右边缘竖排 ----
    side = "DATA  RECOVERY  UNIT  /  NO.072"
    f_side = font(SANS, 14, "Light")
    side_layer = Image.new("RGBA", (600, 40), (0, 0, 0, 0))
    sd = ImageDraw.Draw(side_layer)
    draw_tracked(sd, (0, 8), side, f_side, (120, 150, 158, 120), 3)
    side_layer = side_layer.rotate(90, expand=True)
    img.alpha_composite(side_layer, (W - 42, 300))

    # 最后一道极轻的暗角，压住四边
    img = add_vignette(img)

    raw = OUT / f"{out_stem}_1280x1640.png"
    img.convert("RGB").save(raw, quality=96)
    # 上传主文件 640×820
    small = img.convert("RGB").resize((640, 820), Image.LANCZOS)
    small.save(OUT / f"{out_stem}_640x820.png")
    small.save(OUT / f"{out_stem}_640x820.jpg", quality=92, subsampling=1)
    # 缩略图验收
    thumb_dir = OUT / "_缩略图验收"
    thumb_dir.mkdir(exist_ok=True)
    small.resize((80, 104), Image.LANCZOS).save(thumb_dir / f"{out_stem}_80x104.png")
    small.resize((320, 416), Image.LANCZOS).save(thumb_dir / f"{out_stem}_放大4倍.png")
    return raw


def ImageEnhance_contrast(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Color(img).enhance(1.04)
    img = ImageEnhance.Contrast(img).enhance(1.07)
    img = ImageEnhance.Brightness(img).enhance(0.94)
    return img


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=str(OUT / "cover_72h_v3_s721103_raw.png"))
    ap.add_argument("--stem", default="封面_第七十二小时")
    args = ap.parse_args()
    p = build(Path(args.base), args.stem)
    print(f"-> {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
