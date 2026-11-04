# -*- coding: utf-8 -*-
"""人数锁与角色隔离：分镜提示词的"防撞脸 / 防乱入"层。

解决的问题
----------
1. **多人撞脸**：两个角色的外观词挤在同一段里，文本编码器会把特征平均化。
   本模块把每个角色写成独立区块，并在两人之间插入"具体差异对照"与
   "左右分区 + 中间留空"的空间约束。
2. **单人乱入**：场景里的"街头 / 酒馆 / 人群 / 对方 / 肩背"会被模型当成
   补人的理由。本模块在**不改原始分镜数据**的前提下，生成一份"出图视图"：
   把召唤词换成空场景描述，把过肩机位降成斜侧，并补上"唯一人物"的构图锁。
3. **提示词自相矛盾**：正向里写"不要画第二个人"会把"第二个人"喂给模型。
   本模块只输出正面描述；否定词全部交给 ``prompts.negative_prompt``。

设计原则
--------
- 纯规则、零模型调用，可在离线自检里逐条断言；
- 不改 ``Project`` / ``Shot`` 原始数据，返回副本；
- 不依赖 ``prompts``，避免循环导入；需要的人设字段都从 ``Character`` 读。
"""

from __future__ import annotations

import ast
import copy
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import config

# ---------------------------------------------------------------------------
# 词表
# ---------------------------------------------------------------------------

# 单人镜头里会"召唤人群"的词：按长词优先替换，避免"夜晚的街头"只换到"街头"
_SCENE_EMPTY_RULES: tuple[tuple[str, str], ...] = (
    ("夜晚的街头", "空无一人的夜晚街巷"),
    ("深夜的街头", "空无一人的深夜街巷"),
    ("黄昏的街头", "空无一人的黄昏街道"),
    ("热闹的街头", "空无一人的街头"),
    ("夜晚的街道", "空无一人的夜晚街道"),
    ("深夜的街道", "空无一人的深夜街道"),
    ("街头", "空无一人的街头"),
    ("街道", "空无一人的街道"),
    ("街巷", "空无一人的街巷"),
    ("小巷", "空无一人的小巷"),
    ("走廊", "空无一人的走廊"),
    ("过道", "空无一人的过道"),
    ("电梯", "空无一人的电梯"),
    ("酒馆", "吧台空置的安静酒馆"),
    ("酒吧", "吧台空置的安静酒吧"),
    ("咖啡馆", "空座位的咖啡馆"),
    ("茶馆", "空座位的茶馆"),
    ("餐厅", "空座位的餐厅"),
    ("食堂", "空座位的食堂"),
    ("广场", "空旷的广场"),
    ("车站", "空旷的站台"),
    ("站台", "空旷的站台"),
    ("集市", "收摊后的集市"),
    ("商场", "打烊后的商场"),
    ("学校", "放学后的学校"),
    ("医院", "深夜的医院走廊"),
    ("大厅", "空无一人的大厅"),
    ("办公室", "空无一人的办公室"),
    ("机房", "空无一人的机房"),
    ("房间", "空无一人的房间"),
    ("屋内", "空无一人的屋内"),
    ("屋子", "空无一人的屋子"),
)

# 人群 / 关系词：单人镜头里一律不保留
_CROWD_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("画面另一侧只露出一个模糊的肩背", "画面另一侧是空着的门框"),
    ("画面另一侧只露出半截肩背", "画面另一侧是空着的门框"),
    ("画面另一侧只露出一个肩背", "画面另一侧是空着的门框"),
    ("只露出半截肩背", "画面边缘是空着的门框"),
    ("只露出一个模糊的肩背", "画面边缘是空着的门框"),
    ("前景是另一个人的肩背", "前景是门框与墙面"),
    ("前景是对方的肩背", "前景是门框与墙面"),
    ("另一个人的肩背", "门框的轮廓"),
    ("对方的肩背", "门框的轮廓"),
    ("画面外的方向", "画面之外"),
    ("我的手心", "画面前景"),
    ("他的手心", "画面前景"),
    ("短衣帮", "空荡的背景"),
    ("长衫主顾", "空荡的背景"),
    ("长衫客", "空荡的背景"),
    ("酒客", "空荡的背景"),
    ("看客", "空荡的背景"),
    ("邻舍孩子", "空荡的背景"),
    ("孩子们", "空荡的背景"),
    ("众人", "空荡的背景"),
    ("大家", "空荡的背景"),
    ("人群", "空荡的背景"),
    ("群众", "空荡的背景"),
    ("围观者", "空荡的背景"),
    ("围观的人", "空荡的背景"),
    ("观众", "空荡的背景"),
    ("路人", "空荡的背景"),
    ("行人", "空荡的背景"),
    ("旁人", "空荡的背景"),
    ("陌生人", "空荡的背景"),
    ("其他人物", "空荡的背景"),
    ("背景人物", "空荡的背景"),
    ("来往的人", "空荡的背景"),
    ("对方", "画面外的方向"),
    ("对手", "画面外的方向"),
    ("同伴", "画面外的方向"),
    ("同行者", "画面外的方向"),
    ("搭档", "画面外的方向"),
    ("队友", "画面外的方向"),
    ("伙伴", "画面外的方向"),
    ("另一个角色", "画面外的方向"),
    ("另一个人", "画面外的方向"),
    ("两人", "她"),
    ("二人", "她"),
    ("双人", "她"),
    ("他们", "她"),
    ("她们", "她"),
    ("人影", "灯影"),
    ("身影", "轮廓"),
    ("有人", "空荡"),
    ("肩膀", "肩线"),
)

