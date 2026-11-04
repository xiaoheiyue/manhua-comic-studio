# -*- coding: utf-8 -*-
"""分镜版式：把分镜拆成一页多格，自主选择 handraw-style 的漫画分镜版式，并合成页图。

三种形态（config.RENDER_MODE）：

  webtoon  原条漫：一格一张图，竖拼长条（旧行为，完整保留）
  page     分镜页：连续几格拼成一页，版式从 SB-001–SB-071 + 原生 WB-* 里选
  hybrid   混排：情绪/冲击/转场格走原生条漫长格，信息密集处走分镜页

版式选择是**内容驱动**的：
  1. 每格先有 beat（建立/对白/动作/情绪/转场/揭示/蒙太奇…）、pacing、emphasis；
  2. 用这些信号和版式的 fit（对白/动作/情绪/建立/蒙太奇/悬疑/喜剧适配度）打分；
  3. 全局动态规划，保证每格都被覆盖、整页场景不碎、重点格落在大格里；
  4. 若文本模型在线，先让它给一版"分页 + 版式"方案，校验通过就用它，
     失败再退回本地规划（离线时永远走本地）。

合成页时每格仍是**独立生成的单幅画面**（气泡、自检、返修都按格走），
再按版式几何贴进页面画布——这样既能得到真正的多格页，又不会让"一格重画"
牵连整页。
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from PIL import Image, ImageDraw, ImageOps

import config
from . import bubble as bubble_mod
from . import stylelib
from .logger import get_logger
from .models import PagePlan, Project, Shot

log = get_logger(__name__)

ProgressCb = Callable[[str], None]


# =============================================================================
# 内容信号：beat / 节奏 / 视觉权重
# =============================================================================

_MOOD_TAGS: dict[str, tuple[str, ...]] = {
    "悬疑": ("悬疑", "紧张", "压抑", "诡异", "惊悚", "不安"),
    "情绪": ("悲伤", "温馨", "浪漫", "治愈", "压抑", "激动"),
    "动作": ("愤怒", "紧张", "激动", "动作", "打斗"),
    "幽默": ("搞笑", "轻松", "幽默"),
}

_ACTION_WORDS = ("冲", "跑", "打", "摔", "抓", "撞", "踢", "扑", "追", "逃",
                 "挥", "砸", "推开", "翻", "跳", "推门", "拔", "砍", "刺")
_SUSPENSE_WORDS = ("血", "尸", "刀", "枪", "黑暗", "影子", "门缝", "监视", "失踪",
                   "秘密", "线索", "真相", "诡异", "停尸", "证据")


def _mood_text(shot: Shot) -> str:
    return f"{shot.mood or ''} {shot.description or ''} {shot.action or ''}"


def infer_beat(shot: Shot, prev: Shot | None = None) -> str:
    """从已有镜头字段推断这一格的叙事功能（写回 shot.beat）。"""
    if shot.beat:
        return shot.beat
    text = _mood_text(shot)
    speech = sum(1 for d in shot.dialogues
                 if d.kind == "speech" and (d.text or "").strip() and not d.offscreen)
    onscreen_speech = sum(1 for d in shot.dialogues
                          if d.kind == "speech" and (d.text or "").strip())
    if shot.hook:
        return "揭示"
    if prev is not None and (shot.transition or "") == "场景切换":
        return "转场"
    if shot.shot_type in ("远景", "全景") and (prev is None or shot.scene != prev.scene):
        return "建立"
    if shot.object_mode or any(w in (shot.description or "") for w in ("特写", "局部")):
        if any(w in text for w in _SUSPENSE_WORDS):
            return "揭示"
        return "细节"
    if shot.silent and not onscreen_speech:
        if any(w in text for w in _SUSPENSE_WORDS):
            return "悬疑"
        return "情绪"
    if speech >= 2:
        return "对白"
    if speech == 1:
        if any(w in text for w in _SUSPENSE_WORDS) and shot.panel_scale in ("L", "XL"):
            return "悬疑"
        return "对白"
    if any(w in (shot.action or "") for w in _ACTION_WORDS):
        return "动作"
    if any(w in text for w in _MOOD_TAGS["幽默"]):
        return "幽默"
    if any(w in text for w in _SUSPENSE_WORDS):
        return "悬疑"
    return "情绪"


def infer_pacing(shot: Shot) -> str:
    """慢 / 中 / 快：动作、短句、连续转场 → 快；留白、远景、无台词 → 慢。"""
    if shot.pacing:
        return shot.pacing
    text = _mood_text(shot)
    if shot.panel_scale in ("L", "XL") or any(w in text for w in ("慢", "停", "凝视", "沉默")):
        return "慢"
    if sum(1 for d in shot.dialogues if (d.text or "").strip()) >= 2:
        return "快"
    if any(w in (shot.action or "") for w in _ACTION_WORDS) or shot.transition in ("动作衔接", "时刻分解"):
        return "快"
    return "中"


def infer_emphasis(shot: Shot) -> str:
    """normal / main / splash：决定这一格是否该落在大格上。"""
    if shot.panel_emphasis:
        return shot.panel_emphasis
    if shot.hook or shot.panel_scale == "XL":
        return "splash"
    if shot.panel_scale == "L" or shot.shot_type in ("远景", "全景"):
        return "main"
    return "normal"


def annotate_shots(project: Project) -> int:
    """给全部 Shot 补齐 beat / pacing / panel_emphasis（幂等）。"""
    changed = 0
    prev: Shot | None = None
    for shot in project.shots:
        beat = infer_beat(shot, prev)
        pacing = infer_pacing(shot)
        emphasis = infer_emphasis(shot)
        if not shot.beat or not shot.pacing or not shot.panel_emphasis:
            changed += 1
        shot.beat = beat
        shot.pacing = pacing
        shot.panel_emphasis = emphasis
        prev = shot
    return changed


def _speech_count(shot: Shot) -> int:
    return sum(1 for d in shot.dialogues
               if d.kind == "speech" and (d.text or "").strip())


def _profile(group: Sequence[Shot], start_scene_new: bool) -> dict[str, float]:
    """把一页的内容折算成 7 维信号（0~1）。"""
    n = max(1, len(group))
    dialogue = sum(min(1.0, _speech_count(s) / 2.0) for s in group) / n
    action = sum(1.0 if (any(w in (s.action or "") for w in _ACTION_WORDS)
                         or s.panel_scale in ("L", "XL")
                         or s.mood in _MOOD_TAGS["动作"]) else 0.0
                 for s in group) / n
    emotion = sum(1.0 if (s.mood in _MOOD_TAGS["情绪"] or s.silent
                          or s.panel_scale in ("L", "XL")) else 0.0
                  for s in group) / n
    establish = 0.9 if (start_scene_new
                        or group[0].shot_type in config.STORYBOARD_ESTABLISHING_TYPES) else 0.15
    scenes = {s.scene for s in group if s.scene}
    times = {s.time_of_day for s in group if s.time_of_day}
    montage = 1.0 if len(scenes) >= 2 else (0.6 if len(times) >= 2 else 0.2)
    suspense = sum(1.0 if (any(w in _mood_text(s) for w in _SUSPENSE_WORDS)
                           or s.mood in _MOOD_TAGS["悬疑"]) else 0.0
                   for s in group) / n
    comedy = sum(1.0 if s.mood in _MOOD_TAGS["幽默"]
                 or s.beat in ("幽默", "日常") else 0.0 for s in group) / n
    return {"dialogue": dialogue, "action": action, "emotion": emotion,
            "establish": establish, "montage": montage,
            "suspense": suspense, "comedy": comedy}


def _cell_bbox(cell: Any) -> tuple[float, float, float, float]:
    """归一化 cell → (x, y, w, h)。支持 [x,y,w,h] 与 {"poly": [...]}。"""
    if isinstance(cell, dict) and cell.get("poly"):
        pts = [(float(p[0]), float(p[1])) for p in cell["poly"]]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    try:
        return float(cell[0]), float(cell[1]), float(cell[2]), float(cell[3])
    except (TypeError, ValueError, IndexError):
        return 0.0, 0.0, 1.0, 1.0


def _hero_cell_index(cells: Sequence[Any]) -> int:
    best, best_area = 0, -1.0
    for i, cell in enumerate(cells):
        _x, _y, w, h = _cell_bbox(cell)
        if w * h > best_area:
            best, best_area = i, w * h
    return best


def _layout_score(layout: dict[str, Any], group: Sequence[Shot], *,
                  start_scene_new: bool, prev_layout: str = "",
                  mode: str = "hybrid", seed: int = 0,
                  style_id: str = "") -> float:
    """一页的适配度（越大越好）。"""
    cells = (stylelib.geometry(layout.get("geometry")).get("cells") or [])
    if len(cells) != len(group):
        return -99.0
    # 极端窄/宽的格子出图质量差（人物会被压扁、气泡没地方放），轻微降权
    extreme = False
    for cell in cells:
        _x, _y, cw, chh = _cell_bbox(cell)
        cell_aspect = (cw / max(1e-6, chh)) * float(
            stylelib.geometry(layout.get("geometry")).get("ratio")
            or config.PAGE_DEFAULT_RATIO)
        if cell_aspect < 0.32 or cell_aspect > 2.8:
            extreme = True
            break
    prof = _profile(group, start_scene_new)
    f = layout.get("fit") or {}
    weights = {k: max(0.08, prof.get(k, 0.0)) for k in f}
    total_w = sum(weights.values()) or 1.0
    score = sum(float(f.get(k, 0.4)) * weights[k] for k in f) / total_w
    if extreme:
        score -= 0.35

    # 画风偏好：同一页格数不变时，不同风格应选不同的版式气质。
    # 只做小幅加权，不能压过 beat/对白/场景连续性。
    prefix = str(style_id or "").split("-")[0].upper()
    if prefix in ("FB", "FE"):          # 绘本/水墨：安静、情绪、建立
        score += 0.05 * float(f.get("emotion", 0.4))
        score += 0.02 * float(f.get("establish", 0.4))
    elif prefix in ("FA", "FD"):        # 社论/日漫：动作、吐槽、悬疑
        score += 0.05 * float(f.get("action", 0.4))
        score += 0.02 * float(f.get("comedy", 0.4))
    elif prefix == "FI":                # 电影写实：悬疑、建立、情绪
        score += 0.05 * float(f.get("suspense", 0.4))
        score += 0.02 * float(f.get("establish", 0.4))
    elif prefix == "FG":                # 赛璐璐动画：动作、喜剧
        score += 0.04 * float(f.get("action", 0.4))
    elif prefix == "FF":                # 3D 材质：建立、情绪
        score += 0.04 * float(f.get("establish", 0.4))

    # 叙事功能命中：版式自带的 beats 与这一页的 beat 对上
    beats = set(layout.get("beats") or [])
    for shot in group:
        if shot.beat and shot.beat in beats:
            score += 0.035
        for kw in (layout.get("keywords") or []):
            if kw and kw in (shot.description or ""):
                score += 0.012
    score = min(score, 1.35)

    # 场景连续性：一页里塞太多场景会读不懂
    scenes = {s.scene for s in group if s.scene}
    if len(scenes) > int(getattr(config, "PAGE_MAX_SCENES_PER_PAGE", 2) or 2):
        score -= 0.8
    elif len(scenes) == 2 and float(f.get("montage", 0.4)) < 0.6:
        score -= 0.25

    # 重点格要落在大格里
    hero = _hero_cell_index(cells)
    emph = [1.0 if s.panel_emphasis == "splash" else
            0.6 if s.panel_emphasis == "main" else 0.0 for s in group]
    if emph:
        target = max(range(len(group)), key=lambda i: emph[i])
        if emph[target] > 0:
            score -= abs(hero - target) * 0.10

    # 单格台词过多而版式不适合对白 → 扣分
    avg_speech = sum(_speech_count(s) for s in group) / max(1, len(group))
    if avg_speech > 2 and float(f.get("dialogue", 0.4)) < 0.6:
        score -= 0.2
    # 物件/空镜特写塞进对白版式 → 扣分
    if any(s.object_mode for s in group) and float(f.get("dialogue", 0.4)) > 0.7:
        score -= 0.15

    # 单格大页只留给真正的重点格：没有冲击/钩子却用整页，会白白拖长篇幅
    p = len(group)
    need_splash = (group[0].panel_emphasis == "splash" or group[0].hook
                   or (p == 1 and group[0].beat in ("揭示", "情绪", "悬疑")
                       and group[0].panel_scale in ("L", "XL")))
    # 每页都有固定开销（翻页/页边）；单格页天然不划算。
    # 注意：这里不再扣固定分，改用 DP 里的"每页惩罚 × 格数加权"来平衡，
    # 否则"拆成更多页"总能靠每页基础分胜出。
    if p == 1:
        if need_splash:
            score += 0.45
        elif float(f.get("emotion", 0)) >= 0.75 or float(f.get("establish", 0)) >= 0.8:
            score -= 0.25
    else:
        score += min(p - 1, 3) * 0.40
        if p >= 3 and len(scenes) == 1 and prof.get("dialogue", 0) >= 0.3:
            score += 0.05
    if len(scenes) == 1:
        score += 0.05

    # 形态偏好：分镜页模式略偏向 SB；混排里情绪/冲击格偏向原生条漫
    native = bool(layout.get("native"))
    if mode == "page" and native and len(group) > 1:
        score -= 0.04
    if mode == "hybrid" and native and len(group) == 1 \
            and group[0].beat in ("情绪", "悬疑", "揭示", "转场"):
        score += 0.06
    if mode == "hybrid" and native and len(group) >= 2 \
            and len({s.scene for s in group if s.scene}) == 1 \
            and prof.get("dialogue", 0) >= 0.35:
        # 混排模式：同场景对白段走"原生条漫"的连续竖排，读起来更像手机条漫
        score += 0.10
    if layout.get("approx"):
        score -= 0.03

    # 连续同版式 → 收一点，避免整话一个模子
    if prev_layout and layout["id"] == prev_layout:
        score -= float(getattr(config, "PAGE_LAYOUT_REPEAT_PENALTY", 0.22) or 0.0)

    # 极小的确定性抖动，防止同一分数总选字母序在前的版式
    score += ((abs(hash(f"{seed}:{layout['id']}")) % 97) / 97.0) * 0.02
    return score


def _scene_new(shots: Sequence[Shot], i: int) -> bool:
    return i == 0 or shots[i].scene != shots[i - 1].scene


# =============================================================================
# 版式规划
# =============================================================================

def candidate_layouts(shots: Sequence[Shot], mode: str,
                      max_panels: int | None = None) -> list[dict[str, Any]]:
    limit = int(max_panels or getattr(config, "PAGE_MAX_PANELS", 6) or 6)
    lo = int(getattr(config, "PAGE_MIN_PANELS", 1) or 1)
    if mode == "webtoon":
        return [x for x in stylelib.candidate_layouts(range(lo, limit + 1)) if x.get("native")]
    # page / hybrid 都允许原生条漫版式：混排靠打分自然分流，
    # 分镜页模式也保留 WB-*，只是多扣一点分（见 _layout_score）。
    return stylelib.candidate_layouts(range(lo, limit + 1))


def plan_local(project: Project, *, mode: str | None = None) -> list[PagePlan]:
    """
    本地动态规划：在"每页格数 × 版式"上求全局最优覆盖。

    返回 PagePlan 列表（不落盘、不合成图片）。
    """
    mode = (mode or _mode(project)).lower()
    shots = list(project.shots)
    if not shots:
        return []
    annotate_shots(project)
    candidates = candidate_layouts(shots, mode)
    if not candidates:
        candidates = stylelib.candidate_layouts([1])
    n = len(shots)
    # 每个版式只保留一个代表，避免同几何多编号把 DP 撑大（名字不同但审美相近）
    by_geom: dict[str, list[dict[str, Any]]] = {}
    for lay in candidates:
        by_geom.setdefault(str(lay.get("geometry")), []).append(lay)

    dp: list[float] = [-1e9] * (n + 1)
    choice: list[dict[str, Any] | None] = [None] * (n + 1)
    dp[n] = 0.0
    seed = abs(hash(project.id)) % 10000
    page_penalty = float(getattr(config, "PAGE_PLAN_PAGE_PENALTY", 0.9) or 0.0)
    for i in range(n - 1, -1, -1):
        for group_layouts in by_geom.values():
            for lay in group_layouts:
                p = int(lay.get("panels") or 0)
                if p <= 0 or i + p > n:
                    continue
                group = shots[i:i + p]
                sc = _layout_score(lay, group, start_scene_new=_scene_new(shots, i),
                                   mode=mode, seed=seed + i,
                                   style_id=project.style_id)
                if sc <= -50:
                    continue
                # 分数按格数加权，但一页超过 4 格后边际收益递减：
                # 格子太小、气泡没地方放，观感会明显变差。
                effective_p = min(p, 4)
                extra_penalty = max(0, p - 4) * 0.18
                total = (sc * effective_p - page_penalty - extra_penalty
                         + dp[i + p])
                if total > dp[i]:
                    dp[i] = total
                    choice[i] = lay
        if choice[i] is None:
            # 兜底：用 1 格原生版式
            single = stylelib.layout_by_id("WB-001")
            if single:
                group = shots[i:i + 1]
                sc = _layout_score(single, group, start_scene_new=_scene_new(shots, i),
                                   mode=mode, seed=seed + i,
                                   style_id=project.style_id)
                choice[i] = single
                dp[i] = sc - page_penalty + dp[i + 1]

    pages: list[PagePlan] = []
    i = 0
    while i < n:
        lay = choice[i] or stylelib.layout_by_id("WB-001")
        if not lay:
            log.warning("版式数据缺失，分页在第 %d 格中止", i + 1)
            break
        p = int(lay.get("panels") or 1)
        group = shots[i:i + p]
        prev = pages[-1].layout_id if pages else ""
        # 多样性后处理：同版式连续出现时，换一个同格数、分差不多且不重复的版式
        if prev and lay["id"] == prev:
            alt = _alternative_layout(shots, i, group, mode, lay, prev, seed,
                                      style_id=project.style_id)
            if alt:
                lay = alt
        pages.append(_make_page(project, lay, group, len(pages) + 1, mode))
        i += len(group)
    return pages


def _alternative_layout(shots: Sequence[Shot], start: int, group: Sequence[Shot],
                        mode: str, current: dict[str, Any], prev: str,
                        seed: int, style_id: str = "") -> dict[str, Any] | None:
    base = _layout_score(current, group, start_scene_new=_scene_new(shots, start),
                         mode=mode, seed=seed + start, style_id=style_id)
    best: tuple[float, dict[str, Any]] | None = None
    for lay in candidate_layouts(shots, mode):
        if lay["id"] == prev or int(lay.get("panels") or 0) != len(group):
            continue
        if lay.get("geometry") == current.get("geometry"):
            continue
        sc = _layout_score(lay, group, start_scene_new=_scene_new(shots, start),
                           mode=mode, seed=seed + start, style_id=style_id)
        if sc + 0.12 < base:
            continue
        if best is None or sc > best[0]:
            best = (sc, lay)
    return best[1] if best else None


def _make_page(project: Project, layout: dict[str, Any], group: Sequence[Shot],
               index: int, mode: str) -> PagePlan:
    geom_name = str(layout.get("geometry") or "")
    geom = stylelib.geometry(geom_name)
    cells = list(geom.get("cells") or [])
    if len(cells) != len(group):
        # 几何与格数不一致时退化成等分网格，绝不让页面错位
        cells = _fallback_cells(len(group))
        geom_name = "grid-fallback"
    captions = list(geom.get("captions") or [])
    page = PagePlan(
        index=index,
        layout_id=str(layout.get("id") or "WB-001"),
        layout_name=str(layout.get("name") or ""),
        geometry=geom_name,
        mode=("webtoon" if (len(group) == 1 and layout.get("native"))
              else "single" if len(group) == 1 else "page"),
        aspect=float(geom.get("ratio") or config.PAGE_DEFAULT_RATIO),
        rationale=_rationale(layout, group),
    )
    hero = _hero_cell_index(cells)
    for i, shot in enumerate(group):
        cell = cells[i]
        x, y, w, h = _cell_bbox(cell)
        aspect = (w * page.aspect) / max(1e-6, h)
        orientation = "wide" if aspect > 1.12 else ("tall" if aspect < 0.9 else "square")
        role = "main" if i == hero else "normal"
        if geom_name == "inset-3" and i > 0:
            role = "inset"
        shot.page_id = page.id
        shot.panel_index = i
        shot.panel_role = role
        shot.panel_orientation = orientation
        if not shot.panel_emphasis:
            shot.panel_emphasis = "main" if role == "main" else "normal"
        page.shot_ids.append(shot.id)
        page.cells.append({"x": round(x, 5), "y": round(y, 5), "w": round(w, 5),
                           "h": round(h, 5), "poly": (cell.get("poly") if isinstance(cell, dict) else None),
                           "panel_index": i, "orientation": orientation, "role": role})
    if captions:
        page.captions = [dict(c) if isinstance(c, dict) else
                         {"x": c[0], "y": c[1], "w": c[2], "h": c[3]} for c in captions]
        page.caption_texts = [_caption_for(s) for s in group]
    return page


def _fallback_cells(n: int) -> list[list[float]]:
    cols = 2 if n >= 4 else 1
    rows = math.ceil(n / cols)
    cells = []
    for i in range(n):
        r, c = divmod(i, cols)
        cells.append([0.02 + c * (0.96 / cols), 0.02 + r * (0.96 / rows),
                      0.96 / cols - 0.01, 0.96 / rows - 0.01])
    return cells


def _caption_for(shot: Shot) -> str:
    """
    上图下文版式的字幕栏文案。

    优先用模型给的 caption；否则取**画面描述**首句（写"画面里发生什么"），
    最后才退回旁白。这样字幕栏与画面里的旁白气泡分工不同，不会整段重复。
    """
    if shot.caption:
        return shot.caption
    text = re.sub(r"\s+", " ", shot.description or "").strip()
    if text:
        return text[:40]
    for d in shot.dialogues:
        if d.kind == "narration" and (d.text or "").strip():
            return re.sub(r"\s+", " ", d.text.strip())[:40]
    return ""


def _rationale(layout: dict[str, Any], group: Sequence[Shot]) -> str:
    beats: list[str] = []
    for s in group:
        if s.beat and s.beat not in beats:
            beats.append(s.beat)
    fit = layout.get("fit") or {}
    strengths = sorted(fit.items(), key=lambda kv: -float(kv[1]))[:2]
    tag = {"dialogue": "对白密度", "action": "动作冲击", "emotion": "情绪停顿",
           "establish": "建立场景", "montage": "时间/空间跳接", "suspense": "悬疑张力",
           "comedy": "喜剧节奏"}
    why = "、".join(tag.get(k, k) for k, v in strengths if float(v) >= 0.6)
    return (f"{layout.get('name', layout.get('id'))}：{len(group)} 格"
            + (f"，内容偏{'/'.join(beats[:3])}" if beats else "")
            + (f"，版式强项是{why}" if why else ""))


def _mode(project: Project) -> str:
    mode = (getattr(project, "render_mode", "") or config.render_mode()).lower()
    return mode if mode in ("webtoon", "page", "hybrid") else "hybrid"


# ---------------------------------------------------------------------------
# 文本模型给的方案
# ---------------------------------------------------------------------------

def plan_from_llm(project: Project, data: Any) -> list[PagePlan] | None:
    """
    校验并采用文本模型的"分页 + 版式"方案。

    只接受能**完整、按顺序覆盖**全部分镜、且版式格数对得上的方案；
    任何一处不合法就整体放弃，交给本地规划，避免出现漏页/错序。
    """
    if not isinstance(data, dict):
        return None
    raw_pages = data.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        return None
    shots = list(project.shots)
    n = len(shots)
    used: list[int] = []
    pages: list[PagePlan] = []
    for idx, item in enumerate(raw_pages, start=1):
        if not isinstance(item, dict):
            return None
        layout = stylelib.layout_by_id(item.get("layout_id") or item.get("layout"))
        if not layout:
            log.info("AI 版式方案里的 %s 不存在，改用本地规划", item.get("layout_id"))
            return None
        nums: list[int] = []
        for key in ("shots", "shot_indices", "indices"):
            if isinstance(item.get(key), list):
                nums = [int(x) for x in item[key] if str(x).strip().isdigit()]
                break
        if not nums:
            return None
        if len(nums) != int(layout.get("panels") or 0):
            log.info("AI 方案第 %d 页 %s 需要 %s 格，给了 %d 格，改用本地规划",
                     idx, layout["id"], layout.get("panels"), len(nums))
            return None
        if used and nums[0] != used[-1] + 1:
            return None
        if nums[0] < 1 or nums[-1] > n:
            return None
        if nums != list(range(nums[0], nums[0] + len(nums))):
            return None
        used.extend(nums)
    if used != list(range(1, n + 1)):
        return None
    # 版式质量校验：AI 很容易把开头拆成一堆"满版单格页"。
    # 单格页过多 / 平均每页格数太低 / 连续 3 页单格 → 判定为不合格，退回本地规划。
    sizes = [len(item.get("shots") or item.get("shot_indices")
                 or item.get("indices") or []) for item in raw_pages]
    if n >= 8 and sizes:
        single_ratio = sum(1 for x in sizes if x == 1) / len(sizes)
        avg_panels = n / len(sizes)
        if single_ratio > 0.35 or avg_panels < 2.2:
            log.info("AI 分页过于碎片化（单格页 %d/%d，平均 %.1f 格/页），改用本地规划",
                     sum(1 for x in sizes if x == 1), len(sizes), avg_panels)
            return None
    for i in range(len(sizes) - 2):
        if sizes[i] == sizes[i + 1] == sizes[i + 2] == 1:
            log.info("AI 分页连续 3 页单格，改用本地规划")
            return None
    for idx, item in enumerate(raw_pages, start=1):
        layout = stylelib.layout_by_id(item.get("layout_id") or item.get("layout"))
        nums = [int(x) for x in (item.get("shots") or item.get("shot_indices")
                                 or item.get("indices") or [])]
        group = [shots[i - 1] for i in nums]
        page = _make_page(project, layout, group, idx, _mode(project))
        caps = item.get("captions")
        if isinstance(caps, list) and caps and page.captions:
            page.caption_texts = [re.sub(r"\s+", " ", str(c))[:48] if c else _caption_for(s)
                                  for c, s in zip(caps, group)]
        rationale = str(item.get("rationale") or "").strip()
        if rationale:
            page.rationale = f"{rationale}（AI 选版）"
        pages.append(page)
    return pages


def apply_plans(project: Project, pages: list[PagePlan], note: str = "") -> Project:
    """把规划写回工程（不改动已生成的图；分镜图仍按格缓存）。"""
    project.clear_pages()
    project.pages = pages
    # clear_pages 会清掉 shot 上的页绑定；页面方案建好后必须重新绑一次，
    # 否则出图时拿不到"这一格是什么比例 / 在第几页"。
    for page in pages:
        for i, sid in enumerate(page.shot_ids):
            shot = project.shot_by_id(sid)
            if shot is None:
                continue
            cell = page.cells[i] if i < len(page.cells) else {}
            shot.page_id = page.id
            shot.panel_index = i
            shot.panel_role = str(cell.get("role") or "normal")
            shot.panel_orientation = str(cell.get("orientation") or "")
    project.page_plan_note = note
    project.touch()
    return project


def relayout(project: Project, page: PagePlan, layout_id: str) -> PagePlan:
    """
    手动把某一页换成指定版式（格数必须一致）。

    页图会作废、需要重新合成；分镜图本身不动（每格是独立生成的）。
    """
    layout = stylelib.layout_by_id(layout_id)
    if not layout:
        raise ValueError(f"版式不存在：{layout_id}")
    if int(layout.get("panels") or 0) != page.panel_count:
        raise ValueError(f"{layout_id} 是 {layout.get('panels')} 格版式，"
                         f"这一页有 {page.panel_count} 格")
    shots = project.pages_shots(page)
    new_page = _make_page(project, layout, shots, page.index, _mode(project))
    try:
        pos = project.pages.index(page)
    except ValueError:
        pos = len(project.pages)
        project.pages.append(new_page)
        return new_page
    project.pages[pos] = new_page
    project.touch()
    return new_page


def shot_brief_lines(project: Project, limit: int = 120) -> list[str]:
    """给文本模型看的分镜摘要（一行一格）。"""
    lines: list[str] = []
    for s in project.shots[:limit]:
        speeches = [_speech_count(s), sum(1 for d in s.dialogues if d.kind == "narration")]
        desc = re.sub(r"\s+", " ", s.description or "")[:34]
        flags = []
        if s.hook:
            flags.append("章末钩子")
        if s.silent:
            flags.append("无台词")
        if s.object_mode:
            flags.append(s.object_mode)
        lines.append(
            f"{s.index}. {s.scene or '—'}｜{s.shot_type}/{s.camera or '—'}｜"
            f"beat={s.beat or '-'}｜pacing={s.pacing or '-'}｜"
            f"emph={s.panel_emphasis or '-'}｜台词{speeches[0]}旁白{speeches[1]}｜"
            f"{desc}" + (f"｜{'/'.join(flags)}" if flags else ""))
    return lines


# =============================================================================
# 合成页图
# =============================================================================

def page_canvas_size(page: PagePlan) -> tuple[int, int]:
    width = int(getattr(config, "PAGE_CANVAS_WIDTH", 1536) or 1536)
    ratio = float(page.aspect or config.PAGE_DEFAULT_RATIO) or 0.72
    height = int(round(width / ratio))
    return width, max(256, height)


def page_output_path(project: Project, page: PagePlan) -> Path:
    out_dir = Path(config.PAGE_DIR) / project.id
    out_dir.mkdir(parents=True, exist_ok=True)
    name = config.PAGE_FILENAME_TEMPLATE.format(index=page.index)
    return out_dir / f"{name}.png"


def panel_generation_size(cell: dict[str, Any], page: PagePlan) -> tuple[int, int]:
    """
    按格子在页面里的比例，推算这一格单独出图时的画幅。

    极窄/极宽的格子不按原始比例出图（模型画不了 1:5 这样的画幅），
    先把比例夹到 0.42~2.6，再在合成时用等比裁切/留边填满格子。
    """
    W, H = page_canvas_size(page)
    bw = max(1, int(round(float(cell.get("w") or 0) * W)))
    bh = max(1, int(round(float(cell.get("h") or 0) * H)))
    aspect = bw / max(1, bh)
    aspect = min(2.6, max(0.42, aspect))
    hi = int(getattr(config, "PAGE_PANEL_MAX_SIDE", 1280) or 1280)
    lo = int(getattr(config, "PAGE_PANEL_MIN_SIDE", 512) or 512)
    if aspect >= 1.0:
        w, h = hi, int(round(hi / aspect))
    else:
        w, h = int(round(hi * aspect)), hi
    if min(w, h) < lo:          # 极端比例下再兜一层
        k = lo / max(1, min(w, h))
        w, h = int(w * k), int(h * k)
    return config.normalize_image_size((max(256, w), max(256, h)))


def pages_valid(project: Project) -> bool:
    """页面规划是否仍与当前分镜一一对应（编辑/排序/合并后可能失效）。"""
    planned = [sid for page in project.pages for sid in (page.shot_ids or [])]
    return planned == [s.id for s in project.shots]


def compose_page(project: Project, page: PagePlan,
                 out_path: str | Path | None = None) -> PagePlan:
    """按版式几何把每格成品图贴成一页（不重新出图）。"""
    shots = project.pages_shots(page)
    if not shots:
        page.status = "失败"
        page.error = "该页没有分镜"
        return page
    if len(shots) != len(page.cells):
        page.status = "待生成"
        page.error = "分镜与版式格数不一致，请重新规划版式"
        return page

    # 单格原生条漫 / 单格满版页：直接复用那一格的成品，避免重采样
    if len(shots) == 1 and page.mode in ("webtoon", "single"):
        shot = shots[0]
        src = shot.image_path or shot.raw_image_path
        if src and Path(src).exists():
            page.image_path = src
            with Image.open(src) as im:
                page.width, page.height = im.size
            page.status = "已合成"
            page.error = ""
        else:
            page.status = "待生成"
            page.error = "该格还没有成品图"
        return page

    missing = [s.index for s in shots
               if not (s.image_path and Path(s.image_path).exists())]
    if missing:
        page.status = "待生成"
        page.error = "缺第 " + "、".join(str(i) for i in missing) + " 格成品图"
        return page

    W, H = page_canvas_size(page)
    canvas = Image.new("RGB", (W, H), tuple(config.PAGE_BG[:3]))
    draw = ImageDraw.Draw(canvas)
    border = bool(getattr(config, "PAGE_BORDER_ENABLED", True)) and page.mode != "webtoon"
    bw = max(1, int(getattr(config, "PAGE_BORDER_WIDTH", 4) or 4))
    border_color = tuple(config.PAGE_BORDER_COLOR[:3])
    mismatched: list[int] = []

    for i, shot in enumerate(shots):
        cell = page.cells[i] if i < len(page.cells) else {"x": 0, "y": 0, "w": 1, "h": 1}
        x = int(round(float(cell.get("x") or 0) * W))
        y = int(round(float(cell.get("y") or 0) * H))
        cw = max(8, int(round(float(cell.get("w") or 1) * W)))
        ch = max(8, int(round(float(cell.get("h") or 1) * H)))
        src = shot.image_path or shot.raw_image_path
        try:
            with Image.open(src) as im:
                source = im.convert("RGB")
                src_aspect = source.width / max(1, source.height)
                cell_aspect = cw / max(1, ch)
                ratio = max(src_aspect / cell_aspect, cell_aspect / src_aspect)
                if ratio > 1.5:
                    # 差距太大（细长条/超宽格）：只能裁切填满，否则留边会占掉半格
                    tile = ImageOps.fit(source, (cw, ch), method=Image.LANCZOS,
                                        centering=(0.5, 0.5))
                else:
                    # 轻微不一致：letterbox，绝不裁掉贴边的气泡
                    if ratio > 1.12:
                        mismatched.append(shot.index)
                        log.info("第 %s 页第 %s 格画幅比例 %0.2f 与格子 %0.2f 不一致，"
                                 "用留边方式贴合（不裁掉气泡）",
                                 page.index, i + 1, src_aspect, cell_aspect)
                    tile = ImageOps.pad(source, (cw, ch), method=Image.LANCZOS,
                                        color=tuple(config.PAGE_BG[:3]),
                                        centering=(0.5, 0.5))
        except Exception as exc:  # noqa: BLE001
            log.warning("第 %s 格读取失败：%s", shot.index, exc)
            tile = Image.new("RGB", (cw, ch), (228, 228, 232))
            d = ImageDraw.Draw(tile)
            d.text((cw // 8, ch // 3), f"{shot.index:03d} 读取失败", fill=(120, 120, 130))
        poly = cell.get("poly")
        if poly:
            mask = Image.new("L", (cw, ch), 0)
            pts = [((float(p[0]) * W) - x, (float(p[1]) * H) - y) for p in poly]
            ImageDraw.Draw(mask).polygon(pts, fill=255)
            canvas.paste(tile, (x, y), mask)
            if border:
                outline = pts + [pts[0]]
                draw.line(outline, fill=border_color, width=bw, joint="curve")
        else:
            canvas.paste(tile, (x, y))
            if border:
                draw.rectangle((x, y, x + cw - 1, y + ch - 1),
                               outline=border_color, width=bw)

    if page.captions and bool(getattr(config, "PAGE_CAPTION_ENABLED", True)):
        _draw_captions(canvas, page)

    if bool(getattr(config, "PAGE_NUMBER_ENABLED", True)):
        _draw_page_number(canvas, project, page)

    target = Path(out_path) if out_path else page_output_path(project, page)
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, format="PNG")
    page.image_path = str(target)
    page.width, page.height = canvas.size
    page.status = "已合成"
    page.error = (f"第 {'、'.join(str(i) for i in mismatched[:6])} 格的画幅与版式不一致，"
                  f"已留边贴合；重绘这几格后重拼会更贴合"
                  if mismatched else "")
    return page


def _draw_captions(canvas: Image.Image, page: PagePlan) -> None:
    W, H = canvas.size
    draw = ImageDraw.Draw(canvas)
    font_size = max(16, int(W * float(getattr(config, "PAGE_CAPTION_FONT_RATIO", 0.024))))
    font = bubble_mod.load_font(font_size, bold=True, kind="bold")
    num_font = bubble_mod.load_font(int(font_size * 0.85), bold=True, kind="bold")
    pad = int(getattr(config, "PAGE_CAPTION_PADDING", 12) or 12)
    for i, rect in enumerate(page.captions):
        if i >= len(page.shot_ids):
            break
        x = int(round(float(rect.get("x") or 0) * W))
        y = int(round(float(rect.get("y") or 0) * H))
        w = max(8, int(round(float(rect.get("w") or 0) * W)))
        h = max(8, int(round(float(rect.get("h") or 0) * H)))
        draw.rectangle((x, y, x + w, y + h), fill=tuple(config.PAGE_CAPTION_BG[:3]))
        draw.line((x, y, x + w, y), fill=(210, 208, 200), width=2)
        text = (page.caption_texts[i] if i < len(page.caption_texts) else "").strip()
        if not text:
            continue
        # 字幕栏只有 20% 行高：字号按栏高收紧，放不下就截断，绝不压到画面
        size = min(font_size, max(14, int(h * 0.34)))
        f = bubble_mod.load_font(size, bold=True, kind="bold") if size != font_size else font
        cursor = x + pad
        if bool(getattr(config, "PAGE_CAPTION_NUMBER", True)):
            num = f"{i + 1:02d}"
            nf = num_font if size == font_size else bubble_mod.load_font(
                max(12, int(size * 0.85)), bold=True, kind="bold")
            draw.text((cursor, y + max(2, (h - int(size * 0.9)) // 2)), num,
                      font=nf, fill=tuple(config.PAGE_CAPTION_TEXT[:3]))
            cursor += bubble_mod.measure(num, nf)[0] + pad
        lines = bubble_mod.wrap_text(text, f, max(40, x + w - pad - cursor))
        line_h = max(1, int(size * 1.30))
        max_lines = max(1, (h - 4) // line_h)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            if lines:
                lines[-1] = lines[-1][:-1] + "…"
        text_h = line_h * len(lines)
        ty = y + max(2, (h - text_h) // 2)
        for line in lines:
            draw.text((cursor, ty), line, font=f,
                      fill=tuple(config.PAGE_CAPTION_TEXT[:3]))
            ty += line_h


def _draw_page_number(canvas: Image.Image, project: Project, page: PagePlan) -> None:
    W, H = canvas.size
    size = max(14, int(W * float(getattr(config, "PAGE_NUMBER_FONT_RATIO", 0.018))))
    bottom = max((float(c.get("y") or 0) + float(c.get("h") or 0) for c in page.cells),
                 default=1.0)
    bottom_px = int(round(bottom * H))
    if H - bottom_px < size + 12:
        return          # 满版页没有页边，不把页码压到画面上
    draw = ImageDraw.Draw(canvas)
    font = bubble_mod.load_font(size, bold=True, kind="bold")
    text = f"第 {project.chapter_no or '01'} 话 · {page.index:02d}"
    tw = bubble_mod.measure(text, font)[0]
    x = W - tw - int(W * 0.02)
    y = bottom_px + max(0, (H - bottom_px - size) // 2)
    if y > 0:
        draw.text((x, y), text, font=font, fill=(120, 118, 112))


def _page_stale(project: Project, page: PagePlan) -> bool:
    """页图是否比它的分镜成品旧（重画过某格就要重拼这一页）。"""
    if not page.image_path or not Path(page.image_path).exists():
        return True
    if page.status != "已合成":
        return True
    try:
        page_mtime = Path(page.image_path).stat().st_mtime
    except OSError:
        return True
    for shot in project.pages_shots(page):
        src = shot.image_path or shot.raw_image_path
        if src and Path(src).exists() and Path(src).stat().st_mtime > page_mtime + 0.5:
            return True
    return False


def compose_all_pages(project: Project, *, only_pending: bool = True,
                      progress: ProgressCb | None = None) -> dict[str, int]:
    """把已出完图的页面全部合成（缺图的页跳过，等补图后再合）。"""
    stats = {"pages": 0, "done": 0, "pending": 0, "failed": 0}
    if project.pages and not pages_valid(project):
        # 分镜被增删/排序过：旧页作废，按新分镜就地重排（不调模型）
        try:
            pages = plan_local(project, mode=_mode(project))
            apply_plans(project, pages, "分镜已变更，自动重排版式")
            log.info("分镜已变更，页面按新分镜重排为 %d 页", len(pages))
        except Exception as exc:  # noqa: BLE001
            log.warning("分镜变更后重排页面失败：%s", exc)
    for page in project.pages:
        stats["pages"] += 1
        if only_pending and not _page_stale(project, page):
            stats["done"] += 1
            continue
        try:
            if progress:
                progress(f"正在合成第 {page.index} 页（{page.layout_name}）…")
            compose_page(project, page)
        except Exception as exc:  # noqa: BLE001
            page.status = "失败"
            page.error = str(exc)[:200]
            log.warning("第 %s 页合成失败：%s", page.index, exc)
        if page.status == "已合成":
            stats["done"] += 1
        elif page.status == "失败":
            stats["failed"] += 1
        else:
            stats["pending"] += 1
    return stats


def strip_tiles(project: Project) -> list[str]:
    """
    导出条漫/PDF 时用的图块顺序。

    - 有页面成品：用页图；没合成的页跳过（避免半页混进长条）
    - 未被任何页覆盖的分镜：用单格成品
    - 页面规划与分镜对不上时：退回单格图，绝不把错页拼进成品
    """
    if project.pages and not pages_valid(project):
        log.warning("页面规划与当前分镜不一致，本次按单格图拼接")
        covers: list[str] = []
        for shot in project.shots:
            path = shot.image_path or shot.raw_image_path
            if path and Path(path).exists():
                covers.append(path)
        return covers
    items: list[tuple[int, int, str]] = []
    covered: set[str] = set()
    for page in project.pages:
        covered.update(page.shot_ids or [])
        if page.image_path and Path(page.image_path).exists():
            first = project.shot_by_id(page.shot_ids[0]) if page.shot_ids else None
            items.append((first.index if first else page.index, page.index, page.image_path))
    for shot in project.shots:
        if shot.id in covered:
            continue
        path = shot.image_path or shot.raw_image_path
        if path and Path(path).exists():
            items.append((shot.index, 0, path))
    items.sort(key=lambda x: (x[0], x[1]))
    return [p for _, _, p in items]


def page_plan_payload(project: Project) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in project.pages:
        image_ok = bool(page.image_path and Path(page.image_path).exists())
        out.append({
            "id": page.id,
            "index": page.index,
            "layout_id": page.layout_id,
            "layout_name": page.layout_name,
            "geometry": page.geometry,
            "mode": page.mode,
            "panels": page.panel_count,
            "shot_indices": page.shot_indices(project),
            "shot_ids": list(page.shot_ids),
            "cells": page.cells,
            "captions": page.caption_texts,
            "rationale": page.rationale,
            "aspect": page.aspect,
            "image_path": page.image_path if image_ok else "",
            "width": page.width if image_ok else 0,
            "height": page.height if image_ok else 0,
            "status": page.status if image_ok else "待生成",
            "error": page.error if image_ok else "页图不存在，请重新合成",
        })
    return out


def summary(project: Project) -> dict[str, Any]:
    layouts: dict[str, int] = {}
    for page in project.pages:
        layouts[page.layout_id] = layouts.get(page.layout_id, 0) + 1
    return {
        "mode": _mode(project),
        "pages": len(project.pages),
        "panels": sum(p.panel_count for p in project.pages),
        "composed": sum(1 for p in project.pages
                        if p.status == "已合成" and p.image_path and Path(p.image_path).exists()),
        "layouts": layouts,
        "note": project.page_plan_note,
    }
