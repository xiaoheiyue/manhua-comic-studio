# -*- coding: utf-8 -*-
"""数据模型：角色、场景、分镜、对话、工程。

全部使用 dataclass，并提供 to_dict / from_dict，方便把中间结果落盘成 JSON，
用户可以随时中断、重启后继续调整和重新生成（需求：中间结果要保留）。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import config


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class _Base:
    """提供统一的 dict 互转能力（仅处理自身声明的字段）。"""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[arg-type]

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


# =============================================================================
# 文本清洗小工具（身份卡 / 人设用）
# =============================================================================

_HEADING_RE = re.compile(r"【[^】]{1,16}】")
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？…；;])")


def _strip_design_headings(text: str) -> str:
    """去掉【基本形象】这类给人看的小标题，并把换行压成句号。"""
    t = _HEADING_RE.sub("", text or "")
    t = t.replace("\r", "\n")
    t = re.sub(r"\n+", "。", t)
    t = re.sub(r"[ \t\u3000]+", " ", t)
    t = re.sub(r"。{2,}", "。", t)
    return t.strip("。 ")


def _truncate_at_sentence(text: str, limit: int) -> str:
    """按句边界截断，绝不切在句子中间（旧版 design_brief 的坑）。"""
    text = (text or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[:limit]
    sentences = _SENT_SPLIT_RE.split(cut)
    # split 会保留句末标点在前一段；取最长能拼回来的前缀
    buf = ""
    for sent in sentences:
        if not sent:
            continue
        if len(buf) + len(sent) > limit:
            break
        buf += sent
    if buf.strip():
        return buf.strip()
    # 整段没有句号：退到最近的逗号/顿号
    pos = max((cut.rfind(p) for p in "，、,；;：:"), default=-1)
    return (cut[:pos] if pos >= max(6, limit // 3) else cut[:limit]).strip()


def _first_clause(text: str, limit: int = 18) -> str:
    """取第一个短句（遇到逗号/顿号/句号就停）。"""
    t = (text or "").strip()
    if not t:
        return ""
    m = re.search(r"[，,、；;。！？\n]", t)
    head = t[:m.start()] if m else t
    head = head.strip("，,、；;。 ")
    return head[:limit] if limit and len(head) > limit else head


def _trait_buckets(text: str) -> dict[str, set[str]]:
    """按 rules/identity_rules.json 的 trait_patterns 把一段文字分桶。"""
    try:
        from comicgen import rules as rules_mod
        patterns = rules_mod.trait_patterns()
    except Exception:  # noqa: BLE001
        patterns = {}
    out: dict[str, set[str]] = {}
    for bucket, words in (patterns or {}).items():
        hits = {w for w in words if w and w in (text or "")}
        if hits:
            out[bucket] = hits
    return out


# =============================================================================
# 角色
# =============================================================================

@dataclass
class Character(_Base):
    """出场角色。既承载分析结果，也承载人设与参考图。"""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = ""                     # 姓名
    gender: str = ""                   # 性别
    age: str = ""                      # 年龄感
    identity: str = ""                 # 身份
    personality: str = ""              # 性格特点
    role: str = ""                     # 在故事中的作用
    is_extra: bool = False             # 是否为群众角色/路人（单独归类）
    # ---- 分析阶段的长文本提取补充（可画、可核对，不替代第三阶段人设）----
    appearance: str = ""               # 原文里可画的外貌/服装短句（≤60 字）
    aliases: list[str] = field(default_factory=list)  # 别名/称呼（他、老孔…）
    first_appearance: str = ""         # 首次出场位置，便于回溯原文

    # ---- 人设（第三阶段生成，用户可编辑）----
    design: str = ""                   # 完整人设描述文本
    keywords: list[str] = field(default_factory=list)  # 注入分镜提示词的外貌关键词
    # ---- 身份锚（rules/01 文档 H1–H3）----
    # hard_features：机器可读的硬特征（face/hair/eyes/outfit/build/signature/palette），
    # 用来做跨角色撞车检测；旧工程为空也能跑，会从 keywords/design 里现推。
    hard_features: dict[str, str] = field(default_factory=dict)
    # identity_tag：该角色唯一的"记忆点"（如"左耳细银耳线"），全局不得重复。
    identity_tag: str = ""
    # design_patchs：为区分角色追加过的点缀（可追溯，见 rules/01 H5）
    design_patchs: list[str] = field(default_factory=list)

    # ---- 参考图（第三阶段生成）----
    main_visual: str = ""              # 主视图路径（唯一身份参考）
    face_ref: str = ""                 # 面部特写（特写/近景的身份参考）
    views: dict[str, str] = field(default_factory=dict)  # front/side/back/top 单视图
    view_quality: dict[str, str] = field(default_factory=dict)  # top: top_down/high_angle
    pose_sheet: str = ""               # 多姿态 + 三视图总图路径（人工查看/姿态库）
    top_view: str = ""                 # 俯视参考（质检 top_down/high_angle；不保证是正顶视）
    top_source: str = ""               # 用户上传的原始俯视参考（照片/3D 渲染）
    top_skeleton: str = ""             # 从 top_source 自动提取的姿态骨架
    top_note: str = ""                 # 俯视流程提示（如"未提取到骨架，已用文本俯视"）
    pose_source: str = ""              # 用户上传的自定义骨架图（空=内置姿态总图）
    # 旧工程兼容字段：不再生成、不再作为分镜参考；仅用于读取旧 JSON。
    triple_view: str = ""
    view_paths: dict[str, str] = field(default_factory=dict)
    active_look: str = "默认造型"      # 当前生效造型名
    extra_looks: dict[str, dict[str, str]] = field(default_factory=dict)  # 其他造型
    seed: int = 0                      # 固角色种子：主视图/姿态图共用
    # ---- 旧 LoRA 字段（仅兼容旧工程，不再使用）----
    lora_name: str = ""
    lora_strength: float = 0.8
    lora_trigger: str = ""

    # ---- 状态 ----
    design_status: str = "待生成"      # 待生成 / 生成中 / 已完成 / 失败
    image_status: str = "待生成"
    pose_status: str = "待生成"         # 姿态总图：待生成 / 生成中 / 已完成 / 失败
    top_status: str = "待生成"          # 俯视图：待生成 / 生成中 / 已完成 / 失败
    error: str = ""
    created_at: str = field(default_factory=_now)

    # ------------------------------------------------------------------
    @property
    def display(self) -> str:
        tag = f"（{self.role}）" if self.role else ""
        return f"{self.name}{tag}" if self.name else "未命名角色"

    def reference_image(self) -> str | None:
        """返回该角色用于分镜参考的图片路径。

        只使用当前造型的一张**主视图**作为身份参考。
        姿态总图/三视图包含多个身体，不能直接送进分镜出图；旧工程没有主视图时
        直接返回 None，让分镜退回文本身份卡，避免模型抄成多人。
        """
        look = self.extra_looks.get(self.active_look) or {}
        for candidate in (
            look.get("main_visual"),
            self.main_visual,
        ):
            if candidate and Path(candidate).exists():
                return candidate
        # 旧工程没有主视图时，不再把姿态总图/三视图（多身体）当分镜参考；
        # 分镜会退回文本身份卡，避免模型抄成多人。
        return None

    def effective_pose_sheet(self) -> str:
        """当前可用的姿态图；旧工程回退到 triple_view。"""
        for p in (self.pose_sheet, self.triple_view):
            if p and Path(p).exists():
                return p
        return ""

    def face_reference(self) -> str:
        """面部特写；不存在时回退主视图。"""
        for p in (self.face_ref, self.main_visual):
            if p and Path(p).exists():
                return p
        return ""

    def view(self, name: str) -> str:
        """取单视图（front/side/back/top）；side 兼容 side_l/side_r。"""
        want = str(name or "").strip().lower()
        candidates = [want]
        if want == "side":
            candidates += ["side_l", "side_r", "side_view"]
        for key in candidates:
            p = (self.views or {}).get(key, "")
            if p and Path(p).exists():
                return p
        return ""

    def view_quality_of(self, name: str) -> str:
        return str((self.view_quality or {}).get(str(name or "").lower(), "") or "")

    def has_reference(self) -> bool:
        return self.reference_image() is not None

    def look_options(self) -> list[str]:
        """可选造型列表（用于分镜中指定造型）。"""
        options = ["默认造型"]
        options.extend(sorted(self.extra_looks.keys()))
        return options

    def design_brief(self, max_chars: int = 0) -> str:
        """
        人设简述（给人看 / 兼容旧调用）。

        与旧版的区别：**按句截断**，不再从字中间切；小标题（【基本形象】这类）
        会被去掉——图像模型不解析方括号，只会白占字数。

        注意：出图提示词不再使用完整人设，而是用 :meth:`identity_card`
        （见 rules/01 与 rules/03：长人设会把角色特征互相污染）。
        """
        parts: list[str] = []
        head = "、".join(x for x in (self.gender, self.age, self.identity) if x)
        if head:
            parts.append(head)
        if self.keywords:
            limit = max(1, int(getattr(config, "CHARACTER_KEYWORD_COUNT", 6)))
            parts.append("、".join(self.keywords[:limit]))
        if self.design:
            parts.append(_strip_design_headings(self.design))
        text = "；".join(parts)
        if max_chars and len(text) > max_chars:
            text = _truncate_at_sentence(text, max_chars)
        return text

    # ------------------------------------------------------------------
    # 身份锚：跨格复用的一小段外貌描述（rules/01 H1）
    # ------------------------------------------------------------------
    def trait_buckets(self) -> dict[str, set[str]]:
        """把关键词/人设映射到 face/eyes/hair/outfit/build 五个可比较的桶。"""
        text = "、".join([*(self.keywords or []), self.identity_tag or "",
                          *(self.hard_features or {}).values(), self.design or ""])
        return _trait_buckets(text)

    def derived_identity_tag(self) -> str:
        """
        推断"记忆点"，并把它清洗成**一个短特征**。

        文本模型经常把 identity_tag 写成"颈挂磨白旧U盘的墨黑齐发女"这种
        自我描述句；直接塞进身份卡会变成"及肩黑发、颈挂…女、窄长脸"。
        规则：
          1. 优先 hard_features.signature（通常是最干净的配饰/疤痕短语）；
          2. 没有 signature 才用 identity_tag，并砍掉"的…"后面的身份描述；
          3. 只取第一个短语，≤18 字。
        """
        def clean(raw: str) -> str:
            text = str(raw or "").strip()
            text = text.replace("【", "").replace("】", "")
            # "A的B" → 只留 A（B 多半是"…老妇/…老板/…司机"这类身份）
            if "的" in text:
                text = text.split("的", 1)[0]
            # 多个记忆点用 、+，,;； 分隔时只取第一个
            text = re.split(r"[、+，,；;]", text)[0]
            return _first_clause(text, 18)

        sig = (self.hard_features or {}).get("signature", "")
        if sig:
            tag = clean(sig)
            if tag:
                return tag
        if self.identity_tag:
            tag = clean(self.identity_tag)
            if tag:
                return tag
        marks = ("耳", "戒", "疤", "眼镜", "链", "手表", "绳", "簪", "发夹", "围巾",
                 "项链", "怀表", "刺青", "纹身", "袖扣", "挂坠", "绷带")
        for kw in self.keywords or []:
            if any(m in kw for m in marks):
                return _first_clause(kw, 18)
        return ""

    def identity_card(self, max_chars: int = 0) -> str:
        """
        身份卡：≤90 字、跨格逐字复用的外貌描述（rules/01 H1）。

        - 只用可视化特征：发型发色、脸型、眼型、服装、记忆点；
        - **整条丢弃**超预算的关键词，绝不切半句（旧版就是切在"下配黑色直筒长裤与"）；
        - 关键词为空时退回 hard_features，再退回人设首句。
        """
        try:
            from comicgen import rules as rules_mod
            limit = max_chars or rules_mod.card_max_chars()
            max_kw = rules_mod.max_keywords()
        except Exception:  # noqa: BLE001
            limit = max_chars or 88
            max_kw = 8

        head_parts = [x for x in (self.gender, self.age)
                      if x and str(x).strip() not in ("未知", "不确定", "?")]
        head = re.sub(r"[（(][^）)]{0,12}[）)]", "",
                      "，".join(str(x).strip() for x in head_parts))
        head = head.replace(" ", "").strip("，、；;。 ")
        traits: list[str] = []
        seen: set[str] = set()

        def push(value: str) -> None:
            value = _strip_design_headings(str(value or "")).strip("，、；;。 ")
            value = value.replace("【", "").replace("】", "")
            if not value or value in seen:
                return
            # 性别/年龄已经写进开头，不再重复（否则"男四十二岁，42岁男性"）
            if head and ("岁" in value or value[:1] in ("男", "女")):
                return
            seen.add(value)
            traits.append(value)

        patches = [str(p).strip() for p in (self.design_patchs or []) if str(p).strip()]
        # 关键词顺序：基础特征 → 记忆点 → 辨识度点缀 → 其余关键词
        # （点缀如果排在最后，会被 budget 裁掉，等于白加）
        ordered: list[str] = [kw for kw in (self.keywords or [])
                              if kw not in patches][:max(3, max_kw - len(patches))]
        tag = self.derived_identity_tag()
        if tag and not any(tag in t or t in tag for t in ordered):
            ordered.insert(min(2, len(ordered)), tag)
        for p in patches:
            if p not in ordered:
                ordered.append(p)
        for kw in (self.keywords or [])[max(3, max_kw - len(patches)):]:
            if kw not in ordered and kw not in patches:
                ordered.append(kw)
        for kw in ordered:
            push(kw)
        if not traits:
            for key in ("hair", "face", "eyes", "outfit", "signature"):
                push((self.hard_features or {}).get(key, ""))

        # 按预算整条丢弃（从尾部开始），但记忆点与点缀必须保留
        protected = {tag, *patches}

        def compose() -> str:
            body = head
            if traits:
                body = f"{body}，{'、'.join(traits)}" if body else "、".join(traits)
            return f"{self.name}：{body}" if self.name else body

        while traits and len(compose()) > limit:
            idx = next((i for i in range(len(traits) - 1, -1, -1)
                        if traits[i] not in protected), None)
            if idx is None:
                break
            traits.pop(idx)
        if not traits:
            fallback = _first_clause(_strip_design_headings(self.design), max(12, limit - 6))
            if fallback:
                traits = [fallback]
        return compose()


# =============================================================================
# 场景
# =============================================================================

@dataclass
class Scene(_Base):
    """主要场景 / 地点。"""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = ""
    features: str = ""        # 环境特点
    atmosphere: str = ""      # 氛围
    frequency: str = ""       # 出现频率
    objects: list[str] = field(default_factory=list)  # 标志性陈设/道具
    light: str = ""           # 该场景的主光方向/色温

    @property
    def display(self) -> str:
        return self.name or "未命名场景"


# =============================================================================
# 对话
# =============================================================================

@dataclass
class Dialogue(_Base):
    """一条对话 / 旁白 / 拟声词。"""

    speaker: str = ""         # 说话人，旁白用"旁白"，拟声用"音效"
    text: str = ""            # 对话原文
    tone: str = ""            # 语气：平静/激动/愤怒/搞笑/旁白…
    bubble_style: str = ""    # 气泡样式（空则按语气自动推荐）
    kind: str = "speech"      # speech 对话 / narration 旁白 / sfx 拟声词

    # ---- 导演层给的排版提示（可选）----
    slot: str = ""            # 气泡槽位：top-left / top-right / mid-left …
    tail: str = ""            # 尾巴指向：down-left / down-right / up / none
    offscreen: bool = False   # 说话人不在画面内（电话/画外音），气泡不指向具体人物
    size: str = ""            # 字号档：S/M/L（空=自动）

    def resolved_style(self) -> str:
        """决定实际使用的气泡样式。"""
        if self.bubble_style:
            return self.bubble_style
        if self.kind == "narration":
            return "旁白框"
        if self.kind == "sfx":
            return "爆炸"
        return config.TONE_TO_BUBBLE_STYLE.get(self.tone, "圆角矩形")


# =============================================================================
# 分镜
# =============================================================================

@dataclass
class Shot(_Base):
    """一个分镜条目（一个画面）。"""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    index: int = 1                    # 序号（1 起）
    shot_type: str = "中景"           # 镜头类型
    characters: list[str] = field(default_factory=list)   # 出场角色名
    scene: str = ""                   # 场景名称
    description: str = ""             # 画面描述
    mood: str = ""                    # 情绪氛围
    source_text: str = ""             # 对应原文片段
    source_quote: str = ""            # 对应原文依据（逐字，≤30 字）
    light_dir: str = ""               # 主光方向：左侧/右侧/顶部/背面/正面/环境漫射
    dialogues: list[Dialogue] = field(default_factory=list)  # 对话内容

    # ---- 画幅（条漫）----
    # 0 表示不指定，按镜头类型自动分档（见 config.WEBTOON_PANEL_SIZE_MAP）；
    # 用户在界面上单独调整某一格时才写入具体数值。
    panel_w: int = 0
    panel_h: int = 0

    # ---- 导演层字段（第四阶段产出，旧工程为空也能正常渲染）----
    camera: str = ""           # 机位：平视/俯视/仰视/斜侧/过肩/主观/背影
    composition: str = ""      # 构图：三分构图/中心构图/前景遮挡…
    lighting: str = ""         # 光线：自然光/侧光/逆光/低调光…
    focus: str = ""            # 这一格的视觉焦点（一句话）
    expression: str = ""       # 主要角色的表情
    action: str = ""           # 这一格的动作瞬间
    transition: str = ""       # 转场：动作衔接/主体切换/场景切换…
    panel_scale: str = ""      # 画幅档位：S/M/L/XL
    char_sides: dict[str, str] = field(default_factory=dict)  # 角色 -> 左/右（轴线）
    # 角色 -> 画面位置（如"右侧中景，背对门"）。比 char_sides 更细，用于出图绑定。
    char_positions: dict[str, str] = field(default_factory=dict)
    gaze: str = ""             # 视线方向："看向画面左侧"/"看向画外"/"与对方对视"
    props: list[str] = field(default_factory=list)  # 这一格必须出现的道具（连续性）
    time_of_day: str = ""      # 时间：清晨/白天/黄昏/夜晚/凌晨…
    prop_image: str = ""       # 纯道具镜头的道具参考图（无人物镜头不做文生图）
    pose_image: str = ""       # 用户上传/本格自定义姿态骨架图（空=不用姿态控制）
    pose_skeleton: str = ""    # 第 6 步自动提取的姿态骨架
    pose_draft: str = ""       # 第 6 步白底姿态预演（可选）
    # 本格视角：front/side/back/top；空则从 camera/description 推断
    view_hint: str = ""
    # 本格参考图计划（实际送了哪张、谁的、什么职责；用于回溯）
    ref_plan: list[dict] = field(default_factory=list)
    character_actions: dict[str, str] = field(default_factory=dict)  # 每个可见角色这一格的动作
    silent: bool = False       # 导演标记的无台词格
    hook: bool = False         # 是否为章末悬念格

    # ---- 分镜版式（页面/混排；由 comicgen/pagelayout.py 规划）----
    page_id: str = ""          # 所属页 PagePlan.id（空 = 独立条漫格）
    panel_index: int = 0       # 在本页里的阅读顺序（0 起）
    panel_role: str = "normal"  # normal / main（主格）/ inset（嵌套小格）
    panel_orientation: str = ""  # wide / tall / square，合成页时写入
    # 内容分析（第四阶段 + 版式规划共同产出）
    beat: str = ""             # 建立/对白/反应/动作/情绪/转场/揭示/蒙太奇/日常/悬疑/幽默/细节
    pacing: str = ""           # 慢/中/快
    panel_emphasis: str = ""   # normal / main / splash（本格在页面里的视觉权重）
    caption: str = ""          # 上图下文版式的字幕栏文案（SB-069/070/071）
    layout_hint: str = ""      # 文本模型建议的版式编号（SB-xxx / WB-xxx）
    # 气泡是否由程序绘制（色彩迁移工作流会染色，必须程序画）
    bubble_programmatic: bool = False

    # ---- 生成结果 ----
    image_path: str = ""              # 带气泡成品图
    raw_image_path: str = ""          # 纯画面原图
    bubble_layout: list[dict] = field(default_factory=list)  # 气泡布局（可拖动微调）
    # 出图前的气泡预留计划（空白气泡的位置/尺寸；图像模型按它画空白气泡，
    # 后期只往这些框里填字，避免事后贴气泡盖住人物）
    bubble_plan: list[dict] = field(default_factory=list)
    # 气泡回填诊断：expected/detected/extra/fallback，用于界面与自检回溯
    bubble_debug: dict[str, int] = field(default_factory=dict)
    # 物件/空镜语义模式：物证照 / 屏幕界面 / 环境细节
    object_mode: str = ""
    status: str = "待生成"             # 待生成 / 生成中 / 已完成 / 跳过 / 失败
    error: str = ""
    attempts: int = 0
    skip: bool = False                # 用户标记跳过
    seed: int | None = None
    reference_paths: list[str] = field(default_factory=list)  # 本次实际用的参考图
    look_override: dict[str, str] = field(default_factory=dict)  # 角色 -> 指定造型
    # ---- 自检与自动返修 ----
    quality_score: int = 0            # 0~100，出图后由 quality.py 评估
    quality_issues: list[str] = field(default_factory=list)   # 自检问题代码
    image_note: str = ""              # 下一轮出图要追加的返修要求

    def panel_size(self) -> tuple[int, int]:
        """
        本格的出图画幅（宽, 高）。

        显式指定 panel_w/panel_h 时优先用它（界面上单独调整过的格子），
        否则按镜头类型自动分档，再乘导演层给的画幅档位（S/M/L/XL）。
        返回值一定满足服务端对尺寸的约束，且条漫模式下宽度恒定。
        """
        if self.panel_w and self.panel_h:
            return config.normalize_image_size((self.panel_w, self.panel_h))
        base = config.webtoon_panel_size(self.shot_type)
        factor = config.PANEL_SCALE_FACTOR.get((self.panel_scale or "").upper(), 1.0)
        if factor == 1.0:
            return base
        w = int(base[0])
        h = int(base[1] * factor)
        h = min(int(getattr(config, "WEBTOON_PANEL_MAX_HEIGHT", 1536)), h)
        h = max(int(getattr(config, "WEBTOON_PANEL_MIN_HEIGHT", 1024)), h)
        h = max(32, int(round(h / 32)) * 32)
        return config.normalize_image_size((w, h))

    def panel_label(self) -> str:
        """画幅的中文描述（界面展示用）。"""
        w, h = self.panel_size()
        tag = f"{self.panel_scale}/{self.shot_type}" if self.panel_scale else self.shot_type
        return f"{tag} {w}×{h}"

    @property
    def has_dialogue(self) -> bool:
        return any(d.text.strip() for d in self.dialogues)

    def dialogue_text(self) -> str:
        """把所有对话拼成一段文本（导出文档用）。"""
        lines = []
        for d in self.dialogues:
            if not d.text.strip():
                continue
            if d.kind == "narration":
                lines.append(f"[旁白] {d.text}")
            elif d.kind == "sfx":
                lines.append(f"[音效] {d.text}")
            elif d.speaker:
                lines.append(f"{d.speaker}：{d.text}")
            else:
                lines.append(d.text)
        return "\n".join(lines)

    def status_icon(self) -> str:
        return {
            "待生成": "○", "生成中": "◐", "已完成": "●",
            "跳过": "⏭", "失败": "✗",
        }.get(self.status, "○")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Shot":  # type: ignore[override]
        data = dict(data or {})
        dialogues = data.pop("dialogues", []) or []
        shot = super().from_dict(data)
        shot.dialogues = [Dialogue.from_dict(d) if isinstance(d, dict) else d
                          for d in dialogues]
        return shot


# =============================================================================
# 页面（分镜版式）
# =============================================================================

@dataclass
class PagePlan(_Base):
    """
    一页漫画的排版计划。

    一页由 1~6 个 Shot 组成，版式来自 handraw-style 的 SB-001–SB-071 或本项目的
    原生条漫版式（WB-*）。``cells`` 是已经解析好的归一化格子（x/y/w/h 或多边形），
    合成页图时按 PAGE_CANVAS_WIDTH 换算成像素。
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    index: int = 1                       # 页码（1 起）
    layout_id: str = "WB-001"
    layout_name: str = ""
    geometry: str = ""                   # 几何模板名（rules/data/storyboard_layouts.json）
    mode: str = "page"                   # page（拼版）/ webtoon（直接复用该格成图）
    shot_ids: list[str] = field(default_factory=list)
    cells: list[dict] = field(default_factory=list)      # 归一化格子
    captions: list[dict] = field(default_factory=list)   # 字幕栏矩形（归一化）
    caption_texts: list[str] = field(default_factory=list)
    rationale: str = ""                  # 为什么选这个版式（给界面看）
    aspect: float = 0.72                 # 页宽/页高
    image_path: str = ""
    width: int = 0
    height: int = 0
    status: str = "待生成"               # 待生成 / 已合成 / 失败
    error: str = ""

    @property
    def panel_count(self) -> int:
        return len(self.shot_ids)

    def shot_indices(self, project: "Project") -> list[int]:
        out = []
        for sid in self.shot_ids:
            shot = project.shot_by_id(sid)
            out.append(shot.index if shot else 0)
        return out

    def label(self) -> str:
        tag = "条漫" if self.mode == "webtoon" else self.layout_name or self.layout_id
        return f"第 {self.index} 页 · {tag} · {self.panel_count} 格"