# 单人镜头里"整句删除"的人群名词：这些词一旦出现，模型就会补人；
# 只替换词会留下"空荡的背景带笑"这种病句，因此按分句整段丢掉。
_CROWD_NOUNS = (
    "短衣帮", "长衫主顾", "长衫客", "酒客", "看客", "邻舍孩子", "孩子们",
    "众人", "大家", "人群", "群众", "围观者", "围观的人", "观众", "路人",
    "行人", "旁人", "陌生人", "其他人物", "背景人物", "来往的人",
)

_OTHER_PERSON_RE = re.compile(
    r"(前景|画面边缘|另一侧|身后|旁边|对面)[^。；，]{0,8}"
    r"(肩背|肩膀|半边脸|半张脸|侧脸|人影|轮廓)"
)

# 双人区块里用来判断"外套轮廓差异"的词
_LONG_OUTER = ("长款", "长风衣", "过膝", "长外套", "大衣", "风衣", "披风", "斗篷")
_SHORT_OUTER = ("短款", "短外套", "夹克", "工装", "西装外套", "开衫", "马甲")

_OUTERWEAR_WORDS = ("风衣", "大衣", "夹克", "外套", "披风", "斗篷", "羽绒服",
                    "西装外套", "开衫", "马甲", "雨衣")


def _clean(text: str) -> str:
    """清理替换后留下的重复标点与空格。"""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    t = re.sub(r"[，,]{2,}", "，", t)
    t = re.sub(r"[；;]{2,}", "；", t)
    t = re.sub(r"[。]{2,}", "。", t)
    t = re.sub(r"[，；、]\s*[。；]", "。", t)
    return t.strip(" ，、；;。\t\n")


def _one_clause(text: str, limit: int) -> str:
    """取一个短句，超长按标点截断，绝不切在词中间。"""
    t = _clean(text)
    if not t:
        return ""
    parts = [p for p in re.split(r"[、，,；;。]", t) if p.strip()]
    out = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and len(out) + len(p) + 1 > limit:
            break
        out = f"{out}、{p}" if out else p
        if len(out) >= limit * 0.8:
            break
    return out[:limit].strip("、，； ")


def _gender_pronoun(gender: str) -> str:
    return "他" if str(gender or "").strip() in ("男", "男性", "男孩") else "她"


# ---------------------------------------------------------------------------
# 单人场景：去人群
# ---------------------------------------------------------------------------

def empty_scene_text(text: str) -> tuple[str, list[str]]:
    """把场景描述改成"空无一人"的版本，返回 (新文本, 命中的词)。"""
    t = str(text or "")
    hits: list[str] = []
    if not t:
        return "", hits
    # 已经写过"空无一人/空荡/空置/打烊/收摊"就不再叠词
    already_empty = any(w in t for w in ("空无一人", "空荡", "空置", "空座位",
                                         "打烊", "收摊", "空旷"))
    if not already_empty:
        for old, new in _SCENE_EMPTY_RULES:
            if old in t:
                t = t.replace(old, new)
                hits.append(old)
                break
    return _clean(t), hits


