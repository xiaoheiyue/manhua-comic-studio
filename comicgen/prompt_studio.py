# -*- coding: utf-8 -*-
"""界面里的「提示词工作台」：把 prompts/ 下的提示词按步骤渲染成当前工程版本。

设计目标：
* 每个提示词都能用**当前工程数据**实时渲染，而不是只看 prompts/ 里的固定样例；
* 用户可以在界面上改 system / user / 图像提示词，保存到 Project.prompt_overrides；
* 真正生成时由 prompt_overrides 把对应文本替换掉。

这里只做“渲染 + 目录 + 读写覆盖”，不依赖 pipeline，避免循环导入。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

import config
from . import bubble, pagelayout, prompts, prompt_overrides, stylelib
from .models import Character, Dialogue, Project, Shot
from .textutil import chunk_with_offsets, normalize_novel, truncate


STEPS: dict[str, dict[str, str]] = {
    "2": {"title": "内容分析", "bar": "小说整体分析 / 分段合并 / 绘制风格推荐"},
    "3": {"title": "角色三视图与俯视图", "bar": "角色人设 · 撞车重写"},
    "4": {"title": "分镜拆解", "bar": "分镜拆解 / 合并 / 导演 / 旁白 / 分页"},
    "7": {"title": "出图与打包", "bar": "分镜出图 / 气泡指令 / 返修规则"},
}

_PLACEHOLDER_TEXT = (
    "（当前工程还没有输入小说文本。下面用占位文本展示提示词结构，"
    "输入正文后会自动替换成你的内容。）"
)

_FALLBACK_DESIGN = (
    "（当前工程还没有生成角色人设。这里用占位角色展示提示词结构，"
    "完成内容分析与角色生成后会自动替换。）"
)


# =============================================================================
# 工程数据准备
# =============================================================================

def _analysis_text(project: Project) -> str:
    text = normalize_novel(project.novel_text or "")
    if not text.strip():
        return _PLACEHOLDER_TEXT
    return text[:int(getattr(config, "MAX_NOVEL_LENGTH", 0) or len(text))]


def _analysis_chunks(project: Project) -> tuple[str, list[str]]:
    text = _analysis_text(project)
    chunks = [c for _, c in chunk_with_offsets(text)]
    return text, (chunks or [text])


def _storyboard_batches(project: Project) -> tuple[str, list[tuple[str, str]]]:
    text = _analysis_text(project)
    whole_limit = int(getattr(config, "STORYBOARD_WHOLE_CHAPTER_CHARS", 24000) or 24000)
    if len(text) <= whole_limit:
        return text, [("整章", text)]
    chunks = [c for _, c in chunk_with_offsets(
        text, chunk_size=int(getattr(config, "STORYBOARD_CHUNK_SIZE", 9000) or 9000))]
    batch_size = max(1, int(getattr(config, "STORYBOARD_CHUNK_BATCH", 1) or 1))
    groups = [chunks[i:i + batch_size] for i in range(0, len(chunks), batch_size)]
    out = [(f"第 {i}/{len(groups)} 批", "\n\n".join(g))
           for i, g in enumerate(groups, start=1)]
    return text, (out or [("整章", text)])


def _fallback_character(project: Project) -> Character:
    return Character(
        id="preview", name="主角", gender="未知", age="青年",
        identity="故事主角", personality="冷静克制", role="主角",
        design=_FALLBACK_DESIGN, keywords=["主角", "青年"],
        identity_tag="待定",
    )


def _fallback_shot(project: Project) -> Shot:
    names = project.character_names()
    name = names[0] if names else "主角"
    scene = project.scene_names()[0] if project.scene_names() else "主要场景"
    shot = Shot(
        id="preview", index=1, shot_type="中景", camera="平视",
        composition="三分构图", lighting="侧光", panel_scale="M",
        characters=[name], char_sides={name: "左"},
        scene=scene, time_of_day="夜晚",
        action="人物停在门口，回头看向画面外",
        expression="神情克制", focus="手里的关键道具",
        description="人物在画面左侧停步，前景是半开的门，后景是昏暗的走廊；"
                    "一束冷光从侧面打进来，桌面上的关键物件被照亮。",
        mood="悬疑",
        dialogues=[Dialogue(speaker=name, text="……", kind="speech", tone="平静")],
    )
    _ensure_bubble_plan(shot)
    return shot


def _pick_character(project: Project, item: str) -> tuple[Character, bool]:
    cid = item.split(":", 1)[1] if ":" in item else ""
    for c in project.characters:
        if c.id == cid:
            return c, True
    return _fallback_character(project), False


def _pick_shot(project: Project, item: str) -> tuple[Shot, bool]:
    sid = item.split(":", 1)[1] if ":" in item else ""
    for s in project.shots:
        if s.id == sid:
            # 预览可能补算 bubble_plan，用副本避免改动真实工程
            return copy.deepcopy(s), True
    return _fallback_shot(project), False


def _ensure_bubble_plan(shot: Shot) -> None:
    if shot.bubble_plan:
        return
    try:
        width, height = shot.panel_size()
        shot.bubble_plan = bubble.plan_layout(shot, width, height)
    except Exception:  # noqa: BLE001
        shot.bubble_plan = []


def _cast_for(project: Project, shot: Shot) -> list[dict[str, str]]:
    cast: list[dict[str, str]] = []
    names = list(shot.characters or [])
    if not names:
        names = project.character_names()[:1] or ["主角"]
    for name in names:
        c = project.find_character(name)
        if c is None:
            c = _fallback_character(project)
        cast.append({
            "name": c.name,
            "card": c.identity_card(),
            "side": (shot.char_sides or {}).get(c.name, ""),
            "position": (shot.char_positions or {}).get(c.name, ""),
        })
    return cast


def _partial_from_project(project: Project) -> dict[str, Any]:
    return {
        "summary": project.summary or "（当前工程还没有故事梗概）",
        "plotline": project.plotline or "",
        "key_points": list(project.key_points or []),
        "style": project.style,
        "style_detail": project.style_detail,
        "characters": [c.to_dict() for c in project.characters],
        "scenes": [s.to_dict() for s in project.scenes],
    }


# =============================================================================
# 各提示词的渲染器
# =============================================================================

def _render_analysis_overview(project: Project, item: str) -> dict[str, Any]:
    _text, chunks = _analysis_chunks(project)
    idx = _item_index(item, "chunk:", 1) - 1
    idx = max(0, min(idx, len(chunks) - 1))
    return {"messages": prompts.novel_overview_prompt(chunks[idx], idx + 1, len(chunks))}


def _render_analysis_merge(project: Project, item: str) -> dict[str, Any]:
    return {"messages": prompts.merge_analysis_prompt(
        [_partial_from_project(project)],
        (project.novel_text or "")[:1500] or _PLACEHOLDER_TEXT,
    )}


def _render_style_recommend(project: Project, item: str) -> dict[str, Any]:
    story = " ".join(str(x) for x in (
        project.summary, project.plotline, project.style_detail,
        (project.novel_text or "")[:600]) if x)
    shortlist = stylelib.shortlist_style_ids(story or project.title or "漫画", 12)
    return {"messages": prompts.style_recommend_prompt(
        project, shortlist, stylelib.color_brief_table())}


def _render_character_design(project: Project, item: str) -> dict[str, Any]:
    character, _ = _pick_character(project, item)
    return {"messages": prompts.character_design_prompt(character, project)}


def _render_character_distinct(project: Project, item: str) -> dict[str, Any]:
    character, _ = _pick_character(project, item)
    others = [c for c in project.characters if c.id != character.id]
    collisions = []
    if others:
        other = others[0]
        collisions = [{"other": other.name, "shared_labels": ["脸型", "发型"]}]
    return {"messages": prompts.character_distinct_rewrite_prompt(
        character, project, collisions)}


def _render_storyboard(project: Project, item: str) -> dict[str, Any]:
    _text, batches = _storyboard_batches(project)
    idx = _item_index(item, "batch:", 1) - 1
    idx = max(0, min(idx, len(batches) - 1))
    label, merged = batches[idx]
    target = max(6, min(60, int(round(len(merged) / 90))))
    return {"messages": prompts.storyboard_prompt(
        project, merged, 1, label, target_shots=target)}


def _render_storyboard_merge(project: Project, item: str) -> dict[str, Any]:
    shots_json = [s.to_dict() for s in project.shots] or [_fallback_shot(project).to_dict()]
    return {"messages": prompts.storyboard_merge_prompt(shots_json, project)}


def _render_dialogue_split(project: Project, item: str) -> dict[str, Any]:
    shots_json = [s.to_dict() for s in project.shots] or [_fallback_shot(project).to_dict()]
    return {"messages": prompts.storyboard_dialogue_split_prompt(
        project, shots_json, int(getattr(config, "MAX_BUBBLES_PER_SHOT", 3) or 3))}


def _render_shot_fix(project: Project, item: str) -> dict[str, Any]:
    shot, _ = _pick_shot(project, item)
    return {"messages": prompts.shot_fix_prompt(shot, project)}


def _render_director_pass(project: Project, item: str) -> dict[str, Any]:
    shots_json = [s.to_dict() for s in project.shots] or [_fallback_shot(project).to_dict()]
    return {"messages": prompts.director_pass_prompt(
        project, shots_json, (project.novel_text or "")[:1200] or _PLACEHOLDER_TEXT)}


def _render_panel_repair(project: Project, item: str) -> dict[str, Any]:
    shot, _ = _pick_shot(project, item)
    return {"messages": prompts.panel_repair_prompt(
        project, shot.to_dict(), ["描述过短", "说话人不在画面"])}


def _render_narration(project: Project, item: str) -> dict[str, Any]:
    shots_json = [s.to_dict() for s in project.shots] or [_fallback_shot(project).to_dict()]
    return {"messages": prompts.narration_fill_prompt(
        project, shots_json, int(getattr(config, "STORYBOARD_NARRATION_MAX_CHARS", 24) or 24))}


def _render_page_plan(project: Project, item: str) -> dict[str, Any]:
    shot_lines = list(pagelayout.shot_brief_lines(project) or [])
    if not shot_lines:
        shot_lines = ["（当前工程还没有分镜，以下是空白预览）"]
    return {"messages": prompts.page_plan_prompt(
        project, stylelib.layout_brief_table(16), shot_lines)}


def _render_image_prompt(project: Project, item: str) -> dict[str, Any]:
    shot, _ = _pick_shot(project, item)
    _ensure_bubble_plan(shot)
    cast = _cast_for(project, shot)
    text = prompts.shot_image_prompt(
        project, shot, cast=cast,
        include_bubbles=bool(shot.bubble_plan),
        draw_bubbles=bool(getattr(config, "BUBBLE_ENABLED_BY_DEFAULT", True)),
    )
    return {"text": text}


def _render_bubble_block(project: Project, item: str) -> dict[str, Any]:
    shot, _ = _pick_shot(project, item)
    _ensure_bubble_plan(shot)
    names = list(shot.characters or project.character_names() or ["主角"])
    return {"text": prompts._bubble_block(
        project, shot, names,
        draw_bubbles=bool(getattr(config, "BUBBLE_ENABLED_BY_DEFAULT", True)))}


def _render_reference(project: Project, item: str) -> dict[str, Any]:
    # 真正的正文由 detail() 从 prompts/ 文件读取；这里只需要一个占位结构。
    return {"text": ""}


def _item_index(item: str, prefix: str, default: int) -> int:
    if item.startswith(prefix):
        raw = item[len(prefix):]
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    return default


# =============================================================================
# 提示词目录
# =============================================================================

ENTRIES: list[dict[str, Any]] = [
    {
        "id": "analysis.overview", "step": "2", "stage": "01_analysis",
        "file": "prompts/01_analysis/01_novel_overview.md",
        "title": "小说整体分析", "source": "prompts.novel_overview_prompt",
        "purpose": "一次读一段小说，识别角色 / 场景 / 情节 / 画风。",
        "vars": "novel（本段正文）、chunk_index、chunk_total",
        "scope": "analysis_chunk", "kind": "messages", "applied": True,
        "render": _render_analysis_overview,
    },
    {
        "id": "analysis.merge", "step": "2", "stage": "01_analysis",
        "file": "prompts/01_analysis/02_merge_analysis.md",
        "title": "分段分析合并", "source": "prompts.merge_analysis_prompt",
        "purpose": "把多段分析合并去重成一份完整设定。",
        "vars": "partials（分段结果）、novel_head",
        "scope": "global", "kind": "messages", "applied": True,
        "render": _render_analysis_merge,
    },
    {
        "id": "style.recommend", "step": "2", "stage": "09_style",
        "file": "prompts/09_style/01_recommend.md",
        "title": "绘制风格推荐", "source": "prompts.style_recommend_prompt",
        "purpose": "从 365 种 handraw 画风里收敛候选，再结合题材 / 情绪 / 时代选一套风格 + 主题色。",
        "vars": "project、shortlist、colors_table",
        "scope": "global", "kind": "messages", "applied": True,
        "render": _render_style_recommend,
    },
    {
        "id": "character.design", "step": "3", "stage": "02_characters",
        "file": "prompts/02_characters/01_character_design.md",
        "title": "角色人设生成", "source": "prompts.character_design_prompt",
        "purpose": "为单个角色生成人设、硬特征、唯一记忆点与关键词。",
        "vars": "character（分析结果）、project（已有角色清单）",
        "scope": "character", "kind": "messages", "applied": True,
        "render": _render_character_design,
    },
    {
        "id": "character.distinct", "step": "3", "stage": "02_characters",
        "file": "prompts/02_characters/02_distinct_rewrite.md",
        "title": "角色撞车重写", "source": "prompts.character_distinct_rewrite_prompt",
        "purpose": "两个角色过于相似时，定向重写后出现的那个。",
        "vars": "character、project、collisions",
        "scope": "character", "kind": "messages", "applied": True,
        "render": _render_character_distinct,
    },
    {
        "id": "storyboard.decompose", "step": "4", "stage": "03_storyboard",
        "file": "prompts/03_storyboard/01_decompose.md",
        "title": "分镜拆解", "source": "prompts.storyboard_prompt",
        "purpose": "把一段小说拆成可拍的镜头脚本（一格一个 beat）。",
        "vars": "project、chunk_text、start_index、chunk_label、target_shots",
        "scope": "storyboard_batch", "kind": "messages", "applied": True,
        "render": _render_storyboard,
    },
    {
        "id": "storyboard.merge", "step": "4", "stage": "03_storyboard",
        "file": "prompts/03_storyboard/02_merge.md",
        "title": "分镜合并去重", "source": "prompts.storyboard_merge_prompt",
        "purpose": "合并分段拆解的结果，补全字段、去重、保持剧情顺序。",
        "vars": "shots_json、project",
        "scope": "global", "kind": "messages", "applied": True,
        "render": _render_storyboard_merge,
    },
    {
        "id": "storyboard.dialogue_split", "step": "4", "stage": "03_storyboard",
        "file": "prompts/03_storyboard/03_dialogue_split.md",
        "title": "对话拆格", "source": "prompts.storyboard_dialogue_split_prompt",
        "purpose": "一格台词过多时拆成多格（说话人 / 听者 / 动作交替）。",
        "vars": "shots_json、max_per_shot",
        "scope": "global", "kind": "messages", "applied": False,
        "render": _render_dialogue_split,
    },
    {
        "id": "storyboard.shot_fix", "step": "4", "stage": "03_storyboard",
        "file": "prompts/03_storyboard/04_shot_fix.md",
        "title": "单条分镜补全", "source": "prompts.shot_fix_prompt",
        "purpose": "补齐一条分镜缺失的镜头字段（本地兜底后修复）。",
        "vars": "shot、project",
        "scope": "shot", "kind": "messages", "applied": False,
        "render": _render_shot_fix,
    },
    {
        "id": "director.pass", "step": "4", "stage": "04_director",
        "file": "prompts/04_director/01_pass.md",
        "title": "导演重拍", "source": "prompts.director_pass_prompt",
        "purpose": "把已有分镜重写成可拍镜头：补机位 / 构图 / 光线 / 画幅，剔掉画外音误入画面。",
        "vars": "shots_json、novel_excerpt",
        "scope": "global", "kind": "messages", "applied": True,
        "render": _render_director_pass,
    },
    {
        "id": "director.panel_repair", "step": "4", "stage": "04_director",
        "file": "prompts/04_director/02_panel_repair.md",
        "title": "单格修镜头", "source": "prompts.panel_repair_prompt",
        "purpose": "自检不过关时只重写这一格。",
        "vars": "shot_json、issues",
        "scope": "shot", "kind": "messages", "applied": False,
        "render": _render_panel_repair,
    },
    {
        "id": "narration.fill", "step": "4", "stage": "05_narration",
        "file": "prompts/05_narration/01_fill.md",
        "title": "旁白补全", "source": "prompts.narration_fill_prompt",
        "purpose": "为需要旁白的格子补写旁白（时空 / 内心 / 因果）。",
        "vars": "shots_json、max_chars",
        "scope": "global", "kind": "messages", "applied": True,
        "render": _render_narration,
    },
    {
        "id": "layout.page_plan", "step": "4", "stage": "10_layout",
        "file": "prompts/10_layout/01_page_plan.md",
        "title": "分页与版式规划", "source": "prompts.page_plan_prompt",
        "purpose": "把连续分镜编成漫画页，并逐页选择 SB-* / WB-* 版式（格数必须匹配）。",
        "vars": "project、layouts_table、shot_lines",
        "scope": "global", "kind": "messages", "applied": True,
        "render": _render_page_plan,
    },
    {
        "id": "image.shot_prompt", "step": "7", "stage": "06_image",
        "file": "prompts/06_image/01_shot_prompt.md",
        "title": "分镜出图提示词", "source": "prompts.shot_image_prompt",
        "purpose": "组装一张分镜图的最终提示词：人数 → 身份卡 → 说话人 → 镜头 → 画面内容 → 气泡坐标 → 收尾。",
        "vars": "project、shot、cast、ref_names（气泡坐标来自 shot.bubble_plan）",
        "scope": "shot", "kind": "text", "applied": True,
        "render": _render_image_prompt,
    },
    {
        "id": "bubble.block", "step": "7", "stage": "07_bubble",
        "file": "prompts/07_bubble/01_bubble_prompt.md",
        "title": "气泡绘制指令", "source": "prompts._bubble_block",
        "purpose": "把每个气泡的槽位、外接框、形状、尾巴、内部留白写清楚，让模型按坐标画。",
        "vars": "shot.bubble_plan（x/y/w/h/slot/speaker/tail）",
        "scope": "shot", "kind": "text", "applied": True,
        "render": _render_bubble_block,
    },
    {
        "id": "bubble.detection", "step": "7", "stage": "07_bubble",
        "file": "prompts/07_bubble/02_detection_fill.md",
        "title": "气泡定位与回填细则", "source": "comicgen/bubble.py",
        "purpose": "出图后定位空白气泡、回填文字、处理兜底与返修的规则说明。",
        "vars": "规则文档（不直接作为模型提示词发送）",
        "scope": "global", "kind": "reference", "applied": False,
        "render": _render_reference,
    },
    {
        "id": "repair.rules", "step": "7", "stage": "08_repair",
        "file": "prompts/08_repair/01_repair_rules.md",
        "title": "返修规则对照表", "source": "comicgen/quality.py",
        "purpose": "问题 → 扣分 → 追加提示词的对照表，供出图自检与返修查阅。",
        "vars": "规则文档（不直接作为模型提示词发送）",
        "scope": "global", "kind": "reference", "applied": False,
        "render": _render_reference,
    },
]

_ENTRY_BY_ID: dict[str, dict[str, Any]] = {e["id"]: e for e in ENTRIES}


# =============================================================================
# 目录 / 详情 / 保存
# =============================================================================

def _read_reference(entry: dict[str, Any]) -> str:
    path = Path(config.BASE_DIR) / entry["file"]
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"（读取规则文档失败：{exc}）"


def _entry_items(project: Project, entry: dict[str, Any]) -> list[dict[str, Any]]:
    scope = entry["scope"]
    if scope == "analysis_chunk":
        text, chunks = _analysis_chunks(project)
        savable = bool(normalize_novel(project.novel_text or "").strip())
        label_prefix = "第" if savable else "示例文段"
        items = []
        for i, chunk in enumerate(chunks, start=1):
            label = (f"第 {i}/{len(chunks)} 段 · {len(chunk)} 字" if savable
                     else f"{label_prefix} · {len(chunk)} 字（当前工程暂无正文）")
            items.append({"id": f"chunk:{i}", "label": label, "savable": savable})
        return items
    if scope == "storyboard_batch":
        _text, batches = _storyboard_batches(project)
        savable = bool(normalize_novel(project.novel_text or "").strip())
        items = []
        for i, (label, chunk) in enumerate(batches, start=1):
            suffix = "" if savable else "（当前工程暂无正文）"
            items.append({"id": f"batch:{i}",
                          "label": f"{label} · {len(chunk)} 字{suffix}",
                          "savable": savable})
        return items
    if scope == "character":
        if not project.characters:
            return [{"id": "character:preview", "label": "示例角色（当前工程暂无角色）",
                     "savable": False}]
        return [{"id": f"character:{c.id}", "label": c.name or "未命名角色",
                 "savable": True} for c in project.characters]
    if scope == "shot":
        if not project.shots:
            return [{"id": "shot:preview", "label": "示例分镜（当前工程暂无分镜）",
                     "savable": False}]
        return [{"id": f"shot:{s.id}",
                 "label": f"第 {s.index} 格 · {truncate(s.description or s.action or '未填写描述', 20)}",
                 "savable": True} for s in project.shots]
    return [{"id": "global", "label": "当前工程", "savable": True}]


def _decorate_items(project: Project, entry: dict[str, Any],
                    items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for item in items:
        out.append({
            **item,
            "customized": prompt_overrides.get(project, entry["id"], item["id"]) is not None,
        })
    return out


def _entry_public(project: Project, entry: dict[str, Any]) -> dict[str, Any]:
    items = _decorate_items(project, entry, _entry_items(project, entry))
    return {
        "id": entry["id"],
        "step": entry["step"],
        "stage": entry["stage"],
        "file": entry["file"],
        "title": entry["title"],
        "source": entry["source"],
        "purpose": entry["purpose"],
        "vars": entry["vars"],
        "scope": entry["scope"],
        "kind": entry["kind"],
        "applied": entry["applied"],
        "items": items,
        "customized": sum(1 for i in items if i["customized"]),
    }


def catalog(project: Project, step: str) -> dict[str, Any]:
    step = str(step)
    if step not in STEPS:
        raise KeyError(f"未知步骤：{step}")
    entries = [_entry_public(project, e) for e in ENTRIES if e["step"] == step]
    return {"step": step, **STEPS[step], "entries": entries}


def overview(project: Project) -> list[dict[str, Any]]:
    out = []
    for step, meta in STEPS.items():
        entries = [e for e in ENTRIES if e["step"] == step]
        out.append({
            "step": step,
            **meta,
            "entries": len(entries),
            "customized": sum(prompt_overrides.count(project, e["id"]) for e in entries),
        })
    return out


def _default_payload(project: Project, entry: dict[str, Any], item: str) -> dict[str, Any]:
    try:
        rendered = entry["render"](project, item)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"渲染失败：{exc}", "messages": [], "text": ""}
    if not isinstance(rendered, dict):
        return {"error": "渲染器返回结构异常", "messages": [], "text": ""}
    return rendered


def detail(project: Project, prompt_id: str, item: str = "") -> dict[str, Any]:
    entry = _ENTRY_BY_ID.get(str(prompt_id))
    if entry is None:
        raise KeyError(f"未知提示词：{prompt_id}")
    items = _decorate_items(project, entry, _entry_items(project, entry))
    item_id = str(item or "").strip()
    if not item_id:
        item_id = items[0]["id"]
    item_info = next((i for i in items if i["id"] == item_id), None)
    if item_info is None:
        raise KeyError(f"未知提示词对象：{item_id}")

    default = _default_payload(project, entry, item_id)
    override = prompt_overrides.get(project, entry["id"], item_id)
    kind = entry["kind"]

    system = ""
    user = ""
    text = ""
    if kind == "messages":
        messages = default.get("messages") or []
        system = "\n\n".join(str(m.get("content", ""))
                             for m in messages if m.get("role") == "system")
        user = "\n\n".join(str(m.get("content", ""))
                           for m in messages if m.get("role") == "user")
        if override:
            system = override.get("system", system) if "system" in override else system
            user = override.get("user", user) if "user" in override else user
    elif kind == "reference":
        text = override.get("text", "") if override else ""
        if not text:
            text = _read_reference(entry)
    else:
        text = str(default.get("text") or "")
        if override:
            text = override.get("text", text) if "text" in override else text

    return {
        "entry": _entry_public(project, entry),
        "item": item_id,
        "item_label": item_info["label"],
        "savable": bool(item_info["savable"]),
        "override": override is not None,
        "updated_at": (override or {}).get("updated_at", ""),
        "system": system,
        "user": user,
        "text": text,
        "error": default.get("error", ""),
    }


def save(project: Project, prompt_id: str, item: str, *,
         system: str | None = None, user: str | None = None,
         text: str | None = None) -> dict[str, Any]:
    entry = _ENTRY_BY_ID.get(str(prompt_id))
    if entry is None:
        raise KeyError(f"未知提示词：{prompt_id}")
    items = _entry_items(project, entry)
    item_id = str(item or "").strip() or items[0]["id"]
    item_info = next((i for i in items if i["id"] == item_id), None)
    if item_info is None:
        raise ValueError(f"未知提示词对象：{item_id}")
    if not item_info.get("savable"):
        raise ValueError("当前工程还没有对应的正文 / 角色 / 分镜，先完成上一步再编辑这条提示词")

    kind = entry["kind"]
    if kind == "messages":
        user_text = str(user or "")
        if not user_text.strip():
            raise ValueError("User 提示词不能为空")
        prompt_overrides.set_override(
            project, entry["id"], item_id,
            system="" if system is None else str(system),
            user=user_text,
        )
    else:
        text_value = str(text or "")
        if not text_value.strip():
            raise ValueError("提示词不能为空")
        prompt_overrides.set_override(project, entry["id"], item_id, text=text_value)
    return detail(project, entry["id"], item_id)


def reset(project: Project, prompt_id: str, item: str = "") -> dict[str, Any]:
    entry = _ENTRY_BY_ID.get(str(prompt_id))
    if entry is None:
        raise KeyError(f"未知提示词：{prompt_id}")
    items = _entry_items(project, entry)
    item_id = str(item or "").strip() or items[0]["id"]
    prompt_overrides.clear(project, entry["id"], item_id)
    return detail(project, entry["id"], item_id)
