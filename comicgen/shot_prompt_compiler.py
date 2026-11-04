# -*- coding: utf-8 -*-
"""分镜 JSON → 观察式视觉 prompt 的翻译层。

这一层只做三件事：
1. 把 ``shot/angle/panel_orientation/panel_role/scene/time/light`` 展开成
   Qwen-Image 文本编码器第一眼就能抓住的画面框架（首句定框架）；
2. 把情绪/抽象词翻译成可见的空间、光影、动作，并按景别裁剪身份锁的详略；
3. 给出可核对的预算/风险提示，供 ``prompts.shot_image_prompt`` 决定保留或回退。

不修改 ``Shot`` 的结构字段，不生成 negative，不新增人设里没有的特征。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import config
from .logger import get_logger

log = get_logger(__name__)

_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_MAX = 128

_ORIENT = {"wide": "横向", "tall": "竖向", "square": "方形"}
_ROLE = {"main": "主格", "inset": "嵌套小格", "normal": "常规分格"}

# 景别分级：决定身份锁的详略与预算。
#   establishing = 远景/大远景/建立镜头：只保留轮廓级特征
#   wide         = 全景：年龄/体型/发型轮廓/服装主色
#   full         = 全身：加服装层次
#   medium       = 中景：加脸型/发型细节
#   close        = 近景：加眼型/表情，压缩全身服装
#   extreme      = 大特写：全部记忆点
_LEVEL_ALIASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("大远景", "远景", "建立", "全景"), "wide"),
    (("全身", "七分身"), "full"),
    (("中景", "半身"), "medium"),
    (("大特写",), "extreme"),
    (("特写", "近景"), "close"),
)

# 抽象语义 → 可见元素。只替换正向描述，不碰结构字段。
# 长词优先，避免"看客的笑"先被"笑"命中。
_DEFAULT_ABSTRACT: tuple[tuple[str, str], ...] = (
    ("看客的笑", "两三个半身人物、嘴角上扬但眼神避开主体、位于画面边缘"),
    ("哄笑", "两三个半身人物、嘴角上扬、身体朝主体倾斜、位于画面边缘"),
    ("嘲弄", "嘴角上扬、眼神斜向主体、身体前倾、位于画面边缘"),
    ("静默反应", "听者半身、视线看向画外、背景虚化"),
    ("命运转折", "前景与背景分割、主体在画面下三分之一、上方大面积空墙或天空"),
    ("悲凉", "冷灰低饱和、人物低头或背对、光源偏侧、大面积留白"),
    ("凄凉", "冷灰低饱和、人物缩肩、背景空旷、侧光"),
    ("萧瑟", "冷灰低饱和、空旷环境、落叶或风痕、侧光"),
    ("冷清", "低照度、空旷环境、少人物、留白"),
    ("压抑", "低饱和、空间拥挤、顶部压低、侧逆光"),
    ("颓唐", "肩背塌下、视线下垂、衣褶松垮、侧光"),
    ("窘迫", "身体后缩、手部收紧、视线避开、背景留白"),
    ("冷漠", "表情平直、视线避开主体、冷色环境、背景虚化"),
    ("麻木", "表情平直、动作停顿、眼神失焦、灰调环境"),
    ("荒诞", "正常构图里出现一个不合时宜的物件、人物表情平静、光线偏硬"),
    ("疏离", "人物之间留出大片空隙、前后景分离、冷色环境"),
    ("孤独", "单个人物、周围留白、远景空间、冷色环境"),
    ("孤寂", "单个人物、周围留白、远景空间、冷色环境"),
    ("紧张", "身体前倾、手部收紧、对角线构图、硬侧光"),
    ("不安", "视线游移、手指交握、背景倾斜、阴影压住半张脸"),
    ("绝望", "人物跪坐或靠墙、顶部大量暗部、光源在画面外"),
    ("希望", "人物抬头、画面右侧亮部、暖色光、前方留白"),
    ("温情", "两人距离靠近、暖色侧光、背景柔化"),
    ("蓄势", "同构图重复、人物静止、手部或道具特写、格间留白加宽"),
)


def _clean(text: Any, limit: int = 0) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip("；，、 ")
    if limit and len(s) > limit:
        cut = s[:limit]
        pos = max((cut.rfind(p) for p in "，、；：。"), default=-1)
        s = (cut[:pos] if pos >= max(6, limit // 2) else cut[:limit]).rstrip("；，、 ")
    return s


def detail_level(shot_type: str) -> str:
    """把中文景别映射到 5 级详略（旧调用仍返回原字符串语义）。"""
    t = str(shot_type or "")
    for keys, level in _LEVEL_ALIASES:
        if any(k in t for k in keys):
            return level
    return "medium"


def _budget(key: str, fallback: int) -> int:
    try:
        return int((getattr(config, "PROMPT_BUDGET", {}) or {}).get(key, fallback))
    except (TypeError, ValueError):
        return fallback


def identity_budget(level: str, n_cast: int = 1) -> int:
    """身份锁预算：按景别 + 人数递增，绝不为了塞人多删掉一整个人。"""
    base = {
        "wide": _budget("cast_wide", 150),
        "full": _budget("cast_full", 190),
        "medium": _budget("cast_medium", 280),
        "close": _budget("cast_close", 420),
        "extreme": _budget("cast_extreme", 520),
    }.get(level, 280)
    return int(base + max(0, int(n_cast) - 1) * 55)


def _abstract_map() -> tuple[tuple[str, str], ...]:
    """允许 rules/data/shot_prompt_rules.json 覆盖抽象词语表。"""
    try:
        from . import rules
        custom = rules.shot_prompt().get("abstract_visuals") or {}
        pairs = [(str(k), str(v)) for k, v in custom.items() if k and v]
        if pairs:
            return tuple(sorted(pairs, key=lambda kv: -len(kv[0]))) + _DEFAULT_ABSTRACT
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_ABSTRACT


def visualize_abstract(text: str) -> str:
    """把抽象词替换成可见元素；已替换过的句子不会被二次替换。"""
    s = _clean(text)
    if not s:
        return ""
    for key, visual in _abstract_map():
        if key in s:
            s = s.replace(key, visual)
    return s


def _light_clause(shot: Any) -> str:
    lighting = _clean(getattr(shot, "lighting", ""), 10)
    light_dir = _clean(getattr(shot, "light_dir", ""), 12)
    if lighting and light_dir:
        return f"{lighting}，主光来自{light_dir}"
    if lighting:
        return lighting
    if light_dir:
        return f"主光来自{light_dir}"
    return ""


def subject_clause(cast_items: list[dict] | None, project: Any = None) -> str:
    """把主身份压成一个短观察句，塞进首句（不写角色名）。

    首句是文本编码器的注意力框架：只写"中景/平视/酒店"而不写"花白头发、
    旧长衫的中年男子"，模型会把身份锁留到后半段才看见，实测容易画成年轻人。
    这里只取年龄/性别 + 发型轮廓 + 服装主色，不写记忆点与小痣。
    """
    items = [c for c in (cast_items or []) if isinstance(c, dict)]
    if not items:
        return ""
    if len(items) >= 3:
        return f"{len(items)}名人物，排成错落横列"
    if len(items) == 2:
        return "两名人物，一左一右"
    item = items[0]
    character = None
    if project is not None and hasattr(project, "find_character"):
        character = project.find_character(str(item.get("name", "") or ""))
    if character is None:
        card = _clean(item.get("card", ""), 30)
        return f"一名{card}" if card else ""
    age = _clean(getattr(character, "age", ""), 8)
    if age in ("未知", "不确定", "?"):
        age = ""
    gender = _clean(getattr(character, "gender", ""), 4)
    gender_word = {"男": "男子", "女": "女子"}.get(gender, "人物")
    hf = dict(getattr(character, "hard_features", {}) or {})
    hair = _clean(hf.get("hair", "") or getattr(character, "appearance", ""), 18)
    outfit = _clean(hf.get("outfit", "") or "", 20)
    design = str(getattr(character, "design", "") or "")
    # 分析阶段的 age 常是"未知"；从人设/外貌里补一个年龄锚。
    if not age and design:
        m = re.search(r"([一二三四五六七八九十百\d]{1,3})\s*十?\s*岁", design)
        if m:
            age = m.group(0).replace(" ", "")
    # 花白头发 + 胡子/皱纹 = 老者：比"四十岁男子"更能阻止模型画成年轻人。
    old_hint = ("花白" in hair or "白发" in hair or "银发" in hair) and (
        "胡" in design or "皱纹" in design or "苍老" in design)
    prefix = "一名老者" if old_hint else f"一名{age}{gender_word}"
    bits = [x for x in (hair, outfit) if x]
    if not bits:
        return prefix
    return f"{prefix}，" + "、".join(bits)


def frame_clause(shot: Any, project: Any = None, style_hint: str = "",
                 cast_items: list[dict] | None = None) -> str:
    """第一句：媒介/版式 + 景别 + 机位 + 场景 + 时间 + 光。

    首句是文本编码器的"注意力框架"，必须 ≤120 字且不参与预算裁剪。
    画风不塞进首句：它另有 style_anchor 区块，混在一起会让首句变成风格清单。
    """
    orient = _ORIENT.get(str(getattr(shot, "panel_orientation", "") or ""), "竖向")
    role = _ROLE.get(str(getattr(shot, "panel_role", "") or "normal"), "常规分格")
    shot_type = _clean(getattr(shot, "shot_type", ""), 12) or "中景"
    camera = _clean(getattr(shot, "camera", ""), 12) or "平视"
    scene = _clean(getattr(shot, "scene", ""), 26)
    scene_note = ""
    if project is not None and scene:
        obj = next((x for x in getattr(project, "scenes", []) if getattr(x, "name", "") == scene), None)
        if obj is not None:
            scene_note = _clean(getattr(obj, "features", ""), 18)
    time = _clean(getattr(shot, "time_of_day", ""), 12)
    light = _light_clause(shot)
    bits = [f"一张{orient}构图的漫画{role}", shot_type, f"{camera}机位"]
    subject = subject_clause(cast_items, project)
    if subject:
        bits.append(subject)
    if scene:
        bits.append(scene + (f"（{scene_note}）" if scene_note else ""))
    if time:
        bits.append(time)
    if light:
        bits.append(light)
    if str(getattr(shot, "panel_role", "")) == "inset":
        bits.append("画面边缘留出叠放空间，主体偏一侧")
    sentence = "，".join(b for b in bits if b)
    return _clean(sentence, _budget("frame", 120)) + "。"


def _split_segments(text: str) -> list[str]:
    """按分号/句号切成身份锁小段；用于"整段保留、只压最长段"的裁剪。"""
    parts = [p.strip() for p in re.split(r"[；;]+", text or "") if p.strip()]
    return parts or ([text] if text else [])


def trim_identity(text: str, level: str, n_cast: int = 1) -> str:
    """按景别裁身份锁：保留完整人物段，只压最长的那一段。

    旧实现直接截字符串，双人时会把第二个人整段切掉；这里改为
    "先测总预算，再反复压最长段"，每个人至少保留名字 + 发型/服装主色。
    """
    s = _clean(text)
    if not s:
        return s
    cap = identity_budget(level, n_cast)
    if len(s) <= cap:
        return s
    segs = _split_segments(s)
    if not segs:
        return s
    # 每人至少 48 字；先按比例给每段设上限，再逐轮压最长段。
    floor = 48
    guard = 0
    while len("；".join(segs)) > cap and guard < 24:
        guard += 1
        idx = max(range(len(segs)), key=lambda i: len(segs[i]))
        seg = segs[idx]
        if len(seg) <= floor:
            break
        target = max(floor, len(seg) - max(8, (len("；".join(segs)) - cap)))
        cut = seg[:target]
        pos = max((cut.rfind(p) for p in "，、：:"), default=-1)
        segs[idx] = (cut[:pos] if pos >= max(8, target // 2) else cut).rstrip("，、；; ")
    out = "；".join(x for x in segs if x)
    return out if len(out) <= cap else _clean(out, cap)


def crowd_clause(cast_items: list[dict], level: str = "medium",
                 project: Any = None) -> str:
    """3 人以上：把"人群"展开成可区分的个体，降低同格克隆脸。"""
    items = [c for c in (cast_items or []) if isinstance(c, dict)]
    if len(items) < 3:
        return ""
    rows: list[str] = []
    for item in items[:4]:
        name = str(item.get("name", "") or "").strip()
        card = str(item.get("card", "") or "").strip()
        if "：" in card:
            card = card.split("：", 1)[1]
        distinct = _clean(card, 20)
        rows.append(f"{name}（{distinct}）" if name and distinct else (name or distinct))
    head = ("画面从左到右依次是" + "、".join(rows) + "；") if rows else ""
    return (head +
            "每个人的年龄、体型、发型、服装主色至少两项不同，"
            "各自有清晰可辨的脸和轮廓；"
            + ("远景只保留体型轮廓与服装主色。" if level == "wide" else ""))


def _llm_frame(project: Any, shot: Any, cast_items: list[dict],
               style_hint: str) -> str:
    """可选 LLM 首句；失败回退模板。只在 PROMPT_REWRITER=llm 时调用。"""
    key_src = json.dumps({
        "shot": getattr(shot, "shot_type", ""),
        "camera": getattr(shot, "camera", ""),
        "scene": getattr(shot, "scene", ""),
        "time": getattr(shot, "time_of_day", ""),
        "light": getattr(shot, "lighting", ""),
        "orientation": getattr(shot, "panel_orientation", ""),
        "role": getattr(shot, "panel_role", ""),
        "chars": [c.get("name") for c in cast_items],
        "style": style_hint,
    }, ensure_ascii=False, sort_keys=True)
    key = hashlib.sha1(key_src.encode("utf-8")).hexdigest()
    if key in _CACHE:
        return str(_CACHE[key].get("frame") or "")
    fallback = frame_clause(shot, project, style_hint, cast_items)
    try:
        from .textclient import TextClient
        client = TextClient()
        text = client.chat([
            {"role": "system", "content": (
                "你是漫画分镜的视觉翻译。只把镜头字段翻译成一条不超过120字的"
                "观察式画面首句：媒介/版式、景别、机位、场景、时间、主光方向、"
                "画面里看得见的人物轮廓。不要写角色名，不要写抽象词，不要写“不要”，"
                "不要新增人设里没有的发型/服装/配饰。只输出这一句。")},
            {"role": "user", "content": (
                f"景别：{getattr(shot, 'shot_type', '')}\n"
                f"机位：{getattr(shot, 'camera', '')}\n"
                f"场景：{getattr(shot, 'scene', '')}\n"
                f"时间：{getattr(shot, 'time_of_day', '')}\n"
                f"主光：{getattr(shot, 'lighting', '')}/{getattr(shot, 'light_dir', '')}\n"
                f"分格：{getattr(shot, 'panel_orientation', '')} / {getattr(shot, 'panel_role', '')}\n"
                f"画风：{style_hint}\n"
                f"人物：{('、'.join(str(c.get('name')) for c in cast_items)) or '无'}")},
        ], temperature=0.2, max_tokens=220)
        frame = _clean(text, 160)
        if len(frame) < 16:
            raise ValueError("首句过短")
        _CACHE[key] = {"frame": frame}
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.clear()
        return frame
    except Exception as exc:  # noqa: BLE001
        log.debug("LLM 分镜首句失败，回退模板：%s", exc)
        return fallback


def validate_prompt(text: str, shot: Any = None,
                    project: Any = None,
                    cast_items: list[dict] | None = None) -> list[str]:
    """轻量校验：只报风险，不改 prompt。供提示词回溯 / 界面提示使用。"""
    issues: list[str] = []
    s = str(text or "")
    if not s:
        return ["prompt 为空"]
    for word in ("不要", "禁止", "分镜", "一格", "漫画格", "参考图"):
        if word in s:
            issues.append(f"正向里出现制作术语/否定词：{word}")
    for word in ("悲凉", "凄凉", "压抑", "命运", "麻木", "氛围感", "高级感", "故事感"):
        if word in s:
            issues.append(f"抽象词未翻译：{word}")
    if shot is not None:
        if not re.search(r"远景|全景|中景|近景|特写|全身", s[:140]):
            issues.append("首句缺少景别")
        if not re.search(r"机位|平视|俯视|仰视|斜侧", s[:160]):
            issues.append("首句缺少机位")
    for item in (cast_items or []):
        name = str(item.get("name", "") or "").strip()
        if name and name not in s:
            issues.append(f"身份锁里缺少角色姓名：{name}")
    return issues


def compile_shot(project: Any, shot: Any, cast_items: list[dict],
                 style_hint: str = "") -> dict[str, Any]:
    """返回编译结果，供 prompts.py 拼装。"""
    mode = str(getattr(config, "PROMPT_REWRITER", "template") or "template").lower()
    level = detail_level(getattr(shot, "shot_type", ""))
    if mode == "off":
        return {}
    frame = frame_clause(shot, project, style_hint, cast_items)
    if mode == "llm":
        frame = _llm_frame(project, shot, cast_items, style_hint) or frame
    mood = visualize_abstract(getattr(shot, "mood", ""))
    # 画面描述里的抽象词也做一次轻量替换；只影响 prompt，不改 Shot。
    desc = visualize_abstract(getattr(shot, "description", ""))
    return {
        "frame": frame,
        "level": level,
        "mood": mood,
        "description": desc,
        "crowd": crowd_clause(cast_items, level, project),
        "light": _light_clause(shot),
        "identity_budget": identity_budget(level, len(cast_items)),
        "warnings": [],
    }