def depopulate_text(text: str, gender: str = "",
                    visible: Sequence[str] = (),
                    project: Any = None) -> tuple[str, list[str]]:
    """单人称谓清洗：去掉人群词、关系词、画面外角色名。

    只替换，不删除整句；替换后由 ``_clean`` 收口。返回命中的原词，供校验层
    在导演自检里报"这一格有乱入风险"。
    """
    if isinstance(text, dict):
        # 旧工程把 gaze 存成 {"陆鸣": "看向走廊深处"}，直接 str() 会进提示词
        t = "；".join(str(v) for v in text.values() if str(v or "").strip())
    elif isinstance(text, (list, tuple)):
        t = "；".join(str(v) for v in text if str(v or "").strip())
    else:
        t = str(text or "")
    # 兼容旧工程：gaze 被存成字符串形式的 dict/list（"{'陆鸣': '看向…'}"）
    if t[:1] in ("{", "[") and t[-1:] in ("}", "]"):
        try:
            parsed = ast.literal_eval(t)
            if isinstance(parsed, dict):
                t = "；".join(str(v) for v in parsed.values() if str(v or "").strip())
            elif isinstance(parsed, (list, tuple)):
                t = "；".join(str(v) for v in parsed if str(v or "").strip())
        except (ValueError, SyntaxError):
            pass
    hits: list[str] = []
    if not t:
        return "", hits

    # 画面外角色名 -> 画面外的方向（D5：提示词里出现谁，模型就画谁）
    if project is not None:
        visible_set = set(visible or [])
        for c in getattr(project, "characters", []) or []:
            name = str(getattr(c, "name", "") or "").strip()
            if name and name not in visible_set and name in t:
                t = t.replace(name, "画面外的方向")
                hits.append(name)

    # 另一侧肩背 / 半张脸这类"合法路人"最容易变成第二个人
    if _OTHER_PERSON_RE.search(t):
        hits.append("肩背/半张脸")
        t = _OTHER_PERSON_RE.sub("边缘的墙面", t)

    # 单人镜头里出现"短衣帮/酒客/看客/众人"时，整分句丢掉。
    # 只把词替换成"空荡的背景"会留下"空荡的背景带笑"这种病句，
    # 模型仍会按"带笑"补出一圈人。
    clauses = re.split(r"(?<=[，；。])", t)
    kept_clauses: list[str] = []
    for clause in clauses:
        if any(n in clause for n in _CROWD_NOUNS):
            hits.extend(n for n in _CROWD_NOUNS if n in clause)
            continue
        kept_clauses.append(clause)
    if len(kept_clauses) < len(clauses):
        t = "".join(kept_clauses).strip("，；。 ")

    pronoun = _gender_pronoun(gender)
    for old, new in _CROWD_REPLACEMENTS:
        if old in t:
            hits.append(old)
            replacement = new
            if old in ("两人", "二人", "双人", "他们", "她们"):
                replacement = pronoun
            t = t.replace(old, replacement)
    return _clean(t), hits


# ---------------------------------------------------------------------------
# 角色卡：短、硬、逐字复用
# ---------------------------------------------------------------------------

def _split_outfit(text: str, limit: int = 46) -> str:
    """把服装压成 1~3 个短语：外层 + 内搭 + 下装，去掉场合/材质长句。"""
    t = _clean(text)
    if not t:
        return ""
    parts = [p.strip() for p in re.split(r"[、，,；;。]", t) if p.strip()]
    keep: list[str] = []
    for p in parts:
        if any(w in p for w in ("特殊场合", "场合", "材质", "面料", "轮廓",
                                "袖口", "下摆", "无其他变化", "无变化")):
            continue
        if p.startswith(("不要", "别", "禁止")):
            continue
        keep.append(p)
        if len("、".join(keep)) >= limit:
            break
    if not keep:
        keep = parts[:2]
    return _one_clause("、".join(keep), limit)


def _design_outfit(character: Any) -> str:
    design = str(getattr(character, "design", "") or "")
    m = re.search(r"【服装风格】([\s\S]*?)(?=【|$)", design)
    return (m.group(1).strip() if m else design).strip()


def _appearance_features(character: Any) -> dict[str, str]:
    """只跑过分析、还没生成人设时的硬特征兜底。

    ``appearance`` 是第 2/3 步从原文抄出的可画外貌短句；按分句粗分到
    face/hair/outfit/signature/build，比只输出“男”要可靠得多。
    """
    text = str(getattr(character, "appearance", "") or "").strip()
    if not text:
        return {}
    out: dict[str, str] = {}
    for part in [p.strip() for p in re.split(r"[，、；。]", text) if p.strip()]:
        if not out.get("hair") and any(w in part for w in ("发", "胡", "须")):
            out["hair"] = part
        if not out.get("face") and any(w in part for w in
                                       ("脸", "面", "皱纹", "颧", "肤色")):
            out["face"] = part
        if not out.get("outfit") and any(w in part for w in
                                         ("衫", "袄", "袍", "衣", "裤", "裙", "褂")):
            out["outfit"] = part
        if not out.get("signature") and any(w in part for w in
                                            ("伤", "疤", "眼镜", "袖口", "满手", "泥")):
            out["signature"] = part
        if not out.get("build") and any(w in part for w in
                                        ("高大", "清瘦", "瘦削", "微胖",
                                         "魁梧", "佝偻", "矮小", "壮实")):
            out["build"] = part
    return out


