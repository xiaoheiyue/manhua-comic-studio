# -*- coding: utf-8 -*-
"""信息图程序排版：T2I 只负责底图，节点/连线/中文标签在这里画。"""

from __future__ import annotations

import re
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from .bubble import load_font


def _hex_rgb(value: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return default
    try:
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return default


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for ch in text:
        trial = current + ch
        try:
            width = draw.textlength(trial, font=font)
        except Exception:  # noqa: BLE001
            width = len(trial) * 14
        if current and width > max_w:
            lines.append(current)
            current = ch
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def compose(background: Image.Image, plan: dict[str, Any], *,
            title: str, subtitle: str, accent: str = "#5b5f73") -> Image.Image:
    """把底图排成一张真正“语义成立”的信息图。"""
    size = background.size
    w, h = size
    base = ImageOps.fit(background.convert("RGB"), size, method=Image.LANCZOS)
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    accent_rgb = _hex_rgb(accent, (91, 95, 115))
    paper = (250, 247, 240, 244)
    ink = (30, 28, 26, 255)
    muted = (92, 88, 82, 255)
    line = (188, 178, 160, 220)

    # 整页纸张底板：把底图可能残留的字/结构盖住，只保留纸张纹理边缘。
    margin = max(28, int(w * 0.035))
    draw.rounded_rectangle((margin, margin, w - margin, h - margin),
                           radius=max(18, int(w * 0.018)),
                           fill=paper, outline=line, width=2)

    title_size = max(40, int(w * 0.064))
    sub_size = max(20, int(w * 0.026))
    label_size = max(26, int(w * 0.030))
    cap_size = max(18, int(w * 0.021))
    f_title = load_font(title_size, bold=True, kind="title")
    f_sub = load_font(sub_size, bold=False, kind="body")
    f_label = load_font(label_size, bold=True, kind="body")
    f_cap = load_font(cap_size, bold=False, kind="body")

    x0 = margin + int(w * 0.045)
    x1 = w - margin - int(w * 0.045)
    y = margin + int(h * 0.038)

    # 标题区
    draw.rectangle((x0, y, x0 + max(6, int(w * 0.009)), y + title_size + sub_size + 16),
                   fill=accent_rgb + (255,))
    tx = x0 + max(18, int(w * 0.025))
    title_lines = _wrap(draw, title, f_title, x1 - tx)[:2]
    ty = y
    for ln in title_lines:
        draw.text((tx, ty), ln, font=f_title, fill=ink)
        bb = draw.textbbox((tx, ty), ln, font=f_title)
        ty = bb[3] + int(title_size * 0.08)
    if subtitle:
        ty += int(sub_size * 0.18)
        for ln in _wrap(draw, subtitle, f_sub, x1 - tx)[:2]:
            draw.text((tx, ty + 2), ln, font=f_sub, fill=muted)
            bb = draw.textbbox((tx, ty + 2), ln, font=f_sub)
            ty = bb[3] + int(sub_size * 0.18)
    body_top = max(ty + int(h * 0.035), y + int(h * 0.115))
    draw.line((x0, body_top - 18, x1, body_top - 18), fill=line, width=2)

    nodes = list((plan or {}).get("nodes") or [])
    edges = list((plan or {}).get("edges") or [])
    key_points = list((plan or {}).get("key_points") or [])
    body_bottom = h - margin - int(h * 0.045)

    if nodes:
        n = max(1, min(6, len(nodes)))
        nodes = nodes[:n]
        node_labels = {str(node.get("id")): str(node.get("label") or "") for node in nodes}
        gap = max(14, int(h * 0.012))
        # 卡片不要拉满整页：3 个节点时旧算法每张卡高约 0.20h，中间大片空白。
        # 上限 0.18h，并从标题下方开始排；剩余空间留在页脚，避免出现
        # “标题和第一张卡之间空一大块”的断档。
        card_h = min(int((body_bottom - body_top - gap * (n - 1)) / n),
                     max(120, int(h * 0.18)))
        start_y = body_top
        spine_x = x0 + int(w * 0.045)
        card_x = spine_x + int(w * 0.055)
        # 连线先画在卡片下面
        first_cy = start_y + card_h // 2
        last_cy = start_y + (n - 1) * (card_h + gap) + card_h // 2
        draw.line((spine_x, first_cy, spine_x, last_cy),
                  fill=accent_rgb + (130,), width=max(3, int(w * 0.004)))
        for i, node in enumerate(nodes):
            cy = start_y + i * (card_h + gap) + card_h // 2
            top = cy - card_h // 2
            draw.rounded_rectangle(
                (card_x, top, x1, top + card_h), radius=max(12, int(w * 0.014)),
                fill=(255, 253, 248, 236), outline=line, width=2)
            r = max(15, int(w * 0.020))
            draw.ellipse((spine_x - r, cy - r, spine_x + r, cy + r),
                         fill=accent_rgb + (255,), outline=(255, 255, 255, 220), width=2)
            num = str(i + 1)
            nf = load_font(max(16, int(r * 0.95)), bold=True, kind="body")
            nb = draw.textbbox((0, 0), num, font=nf)
            draw.text((spine_x - (nb[2] - nb[0]) / 2,
                       cy - (nb[3] - nb[1]) / 2 - nb[1]), num, font=nf,
                      fill=(255, 255, 255, 255))
            # 卡片右侧放一个很淡的步骤大字：离线兜底没有 caption 时，
            # 卡片不会显得像空模板；有 caption 时也不会压住正文。
            ghost = f"{i + 1:02d}"
            gf = load_font(max(40, int(card_h * 0.60)), bold=True, kind="title")
            gb = draw.textbbox((0, 0), ghost, font=gf)
            draw.text((x1 - (gb[2] - gb[0]) - 26,
                       top + (card_h - (gb[3] - gb[1])) // 2 - gb[1]),
                      ghost, font=gf, fill=accent_rgb + (26,))
            label = str(node.get("label") or "")
            caption = str(node.get("caption") or "")
            draw.text((card_x + 20, top + 18), label, font=f_label, fill=ink)
            if caption:
                cap_y = top + 18 + int(label_size * 1.45)
                for ln in _wrap(draw, caption, f_cap, x1 - card_x - 40)[:2]:
                    draw.text((card_x + 20, cap_y), ln, font=f_cap, fill=muted)
                    cap_y += int(cap_size * 1.45)
            # 所有出边都写在源节点卡片底部，不依赖节点顺序，避免关系丢失。
            rels: list[str] = []
            for e in edges:
                if e.get("from") != node.get("id"):
                    continue
                target = node_labels.get(str(e.get("to")), "")
                if not target:
                    continue
                head = str(e.get("label") or "").strip()
                rels.append((head + " → " if head else "→ ") + target)
            if rels:
                max_rel = min(3, len(rels))
                rel_y = top + card_h - int(cap_size * 1.35) * max_rel - 12
                draw.line((card_x + 20, rel_y - 6, x1 - 20, rel_y - 6),
                          fill=line, width=1)
                for rel in rels[:max_rel]:
                    draw.text((card_x + 20, rel_y), rel, font=f_cap,
                              fill=accent_rgb + (245,))
                    rel_y += int(cap_size * 1.35)
    elif key_points:
        f_body = f_cap
        yy = body_top + 10
        for i, point in enumerate(key_points[:4]):
            draw.ellipse((x0, yy + 7, x0 + 9, yy + 16), fill=accent_rgb + (255,))
            for ln in _wrap(draw, str(point), f_body, x1 - x0 - 28)[:2]:
                draw.text((x0 + 22, yy), ln, font=f_body, fill=ink)
                yy += int(cap_size * 1.5)
            yy += 8

    out = Image.alpha_composite(base.convert("RGBA"), overlay)
    return out.convert("RGB")


def compose_social(image: Image.Image, *, title: str, subtitle: str,
                   key_points: list[str] | None = None,
                   accent: str = "#5b5f73") -> Image.Image:
    """社媒图文：保留插画，底部加干净的标题/要点面板。"""
    w, h = image.size
    base = image.convert("RGB")
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    accent_rgb = _hex_rgb(accent, (91, 95, 115))
    margin = max(26, int(w * 0.05))
    title_size = max(34, int(w * 0.062))
    sub_size = max(18, int(w * 0.026))
    point_size = max(17, int(w * 0.022))
    f_title = load_font(title_size, bold=True, kind="title")
    f_sub = load_font(sub_size, bold=False, kind="body")
    f_point = load_font(point_size, bold=False, kind="body")
    max_w = w - margin * 2 - 28

    title_lines = _wrap(draw, title, f_title, max_w)[:2] if title else []
    sub_lines = _wrap(draw, subtitle, f_sub, max_w)[:2] if subtitle else []
    points = [p for p in (key_points or []) if str(p).strip()][:3]
    point_lines: list[str] = []
    for p in points:
        point_lines.extend(_wrap(draw, str(p), f_point, max_w - 24)[:2])

    pad = max(18, int(w * 0.035))
    panel_h = pad * 2 + len(title_lines) * int(title_size * 1.45) \
        + len(sub_lines) * int(sub_size * 1.75) + len(point_lines) * int(point_size * 1.7) \
        + (12 if points else 0)
    panel_h = min(panel_h, int(h * 0.52))
    top = h - panel_h - margin
    draw.rounded_rectangle((margin, top, w - margin, h - margin),
                           radius=max(16, int(w * 0.022)),
                           fill=(250, 247, 240, 238),
                           outline=accent_rgb + (120,), width=2)
    draw.rectangle((margin, top, margin + max(5, int(w * 0.010)), h - margin),
                   fill=accent_rgb + (255,))
    y = top + pad
    for ln in title_lines:
        draw.text((margin + pad + 10, y), ln, font=f_title, fill=(30, 28, 26, 255))
        bb = draw.textbbox((margin + pad + 10, y), ln, font=f_title)
        y = bb[3] + int(title_size * 0.10)
    if sub_lines:
        y += int(sub_size * 0.16)
    for ln in sub_lines:
        draw.text((margin + pad + 10, y), ln, font=f_sub, fill=(86, 82, 76, 255))
        bb = draw.textbbox((margin + pad + 10, y), ln, font=f_sub)
        y = bb[3] + int(sub_size * 0.18)
    if points:
        y += 4
        for ln in point_lines:
            draw.ellipse((margin + pad + 10, y + 8,
                          margin + pad + 17, y + 15), fill=accent_rgb + (255,))
            draw.text((margin + pad + 25, y), ln, font=f_point, fill=(48, 45, 41, 255))
            y += int(point_size * 1.55)
    return Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
