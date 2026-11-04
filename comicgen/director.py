# -*- coding: utf-8 -*-
"""导演层：把"原文搬运式"分镜整理成可拍的条漫镜头。

这一层解决的问题不是"画风"，而是**分镜本身不成立**：

  · 画面描述像小说，出图模型只能自由发挥 → 每格看起来都差不多；
  · 打电话时把电话另一端的人也塞进 characters → 画面里两个人干站着；
  · 全篇同一个景别、同一个机位、同一种画幅 → 读者感觉在翻 PPT；
  · 台词、旁白、画外音混在一起 → 气泡乱飞、压住脸、剧情读不懂。

本模块只做**确定性**整理（不依赖模型听话），顺序是：

    清理描述 → 补镜头字段 → 修节奏（景别/机位/画幅）→ 锁轴线（左右）
    → 分配气泡槽位 → 补封面文案 → 自检

真正的"重写"由 pipeline 调文本模型完成（prompts.director_pass_prompt），
本模块负责模型输出后的兜底与验收。
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Iterable, Sequence

import config
from . import castguard, rules as rules_mod
from .logger import get_logger
from .models import Dialogue, Project, Shot

log = get_logger(__name__)


# =============================================================================
# 基础映射
# =============================================================================

# 描述里由旧版规则塞进去的"构图提示"，不是画面内容，必须清掉
_META_HINTS = (
    "（对话密集：说话人近景与听者反应交替）",
    "（有一句台词：取说话人近景，突出表情）",
    "（无台词：以场景与物件细节为主）",
    "（人物反应与环境细节）",
    "（说话人近景）",
    "（听者反应近景）",
    "（手部或物件特写）",
    "（同一场景的另一角度）",
)
_META_RE = re.compile(r"（[^）]*?(?:对话密集|有一句台词|无台词|近景|反应|特写|另一角度|该分镜)[^）]*?）")

_EXPRESSION_BY_MOOD: dict[str, str] = {
    "紧张": "眉心紧锁，嘴唇抿成一条线，眼神快速扫向声音来处",
    "悬疑": "眼神微沉，眼睑压低，嘴角没有表情",
    "压抑": "下颌绷紧，目光垂落，呼吸放得很轻",
    "悲伤": "眼眶发红，睫毛低垂，嘴角向下",
    "愤怒": "眉头压低，牙关咬紧，眼神直逼对方",
    "激动": "眼睛睁大，呼吸急促，身体前倾",
    "温馨": "眼神柔和，嘴角带一点笑意",
    "浪漫": "目光停在对方脸上，耳根微微发红",
    "搞笑": "眉毛一挑，嘴角抽了一下，表情管理失败",
    "平静": "表情克制，眼神平稳",
}

_TIME_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("凌晨", ("凌晨", "深夜", "半夜", "两点", "三点", "夜深")),
    ("夜晚", ("夜", "月光", "霓虹", "路灯", "雨夜")),
    ("黄昏", ("黄昏", "傍晚", "日落", "夕阳")),
    ("清晨", ("清晨", "黎明", "早上", "天刚亮")),
    ("白天", ("白天", "正午", "午后", "阳光")),
)

_CAMERA_BY_SHOT: dict[str, tuple[str, ...]] = {
    "远景": ("平视", "俯视", "斜侧"),
    "全景": ("平视", "斜侧", "俯视"),
    "中景": ("平视", "斜侧", "过肩"),
    "近景": ("平视", "斜侧", "过肩"),
    "特写": ("平视", "斜侧", "仰视"),
}

_COMPOSITION_BY_CAMERA: dict[str, str] = {
    "平视": "三分构图",
    "俯视": "中心构图",
    "仰视": "对角线构图",
    "斜侧": "前景遮挡",
    "过肩": "前景遮挡",
    "主观": "中心构图",
    "背影": "对称构图",
}

_LIGHT_BY_TIME: dict[str, str] = {
    "凌晨": "低调光",
    "夜晚": "低调光",
    "黄昏": "逆光",
    "清晨": "自然光",
    "白天": "自然光",
}
_LIGHT_BY_MOOD: dict[str, str] = {
    "悬疑": "侧光",
    "紧张": "低调光",
    "压抑": "顶光",
    "悲伤": "低调光",
    "愤怒": "侧光",
    "激动": "高调光",
    "温馨": "自然光",
    "浪漫": "逆光",
    "搞笑": "高调光",
    "平静": "自然光",
}


def _log(msg: str) -> None:
    log.info(msg)


# =============================================================================
# 一、描述清理
# =============================================================================

_QUOTE_RE = re.compile(r'[“「『]([^”」』]{1,120})[”」』]|"([^"\n]{1,120})"')
_SENT_SPLIT = re.compile(r"(?<=[。！？…；;])")

# 描述开头的景别/机位词：景别已经由 shot_type/camera 字段传给提示词，
# 描述里再写一遍等于同一句话说两次，还会和字段打架（rules/02 P2）。
_CAMERA_PREFIX_RE = re.compile(
    r"^(?:(?:平视|俯视|仰视|斜侧|过肩|主观|背影|远景|全景|中景|近景|特写|"
    r"俯拍|仰拍|大特写|微距特写|微距)(?:镜头|机位|构图|画面)?[，、,\s]*)+")
# "陆鸣出现在画面中，作为视觉主体" 这类由旧版规则注入的 meta 语（rules/00 证据 E）
_META_SUBJECT_RE = re.compile(
    r"[，,]?\s*[^，。；]{0,14}出现在画面中\s*[，,]?\s*作为(?:视觉)?主体\s*[。]?")


def clean_description(text: str) -> str:
    """把描述里的 meta 提示、引号、台词残片、开头镜头词清掉。"""
    t = (text or "").strip()
    if not t:
        return ""
    t = _CAMERA_PREFIX_RE.sub("", t)
    t = _META_SUBJECT_RE.sub("", t)
    for hint in _META_HINTS:
        t = t.replace(hint, "")
    t = _META_RE.sub("", t)
    # 残留的引号：只去引号符号与其中的台词（真正的台词已在 dialogues 里）
    t = _QUOTE_RE.sub("", t)
    for ch in ("“", "”", "「", "」", "『", "』", '"'):
        t = t.replace(ch, "")
    # 去掉"像…/如同…"比喻从句：空镜里"像指印"会诱导模型画手或人
    t = re.sub(r"[，,](像|如同|仿佛|好似)[^。；;]*", "", t)
    # 空镜描述里不要写"没有人物/无人"（否定句会反而把人物喂给模型）
    t = re.sub(r"(没有人物|无人物|空无一人|画面中无人|不见人影)[，,。；;]?", "。", t)
    t = re.sub(r"[，,；;]。", "。", t)
    t = re.sub(r"。{2,}", "。", t)
    # 引号内容被删掉后常留下"状态为。""显示：。"这类残句
    t = re.sub(r"(为|是|显示|写着|标着|标注)[。；，,]", "。", t)
    t = re.sub(r"[，,、；;：:]{2,}", "。", t)
    t = re.sub(r"[ \t\u3000]{2,}", " ", t)
    t = re.sub(r"^[。，、；;：:\s]+", "", t)
    t = re.sub(r"[。，、；;：:\s]+$", "", t)
    return t.strip()


def _sentences(text: str) -> list[str]:
    return [x.strip() for x in _SENT_SPLIT.split(text or "") if x.strip()]


def _first_sentence(text: str, limit: int = 46) -> str:
    sents = _sentences(text)
    first = sents[0] if sents else (text or "")
    if len(first) > limit:
        cut = first[:limit]
        pos = max((cut.rfind(p) for p in "。！？…，、；："), default=-1)
        first = cut[:pos] if pos >= max(6, limit // 2) else cut[:limit - 1] + "…"
    return first.strip("，。；： ")


# =============================================================================
# 二、镜头字段兜底
# =============================================================================

def _infer_time(shot: Shot) -> str:
    if shot.time_of_day:
        return shot.time_of_day
    text = f"{shot.source_text or ''} {shot.description or ''} {shot.scene or ''}"
    for name, cues in _TIME_CUES:
        if any(c in text for c in cues):
            return name
    return ""


def _infer_camera(shot: Shot, idx: int) -> str:
    if shot.camera:
        return shot.camera
    pool = _CAMERA_BY_SHOT.get(shot.shot_type, ("平视", "斜侧"))
    if shot.mood in ("紧张", "压抑") and "俯视" in pool:
        return "俯视"
    if shot.mood in ("愤怒", "激动") and "仰视" in pool:
        return "仰视"
    if shot.shot_type in ("近景", "特写") and "过肩" in pool and idx % 3 == 0:
        return "过肩"
    return pool[idx % len(pool)]


def _infer_composition(shot: Shot) -> str:
    if shot.composition:
        return shot.composition
    return _COMPOSITION_BY_CAMERA.get(shot.camera or "平视", "三分构图")


def _infer_lighting(shot: Shot) -> str:
    if shot.lighting:
        return shot.lighting
    if shot.time_of_day and shot.time_of_day in _LIGHT_BY_TIME:
        base = _LIGHT_BY_TIME[shot.time_of_day]
        if shot.mood in ("悬疑", "紧张") and base == "自然光":
            return "侧光"
        return base
    return _LIGHT_BY_MOOD.get(shot.mood, "自然光")


def _infer_panel_scale(shot: Shot) -> str:
    if shot.panel_scale:
        return shot.panel_scale
    if shot.shot_type in ("远景", "全景"):
        return "L"
    if shot.shot_type == "特写":
        return "S"
    if shot.mood in ("紧张", "激动", "愤怒"):
        return "S"
    if shot.mood in ("悲伤", "压抑", "悬疑"):
        return "L"
    return "M"


def _infer_expression(shot: Shot) -> str:
    if shot.expression:
        return shot.expression
    return _EXPRESSION_BY_MOOD.get(shot.mood, "")


def _infer_action(shot: Shot) -> str:
    if shot.action:
        return shot.action
    desc = shot.description or ""
    if not desc:
        return ""
    first = _first_sentence(desc, 42)
    action_words = ("走", "站", "坐", "推", "拿", "看", "望", "转身", "抬手", "握住",
                    "放下", "低头", "抬头", "靠", "冲", "退", "停", "伸", "收",
                    "点", "摇", "开", "关", "翻", "递", "接", "擦", "敲", "拨")
    if any(w in first for w in action_words):
        return first
    name = shot.characters[0] if shot.characters else "人物"
    return f"{name}保持当前姿态，画面停在这一瞬间"


def _infer_focus(shot: Shot) -> str:
    if shot.focus:
        return shot.focus
    name = shot.characters[0] if shot.characters else ""
    if shot.shot_type in ("特写",) and name:
        return f"{name}的眼睛与表情"
    if shot.shot_type == "特写":
        return "关键物件的局部细节"
    if shot.shot_type in ("远景", "全景"):
        return "整个空间的光线与人物的位置关系"
    if name:
        return f"{name}的手部动作与身体朝向"
    return "画面中心的主体"


def _infer_transition(shot: Shot, prev: Shot | None) -> str:
    if shot.transition:
        return shot.transition
    if prev is None:
        return "场景切换"
    if (shot.scene or "") != (prev.scene or ""):
        return "场景切换"
    if not shot.characters and prev.characters:
        return "氛围铺陈"
    if shot.characters and prev.characters and set(shot.characters) != set(prev.characters):
        return "主体切换"
    if shot.mood in ("紧张", "激动") or shot.panel_scale == "S":
        return "动作衔接"
    return "时刻分解"


# =============================================================================
# 三、节奏修正
# =============================================================================

def _is_establishing(shot: Shot) -> bool:
    return shot.shot_type in config.STORYBOARD_ESTABLISHING_TYPES


def _varied_shot_type(shot: Shot, avoid: str) -> str:
    # 打断连续镜头时优先用"中景/全景/远景"，避免把近景配额又顶上去
    if _is_establishing(shot):
        pool = ["全景", "中景", "远景"]
    elif shot.shot_type in ("近景", "特写"):
        pool = ["中景", "全景", "远景"]
    else:
        pool = ["全景", "中景", "远景"]
    for t in pool:
        if t != avoid:
            return t
    return avoid


def _varied_camera(avoid: str, idx: int) -> str:
    pool = [c for c in config.SHOT_ANGLES if c != avoid]
    return pool[idx % len(pool)] if pool else avoid


_DETAIL_WORDS = ("特写", "近景", "中景", "屏幕", "手机", "U盘", "桌面", "墙面",
                 "纸张", "界面", "文件夹", "哈希", "短信", "进度条", "余额", "表格")


def _sync_shot_type_with_description(shots: Sequence[Shot]) -> int:
    """
    用描述里的自述修正景别。

    旧版节奏规则会把"屏幕特写/手机特写"改成"全景"（因为它是新场景的第一格），
    于是提示词里出现"全景镜头 + 手机特写"的自相矛盾。这里先按描述自述纠正：
      · 描述明确写"特写" → 特写
      · 描述明确写"近景" → 近景（不覆盖特写）
      · 描述写"过肩"但景别是全景/远景 → 中景
    只做这几条，不改写描述本身。
    """
    changed = 0
    for s in shots:
        desc = s.description or ""
        target = s.shot_type
        if "特写" in desc:
            target = "特写"
        elif "近景" in desc and s.shot_type not in ("特写", "近景"):
            target = "近景"
        elif "过肩" in desc and s.shot_type in ("全景", "远景"):
            target = "中景"
        if target != s.shot_type:
            s.shot_type = target
            changed += 1
    return changed


def _classify_object_mode(shot: Shot) -> str:
    """物件/空镜的语义模式：物证照 / 屏幕界面 / 环境细节。"""
    if shot.characters:
        return ""
    desc = shot.description or ""
    if any(k in desc for k in ("手机", "屏幕", "显示器", "平板")):
        if any(k in desc for k in ("血", "裂", "碎", "尸", "死")):
            return "物证照"
        return "屏幕界面"
    return "环境细节"


def _is_detail_shot(shot: Shot) -> bool:
    """是否为"屏幕/物件/局部"类特写——这类镜头不适合强行改成建立镜头。"""
    desc = shot.description or ""
    return (shot.shot_type == "特写"
            or any(w in desc for w in _DETAIL_WORDS))


def _enforce_scene_establishing(shots: Sequence[Shot]) -> int:
    changed = 0
    prev_scene = None
    for i, s in enumerate(shots):
        if i == 0 or (s.scene or "") != (prev_scene or ""):
            # 屏幕/手机/物件特写不要强行拉成全景，否则描述与景别自相矛盾
            if not _is_establishing(s) and not _is_detail_shot(s):
                s.shot_type = "远景" if s.mood in ("压抑", "悲伤") else "全景"
                s.panel_scale = "L"
                changed += 1
        prev_scene = s.scene
    return changed


def _enforce_shot_run(shots: Sequence[Shot]) -> int:
    changed = 0
    run_type = ""
    run_n = 0
    for i, s in enumerate(shots):
        if s.shot_type == run_type:
            run_n += 1
            if run_n > int(config.STORYBOARD_MAX_SAME_SHOT_RUN):
                s.shot_type = _varied_shot_type(s, run_type)
                run_type = s.shot_type
                run_n = 1
                changed += 1
        else:
            run_type = s.shot_type
            run_n = 1
    return changed


def _enforce_camera_run(shots: Sequence[Shot]) -> int:
    changed = 0
    run_cam = ""
    run_n = 0
    for i, s in enumerate(shots):
        if s.camera == run_cam:
            run_n += 1
            if run_n > int(getattr(config, "DIRECTOR_MAX_SAME_ANGLE_RUN", 2)):
                s.camera = _varied_camera(run_cam, i)
                s.composition = _COMPOSITION_BY_CAMERA.get(s.camera, s.composition)
                run_cam = s.camera
                run_n = 1
                changed += 1
        else:
            run_cam = s.camera
            run_n = 1
    return changed


def _enforce_panel_scale_mix(shots: Sequence[Shot]) -> int:
    """画幅不能全篇一个档：至少 3 种，单一档位不超过 55%。"""
    if len(shots) < 6:
        return 0
    changed = 0
    counts: dict[str, int] = {}
    for s in shots:
        counts[s.panel_scale] = counts.get(s.panel_scale, 0) + 1
    if len(counts) >= 3 and max(counts.values()) / len(shots) <= 0.6:
        return 0
    cycle = ["M", "S", "L", "M", "XL", "S"]
    for i, s in enumerate(shots):
        if i in (0, len(shots) - 1):
            continue
        if s.panel_scale == "M" and i % 2 == 0:
            continue
        want = cycle[i % len(cycle)]
        if want != s.panel_scale:
            s.panel_scale = want
            changed += 1
    return changed


def _enforce_closeup_ratio(shots: Sequence[Shot]) -> int:
    need = int(len(shots) * float(getattr(config, "DIRECTOR_CLOSEUP_MIN_RATIO", 0.24)))
    close = sum(1 for s in shots if s.shot_type in ("近景", "特写"))
    changed = 0
    # 优先把"有台词的 中景"提升为近景，情绪才有落点
    for s in shots:
        if close >= need:
            break
        if s.shot_type == "中景" and s.has_dialogue:
            s.shot_type = "近景"
            close += 1
            changed += 1
    # 还不够就把中景逐格提升（不要动建立镜头）
    for s in shots:
        if close >= need:
            break
        if s.shot_type == "中景":
            s.shot_type = "近景"
            close += 1
            changed += 1
    return changed


# =============================================================================
# 四、轴线与气泡槽位
# =============================================================================

def _assign_sides(shots: Sequence[Shot], project: Project | None = None) -> None:
    """
    同一场景内锁定角色的左右站位（180° 轴线）。

    规则：
      · 描述里明确写了"X在左/右" → 以描述为准（画面已经这么写了）；
      · 否则同一场景内第一个出场角色在左、第二个在右，后续沿用；
      · 缺的角色按全局角色顺序补，避免同一场戏里左右乱跳。
    """
    roster = [c.name for c in (project.characters if project else []) if c.name]
    rank = {name: i for i, name in enumerate(roster)}
    scene_sides: dict[str, dict[str, str]] = {}
    for s in shots:
        sc = s.scene or ""
        table = scene_sides.setdefault(sc, {})
        # 1) 描述里写明的左右优先
        for name in s.characters:
            m = re.search(re.escape(name) + r"在(?:画面)?(左|右|中)", s.description or "")
            if m:
                table[name] = m.group(1)
        # 2) 导演模型给出的左右，只在描述没写时采用
        for name, side in (s.char_sides or {}).items():
            if name in s.characters and side in ("左", "右", "中"):
                table.setdefault(name, side)
        # 3) 仍然缺的：按全局角色顺序补（2 人左/右；3 人左/中/右；
        #    4 人左/中/中/右，给中间的两个人各留一个位置）
        cap = {"左": 1, "中": 2 if len(s.characters) >= 4 else 1, "右": 1}
        for name in sorted(s.characters, key=lambda n: rank.get(n, 999)):
            if name in table:
                continue
            used = list(table.values())
            pick = next((k for k in ("左", "中", "右")
                         if used.count(k) < cap[k]), "中")
            table[name] = pick
        # 双人同框必须一左一右：模型给了"中"或者描述没写时，按描述/全局顺序纠正
        if len(s.characters) == 2:
            a, b = s.characters
            sa, sb = table.get(a, "中"), table.get(b, "中")
            if sa == "中" and sb == "中":
                table[a], table[b] = "左", "右"
            # 只把"两个都中"纠正成一左一右；描述明确写"在画面中央"的角色
            # 不要硬挪到左边，否则气泡会飞到没有人的那一侧（实测第 40 格）。
            elif sa == "中" or sb == "中":
                pass
        s.char_sides = {n: table[n] for n in s.characters if n in table}


def _slot_for_speaker(shot: Shot, d: Dialogue, order_in_shot: int) -> str:
    """按说话人站位与台词顺序选气泡位置（阅读顺序：上 → 中 → 下）。"""
    if d.kind == "narration":
        return "top-center"
    if d.kind == "sfx":
        return "top-right" if order_in_shot % 2 == 0 else "mid-right"
    if d.offscreen or (d.speaker and d.speaker not in (shot.characters or [])):
        # 画外音/说话人不在画面里：顶部居中，不指向具体人物
        return "top-center"
    side = shot.char_sides.get(d.speaker, "")
    band = ("top", "mid", "bottom")[min(order_in_shot, 2)]
    if side == "左":
        return f"{band}-left"
    if side == "右":
        return f"{band}-right"
    if side == "中":
        # 人物在画面中央：顶部居中，不要默认甩到左边
        return "top-center"
    return "top-left" if order_in_shot % 2 == 0 else "top-right"


def _flip_slot(slot: str) -> str:
    """把气泡槽位左右翻转（保持上/中/下band不变）。"""
    if slot.endswith("left"):
        return slot[:-4] + "right"
    if slot.endswith("right"):
        return slot[:-5] + "left"
    return slot




# ---------------------------------------------------------------------------
# 台词/气泡兜底：长台词拆成多个气泡、空说话人转旁白或画外音
# ---------------------------------------------------------------------------

def _split_text_chunks(text: str, limit: int) -> list[str]:
    """按标点把一句长台词切成 ≤limit 的若干块（尽量在逗号/句号处断）。

    旧版只在"读到标点时 buf 已经 >= limit"才切；像
    「你姐姐死的那天晚上，把最重要的东西交给了我。」这种 22 字、
    逗号在 11 字处的句子会原样留下，导致自检一直报"超过 20 字"。
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []
    parts = re.split(r"(?<=[，。！？…；;,.!?])", text)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 单个分句仍然超长：在 limit 附近找标点硬切
        while len(part) > limit:
            head = part[:limit]
            cut = max(head.rfind(p) for p in "，。！？…；;,.!?")
            if cut >= max(4, limit // 2):
                chunks.append(part[:cut + 1].strip())
                part = part[cut + 1:].strip()
            else:
                chunks.append(head.strip())
                part = part[limit:].strip()
        if not part:
            continue
        if len(buf) + len(part) <= limit:
            buf += part
        else:
            if buf:
                chunks.append(buf.strip())
            buf = part
    if buf.strip():
        chunks.append(buf.strip())
    return [c for c in chunks if c]


def _split_long_speech(shot: Shot, limit: int | None = None) -> int:
    """把超过单条字数的台词拆成同格的多个气泡（最多 3 个）。"""
    limit = int(limit or getattr(config, "DIRECTOR_MAX_DIALOGUE_CHARS", 20))
    out: list[Dialogue] = []
    changed = 0
    for d in shot.dialogues:
        if d.kind != "speech" or len((d.text or "").strip()) <= limit:
            out.append(d)
            continue
        chunks = _split_text_chunks(d.text, limit)
        if not chunks:
            out.append(d)
            continue
        changed += 1
        for k, piece in enumerate(chunks):
            if k == 2 and len(chunks) > 3:
                piece = piece + "…"
            if k >= 3:
                break
            out.append(Dialogue(
                speaker=d.speaker, text=piece, tone=d.tone,
                bubble_style=d.bubble_style, kind=d.kind,
                slot=d.slot, tail=d.tail, offscreen=d.offscreen, size=d.size,
            ))
    # 单格最多 3 个对话气泡；超出的并到第 3 条（保持信息不丢）
    speech = [x for x in out if x.kind == "speech"]
    if len(speech) > 3:
        keep = speech[:3]
        rest = speech[3:]
        keep[-1].text = keep[-1].text + "…"
        out = [x for x in out if x.kind != "speech" or x in keep]
    shot.dialogues = out
    return changed


def _fix_empty_speakers(shots: Sequence[Shot]) -> int:
    """
    没写说话人的台词：
      · ≤12 字（时间/状态/屏幕文字）→ 转旁白条，不占气泡；
      · 更长的 → 标画外音，不指向具体人物。
    """
    changed = 0
    for s in shots:
        for d in s.dialogues:
            if d.kind != "speech" or not (d.text or "").strip() or d.speaker:
                continue
            if len(d.text.strip()) <= 12:
                d.kind = "narration"
                d.slot = "top-center"
                d.tail = "none"
            else:
                d.offscreen = True
                d.slot = "top-center"
                d.tail = "none"
            changed += 1
    return changed


def _cap_closeup_ratio(shots: Sequence[Shot]) -> int:
    """近景+特写超过上限（默认 30%）时，把近景降成中景；特写保留给情绪峰值。"""
    if len(shots) < 8:
        return 0
    max_ratio = float(getattr(config, "DIRECTOR_CLOSEUP_MAX_RATIO", 0.30))
    allowed = int(len(shots) * max_ratio)
    close = sum(1 for s in shots if s.shot_type in ("近景", "特写"))
    excess = close - allowed
    if excess <= 0:
        return 0
    changed = 0
    # 先降无台词的近景，再降有台词的近景；特写不动
    for allow_dialogue in (False, True):
        for s in shots:
            if excess <= 0:
                break
            if s.shot_type != "近景":
                continue
            if s.has_dialogue and not allow_dialogue:
                continue
            s.shot_type = "中景"
            excess -= 1
            changed += 1
    return changed




# ---------------------------------------------------------------------------
# 分镜配额：总格数 / 双人同框 / 近景+特写（rules/02 §4、§6）
# ---------------------------------------------------------------------------

def max_total_shots(novel_text: str = "") -> int:
    """按正文长度换算总格数上限：16 格/千字，夹在 30~120。"""
    n = len(novel_text or "")
    if n <= 0:
        return int(getattr(config, "STORYBOARD_MAX_SHOTS", 120))
    per_k = float(getattr(config, "STORYBOARD_SHOTS_PER_1K_CHARS", 16.0))
    cap = int(getattr(config, "STORYBOARD_MAX_SHOTS", 120))
    return max(30, min(cap, int(round(n / 1000.0 * per_k))))


def _combine_desc(a: str, b: str, limit: int = 110) -> str:
    text = (a or "").rstrip("。；; ") + "。" + (b or "").lstrip("。；; ")
    text = re.sub(r"。{2,}", "。", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    pos = max((cut.rfind(p) for p in "。！？…；;"), default=-1)
    return (cut[:pos + 1] if pos >= 20 else cut).strip()


def _mergeable(a: Shot, b: Shot) -> bool:
    if (a.scene or "") != (b.scene or ""):
        return False
    speech = [d for d in list(a.dialogues) + list(b.dialogues)
              if d.kind == "speech" and (d.text or "").strip()]
    return len(speech) <= 3


def _merge_shot(a: Shot, b: Shot) -> Shot:
    a.description = _combine_desc(a.description, b.description)
    a.source_text = _combine_desc(a.source_text, b.source_text, 120)
    for name in b.characters:
        if name not in a.characters:
            a.characters.append(name)
    max_cast = int(getattr(config, "MAX_CHARACTERS_PER_SHOT", 4) or 4)
    a.characters = a.characters[:max_cast]
    a.char_sides.update({k: v for k, v in (b.char_sides or {}).items()
                         if k in a.characters})
    a.char_positions.update({k: v for k, v in (b.char_positions or {}).items()
                             if k in a.characters})
    a.dialogues = list(a.dialogues) + [d for d in b.dialogues
                                       if len(a.dialogues) < 3]
    a.action = a.action or b.action
    a.expression = a.expression or b.expression
    a.focus = a.focus or b.focus
    a.time_of_day = a.time_of_day or b.time_of_day
    return a


def _cap_total_shots(shots: list[Shot], max_total: int) -> int:
    """超过总格数上限时，合并相邻同场景的格子（保留台词，最多 3 条）。"""
    if max_total <= 0:
        return 0
    merged = 0
    while len(shots) > max_total:
        idx = next((i for i in range(len(shots) - 1)
                    if _mergeable(shots[i], shots[i + 1])), -1)
        if idx < 0:
            break
        shots[idx] = _merge_shot(shots[idx], shots[idx + 1])
        del shots[idx + 1]
        merged += 1
    return merged


def _to_over_shoulder(s: Shot) -> bool:
    """双人改单人斜侧（旧名保留，行为已不再加"肩背前景"）。

    纯文生图会把前景肩背画成独立的第三个人，所以这里只保留说话人，
    把机位改成斜侧，用门框/桌沿做前景遮挡；对方转画外音。
    """
    if len(s.characters) != 2:
        return False
    a, b = s.characters
    speaker = next((d.speaker for d in s.dialogues
                    if d.kind == "speech" and d.speaker in (a, b)), "")
    keep = speaker or a
    other = b if keep == a else a
    side = (s.char_sides or {}).get(keep, "")
    s.characters = [keep]
    s.char_sides = {keep: side} if side else {}
    s.char_positions = ({keep: s.char_positions.get(keep, "")}
                        if s.char_positions.get(keep) else {})
    s.camera = "斜侧"
    if not s.composition:
        s.composition = "前景遮挡"
    for d in s.dialogues:
        if d.speaker == other and d.kind == "speech":
            d.offscreen = True
            d.slot = "top-center"
            d.tail = "none"
    # 描述里把对方改成"对方"：否则下一次 normalize 的 char_sync 又会把
    # 对方加回 characters，形成"改斜侧 → 又变双人 → 再改"的循环。
    if other and other in (s.description or ""):
        s.description = s.description.replace(other, "对方")
    return True


def _cap_two_shot_ratio(shots: Sequence[Shot]) -> int:
    """双人同框超过上限时，把中/近/特写改成"单人斜侧"（对方转画外音）。"""
    if len(shots) < 8:
        return 0
    max_ratio = float(getattr(config, "DIRECTOR_MAX_TWO_SHOT_RATIO", 0.25))
    allowed = int(len(shots) * max_ratio)
    current = sum(1 for s in shots if len(s.characters) == 2)
    excess = current - allowed
    if excess <= 0:
        return 0
    changed = 0
    # 双人同框只要超过 1 条台词，无条件拆单人斜侧（正反打比双人对话稳）
    for s in list(shots):
        if len(s.characters) == 2:
            speeches = [d for d in s.dialogues
                        if d.kind == "speech" and (d.text or "").strip()]
            if len(speeches) > 1 and _to_over_shoulder(s):
                changed += 1
                current -= 1
                excess = current - allowed
    # 再按比例改中/近/特写，远景/全景留作建立镜头
    for wide_ok in (False, True):
        for s in reversed(shots):
            if excess <= 0:
                break
            if len(s.characters) != 2:
                continue
            if not wide_ok and s.shot_type in ("远景", "全景"):
                continue
            if _to_over_shoulder(s):
                changed += 1
                excess -= 1
    return changed


def _offscreen_to_narration(shots: Sequence[Shot]) -> int:
    """画外音/电话台词改成旁白条：不画气泡，也就没有"尾巴指错人"。"""
    changed = 0
    for s in shots:
        for d in s.dialogues:
            if d.kind != "speech" or not (d.text or "").strip() or not d.offscreen:
                continue
            prefix = f"（{d.speaker}）" if d.speaker else "（画外）"
            d.kind = "narration"
            d.text = prefix + d.text.strip()
            d.slot = "top-center"
            d.tail = "none"
            changed += 1
    return changed


def assign_bubble_slots(shots: Sequence[Shot]) -> int:
    """
    分配气泡槽位与尾巴，并保证"气泡在说话人那一侧"。

    这是"说别人的话"的排版层修复（rules/02 P4/P5）：
      · 说话人站位是左 → 槽位必须 left；不匹配就翻转；
      · 画外音/旁白 → 顶部居中 + 无尾巴；
      · 尾巴方向按"说话人脸部锚点"算，而不是按槽位写死。
    """
    from . import bubble as bubble_mod

    changed = 0
    for s in shots:
        speech = [d for d in s.dialogues if (d.text or "").strip()]
        order = 0
        for d in speech:
            slot = d.slot if d.slot in config.BUBBLE_SLOTS else ""
            side = s.char_sides.get(d.speaker, "")
            if slot and side in ("左", "右"):
                want = "left" if side == "左" else "right"
                if slot.endswith(("left", "right")) and not slot.endswith(want):
                    slot = _flip_slot(slot)
                    d.slot = slot
                    changed += 1
            elif slot and side == "中" and slot.endswith(("left", "right")):
                # 人物在画面中央：旧工程存下来的 mid-left 要改回顶部居中
                slot = "top-center"
                d.slot = slot
                changed += 1
            if not slot:
                slot = _slot_for_speaker(s, d, order)
                d.slot = slot
                changed += 1
            if d.kind in ("narration", "sfx") or d.offscreen:
                d.tail = "none"
                d.slot = "top-center"
                order += 1
                continue
            else:
                d.tail = bubble_mod.tail_toward_speaker(s, slot, speaker=d.speaker) or d.tail \
                    or config.BUBBLE_SLOT_TO_TAIL.get(slot, "down")
            order += 1
    return changed


def _mark_flags(shots: Sequence[Shot]) -> None:
    for s in shots:
        # silent = 完全没有文字（台词/旁白/音效都没有）。
        # 只按"没有台词"算会把大量有旁白的格子误判成静默格。
        s.silent = not any((d.text or "").strip() for d in s.dialogues)
    if shots:
        for s in shots[-2:]:
            s.hook = False
        shots[-1].hook = True


def _trim_narration(shot: Shot) -> int:
    limit = int(getattr(config, "DIRECTOR_MAX_NARRATION_CHARS", 26))
    changed = 0
    for d in shot.dialogues:
        if d.kind != "narration":
            continue
        t = (d.text or "").strip()
        if len(t) <= limit:
            continue
        cut = t[:limit]
        pos = max((cut.rfind(p) for p in "。！？…，、；："), default=-1)
        if pos >= max(6, int(limit * 0.55)):
            d.text = cut[:pos] + ("…" if cut[pos] in "，、；：" else cut[pos])
        else:
            d.text = cut[:limit - 1] + "…"
        changed += 1
    return changed


# =============================================================================
# 五、封面文案
# =============================================================================

def infer_chapter_title(project: Project) -> str:
    """从原文首行 / 文件名推断"第 X 章 · 标题"。"""
    if project.chapter_title:
        return project.chapter_title
    text = (project.novel_text or "").strip()
    first = text.splitlines()[0].strip() if text else ""
    m = re.match(r"^第\s*([0-9一二三四五六七八九十百零]+)\s*[章话回节卷]\s*[·、.：:—-]?\s*(.*)$", first)
    if m:
        if not project.chapter_no or project.chapter_no == "01":
            project.chapter_no = m.group(1)
        return m.group(2).strip() or first
    if first and len(first) <= 20:
        return first
    stem = re.sub(r"\.[^.]+$", "", project.source_filename or "")
    return stem[:20] or project.title


def build_cover_copy(project: Project) -> None:
    """补齐封面钩子与尾页预告（不调模型，保持稳定）。"""
    if not project.chapter_title:
        project.chapter_title = infer_chapter_title(project)
    if not project.tagline:
        sents = _sentences(project.summary or "")
        project.tagline = _first_sentence(sents[0], 30) if sents else ""
    if not project.next_teaser:
        if project.key_points:
            project.next_teaser = _first_sentence(project.key_points[-1], 34)
        elif project.shots:
            last = project.shots[-1]
            project.next_teaser = _first_sentence(last.description or "", 34)
    project.tagline = (project.tagline or "")[:config.EPISODE_NEXT_TEASER_MAX_CHARS]
    project.next_teaser = (project.next_teaser or "")[:config.EPISODE_NEXT_TEASER_MAX_CHARS]


# =============================================================================
# 六、编排
# =============================================================================

def _clean_solo_shots(shots: Sequence[Shot], project: Project) -> int:
    """单人格去召唤词 + 过肩降斜侧；返回改动的格数。

    会跑两遍：第一次在字段补齐后，第二次在"双人改斜侧/合并"之后。
    因为配额调整可能把双人格改成单人格，描述里仍残留"两人/对方/肩背"。
    """
    fixed = 0
    for s in shots:
        names = [n for n in (s.characters or []) if n]
        if len(names) != 1:
            continue
        c = project.find_character(names[0]) if project else None
        gender = str(getattr(c, "gender", "") or "") if c is not None else ""
        changed = False
        for attr in ("description", "action", "expression", "focus", "gaze"):
            old = str(getattr(s, attr, "") or "")
            new, _hits = castguard.depopulate_text(
                old, gender=gender, visible=names, project=project)
            if attr == "description" and new:
                # 去掉旧版注入的"画面里只有 X 一个人"，出图时 castguard 会再写一次
                new = re.sub(
                    rf"画面里只有\s*{re.escape(names[0])}\s*一个人[。；]?",
                    "", new).strip(" ，；。")
            if new and new != old:
                setattr(s, attr, new)
                changed = True
        # 单人远景/全景补"人物是画面主体"，把画面边缘的空地填掉
        if str(s.shot_type or "") in ("全景", "远景"):
            desc = str(s.description or "")
            if not any(w in desc for w in ("主体", "占画面", "占据画面")):
                s.description = (desc.rstrip("。； ") +
                                 f"，{names[0]}是画面主体").strip("，")
                changed = True
        if str(s.camera or "") == "过肩":
            # 过肩镜头的前景肩背本身就是第二个人
            s.camera = "斜侧"
            changed = True
        if changed:
            fixed += 1
    return fixed


def normalize(project: Project) -> dict[str, Any]:
    """对 project.shots 做一轮导演层确定性整理，返回报告。"""
    shots = list(project.shots)
    if not shots:
        return {"before": 0, "after": 0, "issues": []}
    before = len(shots)
    report: dict[str, Any] = {"before": before}

    # 0) 描述里点名出现、但 characters 漏掉的角色补回来（否则出图没有参考图）
    synced = 0
    for s in shots:
        for c in project.characters:
            if c.name and c.name in (s.description or "") \
                    and c.name not in s.characters:
                s.characters.append(c.name)
                synced += 1
    if synced:
        report["char_sync"] = synced

    # 0.4) 旧工程里"过肩"描述残留：描述已写"另一侧只露肩背"，就只保留一个角色
    shoulder_fixed = 0
    for s in shots:
        desc = s.description or ""
        if "画面另一侧只露出" not in desc:
            continue
        # 折叠重复追加的句子
        desc = re.sub(r"(?:[。；;\s]*画面另一侧只露出一个模糊的肩背)+",
                      "；画面另一侧只露出一个模糊的肩背", desc)
        s.description = desc
        if len(s.characters) > 1:
            speaker = next((d.speaker for d in s.dialogues
                            if d.kind == "speech" and d.speaker in s.characters), "")
            keep = speaker or s.characters[0]
            drop = [n for n in s.characters if n != keep]
            for name in drop:
                if name in s.description:
                    s.description = s.description.replace(name, "对方")
            s.characters = [keep]
            s.char_sides = ({keep: s.char_sides[keep]}
                            if s.char_sides.get(keep) else {})
            s.char_positions = ({keep: s.char_positions[keep]}
                                if s.char_positions.get(keep) else {})
            shoulder_fixed += 1
    if shoulder_fixed:
        report["shoulder_fixed"] = shoulder_fixed

    # 0.5) 空镜自动补主角：避免屏幕/手机/空房间镜头凭空出现陌生男人
    #      但**物件/屏幕特写不补**——"手机屏幕占满画面"再补一个人，只会变成手机上的脸
    #      （rules/00 证据 E；rules/02 P3）。
    if bool(getattr(config, "AUTO_ASSIGN_PROTAGONIST_TO_EMPTY_SHOTS", True)):
        protagonist = next((c.name for c in project.characters
                            if c.name and ("主角" in (getattr(c, "role", "") or "")
                                           or "主人公" in (getattr(c, "role", "") or ""))), "")
        if not protagonist:
            protagonist = next((c.name for c in project.characters
                                if c.name and not getattr(c, "is_extra", False)), "")
        added_p = 0
        if protagonist:
            for s in shots:
                if s.characters or _is_detail_shot(s):
                    continue
                s.characters = [protagonist]
                s.char_sides = {protagonist: "左"}
                # 只补 characters 不够：描述里如果写的是"空荡的楼梯间"，
                # 模型仍可能画成空镜或凭空塞一个路人。补一句主体提示，
                # 让参考图真正落到画面里。
                if protagonist not in (s.description or ""):
                    s.description = ((s.description or "").rstrip("。 ")
                                     + f"，{protagonist}是画面主体。")
                added_p += 1
        if added_p:
            report["protagonist_added"] = added_p

    # 0.6) 物件/空镜语义模式（物证照 / 屏幕界面 / 环境细节）
    for s in shots:
        s.object_mode = _classify_object_mode(s)

    # 1) 描述清理
    cleaned = 0
    for s in shots:
        new = clean_description(s.description)
        if new and new != s.description:
            s.description = new
            cleaned += 1
    report["clean_desc"] = cleaned

    # 1.2) 旧工程里被"空镜补主角"注入过的角色，从纯物件格里撤掉
    detached = 0
    detail_policy = str(rules_mod.cast_policy().get("detail_shot_character_policy", "remove"))
    if detail_policy == "remove":
        for s in shots:
            if not s.characters or not _is_detail_shot(s):
                continue
            if any(name and name in (s.description or "") for name in s.characters):
                continue        # "陆鸣的眼睛特写"这种要保留
            s.characters = []
            s.char_sides = {}
            s.char_positions = {}
            detached += 1
    if detached:
        report["object_char_detached"] = detached

    # 1.5) 用描述自述修正景别（避免"全景 + 手机特写"的自相矛盾）
    report["shot_sync"] = _sync_shot_type_with_description(shots)

    # 2) 字段兜底
    filled = 0
    prev: Shot | None = None
    for i, s in enumerate(shots):
        s.time_of_day = _infer_time(s)
        s.camera = _infer_camera(s, i)
        # 有台词的格子不能用背影/主观：观众看不到说话人的嘴，气泡归属必然错
        if s.has_dialogue and s.camera in ("背影", "主观") and s.characters:
            s.camera = "斜侧"
        s.composition = _infer_composition(s)
        s.lighting = _infer_lighting(s)
        s.panel_scale = _infer_panel_scale(s)
        s.expression = _infer_expression(s)
        s.action = _infer_action(s)
        s.focus = _infer_focus(s)
        s.transition = _infer_transition(s, prev)
        filled += 1
        prev = s
    report["filled"] = filled

    # 2.4) 过肩机位：纯文生图会把前景肩背画成独立的第三个人。
    #      单/双人一律改斜侧；需要纵深用门框/桌沿做前景遮挡。
    over_fixed = 0
    for s in shots:
        if str(s.camera or "") == "过肩":
            s.camera = "斜侧"
            over_fixed += 1
    if over_fixed:
        report["over_shoulder_fixed"] = over_fixed

    # 2.5) 单人格去"召唤词"：两人/对方/路人/肩背/另一个角色名都会让模型补人。
    #      只改描述类字段，不改 shot.scene（场景名要和 project.scenes 对得上；
    #      出图时由 castguard 再把场景名替换成"空无一人的…"）。
    solo_fixed = _clean_solo_shots(shots, project)
    if solo_fixed:
        report["solo_guard_fixed"] = solo_fixed

    # 3) 节奏修正
    report["establishing"] = _enforce_scene_establishing(shots)
    report["shot_run"] = _enforce_shot_run(shots)
    report["camera_run"] = _enforce_camera_run(shots)
    report["scale_mix"] = _enforce_panel_scale_mix(shots)
    report["closeup"] = _enforce_closeup_ratio(shots)

    # 3.5) 台词归属兜底：说话人不在画面里（或没写说话人）→ 画外音，不指向任何人
    fixed_speakers = 0
    for s in shots:
        for d in s.dialogues:
            if d.kind != "speech" or not (d.text or "").strip():
                continue
            if d.speaker and d.speaker in (s.characters or []):
                continue
            if not d.offscreen:
                d.offscreen = True
                fixed_speakers += 1
            d.tail = "none"
            if not d.slot or not d.slot.startswith("top"):
                d.slot = "top-center"
    if fixed_speakers:
        report["speakers_to_offscreen"] = fixed_speakers

    # 3.6) 长台词拆气泡 + 空说话人归位 + 画外音转旁白条
    report["long_speech_split"] = sum(_split_long_speech(s) for s in shots)
    report["empty_speaker_fixed"] = _fix_empty_speakers(shots)
    report["offscreen_narration"] = _offscreen_to_narration(shots)

    # 3.7) 分镜配额：总格数 → 双人 → 近景；不达标就确定性重排
    total_cap = max_total_shots(project.novel_text if project else "")
    report["total_capped"] = _cap_total_shots(shots, total_cap)
    if report["total_capped"]:
        # normalize 里的 shots 可能是 project.shots 的副本，必须写回并重排
        project.shots = shots
        project.reindex_shots()
    report["two_shot_capped"] = _cap_two_shot_ratio(shots)
    report["closeup_capped"] = _cap_closeup_ratio(shots)
    report["establishing_after"] = _enforce_scene_establishing(shots)
    # 双人改单人斜侧后可能新增画外音，再转一次旁白条
    report["offscreen_narration"] += _offscreen_to_narration(shots)
    # 合并/改斜侧会制造连续同景别/同机位：重新打断一次
    report["shot_run_after"] = _enforce_shot_run(shots)
    report["camera_run_after"] = _enforce_camera_run(shots)
    # 2.5 的第二次：配额调整可能把双人改成单人，描述里会残留"两人/对方/肩背"
    report["solo_guard_fixed"] = report.get("solo_guard_fixed", 0) \
        + _clean_solo_shots(shots, project)

    # 4) 轴线 + 气泡槽位 + 标记
    _assign_sides(shots, project)
    report["slots"] = assign_bubble_slots(shots)
    report["narration_trim"] = sum(_trim_narration(s) for s in shots)
    _mark_flags(shots)

    # 5) 封面文案
    build_cover_copy(project)

    report["after"] = len(project.shots)
    report["issues"] = validate(project)
    return report


# =============================================================================
# 七、自检
# =============================================================================

def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a or "", b or "").ratio()


def validate(project: Project) -> list[dict[str, Any]]:
    """快看级分镜自检（导演层）。返回可直接给界面展示的问题列表。"""
    shots = project.shots
    if not shots:
        return [{"level": "warn", "code": "empty", "message": "还没有分镜", "shots": []}]
    issues: list[dict[str, Any]] = []

    # 1) 非画面描述
    bad_desc: list[int] = []
    for s in shots:
        d = (s.description or "").strip()
        if len(d) < int(getattr(config, "DIRECTOR_DESCRIPTION_MIN_CHARS", 18)):
            bad_desc.append(s.index)
        elif len(d) > int(getattr(config, "DIRECTOR_DESCRIPTION_MAX_CHARS", 120)):
            bad_desc.append(s.index)
        elif _QUOTE_RE.search(d):
            bad_desc.append(s.index)
    if bad_desc:
        issues.append({
            "level": "danger", "code": "bad_description",
            "message": f"{len(bad_desc)} 格的画面描述不合格（过短/过长/还带台词），"
                       f"出图会失控：第 {bad_desc[:8]} 格…",
            "shots": bad_desc,
        })

    # 2) 台词量
    heavy = [s.index for s in shots
             if len([d for d in s.dialogues if d.kind == "speech" and (d.text or "").strip()])
             > int(getattr(config, "DIRECTOR_MAX_DIALOGUES", 3))]
    long_line = [s.index for s in shots
                 for d in s.dialogues
                 if d.kind == "speech" and len((d.text or "").strip())
                 > int(getattr(config, "DIRECTOR_MAX_DIALOGUE_CHARS", 20))]
    if heavy:
        issues.append({
            "level": "warn", "code": "dialogue_overload",
            "message": f"{len(heavy)} 格台词超过 3 条，气泡会压住画面：第 {heavy[:8]} 格…",
            "shots": heavy,
        })
    if long_line:
        issues.append({
            "level": "warn", "code": "dialogue_too_long",
            "message": f"{len(set(long_line))} 格有单条超过 20 字的台词，"
                       f"手机上会挤成一坨：第 {sorted(set(long_line))[:8]} 格…",
            "shots": sorted(set(long_line)),
        })

    # 3) 景别 / 机位连续
    for code, label, attr, max_run in (
        ("same_shot_run", "景别", "shot_type", int(config.STORYBOARD_MAX_SAME_SHOT_RUN)),
        ("same_angle_run", "机位", "camera", int(getattr(config, "DIRECTOR_MAX_SAME_ANGLE_RUN", 2))),
    ):
        worst, worst_at, run = 1, 1, 1
        for i in range(1, len(shots)):
            if getattr(shots[i], attr) == getattr(shots[i - 1], attr):
                run += 1
                if run > worst:
                    worst, worst_at = run, shots[i].index
            else:
                run = 1
        if worst > max_run:
            issues.append({
                "level": "warn", "code": code,
                "message": f"第 {worst_at} 格附近连续 {worst} 格同{label}，"
                           f"画面会像翻 PPT；快看要求相邻格必须有变化",
                "shots": [worst_at],
            })

    # 4) 建立镜头
    no_establish: list[int] = []
    prev_scene = None
    for i, s in enumerate(shots):
        if i == 0 or (s.scene or "") != (prev_scene or ""):
            if i > 0 and not _is_detail_shot(s) \
                    and s.shot_type not in config.STORYBOARD_ESTABLISHING_TYPES:
                no_establish.append(s.index)
        prev_scene = s.scene
    if no_establish:
        issues.append({
            "level": "warn", "code": "no_establishing",
            "message": f"{len(no_establish)} 个新场景没有建立镜头（远景/全景），"
                       f"读者会不知道「这是哪」：第 {no_establish[:6]} 格…",
            "shots": no_establish,
        })

    # 5) 近景占比
    close = sum(1 for s in shots if s.shot_type in ("近景", "特写"))
    need = int(len(shots) * float(getattr(config, "DIRECTOR_CLOSEUP_MIN_RATIO", 0.24)))
    if close < need:
        issues.append({
            "level": "warn", "code": "low_closeup",
            "message": f"近景/特写只有 {close} 格（建议 ≥{need} 格），情绪没有落点",
            "shots": [],
        })

    # 6) 画幅节奏
    scales = [s.panel_scale for s in shots if s.panel_scale]
    if len(shots) >= 8:
        if len(set(scales)) < 3:
            issues.append({
                "level": "warn", "code": "flat_scale",
                "message": f"画幅只用了 {sorted(set(scales))}，节奏会平；"
                           f"至少要有 S/M/L/XL 中的 3 种",
                "shots": [],
            })

    # 7) 悬念钩子
    if not any(s.hook for s in shots):
        issues.append({
            "level": "warn", "code": "no_hook",
            "message": "没有标记章末悬念格（hook），读者没有追更动力",
            "shots": [shots[-1].index],
        })

    # 8) 相邻描述雷同
    dup: list[int] = []
    for i in range(1, len(shots)):
        if _sim(shots[i].description, shots[i - 1].description) > 0.88:
            dup.append(shots[i].index)
    if dup:
        issues.append({
            "level": "warn", "code": "duplicate_panel",
            "message": f"{len(dup)} 格与上一格画面高度雷同，读者会觉得「重复画了一张」："
                       f"第 {dup[:8]} 格…",
            "shots": dup,
        })

    # 9) 画外音却出现在画面里
    off_bad: list[int] = []
    for s in shots:
        if not s.characters:
            continue
        desc = s.description or ""
        for name in s.characters:
            if name and len(name) >= 2 and name not in desc:
                off_bad.append(s.index)
                break
    if off_bad:
        issues.append({
            "level": "info", "code": "char_maybe_offscreen",
            "message": f"{len(off_bad)} 格把自己列出场角色写进了 characters，"
                       f"但画面描述里没提到他/她；如果是电话/画外音，应从 characters 移除",
            "shots": off_bad,
        })

    # 9.5) 分镜配额（总格数 / 双人 / 近景）
    cap_total = max_total_shots(project.novel_text or "")
    if len(shots) > int(cap_total * 1.15):
        issues.append({
            "level": "warn", "code": "too_many_shots",
            "message": f"{len(shots)} 格超过本话建议上限 {cap_total} 格（16 格/千字），"
                       f"剧情会被拖碎，建议合并同场景静止格",
            "shots": [],
        })
    two_ratio = sum(1 for s in shots if len(s.characters or []) == 2) / max(1, len(shots))
    two_max = float(getattr(config, "DIRECTOR_MAX_TWO_SHOT_RATIO", 0.25))
    if two_ratio > two_max + 0.05:
        issues.append({
            "level": "warn", "code": "two_shot_ratio",
            "message": f"双人同框占 {two_ratio:.0%}（建议 ≤{two_max:.0%}），"
                       f"纯文生图下双人越多越容易串脸，建议拆成单人反应 + 画外音"
                       f"（过肩也会多画一个前景人）",
            "shots": [],
        })
    close_ratio = sum(1 for s in shots if s.shot_type in ("近景", "特写")) / max(1, len(shots))
    close_max = float(getattr(config, "DIRECTOR_CLOSEUP_MAX_RATIO", 0.30))
    if close_ratio > close_max + 0.05:
        issues.append({
            "level": "warn", "code": "closeup_high",
            "message": f"近景+特写占 {close_ratio:.0%}（建议 ≤{close_max:.0%}），"
                       f"大特写太多会「情绪通货膨胀」，建议降成中景",
            "shots": [],
        })

    # 10) 连续台词格
    run = 0
    worst_at = 1
    for s in shots:
        if any(d.kind == "speech" and (d.text or "").strip() for d in s.dialogues):
            run += 1
            if run == 4:
                worst_at = s.index
        else:
            run = 0
    if run >= 4:
        issues.append({
            "level": "info", "code": "dialogue_clump",
            "message": f"第 {worst_at} 格附近连续 4 格以上都有台词，建议插一个无台词反应格",
            "shots": [worst_at],
        })

    # ------------------------------------------------------------------
    # 11) 本次新增：角色一致性 / 可拍性 / 台词归属（rules/01、02、04）
    # ------------------------------------------------------------------
    # 11.1 角色没身份卡
    no_card = [c.name for c in project.characters
               if c.name and len(c.identity_card()) < 10]
    if no_card:
        issues.append({
            "level": "warn", "code": "character_no_card",
            "message": f"{len(no_card)} 个角色还没有可复用的身份卡（人设/关键词为空）："
                       f"{'、'.join(no_card[:6])}；出图会每格换脸",
            "shots": [],
        })

    # 11.2 角色撞车（脸型/眼型/发型/服装 共享 ≥3 项，或人设过于相似）
    cfg = rules_mod.collision_rules()
    buckets = cfg.get("compare_buckets") or ["face", "eyes", "hair", "outfit"]
    max_shared = int(cfg.get("max_shared_buckets", 2))
    sim_limit = float(cfg.get("design_similarity_limit", 0.45))
    collisions: list[str] = []
    resolved_pairs: list[str] = []
    chars = [c for c in project.characters if c.name]
    for i, a in enumerate(chars):
        for b in chars[i + 1:]:
            ba, bb = a.trait_buckets(), b.trait_buckets()
            shared = [k for k in buckets if (ba.get(k) or set()) & (bb.get(k) or set())]
            da = re.sub(r"【[^】]{1,16}】", "", a.design or a.identity_card())
            db = re.sub(r"【[^】]{1,16}】", "", b.design or b.identity_card())
            sim = _sim(da, db)
            tag_hit = bool(a.derived_identity_tag()) and \
                a.derived_identity_tag() == b.derived_identity_tag()
            if (len(shared) > max_shared) or (sim > sim_limit and len(shared) >= 2) or tag_hit:
                item = f"{a.name}×{b.name}（{'、'.join(shared) or '整体'}，相似度 {sim:.2f}）"
                resolved = bool(a.design_patchs) and bool(b.design_patchs) and \
                    a.derived_identity_tag() != b.derived_identity_tag()
                (resolved_pairs if resolved else collisions).append(item)
    if collisions:
        issues.append({
            "level": "danger", "code": "character_collision",
            "message": f"{len(collisions)} 对角色长得太像，同框时会变成双胞胎："
                       f"{'；'.join(collisions[:4])}…",
            "shots": [],
        })
    if resolved_pairs:
        issues.append({
            "level": "info", "code": "character_collision_patched",
            "message": f"{len(resolved_pairs)} 对角色已用唯一点缀区分（发带/围巾/眼镜/配饰），"
                       f"重出图后请抽检确认；仍分不清就重新生成人设",
            "shots": [],
        })

    # 11.3 描述在复述镜头词 / 出现注入的 meta 语
    camera_bad = [s.index for s in shots
                  if re.match(r"^(平视|俯视|仰视|斜侧|过肩|主观|背影|远景|全景|中景|近景|特写|俯拍|仰拍)",
                              (s.description or "").strip())
                  or any(p in (s.description or "") for p in
                         ("作为视觉主体", "出现在画面中"))]
    if camera_bad:
        issues.append({
            "level": "warn", "code": "desc_camera_words",
            "message": f"{len(camera_bad)} 格的描述开头在复述景别/机位（会和镜头字段打架）："
                       f"第 {camera_bad[:8]} 格…",
            "shots": camera_bad,
        })

    # 11.4 物件/屏幕特写里塞了角色
    obj_bad = [s.index for s in shots if s.characters and _is_detail_shot(s)
               and not any(n in (s.description or "") for n in s.characters)]
    if obj_bad:
        issues.append({
            "level": "warn", "code": "object_shot_with_face",
            "message": f"{len(obj_bad)} 格是屏幕/物件特写却列了角色，模型会在屏幕上画脸："
                       f"第 {obj_bad[:6]} 格…",
            "shots": obj_bad,
        })

    # 11.5 说话人不在画面里 / 没写说话人
    speaker_bad: list[int] = []
    for s in shots:
        for d in s.dialogues:
            if d.kind != "speech" or not (d.text or "").strip():
                continue
            if not d.speaker:
                speaker_bad.append(s.index)
                break
            if d.speaker not in (s.characters or []) and not d.offscreen:
                speaker_bad.append(s.index)
                break
    if speaker_bad:
        issues.append({
            "level": "warn", "code": "speaker_not_visible",
            "message": f"{len(speaker_bad)} 格有台词但没写说话人（或说话人在画面外却没标画外音），"
                       f"气泡只能乱放：第 {speaker_bad[:8]} 格…",
            "shots": speaker_bad,
        })

    # 11.6 气泡在说话人的反侧
    side_bad: list[int] = []
    for s in shots:
        for d in s.dialogues:
            if d.kind != "speech" or not (d.text or "").strip():
                continue
            side = (s.char_sides or {}).get(d.speaker, "")
            slot = d.slot or ""
            if side == "左" and slot.endswith("right"):
                side_bad.append(s.index)
                break
            if side == "右" and slot.endswith("left"):
                side_bad.append(s.index)
                break
    if side_bad:
        issues.append({
            "level": "warn", "code": "bubble_side_mismatch",
            "message": f"{len(side_bad)} 格的气泡在说话人的反侧（读者会以为是对面在说话）："
                       f"第 {side_bad[:8]} 格…",
            "shots": side_bad,
        })

    # 11.7 双人格台词过多 / 三人以上
    # 只统计"不同说话人"：同一个人一句长台词拆成两个气泡是正常处理，
    # 不应反过来报"双人同框台词过多"（否则整理分镜永远无法清零）。
    two_heavy = []
    for s in shots:
        if len(s.characters or []) != 2:
            continue
        speech = [d for d in s.dialogues
                  if d.kind == "speech" and (d.text or "").strip()]
        speakers = {d.speaker for d in speech if d.speaker}
        if len(speech) > 1 and len(speakers) > 1:
            two_heavy.append(s.index)
    if two_heavy:
        issues.append({
            "level": "warn", "code": "two_shot_dialogue",
            "message": f"{len(two_heavy)} 格是双人同框却有 2 条以上台词，建议拆成正反打："
                       f"第 {two_heavy[:8]} 格…",
            "shots": two_heavy,
        })
    max_cast = int(getattr(config, "MAX_CHARACTERS_PER_SHOT", 4) or 4)
    multi_heavy = [s.index for s in shots
                   if len(s.characters or []) >= 3
                   and len([d for d in s.dialogues
                            if d.kind == "speech" and (d.text or "").strip()]) > 1]
    if multi_heavy:
        issues.append({
            "level": "warn", "code": "multi_shot_dialogue",
            "message": f"{len(multi_heavy)} 格是三人以上同框却有 2 条以上台词，"
                       f"气泡和脸都会挤；建议只留 1 条台词，其余转画外音："
                       f"第 {multi_heavy[:8]} 格…",
            "shots": multi_heavy,
        })
    crowd = [s.index for s in shots if len(s.characters or []) > max_cast]
    if crowd:
        issues.append({
            "level": "danger", "code": "too_many_characters",
            "message": f"{len(crowd)} 格有超过 {max_cast} 个角色，纯文生图会串味，"
                       f"必须拆格：第 {crowd[:6]} 格…",
            "shots": crowd,
        })
    multi = [s.index for s in shots if 3 <= len(s.characters or []) <= max_cast]
    if multi:
        issues.append({
            "level": "info", "code": "multi_shot_cast",
            "message": f"{len(multi)} 格是 3~{max_cast} 人群像；已按逐人分块 + "
                       f"差异对照 + 左右留空出图，建议抽检每张脸是否可区分",
            "shots": multi,
        })

    # 11.8 人数/乱入风险（castguard）：单人召唤词、单人过肩、双人站位与撞车
    seen_guard: set[tuple[str, tuple[int, ...]]] = set()
    for s in shots:
        for item in castguard.diagnose(project, s):
            if item.get("code") == "too_many_characters":
                continue  # 11.7 已经报过
            key = (str(item.get("code", "")), tuple(item.get("shots") or []))
            if key in seen_guard:
                continue
            seen_guard.add(key)
            issues.append(item)

    return issues


def summary_text(report: dict[str, Any]) -> str:
    """把 normalize 报告压成一行日志。"""
    bits: list[str] = []
    for key, label in (
        ("clean_desc", "清理描述"), ("shot_sync", "纠正景别"),
        ("char_sync", "补角色"), ("protagonist_added", "空镜补主角"),
        ("filled", "补镜头字段"),
        ("establishing", "补建立镜头"), ("shot_run", "打断同景别"),
        ("camera_run", "打断同机位"), ("scale_mix", "调整画幅"),
        ("closeup", "补近景"), ("slots", "分配气泡位"),
        ("narration_trim", "精简旁白"),
        ("object_char_detached", "物件格撤角色"),
        ("over_shoulder_fixed", "过肩降斜侧"),
        ("solo_guard_fixed", "单人防乱入"),
        ("speakers_to_offscreen", "画外音归位"),
        ("long_speech_split", "长台词拆泡"),
        ("empty_speaker_fixed", "空说话人归位"),
        ("closeup_capped", "近景降中景"),
        ("offscreen_narration", "画外音转旁白"),
        ("total_capped", "合并超量格"),
        ("two_shot_capped", "双人改斜侧"),
    ):
        if report.get(key):
            bits.append(f"{label} {report[key]}")
    return "；".join(bits) or "无需调整"