def _age_anchor(character: Any) -> str:
    """多人卡片里的年龄锚：优先 age 字段，缺失时只从人设取“少年/中年”等年龄带。

    不取“约四十岁”这类数字年龄：主视图才是视觉权威，数字年龄可能和主视图打架。
    """
    raw = str(getattr(character, "age", "") or "").strip()
    if raw and raw not in ("未知", "不确定", "?"):
        m = re.search(r"([一二三四五六七八九十百零两\d]{1,3}岁(?:上下|左右|出头)?)", raw)
        if m:
            return m.group(1)
        for word in ("少年", "青年", "中年偏老", "中年", "老年", "孩童"):
            if word in raw:
                return word
    design = str(getattr(character, "design", "") or "")
    for word in ("少年", "青年", "中年偏老", "中年", "老年", "孩童"):
        if word in design:
            return word
    return ""


def compact_card(character: Any, outfit_override: str = "",
                 limit: int = 128, brief: bool = False,
                 detail_level: str = "") -> str:
    """生成可逐字复用的短身份卡（≤limit 字，按字段整段丢弃）。

    比 ``Character.identity_card`` 更短，专供双人同框：两人各占一段，
    不会因为总预算超限被从中间切断。``brief=True`` 时进一步压缩到
    "名字/性别/脸/发/眼/主服装/记忆点"七项，给双人对照留预算。

    ``detail_level`` 是分镜景别（wide/full/medium/close/extreme）：
    远景只保留"年龄、体型、发型轮廓、服装主色"这类大面积特征，
    小记忆点（痣、缺指、单只耳饰）留给近景/特写，避免占字数却画不出来。
    """
    if character is None:
        return ""
    hf = dict(getattr(character, "hard_features", {}) or {})
    if not any(hf.get(k) for k in ("face", "hair", "eyes", "outfit",
                                   "build", "signature")):
        hf.update({k: v for k, v in _appearance_features(character).items()
                   if not hf.get(k)})
    name = str(getattr(character, "name", "") or "").strip()
    gender = str(getattr(character, "gender", "") or "").strip()
    age = _age_anchor(character)
    level = str(detail_level or "").lower()
    # 远景/全景：小记忆点在画面里只有几个像素，写了也会被模型忽略，
    # 反而稀释体型/服装轮廓；这里直接不给它们预算。
    drop_small = level in ("wide", "full")
    face = _one_clause(str(hf.get("face", "") or ""), 12 if drop_small else (10 if brief else 16))
    hair = _one_clause(str(hf.get("hair", "") or ""), 24 if drop_small else (20 if brief else 30))
    eyes = "" if level == "wide" else _one_clause(
        str(hf.get("eyes", "") or ""), 12 if brief else 18)
    outfit = _split_outfit(outfit_override or hf.get("outfit", "")
                           or _design_outfit(character),
                           26 if drop_small else (22 if brief else 46))
    sig = "" if drop_small else _one_clause(
        str(getattr(character, "derived_identity_tag", lambda: "")()
            or getattr(character, "identity_tag", "") or ""), 16 if brief else 22)

    # 去掉对"辨识"帮助不大、但很占字数的套话
    for junk in ("、双眼对称", "双眼对称、", "、额前干净", "额前干净、",
                 "、两侧推短", "、头顶向后梳拢"):
        hair = hair.replace(junk, "")
        eyes = eyes.replace(junk, "")

    fields: list[str] = [x for x in (gender, age, face, hair, eyes, outfit, sig) if x]
    if not fields:
        return str(getattr(character, "identity_card", lambda: "")() or "")

    def compose(items: list[str]) -> str:
        body = "，".join(items)
        return f"{name}：{body}" if name else body

    # 按"从最不重要开始丢"的顺序，保留 名字/发型/服装/记忆点
    drop_order = [eyes, face, outfit, sig]
    items = list(fields)
    while len(compose(items)) > limit and items:
        for candidate in drop_order:
            if candidate and candidate in items and candidate not in (hair, sig):
                items.remove(candidate)
                break
        else:
            break
    text = compose(items)
    if len(text) > limit:
        text = text[:limit].rstrip("，、； ")
    return text


def _outer_style(outfit: str) -> str:
    """给差异对照用：长款 / 短款 / 未标注。"""
    if any(w in outfit for w in _SHORT_OUTER) and not any(w in outfit for w in _LONG_OUTER):
        return "短款"
    if any(w in outfit for w in _LONG_OUTER):
        return "长款"
    return ""


# ---------------------------------------------------------------------------
# 组装结果
# ---------------------------------------------------------------------------

@dataclass
class GuardResult:
    count_text: str = ""
    cast_text: str = ""
    isolation_text: str = ""
    scene_text: str = ""
    shot: Any = None                 # 出图视图（Shot 副本）
    hits: list[str] = field(default_factory=list)
    contrast: str = ""
    spatial: str = ""


def _sort_cast(items: Sequence[dict]) -> list[dict]:
    """按画面左→中→右排序；同侧保持原始顺序（4 人时中左/中右不互换）。"""
    order = {"左": 0, "中": 1, "右": 2}
    indexed = list(enumerate(items))
    indexed.sort(key=lambda pair: (order.get(str(pair[1].get("side", "")), 3),
                                   pair[0]))
    return [item for _, item in indexed]


