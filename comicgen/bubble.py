# -*- coding: utf-8 -*-
"""对话气泡、旁白条与拟声词排版（快看条漫观感）。

设计目标：**像正式连载漫画的嵌字**，而不是"把字贴在图上"。

  · 气泡：白底 + 4px 近黑描边 + 轻投影，字号按画面宽度取，尾巴指向说话人；
  · 槽位：导演层给 top-left / mid-right 等槽位，渲染层做碰撞、避让与夹取；
  · 旁白：浅米白 caption 条 + 左侧红色竖条，压在画面左上，可读性远高于白字；
  · 拟声词：华文琥珀 + 黑/白双层描边 + 少量旋转，像手写音效字；
  · 长台词：自动按标点拆成多个气泡，绝不把一行字挤成"字墙"。

对外接口保持与旧版兼容：
    build_layout(shot, w, h, position, top_reserved) -> list[BubbleLayout]
    render_bubbles(base, layouts, shot) -> Image
    apply_bubbles(path, shot, output_path, layouts, position, enabled)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import config
from .logger import get_logger
from .models import Dialogue, Shot

log = get_logger(__name__)

_FONT_CACHE: dict[tuple[str | None, int], Any] = {}


# =============================================================================
# 字体
# =============================================================================

def _first_existing(paths: Iterable[str]) -> str | None:
    for p in paths:
        try:
            if p and Path(p).exists():
                return p
        except OSError:
            continue
    return None


def load_font(size: int, bold: bool = False, kind: str = "body"):
    """
    加载中文字体。

    kind: body（对白）/ bold（旁白、标题）/ sfx（拟声词）/ title（封面）。
    找不到候选时退回 PIL 内置字体，保证不崩。
    """
    size = max(8, int(size))
    if kind == "sfx":
        path = _first_existing(getattr(config, "FONT_SFX_CANDIDATES", []))
    elif kind == "title":
        path = _first_existing(getattr(config, "FONT_TITLE_CANDIDATES", []))
    elif bold:
        path = _first_existing(getattr(config, "FONT_BOLD_CANDIDATES", []))
    else:
        path = _first_existing(config.FONT_CANDIDATES)
    if path is None:
        path = config.get_font_path()
    key = (path, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    font = None
    if path:
        try:
            font = ImageFont.truetype(path, size)
        except Exception as exc:  # noqa: BLE001
            log.debug("字体加载失败 %s：%s", path, exc)
    if font is None:
        try:
            font = ImageFont.load_default(size=size)
        except Exception:  # noqa: BLE001
            font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


def measure(text: str, font) -> tuple[int, int]:
    """测量单行文字宽高。"""
    if not text:
        return (0, 0)
    try:
        box = font.getbbox(text)
        return (box[2] - box[0], box[3] - box[1])
    except Exception:  # noqa: BLE001
        try:
            return font.getsize(text)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return (len(text) * 10, 14)


def _line_height(size: int) -> int:
    return max(10, int(round(size * 1.34)))


# =============================================================================
# 文本换行（中英文混排，遵守中文避头尾）
# =============================================================================

_NO_LINE_START = "，。、；：？！）】》」』…—·,.;:?!)]}\"'"
_NO_LINE_END = "（【《「『([{\"'"


def wrap_text(text: str, font, max_width: int) -> list[str]:
    """
    按像素宽度换行，兼顾中文无空格与英文整词，处理避头尾。

    最后再做一次"断行平衡"：如果最后一行只剩 1~2 个字（比如"你。"），
    就收窄一点宽度重排，避免出现"孤字行"——这是嵌字最显业余的细节之一。
    """
    lines = _wrap_once(text, font, max_width)
    # 两行时尽量"对半"：6+3 这种一行挤满、一行只剩几个字的排版很业余
    if len(lines) == 2 and abs(len(lines[0]) - len(lines[1])) >= 2:
        best = lines
        best_diff = abs(len(lines[0]) - len(lines[1]))
        for factor in (0.94, 0.88, 0.82, 0.76, 0.70, 0.64):
            alt = _wrap_once(text, font, max(24, int(max_width * factor)))
            if len(alt) != 2:
                continue
            diff = abs(len(alt[0]) - len(alt[1]))
            if diff < best_diff:
                best, best_diff = alt, diff
        if best_diff < abs(len(lines[0]) - len(lines[1])):
            return best
    # 多行时只处理"孤字行"（最后一行 1~2 个字）
    if len(lines) >= 2 and len(lines[-1].strip()) <= 2:
        for factor in (0.9, 0.82, 0.74, 0.66):
            alt = _wrap_once(text, font, max(24, int(max_width * factor)))
            if len(alt) == len(lines) and len(alt[-1].strip()) >= 3:
                return alt
    return lines


def _wrap_once(text: str, font, max_width: int) -> list[str]:
    """实际的按宽度换行（不含平衡处理）。"""
    text = (text or "").replace("\r", "").strip()
    if not text:
        return []

    tokens: list[str] = []
    buf = ""
    for ch in text:
        if ch == "\n":
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append("\n")
            continue
        if _is_ascii_word_char(ch):
            buf += ch
        else:
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
    if buf:
        tokens.append(buf)

    lines: list[str] = []
    cur = ""
    for tok in tokens:
        if tok == "\n":
            lines.append(cur)
            cur = ""
            continue
        candidate = cur + tok
        if measure(candidate, font)[0] <= max_width or not cur:
            cur = candidate
            continue
        if tok in _NO_LINE_START and cur:
            # 标点悬挂：允许略微超出，避免标点掉到下一行行首
            cur = candidate
            lines.append(cur)
            cur = ""
            continue
        if cur and cur[-1] in _NO_LINE_END:
            moved = cur[-1]
            cur = cur[:-1]
            lines.append(cur)
            cur = moved + tok
            continue
        lines.append(cur)
        cur = tok
    if cur:
        lines.append(cur)

    return [ln for ln in lines if ln != ""] or [text]


def _is_ascii_word_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in "'’-")


# =============================================================================
# 布局数据
# =============================================================================

@dataclass
class BubbleLayout:
    """单个气泡的布局（相对画面的比例，便于缩放与重绘）。"""

    text: str = ""
    speaker: str = ""
    style: str = "圆角矩形"
    kind: str = "speech"
    x: float = 0.30          # 左边缘
    y: float = 0.70          # 上边缘
    w: float = 0.40          # 宽度
    h: float = 0.12          # 高度
    visible: bool = True
    slot: str = ""           # top-left / top-right / mid-left …
    tail: str = "auto"       # down-left / down-right / up / none
    font_size: int = 0       # 0 = 自动
    tail_tip: tuple[float, float] = (0.5, 0.0)  # 尾巴尖端相对气泡框的位置（可选）
    drawn_in_art: bool = False  # 气泡框已由图像模型画好，只需填字
    overflow: bool = False      # 文字在最小字号下仍放不进气泡（自检用）

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text, "speaker": self.speaker, "style": self.style,
            "kind": self.kind, "x": round(self.x, 4), "y": round(self.y, 4),
            "w": round(self.w, 4), "h": round(self.h, 4),
            "visible": self.visible, "slot": self.slot, "tail": self.tail,
            "font_size": self.font_size,
            "tail_tip": [round(self.tail_tip[0], 4), round(self.tail_tip[1], 4)],
            "drawn_in_art": self.drawn_in_art,
            "overflow": self.overflow,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BubbleLayout":
        data = dict(data or {})
        tip = data.get("tail_tip")
        if isinstance(tip, (list, tuple)) and len(tip) == 2:
            try:
                data["tail_tip"] = (float(tip[0]), float(tip[1]))
            except (TypeError, ValueError):
                data.pop("tail_tip", None)
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


# =============================================================================
# 基础绘制工具
# =============================================================================

def _rgba(color: Sequence[int], alpha_scale: float = 1.0) -> tuple[int, int, int, int]:
    if len(color) == 3:
        return (color[0], color[1], color[2], int(255 * alpha_scale))
    return (color[0], color[1], color[2], int(color[3] * alpha_scale))


def _blur_shadow(layer: Image.Image, radius: int) -> Image.Image:
    return layer.filter(ImageFilter.GaussianBlur(max(0, int(radius))))


def _draw_bubble_shape(draw: ImageDraw.ImageDraw, style: str,
                       box: tuple[int, int, int, int],
                       fill, outline, width: int) -> None:
    x0, y0, x1, y1 = box
    w = max(2, x1 - x0)
    h = max(2, y1 - y0)
    if style == "椭圆":
        draw.ellipse(box, fill=fill, outline=outline, width=width)
    elif style == "方形":
        draw.rectangle(box, fill=fill, outline=outline, width=width)
    elif style == "云朵":
        _draw_cloud(draw, box, fill, outline, width)
    elif style == "爆炸":
        _draw_explosion(draw, box, fill, outline, width)
    elif style == "旁白框":
        draw.rectangle(box, fill=fill, outline=outline, width=width)
        draw.line((x0, y0 + 14, x0 + 14, y0), fill=outline, width=max(2, width - 1))
    else:
        radius = min(int(config.BUBBLE_CORNER_RADIUS), h // 2, w // 2)
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline,
                               width=width)


def _draw_cloud(draw, box, fill, outline, width: int) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    bumps = max(8, int(w / 26))
    for i in range(bumps):
        t = i / max(1, bumps - 1)
        cx = x0 + t * w
        r = h * (0.30 + 0.10 * math.sin(t * math.pi))
        _bump(draw, cx, y0 + r * 0.55, r, fill, outline, width)
        _bump(draw, cx, y1 - r * 0.55, r, fill, outline, width)
    side_bumps = max(2, int(h / 30))
    for i in range(side_bumps):
        t = (i + 0.5) / side_bumps
        cy = y0 + t * h
        r = h * 0.28
        _bump(draw, x0 + r * 0.55, cy, r, fill, outline, width)
        _bump(draw, x1 - r * 0.55, cy, r, fill, outline, width)
    draw.rounded_rectangle((x0 + w * 0.08, y0 + h * 0.15, x1 - w * 0.08, y1 - h * 0.15),
                           radius=int(min(h, w) * 0.22), fill=fill, outline=None)


def _bump(draw, cx: float, cy: float, r: float, fill, outline, width: int) -> None:
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill, outline=outline,
                 width=max(1, width - 1))


def _draw_explosion(draw, box, fill, outline, width: int) -> None:
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
    spikes = 18
    pts: list[tuple[float, float]] = []
    for i in range(spikes * 2):
        ang = math.pi * i / spikes
        k = 1.0 if i % 2 == 0 else 0.64
        pts.append((cx + math.cos(ang) * rx * k, cy + math.sin(ang) * ry * k))
    draw.polygon(pts, fill=fill, outline=outline)
    if width > 0:
        draw.line(pts + [pts[0]], fill=outline, width=width, joint="curve")


def _tail_polygon(box: tuple[int, int, int, int], tail: str,
                  tail_width: int, length: int) -> list[tuple[float, float]] | None:
    """根据尾巴方向生成三角形顶点（尖端 + 两个根部点）。"""
    if not tail or tail == "none":
        return None
    x0, y0, x1, y1 = box
    bw = x1 - x0
    bh = y1 - y0
    half = max(9, int(tail_width) // 2)
    horizontal = tail.split("-")[-1] if "-" in tail else ""
    if tail.startswith("down"):
        if horizontal == "left":
            base = x0 + bw * 0.36
            tip = (base - bw * 0.22, y1 + length)
        elif horizontal == "right":
            base = x0 + bw * 0.64
            tip = (base + bw * 0.22, y1 + length)
        else:
            base = x0 + bw * 0.50
            tip = (base, y1 + length)
        return [(base - half, y1 - 2), (base + half, y1 - 2), tip]
    if tail.startswith("up"):
        if horizontal == "left":
            base = x0 + bw * 0.36
            tip = (base - bw * 0.22, y0 - length)
        elif horizontal == "right":
            base = x0 + bw * 0.64
            tip = (base + bw * 0.22, y0 - length)
        else:
            base = x0 + bw * 0.50
            tip = (base, y0 - length)
        return [(base - half, y0 + 2), (base + half, y0 + 2), tip]
    return None


def _draw_box_text(draw: ImageDraw.ImageDraw, b: BubbleLayout,
                   box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    font_size = b.font_size or _font_size(x1 - x0, b.text)
    font = load_font(font_size)
    pad_x = int(config.BUBBLE_PADDING_X * (0.7 if b.drawn_in_art else 1.0))
    pad_y = int(config.BUBBLE_PADDING_Y * (0.6 if b.drawn_in_art else 1.0))
    inner_w = max(20, (x1 - x0) - pad_x * 2)
    text = f"{b.speaker}：{b.text}" if (config.BUBBLE_SHOW_SPEAKER and b.speaker) else b.text
    lines = wrap_text(text, font, inner_w)
    line_h = _line_height(font_size)
    spacing = int(font_size * config.BUBBLE_LINE_SPACING_RATIO)
    total_h = len(lines) * line_h + max(0, len(lines) - 1) * spacing
    available = max(20, (y1 - y0) - pad_y * 2)
    # 最后一道保险：无论如何不能让文字溢出气泡框
    min_size = 18 if b.drawn_in_art else config.BUBBLE_FONT_MIN
    while total_h > available and font_size > min_size:
        font_size -= 2
        font = load_font(font_size)
        lines = wrap_text(text, font, inner_w)
        line_h = _line_height(font_size)
        spacing = int(font_size * config.BUBBLE_LINE_SPACING_RATIO)
        total_h = len(lines) * line_h + max(0, len(lines) - 1) * spacing
    b.overflow = total_h > available + 2
    ty = y0 + ((y1 - y0) - total_h) // 2 + 2
    # 只填字时给字加一圈很细的白晕：字在气泡里时看不见，万一贴边/压到
    # 背景也能保持可读。这不是气泡框，只是文字的描边。
    extra = {"stroke_width": 2, "stroke_fill": (255, 255, 255, 235)} \
        if b.drawn_in_art else {}
    for line in lines:
        lw = measure(line, font)[0]
        tx = x0 + ((x1 - x0) - lw) // 2
        draw.text((tx, ty), line, font=font, fill=_rgba(config.BUBBLE_TEXT_COLOR),
                  **extra)
        ty += line_h + spacing


def _render_one(draw: ImageDraw.ImageDraw, b: BubbleLayout, W: int, H: int,
                shadow_draw: ImageDraw.ImageDraw | None = None) -> None:
    box = (int(b.x * W), int(b.y * H),
           int(b.x * W) + max(26, int(b.w * W)),
           int(b.y * H) + max(22, int(b.h * H)))
    x0, y0, x1, y1 = box

    # 气泡框是图像模型画好的：只填文字，不再补白底/描边/尾巴。
    # 模型已经画了白底黑边的气泡，再叠一层背景和边框属于重复劳动，
    # 还会盖掉原气泡的形状和尾巴。
    if b.drawn_in_art:
        _draw_box_text(draw, b, box)
        return

    fill = _rgba(config.BUBBLE_FILL)
    outline = _rgba(config.BUBBLE_OUTLINE)
    width = int(config.BUBBLE_OUTLINE_WIDTH)
    tail = b.tail or "none"

    # 尾巴先画，气泡本体后画，接缝被本体盖住
    tri = _tail_polygon(box, tail, int(max(16, (x1 - x0) * config.BUBBLE_TAIL_WIDTH_RATIO)),
                        int(config.BUBBLE_TAIL_LENGTH))
    if tri:
        if shadow_draw is not None:
            off = int(config.BUBBLE_SHADOW_OFFSET)
            shadow_draw.polygon([(px + off, py + off) for px, py in tri],
                                fill=(0, 0, 0, int(config.BUBBLE_SHADOW_ALPHA)))
        draw.polygon(tri, fill=fill, outline=outline)
        if width > 0:
            draw.line([tri[0], tri[2]], fill=outline, width=width)
            draw.line([tri[2], tri[1]], fill=outline, width=width)

    if shadow_draw is not None:
        off = int(config.BUBBLE_SHADOW_OFFSET)
        _draw_bubble_shape(shadow_draw, b.style,
                           (x0 + off, y0 + off, x1 + off, y1 + off),
                           (0, 0, 0, int(config.BUBBLE_SHADOW_ALPHA)),
                           (0, 0, 0, 0), 0)
    _draw_bubble_shape(draw, b.style, box, fill, outline, width)

    _draw_box_text(draw, b, box)


# =============================================================================
# 旁白排版
# =============================================================================

def split_plain_narration(shot: Shot) -> tuple[list[Dialogue], list[Dialogue]]:
    """
    把分镜里的文字分成两组：(走旁白通道, 走气泡通道)。

    caption / plain 两种旁白样式都走旁白通道；只有用户显式指定了别的气泡样式
    （例如"云朵"）时才走气泡。
    """
    plain: list[Dialogue] = []
    boxed: list[Dialogue] = []
    for d in shot.dialogues:
        if not d.text.strip():
            continue
        explicit = (d.bubble_style or "").strip()
        if d.kind == "narration" and explicit in ("", "旁白框"):
            plain.append(d)
        else:
            boxed.append(d)
    return plain, boxed


def _panel_text_factor(shot: Shot) -> float:
    """
    分格页里的文字整体收一档。

    一页 3~4 格时，每格只有页面宽度的 1/2~1/3；如果仍按整幅条漫的字号/气泡宽度，
    文字会占掉半格画面。这里统一乘一个系数（提示词预留、旁白、气泡共用）。
    """
    return 0.92 if getattr(shot, "panel_orientation", "") else 1.0


def _narration_font(width: int, factor: float = 1.0):
    size = int(width * config.NARRATION_FONT_RATIO * factor)
    size = max(config.NARRATION_FONT_MIN, min(config.NARRATION_FONT_MAX, size))
    return load_font(size, bold=True), size


def _narration_style() -> str:
    style = str(getattr(config, "NARRATION_STYLE", "caption") or "caption").lower()
    if style not in ("caption", "plain"):
        style = "caption" if not getattr(config, "NARRATION_PLAIN", False) else "plain"
    return style


def layout_narration(shot: Shot, width: int, height: int) -> list[dict[str, Any]]:
    """
    计算旁白的位置、字号、换行与框体（caption 模式）。

    多条旁白会合并成**一条**：分格页里一格只有页面的一部分，堆两条旁白会把
    画面顶部整块盖住。合并后按 NARRATION_MAX_CHARS 截断，保证文字只是"说明"。
    """
    plain, _ = split_plain_narration(shot)
    if not plain or width <= 0 or height <= 0:
        return []
    texts = [d.text.strip() for d in plain if d.text.strip()]
    if not texts:
        return []
    text = "；".join(texts)
    limit = max(16, int(getattr(config, "NARRATION_MAX_CHARS", 56) or 56))
    if len(text) > limit:
        cut = text[:limit]
        pos = max((cut.rfind(p) for p in "，、；：。！？"), default=-1)
        text = (cut[:pos] if pos >= 12 else cut).rstrip("，、；：。") + "…"
    factor = _panel_text_factor(shot)
    font, size = _narration_font(width, factor)
    max_w = int(width * config.NARRATION_MAX_WIDTH_RATIO * factor)
    pad_x = int(getattr(config, "NARRATION_PADDING_X", 26))
    pad_y = int(getattr(config, "NARRATION_PADDING_Y", 18))
    line_h = _line_height(size)
    spacing = int(size * config.NARRATION_LINE_SPACING_RATIO)
    style = _narration_style()
    x = int(width * 0.055)
    y = int(height * config.NARRATION_TOP_RATIO)
    accent_w = int(getattr(config, "NARRATION_ACCENT_WIDTH", 7))
    inner_max = max(40, max_w - pad_x * 2 - accent_w)
    lines = wrap_text(text, font, inner_max)
    if not lines:
        return []
    text_w = max((measure(ln, font)[0] for ln in lines), default=size * 4)
    bw = min(max_w, text_w + pad_x * 2 + accent_w)
    bh = len(lines) * line_h + max(0, len(lines) - 1) * spacing + pad_y * 2
    return [{
        "text": text, "lines": lines, "font": font, "size": size,
        "line_h": line_h, "spacing": spacing, "style": style,
        "x": x, "y": y, "w": int(bw), "h": int(bh),
        "pad_x": pad_x, "pad_y": pad_y,
    }]


def narration_bottom_ratio(shot: Shot, width: int, height: int) -> float:
    """旁白占到的底部位置（占画面高度比例），用于让气泡避开。"""
    entries = layout_narration(shot, width, height)
    if not entries:
        return 0.0
    last = entries[-1]
    bottom = last["y"] + last["h"]
    return max(0.0, min(0.7, bottom / float(max(1, height))))


def render_narration(img: Image.Image, shot: Shot) -> Image.Image:
    """把旁白画到画面上：caption 条（默认）或白字投影（plain）。"""
    entries = layout_narration(shot, img.width, img.height)
    if not entries:
        return img
    base = img.convert("RGBA")
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for e in entries:
        if e["style"] == "plain":
            _draw_plain_narration(draw, e, img.width)
            continue
        _draw_caption(draw, e)
    return Image.alpha_composite(base, layer).convert("RGB")


def _draw_plain_narration(draw: ImageDraw.ImageDraw, e: dict, width: int) -> None:
    font, size = e["font"], e["size"]
    off = max(1, int(size * config.NARRATION_SHADOW_OFFSET_RATIO))
    text_fill = _rgba(config.NARRATION_TEXT_COLOR)
    shadow_fill = _rgba(config.NARRATION_SHADOW_COLOR)
    y = int(e["y"])
    for line in e["lines"]:
        lw = measure(line, font)[0]
        x = (width - lw) // 2
        for dx in (-off, 0, off):
            for dy in (-off, 0, off):
                if dx or dy:
                    draw.text((x + dx, y + dy), line, font=font, fill=shadow_fill)
        draw.text((x, y), line, font=font, fill=text_fill)
        y += e["line_h"] + e["spacing"]


def _draw_caption(draw: ImageDraw.ImageDraw, e: dict) -> None:
    x, y, w, h = e["x"], e["y"], e["w"], e["h"]
    fill = _rgba(config.NARRATION_FILL)
    outline = _rgba(config.NARRATION_OUTLINE)
    accent = _rgba(config.NARRATION_ACCENT)
    radius = int(getattr(config, "NARRATION_CORNER_RADIUS", 10))
    # 投影
    shadow = (x + 4, y + 5, x + w + 4, y + h + 5)
    draw.rounded_rectangle(shadow, radius=radius, fill=(0, 0, 0, 55))
    draw.rounded_rectangle((x, y, x + w, y + h), radius=radius, fill=fill,
                           outline=outline, width=3)
    # 左侧红色竖条
    aw = int(getattr(config, "NARRATION_ACCENT_WIDTH", 7))
    draw.rounded_rectangle((x + 4, y + 4, x + 4 + aw, y + h - 4),
                           radius=max(2, aw // 2), fill=accent)
    # 文本
    font = e["font"]
    tx = x + e["pad_x"] + aw
    ty = y + e["pad_y"]
    for line in e["lines"]:
        draw.text((tx, ty), line, font=font, fill=_rgba(config.NARRATION_TEXT_COLOR))
        ty += e["line_h"] + e["spacing"]


# =============================================================================
# 对话拆分与字号
# =============================================================================

def _split_long_dialogue(text: str) -> list[str]:
    """长对话按标点拆成多个气泡，尽量保持每块长度均衡。"""
    text = (text or "").strip()
    limit = int(config.BUBBLE_TEXT_SPLIT_THRESHOLD)
    if len(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in "。！？!?…；;" and len(buf) >= max(8, limit * 0.6):
            parts.append(buf.strip())
            buf = ""
    if buf.strip():
        parts.append(buf.strip())
    # 再按逗号/顿号细切仍过长的块
    out: list[str] = []
    for p in parts:
        if len(p) <= int(limit * 1.7):
            out.append(p)
            continue
        chunks = re.split(r"(?<=[，,、：:])", p)
        cur = ""
        for c in chunks:
            if len(cur) + len(c) <= limit and cur:
                cur += c
            elif len(cur) + len(c) <= limit:
                cur = c
            else:
                if cur:
                    out.append(cur.strip())
                cur = c
        if cur.strip():
            out.append(cur.strip())
    # 最后仍超长的硬切（极少见）
    final: list[str] = []
    for p in out:
        if len(p) <= int(limit * 2):
            final.append(p)
        else:
            for i in range(0, len(p), int(limit * 1.5)):
                final.append(p[i:i + int(limit * 1.5)])
    return final or [text]


def _font_size(width: int, text: str = "") -> int:
    """字号按画面宽度取；短句放大、长句缩小，保证一屏可读。"""
    base = int(width * config.BUBBLE_FONT_RATIO)
    n = len(text or "")
    if n <= 6:
        base = int(base * 1.12)
    elif n >= 20:
        base = int(base * 0.92)
    return max(config.BUBBLE_FONT_MIN, min(config.BUBBLE_FONT_MAX, base))


def _bubble_size(text: str, width: int, height: int, crowded: bool,
                 font_size: int = 0, factor: float = 1.0) -> tuple[int, int, int, list[str]]:
    """返回 (w, h, font_size, lines)。超过画面高度时自动降字号。"""
    ratio = (config.BUBBLE_MAX_WIDTH_RATIO_CROWDED if crowded
             else config.BUBBLE_MAX_WIDTH_RATIO) * factor
    max_w = int(width * ratio)
    size = font_size or max(config.BUBBLE_FONT_MIN,
                            int(_font_size(width, text) * factor))
    while True:
        font = load_font(size)
        inner = max(20, max_w - config.BUBBLE_PADDING_X * 2)
        lines = wrap_text(text, font, inner)
        line_w = max((measure(ln, font)[0] for ln in lines), default=size * 4)
        bw = min(max_w, line_w + config.BUBBLE_PADDING_X * 2)
        line_h = _line_height(size)
        spacing = int(size * config.BUBBLE_LINE_SPACING_RATIO)
        bh = max(config.BUBBLE_MIN_HEIGHT,
                 len(lines) * line_h + max(0, len(lines) - 1) * spacing
                 + config.BUBBLE_PADDING_Y * 2)
        if bh <= height * 0.34 or size <= config.BUBBLE_FONT_MIN:
            return bw, bh, size, lines
        size = max(config.BUBBLE_FONT_MIN, int(size * 0.9))


# =============================================================================
# 自动布局
# =============================================================================

_SLOT_ORDER = {
    "top-left": 0, "top-center": 1, "top-right": 2,
    "mid-left": 3, "mid-right": 4,
    "bottom-left": 5, "bottom-right": 6,
}

# 槽位先验：优先上/中部，底部作为兜底（避免一上来就压住人物下半身）
_SLOT_PRIOR = {
    "top-left": 0.00, "top-center": 0.02, "top-right": 0.00,
    "mid-left": 0.10, "mid-right": 0.10,
    "bottom-left": 0.24, "bottom-right": 0.24,
}


def _detail_grid(img: Image.Image, cols: int = 40) -> dict[str, Any] | None:
    """
    计算画面的"细节密度"网格（0~1）。

    漫画嵌字要避开脸和复杂纹理，否则字就读不清。这里用边缘强度近似：
    边缘越密（五官、发丝、机柜、窗格），越不适合放气泡；墙面、天空、
    纯色区域得分低，优先放字。
    """
    try:
        import numpy as np  # 项目依赖里已有 numpy
    except Exception:  # noqa: BLE001
        return None
    try:
        small_w = max(16, int(cols))
        ratio = img.height / max(1, img.width)
        small_h = max(16, int(small_w * ratio))
        gray = img.convert("L").resize((small_w, small_h), Image.BILINEAR)
        edges = gray.filter(ImageFilter.FIND_EDGES)
        arr = np.asarray(edges, dtype="float32")
        # 忽略边框一圈（边框本身是高频）
        if arr.shape[0] > 4 and arr.shape[1] > 4:
            arr = arr[2:-2, 2:-2]
        if arr.size == 0:
            return None
        p95 = float(np.percentile(arr, 95)) or 1.0
        arr = np.clip(arr / p95, 0.0, 1.0)
        # 轻度模糊，避免单像素噪声决定位置
        kernel = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], dtype="float32") / 16.0
        pad = np.pad(arr, 1, mode="edge")
        blur = sum(kernel[i, j] * pad[i:i + arr.shape[0], j:j + arr.shape[1]]
                   for i in range(3) for j in range(3))
        return {"grid": blur, "rows": blur.shape[0], "cols": blur.shape[1]}
    except Exception as exc:  # noqa: BLE001
        log.debug("细节网格计算失败：%s", exc)
        return None


def _skin_grid(img: Image.Image, cols: int = 40) -> dict[str, Any] | None:
    """
    粗略的"人脸/皮肤"热区。

    气泡盖住眼睛和嘴是最典型的业余感来源。这里不追求精确的人脸检测，
    只用 YCbCr 肤色阈值做一张低分辨率热区图：命中皮肤的区域在排版时会被
    明显扣分，气泡就会优先落到墙面/天空/纯色背景上。
    """
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return None
    try:
        small_w = max(16, int(cols))
        ratio = img.height / max(1, img.width)
        small_h = max(16, int(small_w * ratio))
        small = img.convert("YCbCr").resize((small_w, small_h), Image.BILINEAR)
        arr = np.asarray(small, dtype="float32")
        y, cb, cr = arr[..., 0], arr[..., 1], arr[..., 2]
        mask = ((cb >= 76) & (cb <= 130) & (cr >= 132) & (cr <= 178)
                & (y >= 45)).astype("float32")
        if mask.mean() > 0.45:
            # 整张图偏暖（夕阳/室内橙光）时阈值会全命中，放弃这一层
            return None
        # 3x3 均值，避免单像素噪点
        kernel = np.ones((3, 3), dtype="float32") / 9.0
        pad = np.pad(mask, 1, mode="edge")
        blur = sum(kernel[i, j] * pad[i:i + mask.shape[0], j:j + mask.shape[1]]
                   for i in range(3) for j in range(3))
        return {"grid": blur, "rows": blur.shape[0], "cols": blur.shape[1]}
    except Exception as exc:  # noqa: BLE001
        log.debug("肤色网格计算失败：%s", exc)
        return None


def _rect_score(grid: dict[str, Any] | None,
                rect: tuple[float, float, float, float]) -> float:
    if not grid:
        return 0.0
    g = grid["grid"]
    rows, cols = grid["rows"], grid["cols"]
    x0 = max(0, min(cols - 1, int(rect[0] * cols)))
    x1 = max(x0 + 1, min(cols, int(rect[2] * cols)))
    y0 = max(0, min(rows - 1, int(rect[1] * rows)))
    y1 = max(y0 + 1, min(rows, int(rect[3] * rows)))
    patch = g[y0:y1, x0:x1]
    if patch.size == 0:
        return 0.0
    return float(patch.mean())


def _rect_detail(detail: dict[str, Any] | None,
                 rect: tuple[float, float, float, float]) -> float:
    """取矩形区域的平均细节密度（rect 为画面归一化坐标）。"""
    return _rect_score(detail, rect)


def _default_slot(index: int, total: int, speaker_side: str = "") -> str:
    if total <= 1:
        return "top-center"
    band = ("top", "mid", "bottom")[min(index, 2)]
    if speaker_side == "左":
        return f"{band}-left"
    if speaker_side == "右":
        return f"{band}-right"
    return ["top-left", "top-right", "bottom-left", "bottom-right"][index % 4]


# ---------------------------------------------------------------------------
# 气泡尾巴：朝向"说话人的脸"，而不是朝向固定的右下方
# ---------------------------------------------------------------------------
# 旧实现按槽位写死（mid-left → down-right），于是站在左边的人说话，
# 尾巴却指向右边的听者，读者会以为台词是听者说的（rules/00 证据 F）。

_SLOT_CENTER = {
    "top-left": (0.28, 0.14), "top-center": (0.50, 0.14), "top-right": (0.72, 0.14),
    "mid-left": (0.28, 0.40), "mid-right": (0.72, 0.40),
    "bottom-left": (0.28, 0.78), "bottom-right": (0.72, 0.78),
}


def speaker_anchor(shot: "Shot", speaker: str = "") -> tuple[float, float]:
    """
    估算说话人脸部在画面里的归一化坐标。

    只用"站位 + 景别"两个信息（rules/data/storyboard_rules.json 的 bubble 段），
    因为程序看不到模型最终把脸画在哪——但同一景别下人物的脸高度是稳定的。
    """
    try:
        from comicgen import rules as rules_mod
        br = rules_mod.bubble_rules()
    except Exception:  # noqa: BLE001
        br = {}
    x_by_side = br.get("anchor_x_by_side") or {"左": 0.28, "右": 0.72, "中": 0.50}
    y_by_type = br.get("anchor_y_by_type") or {"特写": 0.30, "近景": 0.28,
                                               "中景": 0.30, "全景": 0.32, "远景": 0.34}
    side = ""
    try:
        side = (shot.char_sides or {}).get(speaker, "")
    except Exception:  # noqa: BLE001
        side = ""
    x = float(x_by_side.get(side, 0.50))
    y = float(y_by_type.get(getattr(shot, "shot_type", "") or "", 0.30))
    return x, y


def tail_toward_speaker(shot: "Shot", slot: str, speaker: str = "",
                        box: tuple[float, float, float, float] | None = None) -> str:
    """
    计算气泡尾巴方向：从气泡中心指向说话人脸部锚点。

    box 为归一化的 (x, y, w, h)；不传则按槽位估算气泡中心。
    """
    if slot not in _SLOT_CENTER and not box:
        return ""
    if box:
        bx, by, bw, bh = box
        cx, cy = bx + bw / 2, by + bh / 2
    else:
        cx, cy = _SLOT_CENTER[slot]
    ax, ay = speaker_anchor(shot, speaker)
    dy = ay - cy
    dx = ax - cx
    vert = "down" if dy >= 0 else "up"
    if dx < -0.08:
        return f"{vert}-left"
    if dx > 0.08:
        return f"{vert}-right"
    return vert


def _slot_position(slot: str, bw: int, bh: int, W: int, H: int,
                   margin: int, top: int, stack: int) -> tuple[int, int]:
    if slot == "top-left":
        x, y = margin, top + stack
    elif slot == "top-right":
        x, y = W - margin - bw, top + stack
    elif slot == "top-center":
        x, y = (W - bw) // 2, top + stack
    elif slot == "mid-left":
        x, y = margin, int(H * 0.34) + stack
    elif slot == "mid-right":
        x, y = W - margin - bw, int(H * 0.34) + stack
    elif slot == "bottom-left":
        x, y = margin, H - margin - bh - stack
    elif slot == "bottom-right":
        x, y = W - margin - bw, H - margin - bh - stack
    else:
        x, y = (W - bw) // 2, top + stack
    return int(x), int(y)


def _rects_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int],
                   pad: int = 6) -> bool:
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0]
                or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def _fit_font_in_box(text: str, max_w: int, max_h: int, base_size: int) -> int:
    """在给定文本框里找一个能放下的字号（允许降到 16px，避免长句溢出）。"""
    size = max(16, int(base_size))
    while size > 16:
        font = load_font(size)
        lines = wrap_text(text, font, max(20, max_w))
        line_h = _line_height(size)
        spacing = int(size * config.BUBBLE_LINE_SPACING_RATIO)
        total = len(lines) * line_h + max(0, len(lines) - 1) * spacing
        if total <= max_h:
            return size
        size -= 2
    return 16


def plan_layout(shot: Shot, width: int, height: int) -> list[dict]:
    """
    出图前的"空白气泡"预留计划。

    这一步发生在生成画面**之前**：按台词长度算出每个气泡的框，告诉图像模型
    在这些位置画空白气泡；画面会围绕气泡构图，后期只往框里填字，就不会出现
    "事后贴气泡把脸盖住"的问题。

    返回可直接存进 shot.bubble_plan 的字典列表。
    """
    _, items = split_plain_narration(shot)
    items = [d for d in items if d.kind != "sfx" and (d.text or "").strip()]
    if not items:
        return []

    entries: list[tuple[Dialogue, str]] = []
    for d in items:
        for piece in _split_long_dialogue(d.text):
            entries.append((d, piece))
    if not entries:
        return []

    crowded = len(entries) >= int(config.BUBBLE_CROWDED_THRESHOLD)
    factor = _panel_text_factor(shot)
    margin = int(config.BUBBLE_MARGIN)
    # 有旁白时，气泡要给顶部旁白条让位
    reserved = layout_narration(shot, width, height)
    top = int(height * float(getattr(config, "BUBBLE_PLAN_TOP_RATIO", 0.06)))
    if reserved:
        top = max(top, reserved[-1]["y"] + reserved[-1]["h"]) + int(config.BUBBLE_GAP)
    top += margin // 2

    max_w_ratio = float(getattr(config, "BUBBLE_PLAN_MAX_WIDTH_RATIO", 0.50)) * factor
    max_h = int(height * float(getattr(config, "BUBBLE_PLAN_MAX_HEIGHT_RATIO", 0.22)) * factor)
    plan: list[dict] = []
    stack_by_slot: dict[str, int] = {}
    occupied: list[tuple[int, int, int, int]] = []

    for i, (d, piece) in enumerate(entries):
        bw, bh, size, _lines = _bubble_size(piece, width, height, crowded, factor=factor)
        bw = min(int(width * max_w_ratio), max(180, bw))
        min_h = int(height * float(getattr(config, "BUBBLE_PLAN_MIN_HEIGHT_RATIO", 0.12)))
        bh = min(max_h, max(min_h, config.BUBBLE_MIN_HEIGHT, bh))
        slot = (d.slot or "").strip()
        if slot not in config.BUBBLE_SLOTS or d.kind == "narration":
            side = shot.char_sides.get(d.speaker, "")
            slot = _default_slot(i, len(entries), side)
        # 近景/特写里人物脸占掉大半画面：气泡强制放顶部，避免"右边没地方
        # 就画到左边"，导致气泡落到不说话的人那一侧。
        if getattr(shot, "shot_type", "") in ("近景", "特写") \
                and not str(slot).startswith("top"):
            side = shot.char_sides.get(d.speaker, "")
            if len(shot.characters) >= 2:
                slot = "top-center"
            elif side == "左":
                slot = "top-left"
            elif side == "右":
                slot = "top-right"
            else:
                slot = "top-center"
        programmatic = (not bool(getattr(config, "IMAGE_DRAW_BUBBLES", False))
                        or bool(getattr(shot, "bubble_programmatic", False)))
        # 程序画气泡时只用顶部两列：提示词里预留的就是这一带，
        # 气泡跑到 mid/bottom 会和"留白区"对不上，也更容易压到人物。
        if programmatic and d.kind != "narration":
            side = shot.char_sides.get(d.speaker, "")
            if side == "左":
                slot = "top-left"
            elif side == "右":
                slot = "top-right"
            elif slot.endswith("right"):
                slot = "top-right"
            else:
                slot = "top-left"

        candidates = [slot] + [s for s in config.BUBBLE_SLOTS if s != slot]
        if programmatic:
            # 程序画气泡：只在顶部两列里找位置，避免跑到预留区之外
            candidates = [slot] + [s for s in config.BUBBLE_SLOTS
                                   if s != slot and s.startswith("top")]
        chosen = None
        for cand in candidates:
            stack = stack_by_slot.get(cand, 0)
            x, y = _slot_position(cand, bw, bh, width, height, margin, top, stack)
            rect = (x, y, x + bw, y + bh)
            if y + bh > height - margin // 2:
                continue
            if any(_rects_overlap(rect, o, pad=10) for o in occupied):
                continue
            chosen = (cand, x, y, rect)
            break
        if chosen is None:
            cand = slot
            x = margin
            y = min(height - bh - margin, top + sum(p["h"] for p in plan) + config.BUBBLE_PLAN_GAP * len(plan))
            chosen = (cand, x, y, (x, y, x + bw, y + bh))
        cand, x, y, rect = chosen
        occupied.append(rect)
        stack_by_slot[cand] = stack_by_slot.get(cand, 0) + bh + int(config.BUBBLE_PLAN_GAP)
        tail = config.BUBBLE_SLOT_TO_TAIL.get(cand, "down")
        if d.kind in ("narration", "sfx") or d.offscreen:
            tail = "none"
        elif d.speaker in (getattr(shot, "characters", None) or []):
            # 尾巴朝向说话人的脸，而不是按槽位写死（rules/00 证据 F）
            tail = tail_toward_speaker(
                shot, cand, speaker=d.speaker,
                box=(x / width, y / height, bw / width, bh / height)) or tail
        plan.append({
            "text": piece, "speaker": d.speaker, "style": d.resolved_style(),
            "kind": d.kind, "slot": cand, "tail": tail,
            "x": round(x / width, 4), "y": round(y / height, 4),
            "w": round(bw / width, 4), "h": round(bh / height, 4),
            "font_size": size,
        })
    return plan


# =============================================================================
# 空白气泡检测（找回图像模型画好的气泡框）
# =============================================================================

def _components(mask, min_area: int) -> list[tuple[int, int, int, int, int]]:
    """在布尔网格上做 4 邻域连通域，返回 (x0,y0,x1,y1,area)。"""
    import numpy as np
    rows, cols = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    out: list[tuple[int, int, int, int, int]] = []
    for y in range(rows):
        for x in range(cols):
            if not mask[y, x] or visited[y, x]:
                continue
            stack = [(y, x)]
            visited[y, x] = True
            xs: list[int] = []
            ys: list[int] = []
            while stack:
                cy, cx = stack.pop()
                xs.append(cx)
                ys.append(cy)
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < rows and 0 <= nx < cols and mask[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        stack.append((ny, nx))
            area = len(xs)
            if area < min_area:
                continue
            out.append((min(xs), min(ys), max(xs) + 1, max(ys) + 1, area))
    return out


def _largest_white_rect(mask, x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
    """
    返回连通域内"最大全白矩形"的包围盒（直方图法）。

    这比按行列阈值取主体更准确：圆角、尾巴、不规则边缘都会被排除，
    得到的是气泡里真正能放字的最大矩形。文字只填这个矩形，就不会跑到
    气泡外面。
    """
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return _body_box(mask, x0, y0, x1, y1)
    sub = mask[y0:y1, x0:x1].astype("uint8")
    rows, cols = sub.shape
    if rows < 4 or cols < 4:
        return _body_box(mask, x0, y0, x1, y1)
    heights = np.zeros(cols, dtype="int32")
    best_area = 0
    best = (0, 0, cols, rows)
    for y in range(rows):
        heights = np.where(sub[y] > 0, heights + 1, 0)
        stack: list[int] = []
        for x in range(cols + 1):
            h = int(heights[x]) if x < cols else 0
            while stack and int(heights[stack[-1]]) > h:
                hh = int(heights[stack.pop()])
                left = stack[-1] + 1 if stack else 0
                area = hh * (x - left)
                if area > best_area:
                    best_area = area
                    best = (left, y - hh + 1, x, y + 1)
            stack.append(x)
    bx0, by0, bx1, by1 = best
    if (bx1 - bx0) < 6 or (by1 - by0) < 4:
        return _body_box(mask, x0, y0, x1, y1)
    # 最大矩形太小（可能是云朵/爆炸气泡的尖角），退回行列主体框
    if best_area < max(1, (x1 - x0) * (y1 - y0)) * 0.18:
        return _body_box(mask, x0, y0, x1, y1)
    return (x0 + bx0, y0 + by0, x0 + bx1, y0 + by1)


def _body_box(mask, x0: int, y0: int, x1: int, y1: int) -> tuple[int, int, int, int]:
    """
    在一个白色连通域里找出"气泡主体"的包围盒，排除下方的小尾巴。

    做法：先按行统计白像素宽度，取宽度超过最大值 55% 的连续行作为主体行；
    再在主体行里按列统计，取覆盖率超过主体高度 55% 的列。这样尾部和圆角
    会被排除，填字时就不会跑到气泡外面。
    """
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return (x0, y0, x1, y1)
    sub = mask[y0:y1, x0:x1]
    if sub.size == 0:
        return (x0, y0, x1, y1)
    rows = sub.sum(axis=1)
    if rows.max() <= 0:
        return (x0, y0, x1, y1)
    body_rows = np.where(rows > rows.max() * 0.55)[0]
    if body_rows.size == 0:
        return (x0, y0, x1, y1)
    by0, by1 = int(body_rows.min()), int(body_rows.max()) + 1
    cols = sub[by0:by1].sum(axis=0)
    if cols.max() <= 0:
        return (x0, y0, x1, y1)
    body_cols = np.where(cols > (by1 - by0) * 0.55)[0]
    if body_cols.size == 0:
        return (x0, y0, x1, y1)
    bx0, bx1 = int(body_cols.min()), int(body_cols.max()) + 1
    if bx1 - bx0 < 6 or by1 - by0 < 4:
        return (x0, y0, x1, y1)
    return (x0 + bx0, y0 + by0, x0 + bx1, y0 + by1)


def _side_of(nx: float) -> str:
    if nx < 0.44:
        return "左"
    if nx > 0.56:
        return "右"
    return "中"


def detect_blank_bubbles(img: Image.Image, plan: Sequence[dict],
                         return_extra: bool = False
                         ) -> list[dict | None] | tuple[list[dict | None], list[dict]]:
    """
    在生成图上精确定位"空白气泡"，返回与 plan 一一对应的白色矩形。

    与旧版区别：
      · 候选来自 `_balloon_candidates()`：形状感知（椭圆/贴顶/暗描边/开运算去白边），
        不再用"白色连通域 + 面积比"硬匹配；
      · 匹配是**一对一的全局贪心**：位置距离 + 左右侧别 + 面积比 + IoU 综合打分，
        避免两个计划框抢同一个气泡、或把 A 的气泡填上 B 的台词；
      · 返回的矩形取"气泡白色主体的内接框"，尾巴/描边不参与填字；
      · `return_extra=True` 时额外返回多画的气泡，供补背景/返修使用。

    一个都匹配不到就返回全 None；调用方只在**整张图没有任何气泡**时才程序兜底。
    """
    if not plan:
        return ([], []) if return_extra else []
    candidates = _balloon_candidates(img)
    if not candidates:
        return ([None] * len(plan), []) if return_extra else [None] * len(plan)

    W, H = img.size
    cand_feats: list[dict] = []
    for (x0, y0, x1, y1, body, mask) in candidates:
        bx0, by0, bx1, by1 = body
        cand_feats.append({
            "x": bx0 / W, "y": by0 / H,
            "w": max(1, bx1 - bx0) / W, "h": max(1, by1 - by0) / H,
            "cx": (bx0 + bx1) / 2 / W, "cy": (by0 + by1) / 2 / H,
            "side": _side_of((bx0 + bx1) / 2 / W),
            "area": max(1e-6, (bx1 - bx0) * (by1 - by0) / (W * H)),
            "box": (x0, y0, x1, y1), "mask": mask,
        })

    try:
        max_dist = float(getattr(config, "BUBBLE_MATCH_MAX_DIST", 0.55))
        require_side = bool(getattr(config, "BUBBLE_MATCH_REQUIRE_SIDE", True))
    except (TypeError, ValueError):
        max_dist, require_side = 0.55, True

    pairs: list[tuple[float, int, int]] = []
    for i, item in enumerate(plan):
        px, py = float(item.get("x", 0)), float(item.get("y", 0))
        pw, ph = float(item.get("w", 0)), float(item.get("h", 0))
        parea = max(1e-6, pw * ph)
        pcx, pcy = px + pw / 2, py + ph / 2
        pslot = str(item.get("slot", "") or "")
        pside = "左" if pslot.endswith("left") else ("右" if pslot.endswith("right") else "中")
        for j, cf in enumerate(cand_feats):
            dist = ((cf["cx"] - pcx) ** 2 + (cf["cy"] - pcy) ** 2) ** 0.5
            if dist > max_dist:
                continue
            if require_side and pside in ("左", "右") and cf["side"] in ("左", "右") \
                    and pside != cf["side"]:
                continue
            area_pen = abs(math.log(max(1e-6, cf["area"] / parea)))
            side_pen = 0.0 if (pside == "中" or cf["side"] == "中" or pside == cf["side"]) else 0.8
            iou = _iou((px, py, px + pw, py + ph),
                       (cf["x"], cf["y"], cf["x"] + cf["w"], cf["y"] + cf["h"]))
            score = dist + area_pen * 0.12 + side_pen - iou * 0.5
            pairs.append((score, i, j))

    matched: list[dict | None] = [None] * len(plan)
    used: set[int] = set()
    for _score, i, j in sorted(pairs, key=lambda x: x[0]):
        if matched[i] is not None or j in used:
            continue
        matched[i] = dict(cand_feats[j])
        used.add(j)
    extras = [c for k, c in enumerate(cand_feats) if k not in used]
    if return_extra:
        return matched, extras
    return matched


def _overlaps_norm(a: tuple[float, float, float, float],
                   b: tuple[float, float, float, float], pad: float = 0.01) -> bool:
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0]
                or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def _free_spot(x: float, y: float, w: float, h: float,
               occupied: Sequence[tuple[float, float, float, float]]) -> tuple[float, float]:
    """给"没匹配到模型气泡"的台词找一个不压住现有气泡的位置（优先原地，其次下移/换边）。"""
    candidates: list[tuple[float, float]] = [(x, y)]
    for step in range(1, 18):
        candidates.append((x, min(0.98 - h, y + step * 0.022)))
    for dx in (0.35, -0.35):
        candidates.append((min(max(0.01, x + dx), 0.99 - w), y))
    for cx, cy in candidates:
        rect = (cx, cy, cx + w, cy + h)
        if not any(_overlaps_norm(rect, o) for o in occupied):
            return cx, cy
    return x, y


def layouts_from_plan(shot: Shot, plan: Sequence[dict],
                      detected: Sequence[dict | None],
                      width: int, height: int,
                      avoid: Sequence[tuple[float, float, float, float]] = ()
                      ) -> list[BubbleLayout]:
    """
    把"出图计划 + 检测结果"转成最终排版。

    匹配到模型气泡 → 只填字（drawn_in_art=True）；
    没匹配到 → 程序在**不与任何已检测气泡重叠**的位置补画（drawn_in_art=False），
    并在自检里报 bubble_fallback，触发下一轮"按坐标画气泡"的返修。
    """
    out: list[BubbleLayout] = []
    occupied = list(avoid)
    for i, item in enumerate(plan):
        det = detected[i] if i < len(detected) else None
        if det:
            x, y, w, h = det["x"], det["y"], det["w"], det["h"]
            inset = 0.003
            x += inset
            y += inset
            w = max(0.03, w - inset * 2)
            h = max(0.03, h - inset * 2)
            drawn = True
        else:
            x, y = float(item.get("x", 0)), float(item.get("y", 0))
            w, h = float(item.get("w", 0)), float(item.get("h", 0))
            x, y = _free_spot(x, y, w, h, occupied)
            drawn = False
        text = str(item.get("text", "") or "")
        if not text.strip() or w <= 0 or h <= 0:
            continue
        w = min(0.98, max(0.06, float(w)))
        h = min(0.98, max(0.04, float(h)))
        x = min(max(0.01, float(x)), 1.0 - w - 0.01)
        y = min(max(0.01, float(y)), 1.0 - h - 0.01)
        occupied.append((x, y, x + w, y + h))
        pad_x = int(config.BUBBLE_PADDING_X * (0.7 if drawn else 1.0))
        pad_y = int(config.BUBBLE_PADDING_Y * (0.6 if drawn else 1.0))
        font_size = _fit_font_in_box(text, int(w * width) - pad_x * 2,
                                     int(h * height) - pad_y * 2,
                                     int(item.get("font_size") or _font_size(width, text)))
        out.append(BubbleLayout(
            text=text, speaker=str(item.get("speaker", "") or ""),
            style=str(item.get("style", "") or "圆角矩形"),
            kind=str(item.get("kind", "") or "speech"),
            x=x, y=y, w=w, h=h,
            slot=str(item.get("slot", "") or ""),
            tail=str(item.get("tail", "") or "none"),
            font_size=font_size, drawn_in_art=drawn,
        ))
    return out


def build_layout(shot: Shot, width: int, height: int,
                 position: str | None = None,
                 top_reserved: float = 0.0,
                 detail: dict[str, Any] | None = None,
                 skin: dict[str, Any] | None = None) -> list[BubbleLayout]:
    """
    自动计算气泡布局。

    优先使用导演层给的 slot；没有就按说话人站位左右交替。多个气泡会做碰撞
    检测，并利用画面细节密度避开人脸/复杂纹理，放不下才下移或换槽位。
    """
    position = position or config.BUBBLE_DEFAULT_POSITION
    _, items = split_plain_narration(shot)
    items = [d for d in items if d.kind != "sfx" and (d.text or "").strip()]
    if not items:
        return []

    entries: list[tuple[Dialogue, str]] = []
    for d in items:
        for piece in _split_long_dialogue(d.text):
            entries.append((d, piece))
    if not entries:
        return []

    crowded = len(entries) >= int(config.BUBBLE_CROWDED_THRESHOLD)
    factor = _panel_text_factor(shot)
    margin = int(config.BUBBLE_MARGIN)
    top = max(int(height * float(getattr(config, "BUBBLE_SAFE_TOP_RATIO", 0.045))),
              int(height * max(0.0, min(0.75, top_reserved))))
    top += margin

    layouts: list[BubbleLayout] = []
    occupied: list[tuple[int, int, int, int]] = []
    stack_by_slot: dict[str, int] = {}

    for i, (d, piece) in enumerate(entries):
        size_hint = 0
        if d.size == "S":
            size_hint = int(width * config.BUBBLE_FONT_RATIO * 0.9 * factor)
        elif d.size == "L":
            size_hint = int(width * config.BUBBLE_FONT_RATIO * 1.15 * factor)
        bw, bh, size, _lines = _bubble_size(
            piece, width, height, crowded, size_hint, factor=factor)

        slot = (d.slot or "").strip()
        if slot not in config.BUBBLE_SLOTS:
            side = shot.char_sides.get(d.speaker, "")
            slot = _default_slot(i, len(entries), side)
        if d.kind == "narration":
            slot = "top-center"

        speaker_side = shot.char_sides.get(d.speaker, "")
        best: tuple[float, str, int, int] | None = None
        # 先看候选槽位，按"细节密度 + 槽位先验 + 站位偏差"打分。
        # 程序画气泡时只考虑顶部（提示词预留的就是这一带）。
        slot_pool = list(config.BUBBLE_SLOTS)
        if (not bool(getattr(config, "IMAGE_DRAW_BUBBLES", False))
                or bool(getattr(shot, "bubble_programmatic", False))):
            slot_pool = [s for s in slot_pool if s.startswith("top")] or slot_pool
        # 气泡必须在说话人那一侧：不是"扣分"，而是直接不许放到反侧
        if not d.offscreen:
            side = shot.char_sides.get(d.speaker, "")
            if side == "左":
                slot_pool = [s for s in slot_pool if s.endswith("left")] or slot_pool
            elif side == "右":
                slot_pool = [s for s in slot_pool if s.endswith("right")] or slot_pool
        for cand in slot_pool:
            stack = stack_by_slot.get(cand, 0)
            x, y = _slot_position(cand, bw, bh, width, height, margin, top, stack)
            if x < 0 or y < 0 or x + bw > width or y + bh > height:
                continue
            rect = (x, y, x + bw, y + bh)
            if any(_rects_overlap(rect, o, pad=8) for o in occupied):
                continue
            rect_norm = (x / width, y / height, (x + bw) / width, (y + bh) / height)
            score = (_rect_detail(detail, rect_norm) * 1.35
                     + _rect_score(skin, rect_norm) * 5.0
                     + _SLOT_PRIOR.get(cand, 0.3))
            if cand == slot:
                score -= 0.08
            if not d.offscreen:
                if speaker_side == "左" and cand.endswith("right"):
                    score += 0.45
                elif speaker_side == "右" and cand.endswith("left"):
                    score += 0.45
            if best is None or score < best[0]:
                best = (score, cand, x, y)

        if best is None:
            # 实在放不下：从顶部往下找第一个空位
            cand = slot
            x, y = _slot_position(cand, bw, bh, width, height, margin, top,
                                  sum(l.h * height for l in layouts) + config.BUBBLE_GAP * len(layouts))
            best = (0.0, cand, x, y)
        _score, cand, x, y = best
        # 夹进画面内
        x = max(2, min(int(x), width - bw - 2))
        y = max(2, min(int(y), height - bh - 2))
        rect = (x, y, x + bw, y + bh)
        occupied.append(rect)
        stack_by_slot[cand] = stack_by_slot.get(cand, 0) + bh + int(config.BUBBLE_GAP)

        # 尾巴跟着"实际落位"走：导演层给的 tail 是按首选槽位算的，
        # 一旦避让挪了位置，旧的尾巴就会指向错误的方向。
        tail = config.BUBBLE_SLOT_TO_TAIL.get(cand, "down")
        if d.kind != "narration" and not d.offscreen:
            # 按说话人实际站位算尾巴，不能只看槽位：右侧说话人放在 mid-right 时，
            # 静态映射会给成 down-left，尾巴会指向旁边的人（台词看起来像别人说的）。
            tail = tail_toward_speaker(
                shot, cand, d.speaker,
                box=(x / width, y / height, bw / width, bh / height)) or tail
        elif d.kind == "narration" or d.offscreen:
            tail = "none"
        layouts.append(BubbleLayout(
            text=piece, speaker=d.speaker, style=d.resolved_style(), kind=d.kind,
            x=x / width, y=y / height, w=bw / width, h=bh / height,
            slot=cand, tail=tail, font_size=size,
        ))

    return layouts


# =============================================================================
# 渲染
# =============================================================================

def _iou(a: tuple[float, float, float, float],
         b: tuple[float, float, float, float]) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area_a = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
    return inter / max(1e-6, area_a + area_b - inter)


def cleanup_extra_balloons(img: Image.Image,
                           layouts: Sequence[BubbleLayout],
                           exclude_boxes: Sequence[tuple[float, float, float, float]] = (),
                           report: dict[str, int] | None = None) -> Image.Image:
    """
    处理模型"多画"的空白气泡。

    图像模型经常不听话：要求 1 个气泡却画 2~3 个。多出来的白色气泡如果不管，
    成品里就会出现莫名其妙的空对话框。这里做两件事：
      · 整块的额外气泡 → 填一个"……"，让它看起来像欲言又止/正在输入；
      · 又薄又长的额外白条（通常是被前层气泡挡住一半的背面气泡）→ 用上方
        背景克隆盖掉，避免留下一条白边。
    已经用于填字的 normal 气泡、旁白条、SFX 都不会被当成额外气泡。
    """
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return img
    if report is not None:
        report.setdefault("extra_balloons", 0)
        report.setdefault("extra_strips", 0)
    W, H = img.size
    used = [(b.x * W, b.y * H, (b.x + b.w) * W, (b.y + b.h) * H) for b in layouts]
    used.extend((x0 * W, y0 * H, x1 * W, y1 * H)
                for x0, y0, x1, y1 in exclude_boxes)
    try:
        tw = 160
        th = max(32, int(tw * H / max(1, W)))
        small = img.convert("RGB").resize((tw, th), Image.BILINEAR)
        arr = np.asarray(small, dtype="int16")
        r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
        white = ((r >= 210) & (g >= 210) & (b >= 210)
                 & (np.max(arr, axis=2) - np.min(arr, axis=2) <= 32))
        comps = _components(white, min_area=max(60, int(tw * th * 0.0015)))
    except Exception as exc:  # noqa: BLE001
        log.debug("额外气泡清理失败：%s", exc)
        return img

    draw = ImageDraw.Draw(img)
    for (x0, y0, x1, y1, area) in comps:
        nx0, ny0, nx1, ny1 = x0 / tw, y0 / th, x1 / tw, y1 / th
        nw, nh = nx1 - nx0, ny1 - ny0
        if nw <= 0.04 or nh <= 0.025:
            continue
        px0, py0, px1, py1 = nx0 * W, ny0 * H, nx1 * W, ny1 * H
        box = (px0, py0, px1, py1)
        # 已经用于填字的气泡 / 旁白条，跳过
        if any(_iou(box, u) > 0.12 for u in used):
            continue
        fill_ratio = area / max(1.0, (x1 - x0) * (y1 - y0))
        carea = nw * nh
        if carea < 0.003 or carea > 0.10:
            continue
        if fill_ratio < 0.55:
            continue
        aspect = nw / max(1e-6, nh)

        # 薄白条：多半是背面气泡露出来的一条，克隆上方背景盖掉
        if nh < 0.085 and aspect > 2.4 and fill_ratio < 0.86:
            # 如果白条下方压着正文气泡，只处理正文气泡上沿以上的部分
            cut = py1
            for u in used:
                if u[0] < px1 and u[2] > px0 and py0 < u[1] < cut:
                    cut = u[1]
            if cut - py0 < 8:
                continue
            shift = max(10, int((cut - py0) * 1.25))
            src_y0 = max(0, py0 - shift)
            src_y1 = src_y0 + (cut - py0)
            patch = img.crop((int(px0), int(src_y0), int(px1), int(src_y1)))
            if patch.size[0] > 2 and patch.size[1] > 2:
                mask = Image.new("L", patch.size, 255).filter(ImageFilter.GaussianBlur(7))
                img.paste(patch, (int(px0), int(py0)), mask)
                if report is not None:
                    report["extra_strips"] += 1
            continue

        # 整块额外气泡：填"……"，让它看起来像正在输入/欲言又止。
        # 阈值必须够高：模型自己写进气泡的短信/字幕也是白底，但内部有深色字，
        # 白像素占比会低一些，不能被误判成空白气泡。
        if fill_ratio >= 0.86 and 0.7 <= aspect <= 6.0 and nh >= 0.035:
            size = max(18, min(40, int(nh * H * 0.45)))
            font = load_font(size, bold=True)
            txt = "……"
            tw_, th_ = measure(txt, font)
            tx = px0 + ((px1 - px0) - tw_) / 2
            ty = py0 + ((py1 - py0) - th_) / 2 - 2
            draw.text((tx, ty), txt, font=font,
                      fill=_rgba(config.BUBBLE_TEXT_COLOR))
            if report is not None:
                report["extra_balloons"] += 1
    return img


def render_bubbles(base: Image.Image,
                   layouts: Sequence[BubbleLayout | dict],
                   shot: Shot | None = None,
                   draw_sfx: bool = True,
                   draw_narration: bool = True) -> Image.Image:
    """把气泡叠加到图像上，返回新图像。"""
    img = base.convert("RGBA")
    W, H = img.size
    normalized: list[BubbleLayout] = []
    for item in layouts:
        b = item if isinstance(item, BubbleLayout) else BubbleLayout.from_dict(item)
        if b and b.visible and (b.text or "").strip():
            normalized.append(b)

    if normalized:
        shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        content = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(content)
        for b in normalized:
            _render_one(draw, b, W, H, shadow_draw if config.BUBBLE_SHADOW else None)
        if config.BUBBLE_SHADOW:
            shadow = _blur_shadow(shadow, int(getattr(config, "BUBBLE_SHADOW_BLUR", 7)))
            img = Image.alpha_composite(img, shadow)
        img = Image.alpha_composite(img, content)

    if draw_narration and shot is not None:
        img = render_narration(img.convert("RGB"), shot).convert("RGBA")

    if draw_sfx and shot is not None:
        boxes = [(b.x * W, b.y * H, (b.x + b.w) * W, (b.y + b.h) * H)
                 for b in normalized]
        img = _render_sfx(img, shot, W, H, boxes)
    return img.convert("RGB")


def _render_sfx(img: Image.Image, shot: Shot, W: int, H: int,
                avoid_boxes: Sequence[tuple[float, float, float, float]] = ()) -> Image.Image:
    """画拟声词艺术字：粗黑描边 + 白色内描边 + 彩色字心 + 少量旋转。

    会避开气泡与旁白占据的区域，放不下就退到四角里重叠最少的位置。
    """
    if not config.SFX_ENABLED:
        return img
    sfx = [d for d in shot.dialogues if d.kind == "sfx" and d.text.strip()]
    if not sfx:
        return img

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    base_size = max(28, int(H * config.SFX_FONT_RATIO))
    max_n = int(getattr(config, "SFX_MAX_PER_PANEL", 3))
    candidate_anchors = [
        config.SFX_POSITION_RATIO,
        (0.24, 0.12), (0.78, 0.78), (0.24, 0.78), (0.50, 0.08),
    ]
    for idx, d in enumerate(sfx[:max_n]):
        text = d.text.strip()
        # 长破折号/省略号在部分中文字体里会变成一根横杠，先清掉
        text = (text.replace("——", "").replace("—", "")
                .replace("…", "").replace("...", "")).strip()
        if not text:
            text = d.text.strip()
        if not text:
            continue
        size = base_size if len(text) <= 3 else int(base_size * 0.78)
        font = load_font(size, bold=True, kind="sfx")
        tw, th = measure(text, font)
        pad = int(size * 0.35)
        canvas = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
        cd = ImageDraw.Draw(canvas)
        # 外黑边 + 实心字。不要再加白色内描边：复杂汉字（嗡/嘟）会被白色
        # 内描边糊成一团，看起来像黄色贴纸。
        cd.text((pad, pad), text, font=font,
                fill=_rgba(config.SFX_TEXT_COLOR),
                stroke_width=int(config.SFX_OUTLINE_WIDTH),
                stroke_fill=_rgba(config.SFX_OUTLINE_COLOR))
        cd.text((pad, pad), text, font=font, fill=_rgba(config.SFX_TEXT_COLOR))

        angle_range = float(getattr(config, "SFX_ROTATION_RANGE", 9.0))
        # 用文本 hash 固定角度，保证重绘时不会乱跳
        angle = ((hash(text) % int(angle_range * 20 + 1)) - angle_range * 10) / 10.0
        if abs(angle) > 0.5:
            canvas = canvas.rotate(angle, expand=True, resample=Image.BICUBIC)

        best = None
        best_overlap = None
        for k, anchor in enumerate(candidate_anchors):
            rx, ry = anchor
            cx, cy = int(W * rx), int(H * ry)
            x = max(0, min(W - canvas.width, cx - canvas.width // 2))
            y = max(0, min(H - canvas.height, cy - canvas.height // 2))
            rect = (x, y, x + canvas.width, y + canvas.height)
            overlap = sum(
                max(0, min(rect[2], b[2]) - max(rect[0], b[0]))
                * max(0, min(rect[3], b[3]) - max(rect[1], b[1]))
                for b in avoid_boxes
            )
            if best_overlap is None or overlap < best_overlap:
                best_overlap, best = overlap, (x, y)
            if overlap == 0:
                break
        x, y = best or (0, 0)
        layer.alpha_composite(canvas, (x, y))
    return Image.alpha_composite(img.convert("RGBA"), layer)


# =============================================================================
# 对外主入口
# =============================================================================

def detect_white_borders(img: Image.Image, threshold: int = 242,
                         ratio: float = 0.96,
                         max_frac: float = 0.18) -> dict[str, int]:
    """检测画面四边是否有整条纯白/近白边（模型偶尔会在方图里画竖构图留白）。"""
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return {}
    arr = np.asarray(img.convert("RGB"), dtype="int16")
    h, w = arr.shape[:2]
    if h < 32 or w < 32:
        return {}

    def frac(line) -> float:
        return float(np.mean(np.all(line >= threshold, axis=-1)))

    left = 0
    while left < int(w * max_frac) and frac(arr[:, left, :]) >= ratio:
        left += 1
    right = 0
    while right < int(w * max_frac) and frac(arr[:, w - 1 - right, :]) >= ratio:
        right += 1
    top = 0
    while top < int(h * max_frac) and frac(arr[top, :, :]) >= ratio:
        top += 1
    bottom = 0
    while bottom < int(h * max_frac) and frac(arr[h - 1 - bottom, :, :]) >= ratio:
        bottom += 1
    if left + right > w * 0.7 or top + bottom > h * 0.7:
        return {}       # 整张图几乎全白，不是"白边"，不要裁
    out = {}
    if left:
        out["left"] = left
    if right:
        out["right"] = right
    if top:
        out["top"] = top
    if bottom:
        out["bottom"] = bottom
    return out


def trim_white_borders(img: Image.Image, **kwargs) -> tuple[Image.Image, dict[str, int]]:
    """裁掉四边白边；左右被裁时缩放回原宽度，保持条漫统一宽度。"""
    borders = detect_white_borders(img, **kwargs)
    if not borders:
        return img, {}
    w, h = img.size
    left = borders.get("left", 0)
    right = borders.get("right", 0)
    top = borders.get("top", 0)
    bottom = borders.get("bottom", 0)
    crop = img.crop((left, top, w - right, h - bottom))
    if crop.width < 64 or crop.height < 64:
        return img, {}
    if crop.width != w:
        crop = crop.resize((w, max(64, int(round(crop.height * w / crop.width)))),
                           Image.LANCZOS)
    return crop, borders


def _balloon_candidates(img: Image.Image,
                        keep_boxes: Sequence[tuple[float, float, float, float]] = (),
                        ) -> list[tuple[int, int, int, int, tuple[int, int, int, int], Image.Image]]:
    """
    找出画面里的"空白气泡"，返回 (外接框, 白色主体框, 蒙版)。

    判据（与 rules/02 §5.4 一致）：
      · 近白、低饱和、实心、宽高比像气泡；
      · **至少 3 条边有一圈更暗的描边**（气泡是白底黑边；墙/天空没有闭合边）；
      · 允许贴画面边缘（分镜裁切很常见），但最多只贴 1 条边；
      · 白色主体面积 ≥ 画面 0.6%（排除门把手、高光点这类小白块）；
      · 开运算去掉 1~2px 白色细边框，膨胀 2 次把细尾巴并进来。

    分辨率用 320 宽（不是 180）：气泡描边只有几个像素，降采样太狠会让
    气泡内部和白背景"漏"成一个连通域，直接检测不到（实测第 2 格就是这样）。
    """
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return []
    W, H = img.size
    try:
        tw = 320
        th = max(48, int(tw * H / max(1, W)))
        small = img.convert("RGB").resize((tw, th), Image.BILINEAR)
        arr = np.asarray(small, dtype="int16")
        lum = arr.mean(axis=2)
        r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
        white = ((r >= 226) & (g >= 226) & (b >= 226)
                 & (np.max(arr, axis=2) - np.min(arr, axis=2) <= 26))
        # 先把"深色描边"膨胀 2px 再挖掉：气泡尾巴尖/抗锯齿处常有 1px 缺口，
        # 白底气泡会和浅色背景连成一片（实测第 1/2 格就是这样失败的）。
        dark = lum < 150
        sealed = dark.copy()
        for _ in range(2):
            s = sealed
            sealed = (s | np.roll(s, 1, 0) | np.roll(s, -1, 0)
                      | np.roll(s, 1, 1) | np.roll(s, -1, 1))
        white = white & ~sealed
        # 削掉最外圈：分镜的白色细边框会把气泡和画面边缘连成一块
        white[:10, :] = False
        white[-6:, :] = False
        white[:, :10] = False
        white[:, -10:] = False
        # 开运算两遍：去掉细白边/高光条
        for _ in range(2):
            er = white.copy()
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                er &= np.roll(np.roll(white, dy, 0), dx, 1)
            white = er
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                white = white | np.roll(np.roll(er, dy, 0), dx, 1)
        # 轻微膨胀：把细尾巴并进主体
        grown = white.copy()
        for _ in range(2):
            g0 = grown
            grown = (g0 | np.roll(g0, 1, 0) | np.roll(g0, -1, 0)
                     | np.roll(g0, 1, 1) | np.roll(g0, -1, 1))
        comps = _components(grown, min_area=max(180, int(tw * th * 0.002)))
    except Exception as exc:  # noqa: BLE001
        log.debug("气泡检测失败：%s", exc)
        return []

    out: list[tuple[int, int, int, int, tuple[int, int, int, int], Image.Image]] = []
    for (x0, y0, x1, y1, area) in comps:
        bw, bh = max(1, x1 - x0), max(1, y1 - y0)
        fill = area / float(bw * bh)
        carea = (bw / tw) * (bh / th)
        if carea < 0.006 or carea > 0.28:
            continue
        aspect = bw / max(1, bh)
        if not (0.25 <= aspect <= 7.0):
            continue
        if fill < 0.50:
            continue
        # 最多只允许贴 1 条边；贴 2 条以上基本是角落的墙/桌面
        edge_hits = sum((x0 <= 1, y0 <= 1, x1 >= tw - 2, y1 >= th - 2))
        if edge_hits > 1:
            continue
        inner_mean = float(lum[y0:y1, x0:x1].mean())

        def _band_dark(band) -> bool:
            # 描边只有 1~2px，用均值会被背景稀释；取 20 分位更稳
            return band.size > 0 and float(np.percentile(band, 20)) < inner_mean - 25

        dark_sides = 0
        if y0 > 1 and _band_dark(lum[max(0, y0 - 2):y0, x0:x1]):
            dark_sides += 1
        if y1 < th - 2 and _band_dark(lum[y1:min(th, y1 + 2), x0:x1]):
            dark_sides += 1
        if x0 > 1 and _band_dark(lum[y0:y1, max(0, x0 - 2):x0]):
            dark_sides += 1
        if x1 < tw - 2 and _band_dark(lum[y0:y1, x1:min(tw, x1 + 2)]):
            dark_sides += 1
        if dark_sides < 3:
            continue
        # 填字只用"白色主体"（不含描边/尾巴）
        body_small = white[y0:y1, x0:x1]
        ys, xs = np.nonzero(body_small)
        if not len(xs):
            continue
        body_area = (xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1) / float(tw * th)
        if body_area < 0.006:
            continue
        local = Image.fromarray((grown[y0:y1, x0:x1].astype("uint8") * 255))
        fx0, fy0 = int(x0 * W / tw), int(y0 * H / th)
        fx1, fy1 = max(fx0 + 1, int(x1 * W / tw)), max(fy0 + 1, int(y1 * H / th))
        mask = local.resize((fx1 - fx0, fy1 - fy0), Image.NEAREST)
        body = (int((x0 + xs.min()) * W / tw), int((y0 + ys.min()) * H / th),
                int((x0 + xs.max() + 1) * W / tw), int((y0 + ys.max() + 1) * H / th))
        mask = mask.filter(ImageFilter.MaxFilter(31))
        mask = mask.filter(ImageFilter.GaussianBlur(4))
        out.append((fx0, fy0, fx1, fy1, body, mask))
    return out[:4]


def cover_stray_balloons(img: Image.Image,
                         keep_boxes: Sequence[tuple[float, float, float, float]] = (),
                         report: dict[str, int] | None = None) -> Image.Image:
    """检测并补掉画面里所有"没有台词对应"的自绘气泡（见 _cover_balloon_items）。"""
    return _cover_balloon_items(img, _balloon_candidates(img, keep_boxes), report)


def _cover_balloon_items(img: Image.Image,
                         boxes: Sequence[tuple[int, int, int, int,
                                              tuple[int, int, int, int], Image.Image]],
                         report: dict[str, int] | None = None) -> Image.Image:
    """
    把给定的气泡（box + mask）补成背景。

    只补气泡形状本身（椭圆 + 尾巴），不做矩形补丁；用调和填充
    （Laplace inpainting）让周围的颜色/光影自然渗进来，接缝连续。
    """
    if not boxes:
        return img
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return img
    W, H = img.size
    out = img.copy()
    for (x0, y0, x1, y1, _body, mask) in boxes:
        bw, bh = x1 - x0, y1 - y0
        if bw < 8 or bh < 8:
            continue
        pad = 22
        rx0, ry0 = max(0, x0 - pad), max(0, y0 - pad)
        rx1, ry1 = min(W, x1 + pad), min(H, y1 + pad)
        crop = out.crop((rx0, ry0, rx1, ry1))
        half = 0.35
        sw, sh = max(16, int(crop.width * half)), max(16, int(crop.height * half))
        reg = np.asarray(crop.resize((sw, sh), Image.BILINEAR), dtype=np.float32)
        small_mask = mask.resize((sw, sh), Image.BILINEAR)
        ma = np.asarray(small_mask, dtype=np.uint8) > 40
        mm = np.zeros(reg.shape[:2], dtype=bool)
        ox, oy = int((x0 - rx0) * half), int((y0 - ry0) * half)
        mh, mw = min(ma.shape[0], mm.shape[0] - oy), min(ma.shape[1], mm.shape[1] - ox)
        if mh <= 0 or mw <= 0:
            continue
        mm[oy:oy + mh, ox:ox + mw] = ma[:mh, :mw]
        if mm.sum() < 10:
            continue
        border = reg[~mm]
        if border.size:
            reg[mm] = border.mean(axis=0)
        # 迭代平均四个邻居 = 解拉普拉斯方程：洞里的颜色和边界完全连续
        for _ in range(100):
            avg = (np.roll(reg, 1, 0) + np.roll(reg, -1, 0)
                   + np.roll(reg, 1, 1) + np.roll(reg, -1, 1)) / 4.0
            reg[mm] = avg[mm]
        filled = Image.fromarray(np.clip(reg, 0, 255).astype("uint8")).resize(
            (crop.width, crop.height), Image.BILINEAR)
        alpha = Image.new("L", filled.size, 0)
        alpha.paste(mask, (ox, oy))
        alpha = alpha.filter(ImageFilter.GaussianBlur(8))
        out.paste(filled, (rx0, ry0), alpha)
        if report is not None:
            report["stray_balloons"] = report.get("stray_balloons", 0) + 1
        log.info("补掉一个模型自绘的空白气泡：(%d,%d)-(%d,%d)", x0, y0, x1, y1)
    return out


def apply_bubbles(image_path: str | Path,
                  shot: Shot,
                  output_path: str | Path | None = None,
                  layouts: Sequence[BubbleLayout | dict] | None = None,
                  position: str | None = None,
                  enabled: bool = True) -> tuple[str, list[dict]]:
    """
    对一张分镜图叠加对话气泡。

    返回 (输出图片路径, 布局字典列表)。布局列表可保存进工程，供界面继续微调。
    """
    base = Image.open(image_path).convert("RGB")
    base, borders = trim_white_borders(base)
    W, H = base.size
    if borders and getattr(shot, "bubble_plan", None):
        # 画布被裁过：气泡计划要按新尺寸重算，否则文字会错位
        shot.bubble_plan = plan_layout(shot, W, H)

    if not enabled or not shot.has_dialogue:
        if output_path:
            base.save(output_path, format="PNG")
        return str(output_path or image_path), []

    fill_model = bool(getattr(config, "IMAGE_FILL_MODEL_BUBBLES", False)
                      and getattr(config, "IMAGE_DRAW_BUBBLES", False)
                      and not getattr(shot, "bubble_programmatic", False))
    plan = list(getattr(shot, "bubble_plan", []) or [])
    qa: dict[str, int] = {}
    # 程序画气泡时，plan 只是"预留区"参考，画面里本来就没有空白气泡可检测；
    # expected 归零，避免把正常的程序绘制误报成"气泡定位失败"。
    expected = len(plan) if fill_model else 0
    debug: dict[str, int] = {"expected": expected, "detected": 0, "extra": 0, "fallback": 0}
    extra_items: list[dict] = []

    if layouts is None:
        if fill_model and plan:
            # 正式链路：模型按提示词画了气泡 → 精确定位 → 只填字
            matched, extras = detect_blank_bubbles(base, plan, return_extra=True)
            hit = sum(1 for m in matched if m)
            debug["detected"] = hit
            debug["extra"] = len(extras)
            extra_items = [(c["box"][0], c["box"][1], c["box"][2], c["box"][3],
                            c["box"], c["mask"]) for c in extras]
            if hit == 0 and bool(getattr(config, "BUBBLE_ALLOW_PROGRAM_FALLBACK", True)):
                # 整张图一个气泡都没有：只能程序兜底（并记 detect_fail，触发返修）
                debug["fallback"] = len(plan)
                layout_objs = build_layout(shot, W, H, position,
                                           top_reserved=narration_bottom_ratio(shot, W, H),
                                           detail=_detail_grid(base),
                                           skin=_skin_grid(base))
            else:
                avoid = [(m["x"], m["y"], m["x"] + m["w"], m["y"] + m["h"])
                         for m in matched if m]
                layout_objs = layouts_from_plan(shot, plan, matched, W, H, avoid=avoid)
                debug["fallback"] = sum(1 for b in layout_objs if not b.drawn_in_art)
        else:
            layout_objs = build_layout(shot, W, H, position,
                                       top_reserved=narration_bottom_ratio(shot, W, H),
                                       detail=_detail_grid(base),
                                       skin=_skin_grid(base))
    else:
        layout_objs = [item if isinstance(item, BubbleLayout)
                       else BubbleLayout.from_dict(item) for item in layouts]

    # 多画的气泡（没有台词对应）先补成背景，再填字：否则会留下空白对话框
    if extra_items and getattr(config, "CLEANUP_EXTRA_BALLOONS", True) \
            and getattr(config, "STRAY_BALLOON_COVER", True):
        base = _cover_balloon_items(base, extra_items, report=qa)

    narr = layout_narration(shot, W, H)
    narr_boxes = [(e["x"] / W, e["y"] / H,
                   (e["x"] + e["w"]) / W, (e["y"] + e["h"]) / H)
                  for e in narr]
    result = render_bubbles(base, layout_objs, shot=shot)
    # 主链路里多画的气泡已经按形状补成背景；只有"补背景被关掉"时才退而填"……"
    if fill_model and getattr(config, "CLEANUP_EXTRA_BALLOONS", True) \
            and not getattr(config, "STRAY_BALLOON_COVER", True):
        result = cleanup_extra_balloons(result, layout_objs, narr_boxes, report=qa)

    issues: list[str] = []
    if borders:
        issues.append("white_border_trimmed")
    if debug["detected"] and not debug["fallback"] and not debug["extra"]:
        pass
    if debug["expected"] and debug["detected"] == 0:
        issues.append("bubble_detect_fail")
    if debug["extra"]:
        issues.append("bubble_count_mismatch")
    if debug["fallback"]:
        issues.append("bubble_fallback")
    if qa.get("stray_balloons", 0):
        issues.append("stray_balloon_covered")
    if qa.get("extra_balloons", 0) or qa.get("extra_strips", 0):
        issues.append("extra_balloon")
    if any(b.overflow for b in layout_objs):
        issues.append("text_overflow")
    shot.quality_issues = issues
    shot.bubble_debug = dict(debug)
    log.info("气泡：计划 %d，定位 %d，多画 %d，兜底 %d", debug["expected"],
             debug["detected"], debug["extra"], debug["fallback"])
    target = Path(output_path) if output_path else Path(image_path)
    if target.suffix.lower() in (".jpg", ".jpeg"):
        result.save(target, format="JPEG", quality=int(config.OUTPUT_JPEG_QUALITY))
    else:
        result.save(target, format="PNG")
    return str(target), [b.to_dict() for b in layout_objs]


def preview_from_layout(image_path: str | Path, shot: Shot,
                        layouts: Sequence[dict]) -> Image.Image:
    """按给定布局生成预览图（用于界面上的气泡编辑实时预览）。"""
    base = Image.open(image_path).convert("RGB")
    return render_bubbles(base, layouts, shot=shot)


def style_choices() -> list[str]:
    return list(config.BUBBLE_STYLES)
