# -*- coding: utf-8 -*-
"""成品层：分格边框、话标题、封面、尾页与条漫拼版。

这一层负责把"生成出来的图"变成"可以发布的条漫"：

    封面 → 话标题条 → 分格（墨线边框 + 纸色 gutter）→ 尾页

所有版式都用 PIL 程序化绘制，不依赖额外素材；颜色、字号、间距集中在
config.py 的 PANEL_* / EPISODE_* 里。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import config
from . import bubble
from .logger import get_logger
from .models import Project

log = get_logger(__name__)

# 深色成品底（封面/标题条/尾页）
INK_BG = (15, 17, 22)
INK_BG_2 = (28, 31, 38)
PAPER = tuple(config.PANEL_BG[:3])
ACCENT = (196, 58, 54)
WHITE = (245, 244, 240)
MUTED = (168, 172, 182)


# =============================================================================
# 文字与渐变
# =============================================================================

def _font(size: int, kind: str = "title", bold: bool = True):
    return bubble.load_font(size, bold=bold, kind=kind)


def _wrap(text: str, font, max_width: int) -> list[str]:
    return bubble.wrap_text(text, font, max_width)


def _draw_block(draw: ImageDraw.ImageDraw, lines: Sequence[str], font,
                x: int, y: int, line_h: int, fill, anchor: str = "left") -> int:
    for line in lines:
        w = bubble.measure(line, font)[0]
        tx = x - w // 2 if anchor == "center" else x
        draw.text((tx, y), line, font=font, fill=fill)
        y += line_h
    return y


def _cover_crop(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """等比裁切填满目标尺寸。"""
    W, H = size
    src = img.convert("RGB")
    scale = max(W / src.width, H / src.height)
    nw, nh = int(src.width * scale + 0.5), int(src.height * scale + 0.5)
    src = src.resize((max(1, nw), max(1, nh)), Image.LANCZOS)
    left = (src.width - W) // 2
    top = (src.height - H) // 2
    return src.crop((left, top, left + W, top + H))


def _vertical_overlay(size: tuple[int, int], top_alpha: int, bottom_alpha: int,
                      color: tuple[int, int, int]) -> Image.Image:
    """从上到下的半透明渐变层。"""
    W, H = size
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    for y in range(H):
        t = y / max(1, H - 1)
        a = int(top_alpha + (bottom_alpha - top_alpha) * t)
        draw.line((0, y, W, y), fill=(color[0], color[1], color[2], a))
    return layer


def _paper_bg(size: tuple[int, int]) -> Image.Image:
    return Image.new("RGB", size, PAPER)


def _accent_line(draw: ImageDraw.ImageDraw, x: int, y0: int, y1: int,
                 width: int = 8, color=ACCENT) -> None:
    draw.rectangle((x, y0, x + width, y1), fill=color)


# =============================================================================
# 分格
# =============================================================================

def frame_panel(path: str | Path, width: int | None = None,
                border: bool | None = None) -> Image.Image:
    """
    读取一张分镜成品，等比缩放到统一宽度并画上墨线边框。

    边框画在画面内侧，因此最终宽度仍是统一宽度，拼条时不会错位。
    """
    img = Image.open(path).convert("RGB")
    width = int(width or config.WEBTOON_PANEL_WIDTH)
    if img.width != width:
        ratio = width / img.width
        img = img.resize((width, max(1, int(img.height * ratio))), Image.LANCZOS)
    border = bool(config.PANEL_BORDER_ENABLED) if border is None else border
    if not border or int(config.PANEL_BORDER_WIDTH) <= 0:
        return img
    draw = ImageDraw.Draw(img)
    b = max(1, int(config.PANEL_BORDER_WIDTH))
    # PIL 的 width 向内侧扩张，画在 (0,0)-(w-1,h-1) 上
    draw.rectangle((0, 0, img.width - 1, img.height - 1),
                   outline=tuple(config.PANEL_BORDER_COLOR[:3]), width=b)
    return img


# =============================================================================
# 封面
# =============================================================================

def build_cover(project: Project, size: tuple[int, int] | None = None,
                backdrop_path: str | Path | None = None,
                width: int | None = None) -> Image.Image:
    """生成话封面：首格画面做虚化底，压深色渐变，叠标题/话数/钩子。"""
    if size is None:
        W = int(width or config.EPISODE_COVER_SIZE[0])
        H = int(W * config.EPISODE_COVER_SIZE[1] / max(1, config.EPISODE_COVER_SIZE[0]))
        size = (W, H)
    W, H = size
    if backdrop_path and Path(backdrop_path).exists():
        try:
            src = Image.open(backdrop_path).convert("RGB")
            # 封面底图裁掉顶部：那里通常是旁白条/空白气泡，留着会透出字
            if src.height > src.width * 0.9:
                src = src.crop((0, int(src.height * 0.16), src.width, src.height))
            bg = _cover_crop(src, (W, H))
            bg = bg.filter(ImageFilter.GaussianBlur(7))
            # 压暗，保证文字可读；但保留人物轮廓，别糊成一团黑
            bg = Image.blend(bg, Image.new("RGB", (W, H), INK_BG), 0.42)
        except Exception as exc:  # noqa: BLE001
            log.warning("封面底图读取失败：%s", exc)
            bg = Image.new("RGB", (W, H), INK_BG)
    else:
        bg = Image.new("RGB", (W, H), INK_BG)
        draw = ImageDraw.Draw(bg)
        for i in range(0, W + H, 46):
            draw.line((i, 0, i - H, H), fill=INK_BG_2, width=2)

    img = bg.convert("RGBA")
    img = Image.alpha_composite(img, _vertical_overlay((W, H), 40, 225, INK_BG))
    draw = ImageDraw.Draw(img)

    # 左侧红色竖线
    _accent_line(draw, int(W * 0.075), int(H * 0.17), int(H * 0.72), 8, ACCENT)

    # 顶部小标签
    label_font = _font(int(W * 0.024), "bold")
    draw.text((int(W * 0.105), int(H * 0.155)),
              "快 看 条 漫  ·  A I  漫 画 改 编",
              font=label_font, fill=(215, 216, 220))

    # 话数
    ep_font = _font(int(W * 0.040), "title", True)
    ep_text = f"第 {project.chapter_no or '01'} 话"
    draw.text((int(W * 0.105), int(H * 0.215)), ep_text,
              font=ep_font, fill=WHITE)
    if project.chapter_title:
        ct_font = _font(int(W * 0.034), "title", True)
        draw.text((int(W * 0.105) + bubble.measure(ep_text, ep_font)[0] + 22,
                   int(H * 0.222)), f"·  {project.chapter_title}",
                  font=ct_font, fill=(210, 212, 218))

    # 主标题：按宽度自动换行，最多 2 行
    title = project.title or "未命名作品"
    size = int(W * 0.105)
    while size > int(W * 0.055):
        font = _font(size, "title", True)
        lines = _wrap(title, font, int(W * 0.82))
        if len(lines) <= 2:
            break
        size = int(size * 0.9)
    font = _font(size, "title", True)
    lines = _wrap(title, font, int(W * 0.82))
    y = int(H * 0.38)
    line_h = int(size * 1.18)
    for line in lines[:2]:
        draw.text((int(W * 0.105), y), line, font=font, fill=WHITE)
        y += line_h

    # 钩子文案：紧跟在标题下方，不要空一大段
    if project.tagline:
        tag_font = _font(int(W * 0.029), "bold")
        tag_lines = _wrap(project.tagline, tag_font, int(W * 0.72))
        y = y + int(H * 0.035)
        for line in tag_lines[:2]:
            draw.text((int(W * 0.105), y), line, font=tag_font,
                      fill=(226, 226, 230))
            y += int(W * 0.029 * 1.55)

    # 底部署名
    foot_font = _font(int(W * 0.022), "bold")
    foot = "根据同名小说改编" if not project.author else f"原著 / {project.author}"
    draw.text((int(W * 0.105), int(H * 0.90)), foot, font=foot_font, fill=(180, 182, 190))
    draw.text((int(W * 0.105), int(H * 0.94)),
              f"共 {len(project.shots)} 格 · 竖屏条漫", font=foot_font,
              fill=(150, 152, 162))
    return img.convert("RGB")


# =============================================================================
# 话标题条
# =============================================================================

def build_header(project: Project, width: int | None = None,
                 height: int | None = None) -> Image.Image:
    """条漫顶部的话标题条，让读者一进来就知道在看第几话。"""
    W = int(width or config.WEBTOON_PANEL_WIDTH)
    H = int(height or config.EPISODE_HEADER_HEIGHT)
    img = Image.new("RGB", (W, H), INK_BG)
    draw = ImageDraw.Draw(img)
    # 斜向细线纹理
    for i in range(-H, W + H, 54):
        draw.line((i, H, i + H, 0), fill=INK_BG_2, width=2)
    _accent_line(draw, int(W * 0.055), int(H * 0.26), int(H * 0.74), 7, ACCENT)

    ep_font = _font(int(W * 0.026), "bold")
    draw.text((int(W * 0.085), int(H * 0.20)),
              f"EPISODE {project.chapter_no or '01'}", font=ep_font,
              fill=(185, 188, 198))

    title_font = _font(int(W * 0.060), "title", True)
    title = project.title or "未命名作品"
    lines = _wrap(title, title_font, int(W * 0.62))
    y = int(H * 0.34)
    for line in lines[:1]:
        draw.text((int(W * 0.085), y), line, font=title_font, fill=WHITE)

    if project.chapter_title:
        ct_font = _font(int(W * 0.030), "bold")
        draw.text((int(W * 0.085), int(H * 0.70)),
                  f"第 {project.chapter_no or '01'} 话 · {project.chapter_title}",
                  font=ct_font, fill=(205, 207, 214))

    # 右侧装饰：大号话数
    num_font = _font(int(W * 0.16), "title", True)
    num = f"{project.chapter_no or '01'}"
    nw = bubble.measure(num, num_font)[0]
    draw.text((W - int(W * 0.075) - nw, int(H * 0.18)), num,
              font=num_font, fill=(52, 55, 64))
    return img


# =============================================================================
# 尾页
# =============================================================================

def build_end_card(project: Project, width: int | None = None,
                   height: int | None = None) -> Image.Image:
    """章末尾页：本话完 + 下话预告 + 追更钩子。"""
    W = int(width or config.WEBTOON_PANEL_WIDTH)
    H = int(height or config.EPISODE_END_SIZE[1])
    img = Image.new("RGB", (W, H), INK_BG)
    draw = ImageDraw.Draw(img)
    for i in range(-H, W + H, 58):
        draw.line((i, H, i + H, 0), fill=INK_BG_2, width=2)

    _accent_line(draw, W // 2 - int(W * 0.10), int(H * 0.30),
                 int(H * 0.30) + 6, 6, ACCENT)

    main_font = _font(int(W * 0.095), "title", True)
    text = "本 话 完"
    tw = bubble.measure(text, main_font)[0]
    draw.text(((W - tw) // 2, int(H * 0.36)), text, font=main_font, fill=WHITE)

    sub_font = _font(int(W * 0.026), "bold")
    sub = "THE END OF THIS EPISODE"
    sw = bubble.measure(sub, sub_font)[0]
    draw.text(((W - sw) // 2, int(H * 0.50)), sub, font=sub_font, fill=(160, 163, 172))

    if project.next_teaser:
        teaser_font = _font(int(W * 0.029), "bold")
        lines = _wrap("下话预告 · " + project.next_teaser, teaser_font,
                      int(W * 0.76))
        y = int(H * 0.62)
        for line in lines[:2]:
            lw = bubble.measure(line, teaser_font)[0]
            draw.text(((W - lw) // 2, y), line, font=teaser_font,
                      fill=(222, 223, 228))
            y += int(W * 0.029 * 1.6)

    tip_font = _font(int(W * 0.024), "bold")
    tip = "点 击 追 更 · 下 一 话 见"
    tw = bubble.measure(tip, tip_font)[0]
    draw.text(((W - tw) // 2, int(H * 0.84)), tip, font=tip_font,
              fill=(196, 58, 54))
    return img


# =============================================================================
# 条漫拼版
# =============================================================================

def _pack(tiles: Sequence[Image.Image], max_height: int,
          gap: int) -> list[list[Image.Image]]:
    chunks: list[list[Image.Image]] = []
    cur: list[Image.Image] = []
    cur_h = 0
    for t in tiles:
        add = t.height + (gap if cur else 0)
        if cur and cur_h + add > max_height:
            chunks.append(cur)
            cur, cur_h = [], 0
            add = t.height
        cur.append(t)
        cur_h += add
    if cur:
        chunks.append(cur)
    return chunks


def compose_strip(tiles: Sequence[Image.Image], width: int,
                  max_height: int, gap: int | None = None) -> list[Image.Image]:
    """把等宽图块按纸色 gutter 竖向拼成一张或多张长图。"""
    gap = int(config.PANEL_GUTTER if gap is None else gap)
    chunks = _pack(tiles, max_height, gap)
    out: list[Image.Image] = []
    for chunk in chunks:
        total_h = sum(t.height for t in chunk) + gap * max(0, len(chunk) - 1)
        canvas = _paper_bg((width, max(1, total_h)))
        y = 0
        for t in chunk:
            canvas.paste(t, (0, y))
            y += t.height + gap
        out.append(canvas)
    return out


def build_episode_strip(panel_paths: Sequence[str | Path], project: Project,
                        out_dir: str | Path, width: int | None = None,
                        max_height: int | None = None,
                        name_template: str | None = None,
                        include_cover: bool | None = None,
                        cover_backdrop: str | Path | None = None,
                        progress_cb=None) -> dict:
    """
    生成完整条漫成品：封面、话标题条、带边框的分格、尾页。

    返回 {"files": [条漫长图...], "cover": 封面路径, "chapters": [...]}。
    """
    if not panel_paths:
        raise ValueError("没有可用于拼条漫的分镜图")
    W = int(width or config.WEBTOON_PANEL_WIDTH)
    max_h = int(max_height or config.WEBTOON_STRIP_MAX_HEIGHT)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    template = name_template or config.WEBTOON_STRIP_FILENAME_TEMPLATE
    include_cover = bool(config.EPISODE_COVER_ENABLED) if include_cover is None else include_cover

    tiles: list[Image.Image] = []
    cover_path = ""
    if include_cover:
        cover = build_cover(project, width=W,
                            backdrop_path=cover_backdrop or panel_paths[0])
        cover_path = str(out_dir / "封面.png")
        cover.save(cover_path, format="PNG")
        tiles.append(cover)

    if config.EPISODE_HEADER_ENABLED:
        tiles.append(build_header(project, W))

    for p in panel_paths:
        tiles.append(frame_panel(p, W))

    if config.EPISODE_END_CARD_ENABLED:
        tiles.append(build_end_card(project, W))

    strips = compose_strip(tiles, W, max_h, config.PANEL_GUTTER)
    files: list[str] = []
    for i, s in enumerate(strips, start=1):
        target = out_dir / f"{template.format(index=i)}.png"
        s.save(target, format="PNG")
        files.append(str(target))
        if progress_cb:
            try:
                progress_cb(f"{target.name}（{W}×{s.height}）")
            except Exception:  # noqa: BLE001
                pass
        log.info("条漫长图已生成：%s（%d×%d）", target, W, s.height)
    return {"files": files, "cover": cover_path, "tiles": len(tiles)}