def _sides(items: Sequence[dict]) -> dict[str, str]:
    sides: dict[str, str] = {}
    n = len(items)
    fallback = ("左", "中", "右") if n == 3 else ("左", "中", "中", "右") if n >= 4 else ("左", "右")
    for i, item in enumerate(items):
        name = str(item.get("name", "") or "")
        side = str(item.get("side", "") or "")
        if side not in ("左", "右", "中"):
            side = fallback[min(i, len(fallback) - 1)]
        if name:
            sides[name] = side
    return sides


def count_text(items: Sequence[dict], object_only: bool = False) -> str:
    ordered = _sort_cast(items)
    names = [str(x.get("name", "") or "").strip() for x in ordered if x.get("name")]
    n = len(names)
    if object_only or n == 0:
        return "画面里只有物件与它所在的空间；画面里出现的其他东西都是环境与道具。"
    if n == 1:
        return (f"画面里只有一位人物，就是{names[0]}；"
                f"画面里出现的其他东西都是环境与道具。")
    if n == 2:
        return (f"画面里只有两位人物，就是{names[0]}和{names[1]}；"
                f"画面里出现的其他东西都是环境与道具。")
    return (f"画面里只有{_cn_number(n)}位人物，就是"
            f"{'、'.join(names)}；画面里出现的其他东西都是环境与道具。")


def _cn_number(n: int) -> str:
    return {1: "一", 2: "两", 3: "三", 4: "四", 5: "五"}.get(n, str(n))


def _position_phrase(item: dict, side: str) -> str:
    pos = str(item.get("position", "") or "").strip()
    if pos:
        return _one_clause(pos, 22)
    return {"左": "画面左侧三分之一", "右": "画面右侧三分之一",
            "中": "画面中央"}.get(side, "画面里")


def cast_block(project: Any, items: Sequence[dict],
               detail_level: str = "") -> str:
    """一人一段，写完 A 的全部特征再写 B，绝不交叉。"""
    lines: list[str] = []
    ordered = _sort_cast(items)
    sides = _sides(ordered)
    brief = len(ordered) >= 2
    level = str(detail_level or "").lower()
    # 按景别给单卡预算：远景只保留轮廓，近景/特写才给记忆点留位置。
    base = {"wide": 86, "full": 100, "medium": 128,
            "close": 160, "extreme": 180}.get(level, 128)
    if len(ordered) >= 3:
        card_limit = max(64, int(base * 0.72))
    elif brief:
        card_limit = max(72, int(base * 0.82))
    else:
        card_limit = base
    for item in ordered:
        name = str(item.get("name", "") or "").strip()
        side = sides.get(name, "")
        character = None
        if project is not None and hasattr(project, "find_character"):
            character = project.find_character(name)
        card = compact_card(character, str(item.get("outfit", "") or ""),
                            limit=card_limit, brief=brief, detail_level=level)
        if character is not None and len(card) < 20:
            # 旧工程没有 hard_features 时，compact_card 只剩名字/记忆点；
            # 退回完整身份卡，至少把发型/服装带上。
            card = str(getattr(character, "identity_card", lambda: "")() or card)
        if not card:
            card = str(item.get("card", "") or "").strip()
        pos = _position_phrase(item, side)
        action = _one_clause(str(item.get("char_action", "") or ""), 30)
        text = f"{pos}是{card}" if card else f"{pos}是{name or '画面主体'}"
        if action:
            text = f"{text}；{name or '这位人物'}正在{action}"
        lines.append(text)
    return "；".join(x for x in lines if x)


