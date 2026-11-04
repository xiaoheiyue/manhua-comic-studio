# -*- coding: utf-8 -*-
"""25 格分镜结果 / 姿态预演的选取与拼版。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

import config


def _load_font(size: int):
    """优先加载系统里的中文字体，拿不到就返回 None（由调用方退回默认字体）。"""
    for candidate in getattr(config, "FONT_CANDIDATES", []) or []:
        try:
            if Path(candidate).exists():
                return ImageFont.truetype(candidate, size)
        except Exception:  # noqa: BLE001
            continue
    return None


def select_key_shots(project, n: int = 25, require_image: bool = True) -> list:
    shots = [s for s in project.shots if str(getattr(s, "raw_image_path", "") or "")] if require_image else list(project.shots)
    if not shots:
        shots = list(project.shots)
    if len(shots) <= n:
        return shots
    # 优先保留重点格/关键 beat，其余按全片均匀取样
    important = [s for s in shots
                 if str(getattr(s, "panel_emphasis", "")) in ("main", "splash")
                 or str(getattr(s, "beat", "")) in ("情绪", "揭示", "动作", "建立", "转场")]
    chosen: list = list(important[:n])
    for s in shots:
        if len(chosen) >= n:
            break
        if s not in chosen:
            chosen.append(s)
    chosen = sorted(chosen, key=lambda s: s.index)
    if len(chosen) > n:
        # 均匀压缩，保留首尾
        step = len(chosen) / n
        chosen = [chosen[min(len(chosen) - 1, int(i * step))] for i in range(n)]
    return chosen


def compose_contact_sheet(image_paths: list[str], out_path: str,
                          cols: int = 5, cell: int = 512,
                          labels: list[str] | None = None,
                          total_slots: int = 0,
                          empty_text: str = "无更多关键镜头") -> str:
    """拼联系表。

    total_slots>图片数时，多出来的格子画成灰色占位格，而不是循环复制已有画面。
    短篇（分镜不足 25 格）仍保持 5×5 版式，但重复格会明确标成空位。
    """
    total = max(len(image_paths), int(total_slots or 0))
    rows = max(1, (total + cols - 1) // cols)
    sheet = Image.new("RGB", (cols * cell, rows * cell), (245, 245, 245))
    draw = ImageDraw.Draw(sheet)
    empty_font = _load_font(24)
    empty_label = empty_text if empty_font is not None else "EMPTY"
    if empty_font is None:
        empty_font = ImageFont.load_default()
    for n in range(total):
        x0 = (n % cols) * cell
        y0 = (n // cols) * cell
        pasted = False
        if n < len(image_paths):
            try:
                im = Image.open(image_paths[n]).convert("RGB")
                # 5×5 联系表统一“满格”：按格子比例裁切，避免竖图/方图留下灰边。
                # 主体略偏上，避免裁掉脸。
                try:
                    im = ImageOps.fit(im, (cell - 6, cell - 6), method=Image.LANCZOS,
                                      centering=(0.5, 0.38))
                except Exception:  # noqa: BLE001
                    im.thumbnail((cell - 6, cell - 6), Image.LANCZOS)
                x = x0 + (cell - im.width) // 2
                y = y0 + (cell - im.height) // 2
                sheet.paste(im, (x, y))
                pasted = True
            except Exception:  # noqa: BLE001
                pasted = False
        if not pasted:
            # 占位格：不复制别的镜头，避免“同一画面出现两次”
            draw.rectangle((x0 + 6, y0 + 6, x0 + cell - 6, y0 + cell - 6),
                           fill=(233, 235, 241))
            draw.rectangle((x0 + 7, y0 + 7, x0 + cell - 7, y0 + cell - 7),
                           outline=(205, 209, 219), width=2)
            bbox = draw.textbbox((0, 0), empty_label, font=empty_font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text((x0 + (cell - tw) // 2 - bbox[0],
                       y0 + (cell - th) // 2 - bbox[1]),
                      empty_label, font=empty_font, fill=(150, 154, 165))
        # 编号压在所有格子左上角，占位格也保留格号
        draw.rectangle((x0 + 8, y0 + 8, x0 + 78, y0 + 42), fill=(0, 0, 0))
        draw.text((x0 + 18, y0 + 16),
                  labels[n] if labels and n < len(labels) else f"{n + 1:02d}",
                  fill=(255, 255, 255))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return str(out)