# =============================================================================
# 工程
# =============================================================================

@dataclass
class Project:
    """一次漫画生成任务的完整状态。"""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    title: str = "未命名作品"
    novel_text: str = ""
    source_filename: str = ""

    # 成品层信息（封面 / 话标题 / 尾页用）
    chapter_no: str = "01"                  # 第几话（两位数字符串）
    chapter_title: str = ""                 # 本话标题，如"委托"
    tagline: str = ""                       # 封面钩子文案（一句话）
    next_teaser: str = ""                   # 尾页下话预告（一句话）
    author: str = ""                        # 作者署名（可空）

    # 第二阶段结果
    summary: str = ""                       # 故事梗概
    plotline: str = ""                      # 情节线（含关键转折与高潮）
    key_points: list[str] = field(default_factory=list)
    style: str = config.DEFAULT_STYLE        # 整体画风（config.STYLE_PRESETS 里的中文预设）
    style_detail: str = ""                   # 细化的风格建议
    # ---- handraw-style 绘制风格（365 种编号化画风 + 36 种主题色）----
    style_id: str = ""                       # 如 FA-001（旧编号 001 会自动映射）
    style_color_ids: list[str] = field(default_factory=list)   # 如 ["C-01", "C-12"]
    style_auto: bool = True                  # 是否允许分析后自动推荐/替换
    style_rationale: str = ""                # 推荐理由（界面展示）
    style_ref_path: str = ""                 # 实际送给工作流的画风参考图（PNG 缓存）
    style_tone: str = ""                     # handraw 阶调编号 T-01…T-16
    style_trigger: str = ""                  # 组合出的"触发词"短语（界面展示/可手改）
    style_trigger_custom: bool = False       # 用户是否手改过触发词；否则按 traits 自动重算
    characters: list[Character] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)

    # 第 2 步：小说精修 / 连续故事板
    core_task: str = ""
    visual_anchors: list[dict] = field(default_factory=list)
    continuous_cells: list[dict] = field(default_factory=list)
    continuous_storyboard: str = ""
    refine_status: str = "待生成"
    # 精修的结构化摘要：主角/目标/阻碍/转折/落点/基调/母题。
    # 单独存一份，9 格故事板与分镜拆解可以直接引用，不必重读原文。
    refine_digest: dict = field(default_factory=dict)
    # 精修运行元信息：字数、分段数、耗时、模型、警告；界面用来展示"这次跑了什么"。
    refine_meta: dict = field(default_factory=dict)
    # 第四阶段结果
    shots: list[Shot] = field(default_factory=list)
    # 25 格分镜结果（5×5 场景联系表）
    grid25_image: str = ""
    grid25_shots: list[int] = field(default_factory=list)
    grid25_status: str = "待生成"
    # 分镜版式：一页多格或原生条漫（RENDER_MODE=webtoon 时为空）
    pages: list[PagePlan] = field(default_factory=list)
    render_mode: str = ""                    # 空 = 用 config.render_mode()
    page_plan_note: str = ""                 # 分页规划说明（本地/AI/失败原因）

    # 第五阶段结果
    output_dir: str = ""
    output_folder_name: str = ""
    zip_path: str = ""

    # 运行设置
    settings: dict[str, Any] = field(default_factory=dict)
    # 界面里编辑过的提示词：{"<prompt_id>::<item>": {"system": "...", "user": "..."}}
    prompt_overrides: dict[str, dict] = field(default_factory=dict)

    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    # ------------------------------------------------------------------
    def touch(self) -> None:
        self.updated_at = _now()

    # ---- 查询 ----
    def find_character(self, name: str) -> Character | None:
        name = (name or "").strip()
        for c in self.characters:
            if c.name == name:
                return c
        # 允许部分匹配（模型有时会写"林晚（姐姐）"）
        for c in self.characters:
            if c.name and (c.name in name or name in c.name):
                return c
        return None

    def characters_by_names(self, names: Iterable[str]) -> list[Character]:
        out: list[Character] = []
        seen: set[str] = set()
        for n in names or []:
            c = self.find_character(n)
            if c and c.id not in seen:
                out.append(c)
                seen.add(c.id)
        return out

    def character_names(self) -> list[str]:
        return [c.name for c in self.characters if c.name]

    def scene_names(self) -> list[str]:
        return [s.name for s in self.scenes if s.name]

    def shot_by_id(self, shot_id: str) -> Shot | None:
        for s in self.shots:
            if s.id == shot_id:
                return s
        return None

    def page_by_id(self, page_id: str) -> PagePlan | None:
        for p in self.pages:
            if p.id == page_id:
                return p
        return None

    def page_for_shot(self, shot_id: str) -> PagePlan | None:
        for p in self.pages:
            if shot_id in (p.shot_ids or []):
                return p
        return None

    def pages_shots(self, page: PagePlan) -> list[Shot]:
        return [s for s in (self.shot_by_id(i) for i in page.shot_ids) if s is not None]

    def clear_pages(self) -> None:
        self.pages = []
        for shot in self.shots:
            shot.page_id = ""
            shot.panel_index = 0
            shot.panel_role = "normal"
            shot.panel_orientation = ""

    def reindex_shots(self) -> None:
        """重排序号（1 起）。增删、移动后调用。"""
        for i, shot in enumerate(self.shots, start=1):
            shot.index = i

    def move_shot(self, shot_id: str, delta: int) -> bool:
        """上移/下移一个分镜。"""
        for i, shot in enumerate(self.shots):
            if shot.id != shot_id:
                continue
            j = i + delta
            if j < 0 or j >= len(self.shots):
                return False
            self.shots[i], self.shots[j] = self.shots[j], self.shots[i]
            self.reindex_shots()
            return True
        return False

    def merge_shots(self, shot_id: str) -> bool:
        """把该分镜与下一个相邻分镜合并（需求 4.5）。"""
        for i, shot in enumerate(self.shots):
            if shot.id != shot_id or i + 1 >= len(self.shots):
                return False
            nxt = self.shots[i + 1]
            shot.description = (shot.description + " " + nxt.description).strip()
            shot.source_text = (shot.source_text + "\n" + nxt.source_text).strip()
            shot.dialogues.extend(nxt.dialogues)
            for name in nxt.characters:
                if name not in shot.characters:
                    shot.characters.append(name)
            if not shot.scene:
                shot.scene = nxt.scene
            if not shot.mood:
                shot.mood = nxt.mood
            shot.status = "待生成"
            shot.image_path = ""
            shot.raw_image_path = ""
            shot.bubble_layout = []
            shot.error = ""
            del self.shots[i + 1]
            self.reindex_shots()
            return True
        return False

    def split_shot(self, shot_id: str) -> bool:
        """把一个分镜拆成两个（描述按句子均分，需求 4.5）。"""
        shot = self.shot_by_id(shot_id)
        if shot is None:
            return False
        parts = split_description(shot.description)
        if len(parts) < 2:
            parts = [shot.description, "（请补充该分镜的画面描述）"]
        shot.description = parts[0]
        shot.image_path = ""
        shot.raw_image_path = ""
        shot.bubble_layout = []
        shot.status = "待生成"
        new_shot = Shot(
            shot_type=shot.shot_type,
            characters=list(shot.characters),
            scene=shot.scene,
            description=parts[1],
            mood=shot.mood,
            source_text="",
        )
        idx = self.shots.index(shot)
        self.shots.insert(idx + 1, new_shot)
        self.reindex_shots()
        return True

    def add_shot(self, after_id: str | None = None) -> Shot:
        """新增一个空白分镜。"""
        new_shot = Shot(description="（请填写该分镜的画面描述）")
        if after_id:
            shot = self.shot_by_id(after_id)
            if shot is not None:
                self.shots.insert(self.shots.index(shot) + 1, new_shot)
                self.reindex_shots()
                return new_shot
        self.shots.append(new_shot)
        self.reindex_shots()
        return new_shot

    def delete_shot(self, shot_id: str) -> bool:
        for i, shot in enumerate(self.shots):
            if shot.id == shot_id:
                del self.shots[i]
                self.reindex_shots()
                return True
        return False

    # 覆盖所有出场角色 -> 参考图映射
    def reference_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for c in self.characters:
            p = c.reference_image()
            if p:
                out[c.name] = p
        return out

    # ---- 统计 ----
    def progress(self) -> tuple[int, int]:
        total = len(self.shots)
        done = sum(1 for s in self.shots if s.status == "已完成")
        return done, total

    def statistics(self) -> dict[str, Any]:
        done, total = self.progress()
        return {
            "角色数": len(self.characters),
            "场景数": len(self.scenes),
            "分镜数": total,
            "页数": len(self.pages),
            "已完成": done,
            "失败": sum(1 for s in self.shots if s.status == "失败"),
            "跳过": sum(1 for s in self.shots if s.status == "跳过"),
            "待生成": sum(1 for s in self.shots if s.status == "待生成"),
            "总进度": f"{done}/{total}" if total else "0/0",
        }

    # ---- 序列化 ----
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "novel_text": self.novel_text,
            "source_filename": self.source_filename,
            "chapter_no": self.chapter_no,
            "chapter_title": self.chapter_title,
            "tagline": self.tagline,
            "next_teaser": self.next_teaser,
            "author": self.author,
            "summary": self.summary,
            "plotline": self.plotline,
            "key_points": list(self.key_points),
            "style": self.style,
            "style_detail": self.style_detail,
            "style_id": self.style_id,
            "style_color_ids": list(self.style_color_ids or []),
            "style_auto": bool(self.style_auto),
            "style_rationale": self.style_rationale,
            "style_ref_path": self.style_ref_path,
            "style_tone": self.style_tone,
            "style_trigger": self.style_trigger,
            "style_trigger_custom": bool(self.style_trigger_custom),
            "core_task": self.core_task,
            "visual_anchors": list(self.visual_anchors or []),
            "continuous_cells": list(self.continuous_cells or []),
            "continuous_storyboard": self.continuous_storyboard,
            "refine_status": self.refine_status,
            "refine_digest": dict(self.refine_digest or {}),
            "refine_meta": dict(self.refine_meta or {}),
            "grid25_image": self.grid25_image,
            "grid25_shots": list(self.grid25_shots or []),
            "grid25_status": self.grid25_status,
            "characters": [c.to_dict() for c in self.characters],
            "scenes": [s.to_dict() for s in self.scenes],
            "shots": [s.to_dict() for s in self.shots],
            "pages": [p.to_dict() for p in self.pages],
            "render_mode": self.render_mode,
            "page_plan_note": self.page_plan_note,
            "output_dir": self.output_dir,
            "output_folder_name": self.output_folder_name,
            "zip_path": self.zip_path,
            "settings": dict(self.settings),
            "prompt_overrides": dict(self.prompt_overrides or {}),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        data = dict(data or {})
        characters = [Character.from_dict(c) for c in data.pop("characters", []) or []]
        scenes = [Scene.from_dict(s) for s in data.pop("scenes", []) or []]
        shots = [Shot.from_dict(s) for s in data.pop("shots", []) or []]
        pages = [PagePlan.from_dict(p) for p in data.pop("pages", []) or []]
        known = {f for f in cls.__dataclass_fields__}
        proj = cls(**{k: v for k, v in data.items() if k in known})
        proj.characters = characters
        proj.scenes = scenes
        proj.shots = shots
        proj.pages = pages
        return proj

    # ---- 落盘 / 读取 ----
    def save(self, path: str | Path | None = None) -> Path:
        self.touch()
        target = Path(path) if path else Path(config.PROJECT_DIR) / f"{self.id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(target)
        return target

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)

    @staticmethod
    def list_projects() -> list[Path]:
        root = Path(config.PROJECT_DIR)
        if not root.exists():
            return []
        return sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


# =============================================================================
# 工具
# =============================================================================

_SENTENCE_ENDS = "。！？!?…；;"


def split_description(text: str) -> list[str]:
    """把一段画面描述按句子切成两半，用于拆分分镜。"""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) < 12:
        return [text]
    sentences: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in _SENTENCE_ENDS:
            sentences.append(buf.strip())
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())
    if len(sentences) < 2:
        mid = len(text) // 2
        return [text[:mid].strip(), text[mid:].strip()]
    mid = len(sentences) // 2
    return ["".join(sentences[:mid]).strip(), "".join(sentences[mid:]).strip()]