def contrast_block(project: Any, items: Sequence[dict]) -> str:
    """把同框人物的差异写成可核对的对照，专治"平均脸"。

    2 人：脸型/眼型/发型 + 外套轮廓；
    3~4 人：每人一组"脸/眼/发"，再加一行外套轮廓。只对照真正不同的项，
    不重复身份卡里的"双眼对称/额前干净"这类通用锁定句。
    """
    people = []
    for item in _sort_cast(items):
        name = str(item.get("name", "") or "")
        c = project.find_character(name) if project is not None and hasattr(project, "find_character") else None
        if c is not None:
            people.append((name, c))
    if len(people) < 2:
        return ""

    def clean(value: str, kind: str) -> str:
        v = _one_clause(value, 18 if kind != "outfit" else 22)
        for junk in ("、双眼对称", "双眼对称、", "、额前干净", "额前干净、",
                     "、两侧推短", "、头顶向后梳拢", "、全部头发向后收进发髻",
                     "、瞳孔居中", "、看向同一方向"):
            v = v.replace(junk, "")
        if kind == "eyes":
            v = re.sub(r"、?双眼大小一致.*$", "", v)
        return v.strip("、，； ")

    def val(c: Any, hf: dict, key: str) -> str:
        if key == "outfit":
            return clean(_split_outfit(str(hf.get("outfit", "") or "") or _design_outfit(c), 26),
                         "outfit")
        if key == "sig":
            return clean(str(getattr(c, "derived_identity_tag", lambda: "")() or ""), "sig")
        return clean(str(hf.get(key, "") or ""), key)

    if len(people) == 2:
        (a_name, a), (b_name, b) = people[0], people[1]
        ah, bh = (getattr(a, "hard_features", {}) or {}), (getattr(b, "hard_features", {}) or {})
        a_face, b_face = val(a, ah, "face"), val(b, bh, "face")
        a_eyes, b_eyes = val(a, ah, "eyes"), val(b, bh, "eyes")
        a_hair, b_hair = val(a, ah, "hair"), val(b, bh, "hair")
        a_out, b_out = val(a, ah, "outfit"), val(b, bh, "outfit")
        left = traits_line(a_name, a_face, a_eyes, a_hair)
        right = traits_line(b_name, b_face, b_eyes, b_hair)
        parts: list[str] = []
        if left and right and left != right:
            parts.append(f"两张脸各自独立：{left}；{right}")
        if a_out and b_out and a_out != b_out:
            parts.append(f"{a_name}穿{a_out}，{b_name}穿{b_out}")
        return "。".join(x for x in parts if x) + ("。" if parts else "")

    # 3~4 人：先逐人特征，再一行外套轮廓
    traits: list[str] = []
    for name, c in people:
        hf = getattr(c, "hard_features", {}) or {}
        line = traits_line(name, val(c, hf, "face"), val(c, hf, "eyes"),
                           val(c, hf, "hair"))
        if line:
            traits.append(line)
    parts = []
    if traits:
        parts.append(f"每张脸各自独立：{'；'.join(traits)}")
    outfits = []
    for name, c in people:
        hf = getattr(c, "hard_features", {}) or {}
        out = val(c, hf, "outfit")
        if out:
            style = _outer_style(out)
            outfits.append(f"{name}{style or out}")
    if len(outfits) >= 2:
        parts.append("外套轮廓不同：" + "，".join(outfits))
    return "。".join(x for x in parts if x) + ("。" if parts else "")


def traits_line(name: str, face: str, eyes: str, hair: str) -> str:
    vals = [x for x in (face, eyes, hair) if x]
    return f"{name}是{'、'.join(vals)}" if vals else ""


def spatial_block(items: Sequence[dict]) -> str:
    n = len([x for x in items if x.get("name")])
    if n < 2:
        return ""
    ordered = _sort_cast(items)
    names = [str(x.get("name", "") or "").strip() for x in ordered if x.get("name")]
    if len(names) < 2:
        return ""
    if len(names) == 2:
        return (f"{names[0]}在画面左三分之一，{names[1]}在画面右三分之一；"
                f"两人之间留出一臂宽的空隙，背景从空隙里透出来。")
    if len(names) == 3:
        return (f"{names[0]}在画面左三分之一，{names[1]}在画面中央，"
                f"{names[2]}在画面右三分之一；相邻两人之间留出半臂宽的空隙，"
                f"背景从空隙里透出来。")
    return (f"画面从左到右依次是{'、'.join(names)}，"
            f"排成一条错落的横列；相邻两人之间留出半臂宽的空隙，"
            f"背景从空隙里透出来。")


def solo_block(shot: Any, name: str, scene: str = "") -> str:
    """单人构图锁：用"唯一人物 + 占满画面 + 空环境"堵住补人空间。"""
    if not name:
        return ""
    st = str(getattr(shot, "shot_type", "") or "")
    scene_txt = _clean(scene) or "空无一人的环境"
    if st == "特写":
        return (f"画面里只有{name}一个人物，只画{name}的脸与肩颈，头顶到下颌填满画面；"
                f"背景是虚化的{scene_txt}，画面里唯一的人影就是{name}。")
    if st == "近景":
        return (f"画面里只有{name}一个人物，只画{name}的头顶到胸口，腰部以下在画面之外；"
                f"背景是虚化的{scene_txt}，画面里唯一的人影就是{name}。")
    if st in ("全景", "远景"):
        return (f"画面里只有{name}一个完整人物，{name}占画面高度约三分之二；"
                f"画面四边是{scene_txt}连续的地面、墙面与灯光，"
                f"画面里唯一的人影就是{name}。")
    if st == "中景":
        return (f"画面里只有{name}一个人物，画到{name}的腰部，腰部以下在画面之外；"
                f"画面两侧是{scene_txt}的延续，画面里唯一的人影就是{name}。")
    return (f"画面里只有{name}一个人物，{name}占据画面主体；"
            f"画面两侧是{scene_txt}的延续，画面里唯一的人影就是{name}。")


# ---------------------------------------------------------------------------
# 出图视图
# ---------------------------------------------------------------------------

def prepare(project: Any, shot: Any, items: Sequence[dict],
            object_only: bool = False, detail_level: str = "") -> GuardResult:
    """生成出图视图：不改原 Shot，只返回副本与各区块文本。"""
    view = copy.copy(shot)
    n = len([x for x in items if x.get("name")])
    hits: list[str] = []
    # 过肩镜头在本项目里一律降成斜侧：纯文生图会把"前景肩背"画成一个
    # 独立的第三个人（双人格也一样，实测 2026-10）。需要过肩叙事时，
    # 改用"斜侧 + 前景遮挡物（门框/桌沿）"更稳。
    if str(getattr(view, "camera", "") or "") == "过肩":
        view.camera = "斜侧"
        hits.append("过肩机位")
    scene_base = str(getattr(shot, "scene", "") or "")
    scene_obj = None
    if project is not None and scene_base:
        scene_obj = next((s for s in getattr(project, "scenes", []) or []
                          if getattr(s, "name", "") == scene_base), None)
    scene_full = scene_base
    if scene_obj is not None and getattr(scene_obj, "features", ""):
        scene_full = f"{scene_base}（{scene_obj.features}）"

    if n == 1:
        name = str(items[0].get("name", "") or "").strip()
        gender = ""
        if project is not None and hasattr(project, "find_character"):
            c = project.find_character(name)
            gender = str(getattr(c, "gender", "") or "") if c is not None else ""
        for attr in ("description", "action", "expression", "focus", "gaze"):
            old = str(getattr(view, attr, "") or "")
            new, h = depopulate_text(old, gender=gender, visible=[name],
                                     project=project)
            setattr(view, attr, new)
            hits.extend(h)
        scene_clean, h = depopulate_text(scene_full, gender=gender,
                                         visible=[name], project=project)
        scene_clean, h2 = empty_scene_text(scene_clean)
        hits.extend(h + h2)
        view.scene = _clean(scene_clean)
        scene_out = view.scene
        isolation = solo_block(view, name, scene_out)
    elif n >= 2:
        # 双人：保证一左一右；没有 sides 的补上
        sides = _sides(_sort_cast(items))
        if not getattr(view, "char_sides", None):
            view.char_sides = dict(sides)
        scene_out = _clean(scene_full)
        contrast = contrast_block(project, items)
        spatial = spatial_block(items)
        isolation = "；".join(x for x in (contrast, spatial) if x)
    else:
        scene_out = _clean(scene_full)
        isolation = ""

    return GuardResult(
        count_text=count_text(items, object_only=object_only),
        cast_text=cast_block(project, items, detail_level=detail_level),
        isolation_text=isolation,
        scene_text=scene_out,
        shot=view,
        hits=list(dict.fromkeys(hits)),
    )


# ---------------------------------------------------------------------------
# 负向词
# ---------------------------------------------------------------------------

def negative_clause(shot: Any, project: Any = None) -> str:
    """按人数返回负向清单；与正向的人数锁一一对应。"""
    n = len([x for x in (getattr(shot, "characters", None) or []) if x])
    common = ("低质量, 模糊, 变形, 多余的手指, 水印, 签名, 可读文字, 乱码文字")
    if n == 0:
        return (f"{common}, 人物, 人脸, 人像, 手, 手指, 身体, 腿, 人群, 人影, "
                f"主角, 角色, 完整人物")
    if n == 1:
        return (f"{common}, 第二个人, 多余人物, 背景路人, 人群, 围观者, 观众, "
                f"半截人影, 远处人影, 背景人物, 其他角色, 克隆人, 重复人物, "
                f"双胞胎, 分身, 镜像人, 倒影人, 画面分割, 分屏, 拼接画面, 画中画")
    next_person = _cn_number(n + 1)
    return (f"{common}, 第{next_person}个人, 多余人物, 人群, 围观者, 背景路人, "
            f"克隆人, 双胞胎, 融合脸, 两张一样的脸, 同一张脸, 换脸, 脸型互换, "
            f"五官互换, 服装互换, 发型互换, 所有人物穿同一件外套, 镜像, 倒影, "
            f"分身, 画面分割, 分屏, 画中画")


# ---------------------------------------------------------------------------
# 自检 / 导演层
# ---------------------------------------------------------------------------

def _shared_buckets(a: Any, b: Any) -> list[str]:
    try:
        ba, bb = a.trait_buckets(), b.trait_buckets()
    except Exception:  # noqa: BLE001
        return []
    return [k for k in ("face", "eyes", "hair", "outfit")
            if (ba.get(k) or set()) & (bb.get(k) or set())]


def diagnose(project: Any, shot: Any) -> list[dict[str, Any]]:
    """返回这一格在"撞脸/乱入"上的风险项（不修改数据）。"""
    issues: list[dict[str, Any]] = []
    names = [str(n) for n in (getattr(shot, "characters", None) or []) if n]
    if str(getattr(shot, "camera", "") or "") == "过肩":
        issues.append({
            "code": "over_shoulder_risk", "level": "warn",
            "message": f"第 {shot.index} 格用了过肩机位：纯文生图会把前景肩背"
                       f"画成独立的第三个人（单/双人都实测过），建议改斜侧",
            "shots": [shot.index],
        })
    if len(names) == 1:
        name = names[0]
        c = project.find_character(name) if project is not None and hasattr(project, "find_character") else None
        gender = str(getattr(c, "gender", "") or "") if c is not None else ""
        hits: list[str] = []
        ghosts: list[str] = []
        for attr in ("description", "action", "expression", "focus", "gaze", "scene"):
            raw = str(getattr(shot, attr, "") or "")
            if project is not None:
                for other in getattr(project, "characters", []) or []:
                    oname = str(getattr(other, "name", "") or "").strip()
                    if oname and oname != name and oname in raw and oname not in ghosts:
                        ghosts.append(oname)
            _, h = depopulate_text(raw, gender=gender, visible=[name], project=project)
            hits.extend(h)
        if ghosts:
            issues.append({
                "code": "solo_ghost_names", "level": "warn",
                "message": f"第 {shot.index} 格是单人镜头，但描述里出现了画面外角色名："
                           f"{'、'.join(ghosts[:6])}；提示词里出现谁，模型就画谁",
                "shots": [shot.index],
            })
        hits = [h for h in hits if h not in ghosts]
        if hits:
            issues.append({
                "code": "solo_crowd_words", "level": "warn",
                "message": f"第 {shot.index} 格是单人镜头，但描述里有会召唤人群/关系的词："
                           f"{'、'.join(dict.fromkeys(hits))[:12]}",
                "shots": [shot.index],
            })
        if str(getattr(shot, "shot_type", "") or "") in ("全景", "远景"):
            desc = str(getattr(shot, "description", "") or "")
            if not any(w in desc for w in ("主体", "占画面", "占据画面")):
                issues.append({
                    "code": "solo_wide_empty", "level": "info",
                    "message": f"第 {shot.index} 格是单人远景/全景，画面边缘留白多，"
                               f"模型容易补人；建议补一句「人物占画面主体」",
                    "shots": [shot.index],
                })
    elif len(names) >= 2:
        max_cast = int(getattr(config, "MAX_CHARACTERS_PER_SHOT", 4) or 4)
        if len(names) > max_cast:
            issues.append({
                "code": "too_many_characters", "level": "danger",
                "message": f"第 {shot.index} 格有 {len(names)} 个角色，超过单格上限 "
                           f"{max_cast} 人；纯文生图会串味，建议拆成两格群像/反应格",
                "shots": [shot.index],
            })
        collisions = []
        for i, a_name in enumerate(names):
            a = project.find_character(a_name) if project is not None and hasattr(project, "find_character") else None
            for b_name in names[i + 1:]:
                b = project.find_character(b_name) if project is not None and hasattr(project, "find_character") else None
                if a is None or b is None:
                    continue
                shared = _shared_buckets(a, b)
                if len(shared) >= 2:
                    collisions.append(f"{a_name}×{b_name}（{'、'.join(shared)}）")
        if collisions:
            issues.append({
                "code": "pair_collision", "level": "warn",
                "message": f"第 {shot.index} 格多人同框，"
                           f"{'；'.join(collisions[:3])} 上撞车，容易画成同一张脸",
                "shots": [shot.index],
            })
        if not getattr(shot, "char_sides", None):
            issues.append({
                "code": "pair_no_sides", "level": "info",
                "message": f"第 {shot.index} 格多人没有左右/中央站位，"
                           f"模型会把人物平均化",
                "shots": [shot.index],
            })
    return issues


def self_check() -> dict[str, Any]:
    """离线自检：核心替换与人数锁是否按预期工作。"""
    errors: list[str] = []
    text, hits = empty_scene_text("深夜的街头，远处有围观者，两人站在路灯下")
    text, _ = depopulate_text(text, gender="女")
    if "空无一人" not in text or "围观者" in text:
        errors.append(f"场景去人群失败：{text}")
    txt, _ = depopulate_text("她看向对方，身后的路人停下脚步", gender="女")
    if any(w in txt for w in ("对方", "路人")):
        errors.append(f"人群词清洗失败：{txt}")
    txt2, _ = depopulate_text("画面另一侧只露出一个模糊的肩背", gender="女")
    if "肩背" in txt2:
        errors.append(f"单人肩背未清洗：{txt2}")
    card = {"name": "甲", "side": "左"}
    if "只有一位人物" not in count_text([card]):
        errors.append("单人人数锁措辞错误")
    return {"ok": not errors, "errors": errors}
