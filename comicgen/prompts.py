# -*- coding: utf-8 -*-
"""各阶段提示词模板。

集中管理，方便调优。所有提示词都要求模型输出严格 JSON，便于程序解析。
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

import config
from . import castguard
from . import shot_prompt_compiler as shot_compiler
from . import prompt_overrides, stylelib
from .models import Character, Project, Scene, Shot
from .textutil import truncate


# =============================================================================
# 通用系统提示
# =============================================================================

SYSTEM_ANALYST = (
    "你是一位资深的漫画编剧与剧本分析师，擅长把小说文本拆解成漫画制作所需的"
    "角色设定、场景设定与分镜脚本。你只输出结构化 JSON，不输出任何解释性文字。"
)

SYSTEM_DESIGNER = (
    "你是一位漫画视觉开发设计师，擅长按指定画风写出精确、可落地成"
    "图像提示词的人设描述。你只输出结构化 JSON，不输出任何解释性文字。"
)

_COLOR_WORDS = (
    "普鲁士蓝", "芥末黄", "灰绿", "橄榄绿", "墨绿", "深绿", "灰蓝", "雾蓝",
    "藏青", "深蓝", "酒红", "砖红", "暗红", "深棕", "栗棕", "驼色", "卡其",
    "米白", "灰白", "冷灰", "银灰", "深灰", "炭黑", "黑色", "白色", "棕色",
    "褐色", "红色", "蓝色", "绿色", "黄色", "紫色", "粉色", "橙色", "青色",
)


def _character_color_words(character: Any) -> list[str]:
    blob = " ".join([
        str((getattr(character, "hard_features", {}) or {}).get("palette", "") or ""),
        str((getattr(character, "hard_features", {}) or {}).get("outfit", "") or ""),
        "、".join(getattr(character, "keywords", []) or []),
    ])
    out: list[str] = []
    for word in _COLOR_WORDS:
        if word in blob and word not in out:
            out.append(word)
    if not out:
        # 只在关键词没颜色时才回落人设正文；正文常提“与蓝色背景对比”，
        # 那些是环境色，不能当成角色服装色。
        design = str(getattr(character, "design", "") or "")
        for word in _COLOR_WORDS:
            if word in design and word not in out:
                out.append(word)
    return out[:6]

SYSTEM_STORYBOARD = (
    "你是快看漫画平台的资深分镜主笔（导演），负责把小说改编成可以直接开画的条漫分镜。"
    "你懂镜头语言：景别、机位、构图、轴线、光线、画幅节奏、转场与留白。"
    "你只输出结构化 JSON，不输出任何解释性文字。"
)


# =============================================================================
# 第二阶段：小说内容分析
# =============================================================================

def style_brief(project: Project, traits_limit: int = 90) -> str:
    """给文本模型看的画风说明（handraw 编号风格优先）。"""
    hs = stylelib.project_style(project) if bool(getattr(config, "HANDRAW_ENABLED", True)) else {}
    if hs.get("style_id"):
        meta = hs.get("style") or {}
        colors = "、".join((stylelib.color_meta(c) or {}).get("name_zh", c)
                          for c in hs.get("color_ids") or [])
        return (f"{meta.get('generation_name', '')}"
                f"（参考作者/风格：{meta.get('reference', '')}；"
                f"核心特征：{stylelib.positive_traits(meta.get('traits', ''), traits_limit)}"
                + (f"；主题色：{colors}" if colors else "") + "）")
    detail = f"；{project.style_detail}" if project.style_detail else ""
    return f"{project.style}{detail}"


# 画风推荐规则：先判断题材，再挑风格，避免所有故事都落回韩漫。
# 内置预设已按日系 / 国风与华语 / 欧美 / 欧洲 / 独立 / 媒介 / 韩漫分组，
# 这里只给"题材 → 家族"的对应关系，具体编号由模型在候选清单里选。
STYLE_CHOICE_RULE = (
    "整体风格判断：先看题材、时代、情绪与目标读者，再从候选清单里挑最贴合的一个，"
    "每部作品都要重新判断，不要把韩漫当默认答案。可参考的对应关系："
    "古装/宫廷/仙侠→国风工笔重彩、国风水墨、仙侠国风彩漫；"
    "武侠/江湖/动作→港式武侠漫画、日系少年热血漫画；"
    "校园恋爱/青春→日系少女漫画、日系水彩轻插画；"
    "悬疑/犯罪/黑色氛围→美式黑色电影漫画、日系青年剧画、港式犯罪电影漫画；"
    "热血战斗/超能力→美式超级英雄漫画、日系少年热血漫画；"
    "机甲/未来科技→日系机甲科幻漫画；恐怖/怪谈→日系恐怖漫画；"
    "合家欢/儿童/治愈→儿童绘本水彩、美式报纸连载漫画；"
    "文艺/历史/欧洲背景→法比清线漫画、欧洲文艺图像小说；"
    "实验/独立气质→木刻版画漫画、像素风漫画。"
    "确定画风后再写 style_detail，说明这套画风为什么贴合这个故事。"
)


def novel_overview_prompt(novel: str, chunk_index: int = 1, chunk_total: int = 1) -> list[dict]:
    """小说整体分析（角色 / 场景 / 情节 / 风格）一次调用。

    长文本由 pipeline 先切段，本函数只负责“这一段读准”，因此提示词里
    必须写清边界：不总结全篇、不补后文、不合并同名但不同的人。
    """
    part_note = ""
    if chunk_total > 1:
        part_note = (
            f"\n【分段边界】这是全篇第 {chunk_index}/{chunk_total} 段。"
            f"只提取本段出现的人、场景与情节；不要写后文，不要假设结局，"
            f"不要与其他段合并。人物在本段有多个称呼时，用最正式的那个作 name，"
            f"其余写进 aliases。")

    schema = {
        "summary": "本段故事梗概，150 字以内；只写本段实际发生的事",
        "plotline": "情节线：按时间顺序列出本段主要事件，标注关键转折点与高潮（用换行分隔）",
        "key_points": ["关键转折点或高潮，每条一句话；必须来自本段原文"],
        "style": f"整体画风推荐，按题材从候选中选一个（不要一律选韩漫）：{'、'.join(config.STYLE_PRESETS)}",
        "style_detail": "在推荐画风基础上的细化风格建议，说明题材与文风特点，80 字以内",
        "characters": [
            {
                "name": "角色姓名（无名路人也给一个可辨识称呼，如'黑衣男人'）",
                "aliases": ["本段出现过的其他称呼/代称，如'老孔'、'掌柜的'"],
                "gender": "男 / 女 / 未知",
                "age": "年龄感，如'二十四岁'、'三十岁上下'；原文没写就填'未知'",
                "identity": "身份职业",
                "personality": "性格特点，30 字以内",
                "role": "在故事中的作用，如'主角'、'关键线索人物'",
                "appearance": "原文里可直接画出来的外貌/服装短句，≤60 字；没有就留空",
                "first_appearance": "首次出场的一句话位置提示，≤20 字",
                "is_extra": False,
            }
        ],
        "scenes": [
            {
                "name": "场景名称，如'咸亨酒店'",
                "features": "环境特点，30 字以内",
                "atmosphere": "氛围，如'阴冷压抑'",
                "frequency": "出现频率，如'高频'、'中频'、'低频'",
                "objects": ["这个场景里反复出现、可画的陈设/道具，2-5 个"],
                "light": "这个场景的主光方向/色温，如'门口暖黄灯、屋内偏暗'",
            }
        ],
    }

    user = f"""请阅读下面的小说文本，完成分析任务。{part_note}

【分析要求】
1. 角色识别：找出本段所有出场角色，包括有名字的角色、有对话的角色、有重要描写的角色。
   - 同一人物的不同称呼不要拆成两个人：用最正式的称呼作 name，其余放 aliases。
   - 只出现一次、没有明显特征的路人，把 is_extra 设为 true。
   - appearance 只抄原文里能画出来的外貌/服装，不要自己编发色、瞳色、配饰。
2. 场景识别：找出本段主要场景和地点；objects 只列原文明确写到、且反复出现的陈设。
3. 情节梳理：整理出清晰的情节线，标注关键转折点和高潮；不要写本段没发生的事。
4. {STYLE_CHOICE_RULE}

【输出前自检】
- 每个 character 的 name 在本段原文里确实出现过（或原文明确指向该人）；
- 没有把"他/她/那人"当成新角色；
- 没有把原文没有的外貌、亲属关系、结局写进 appearance / plotline；
- scenes 的名字能在本段原文里找到对应地点。

【严格输出以下 JSON 结构】
{json.dumps(schema, ensure_ascii=False, indent=2)}

【小说文本开始】
{novel}
【小说文本结束】"""
    return [{"role": "system", "content": SYSTEM_ANALYST},
            {"role": "user", "content": user}]


def novel_overview_schema_hint() -> dict:
    """给解析失败时兜底的空结构。"""
    return {"summary": "", "plotline": "", "key_points": [],
            "style": config.DEFAULT_STYLE, "style_detail": "",
            "characters": [], "scenes": []}


def merge_analysis_prompt(partials: list[dict], novel_head: str) -> list[dict]:
    """把多段分析结果合并去重。"""
    user = f"""下面是同一部小说分段分析得到的多份结果，请你合并成一份。

【合并规则】
1. 同一个角色出现在多份结果里时，合并为一条：取信息最全的字段，
   is_extra 只要有一份是 false 就取 false。
2. 同一个场景同理合并。
3. 情节线按故事发生顺序重新排列，去掉重复事件，保留关键转折与高潮。
4. 风格推荐取出现次数最多的那个；style_detail 综合各份建议写得更贴合作品。
5. summary 重新写成一段完整、连贯的梗概（150 字以内）。

【分段结果 JSON】
{json.dumps(partials, ensure_ascii=False, indent=2)}

【原文开头（供你判断作品题材）】
{truncate(novel_head, 1200)}

【严格输出与下面完全相同的 JSON 结构】
{json.dumps(novel_overview_schema_hint(), ensure_ascii=False, indent=2)}

现在只输出合并后的 JSON。"""
    return [{"role": "system", "content": SYSTEM_ANALYST},
            {"role": "user", "content": user}]


# =============================================================================
# 第三阶段：角色人设
# =============================================================================

def character_design_prompt(character: Character, project: Project) -> list[dict]:
    """为单个角色生成详细人设（含跨角色差异化约束，见 rules/01 H1–H5）。"""
    schema = {
        "design": (
            "完整人设描述，按以下小标题分段书写，每段 1-2 句：\n"
            "【基本形象】【发型与发色】【服装风格】【气质与表情】【标志性特征】【色彩方案】"
        ),
        "hard_features": {
            "face": "脸型，从 rules/identity_rules.json 的 face 词表里选一个，如'瘦长脸'",
            "hair": "发型+发色+长度，一个短语，如'及肩黑发低马尾'",
            "eyes": "眼型，一个短语，如'细长上挑杏眼'",
            "outfit": "服装主色+款式，一个短语，如'炭黑短款风衣'",
            "build": "身形，一个短语，如'清瘦挺拔'",
            "signature": "唯一的视觉记忆点，一个短语，如'颈挂细银链旧U盘'",
            "asymmetry": "一处不对称的小特征（破坏完美对称），如'左眉尾一颗小痣'、"
                         "'右眼略小于左眼'、'发缝偏右'、'左耳垂一颗小痣'",
            "palette": "主色 + 辅助色，如'炭黑/冷灰+雾蓝'",
        },
        "identity_tag": "全片唯一的记忆点（≤18 字），不得与任何已有角色重复",
        "keywords": [
            "6-10 个可直接放进图像提示词的外貌关键词，如'及腰黑长直发'、'米色风衣'、'苍白肤色'"
        ],
        "main_visual_prompt": "主视图的图像生成提示词（只描述画面，不含画风词）",
        "pose_sheet_prompt": "多姿态+三视图总图的图像生成提示词（只描述画面，不含画风词）",
    }

    roster_lines = []
    for c in project.characters:
        if not c.name or c.id == character.id:
            continue
        anchor = c.identity_card(64) or c.derived_identity_tag()
        pal = str((getattr(c, "hard_features", {}) or {}).get("palette", "") or "").strip()
        roster_lines.append(f"- {c.name}：{anchor or '（还没定人设）'}"
                            + (f"；配色：{pal}" if pal else "；配色：未定"))
    roster = "\n".join(roster_lines) or "（还没有其他角色）"
    used_color_lines = []
    for c in project.characters:
        if not c.name or c.id == character.id:
            continue
        colors = _character_color_words(c)
        if colors:
            used_color_lines.append(f"- {c.name}：{'、'.join(colors)}")
    used_colors = "\n".join(used_color_lines) or "（还没有其他角色用色）"
    style_line = style_brief(project, 90)

    user = f"""请按上面的画风为这个角色设计人设。

【作品信息】
风格：{style_line}
风格细化：{(project.style_detail if not project.style_id else "已选择编号风格，旧版风格细化不再适用，以当前画风特征为准") or "无"}
故事梗概：{truncate(project.summary, 400) or "无"}

【主题色使用边界（重要）】
1. 作品选择的主题色只用于**背景、环境光、关键物件与整体色调**，
   不要把所有角色的服装主色/发色都设成主题色；
2. 每个角色的【色彩方案】必须从身份、性格、职业与年龄推导，
   并与已有角色的主色/辅助色明显不同；
3. hard_features.palette 写这个角色自己的「主色 + 辅助色」，
   不要照抄作品主题色，也不要把三个角色都写成蓝/黄；
4. **不要使用已占用主色的同色系深浅变体**：例如别人用了灰绿/橄榄绿，
   你就不要再写墨绿/深绿；必须明显换色系（棕、酒红、紫、蓝、青等）。

【已占用配色（你的角色必须避开这些主色）】
{used_colors}

【画风优先（先做这一步，再写人设）】
1. 先读上面的画风核心特征，按这个画风重新解释角色的五官、人体比例、服装轮廓与材质；
2. 例如绘本风要柔和概括、剪影/版画风要块面与高对比、3D 风要材质与体积、
   电影写实风要真实材质与光影；不要默认套用光滑大眼或现成条漫模板；
3. 角色的脸、发型、服装仍要可辨认，但呈现方式必须服从画风，不能只换滤镜。

【角色分析结果】
姓名：{character.name}
性别：{character.gender}
年龄感：{character.age}
身份：{character.identity}
性格特点：{character.personality}
在故事中的作用：{character.role}
是否群众角色：{"是" if character.is_extra else "否"}
原文可画外貌（必须尊重，不要改发色/年龄/服装主色）：{character.appearance or "原文未写，按身份与时代补，但不要套用通用美型"}

【已有角色清单（你必须和他们区分开）】
{roster}

【跨角色差异化硬约束（最重要）】
1. 你的角色在「发型轮廓 / 服装主色与配色 / 体型年龄 / 唯一记忆点」这四项里，
   **至少 3 项必须与上面清单里的任何人都不同**。
2. 记忆点（疤痕、眼镜、耳饰、项链、怀表等）**不得与清单里已有的重复**，连近似写法都不行。
3. 不要用"冷白肤色""五官精致"这类人人都能写的词当特征，它们不构成辨识度。
4. 脸型与眼型如果和某人相近，就必须在发型、服装主色、记忆点上明显拉开。
5. 只做"加法"：小说没有写死的外貌由你补，但已写死的（发色/年龄/身份）不能改。
6. **每个角色必须有一处不对称的小特征**（痣、浅痕、发缝偏分、单侧耳饰、轻微大小眼、
   一侧眉峰更高……），写进 hard_features.asymmetry 和关键词。完美对称的脸最容易被
   模型"平均化"，同一画面里两个人会变成同一张脸。

【人设描述必须包含以下内容】
1. 基本形象：性别、年龄感、身高体型、脸型、五官特征。在该画风下五官要精致，
   但要保持插画的概括性（清晰线稿、概括的鼻影与唇形），不要写成真人照片。
2. 发型与发色：发型样式、长度、发色，是否有特殊发饰。
3. 服装风格：日常服装、特殊场合服装、服装颜色和材质特点。小说中有服装变化描写时分别记录。
4. 气质与表情：整体气质（冷静/活泼/忧郁/威严等）与常见表情特征。
5. 标志性特征：疤痕、眼镜、佩剑、纹身、特殊饰品等（没有就写"无明显标志性特征"）。
   另外必须写一处**不对称的小特征**（左眉尾小痣 / 右眼略小 / 发缝偏右 / 单侧耳饰），
   它是把这张脸从"平均脸"里拽出来的锚点，不能写成左右完全对称。
6. 色彩方案：推荐该角色的主色调和辅助色，既要辨识度高又要与整体画面和谐。

【严格输出以下 JSON 结构】
{json.dumps(schema, ensure_ascii=False, indent=2)}

keywords 字段要特别用心：这些词会被拼成"身份卡"注入每一张分镜（≤90 字、逐字复用），
所以要具体、可视化、可复现，并且**按 importance 排序**（发型发色 → 脸型/眼型 → 服装 → 记忆点）。
发型必须写成可锁定的完整短语；如果角色是马尾/盘发，必须写"全部头发收进低马尾/发髻，额前干净"，
不能只写"及肩黑发低马尾"而留下散发、半扎的解释空间；眼睛必须写清眼型与"双眼对称"。"""
    return [{"role": "system", "content": SYSTEM_DESIGNER},
            {"role": "user", "content": user}]


def character_distinct_rewrite_prompt(character: Character, project: Project,
                                      collisions: list[dict]) -> list[dict]:
    """角色撞车后的定向重写（rules/01 H4）。只改人设，不改剧情。"""
    lines = []
    for item in collisions:
        name = item.get("other", "")
        shared = "、".join(item.get("shared_labels", []) or [])
        lines.append(f"- 与「{name}」撞车：{shared or '整体过于相似'}")
    detail = "\n".join(lines) or "（整体辨识度不足）"

    user = f"""你的角色「{character.name}」和别的角色撞车了，读者分不出谁是谁。请重写人设。

【撞车明细】
{detail}

【当前人设】
{character.design or "（空）"}

【当前关键词】
{"、".join(character.keywords) or "（空）"}

【重写要求】
1. 保留小说已写死的外貌与身份（性别、年龄、发色、职业），只调整可以自由发挥的部分。
2. 在「发型轮廓 / 服装主色与配色 / 体型年龄 / 唯一记忆点」里至少改 2 项，并写清改成了什么；
   如果撞车原因包含「服装」或「配色」，必须换掉服装主色/辅助色，不能只换配饰；
   再补一处新的不对称小特征（痣/浅痕/发缝偏分/单侧耳饰），写进 hard_features.asymmetry。
3. 新记忆点与新配色不得与下面任何角色重复（配色写进 hard_features.palette）：
{chr(10).join(f"- {c.name}：{c.derived_identity_tag() or c.identity_card(48)}；配色：{(c.hard_features or {}).get('palette') or '未定'}" for c in project.characters if c.id != character.id) or "（无）"}
4. keywords 重新排序：发型发色 → 脸型/眼型 → 服装 → 记忆点；总数 6-9 个。
5. 只输出 JSON，结构为：
{{"design": "重写后的人设（仍按【基本形象】…分段）",
  "hard_features": {{"face": "", "hair": "", "eyes": "", "outfit": "", "build": "", "signature": "", "asymmetry": "", "palette": ""}},
  "identity_tag": "新的唯一记忆点",
  "keywords": ["…"],
  "reason": "一句话说明改了哪两项"}}"""
    return [{"role": "system", "content": SYSTEM_DESIGNER},
            {"role": "user", "content": user}]


# =============================================================================
# 第四阶段：分镜拆解
# =============================================================================

def storyboard_prompt(project: Project, chunk_text: str,
                      start_index: int, chunk_label: str,
                      target_shots: int = 0) -> list[dict]:
    """
    把一个章节拆成"可拍的"条漫分镜。

    与旧版最大的区别：
      · 不再要求模型"概括原文"，而是要求它当导演：每格给出景别/机位/构图/光线/
        画幅/转场/焦点/动作/表情，description 里禁止出现台词与心理描写；
      · characters 只填**画面里看得见**的人。电话、画外音另用 offscreen 标记，
        从根上避免"打电话时把对方也画进同一间屋子"；
      · 台词、旁白、拟声词分工与字数硬约束，直接对齐快看单格阅读量。
    """
    # 分镜拆解只需要“谁是谁、什么身份”，不需要把 220 字视觉身份锁全部塞进来；
    # 长清单会稀释 §8 的镜头规则，也会让模型把注意力放在外貌而不是动作上。
    char_list = "；".join(
        f"{c.name}：{truncate('；'.join(x for x in [
            c.identity or '', c.role or '',
            c.appearance or '',
            '、'.join((c.keywords or [])[:3]),
        ] if x), 90)}"
        for c in project.characters[:12]) or "（暂无）"
    scene_list = "；".join(
        f"{s.name}：{truncate('；'.join(x for x in [
            s.features or s.atmosphere or '',
            ('陈设：' + '、'.join(s.objects[:4])) if getattr(s, 'objects', None) else '',
            ('光：' + s.light) if getattr(s, 'light', '') else '',
        ] if x), 90)}"
        for s in project.scenes[:12]) or "（暂无）"
    refine_cells = list(getattr(project, "continuous_cells", []) or [])
    refine_note = "\n".join(
        f"{c.get('cell')}. {c.get('shot_type', '')}｜{c.get('subject_action', '')}"
        f"｜{c.get('environment', '')}｜{c.get('emotion', '')}"
        f"｜原文：{c.get('quote', '')}"
        for c in refine_cells[:25] if isinstance(c, dict)) or "无"
    if not target_shots or target_shots <= 0:
        target_shots = max(6, min(60, int(round(len(chunk_text) / 90))))
    target_low = max(4, int(target_shots * 0.8))
    target_high = max(target_low + 1, int(target_shots * 1.2))
    two_cap = max(1, int(target_high * 0.25))
    multi_cap = max(1, int(target_high * 0.10))
    close_cap = max(2, int(target_high * 0.30))

    min_hint = ""
    hint_n = getattr(config, "STORYBOARD_MIN_SHOTS_HINT", 0)
    if hint_n and hint_n > 0:
        min_hint = (f"- **本段请至少拆出 {hint_n} 个分镜**，"
                    f"宁可细分也不要合并。\n")

    schema = {
        "shots": [
            {
                "shot_type": f"景别，从这些里选一个：{'、'.join(config.SHOT_TYPES)}",
                "camera": f"机位/角度，从这些里选一个：{'、'.join(config.SHOT_ANGLES)}",
                "composition": f"构图，从这些里选一个：{'、'.join(config.SHOT_COMPOSITIONS)}",
                "lighting": f"光线，从这些里选一个：{'、'.join(config.SHOT_LIGHTINGS)}",
                "panel_scale": "画幅档位：S（快节奏/动作）M（常规对白）L（情绪/冲击）XL（转场/大场面）",
                "panel_orientation": "该格在页面里的画幅方向：wide（横）/tall（竖）/square（方）；拿不准按内容判断",
                "panel_role": "该格在页面里的角色：main（主格）/normal（常规格）/inset（嵌套小格）",
                "transition": f"与上一格的转场，从这些里选一个：{'、'.join(config.TRANSITION_TYPES)}",
                "characters": ["这一格里**画面能看见**的角色姓名；画外音/电话另一端不要填；一格最多 4 人"],
                "char_sides": {"角色名": "左 / 中 / 右（同一场景内保持轴线一致）"},
                "char_positions": {"角色名": "画面位置，如'右侧中景，背对门'；3~4 人时写清从左到右的顺序"},
                "character_actions": {"每个可见角色名": "这一格该角色正在做的动作/姿态，一个短语；与 action 对应"},
                "gaze": "这一格主要人物的视线方向：看向画面左侧/右侧/上方/下方/深处（不写'画外/对方'）",
                "props": ["这一格必须出现的道具，如'碎屏手机'、'读盘器'"],
                "scene": "该分镜发生的场景，从上方清单里选，可补充环境细节",
                "time_of_day": "清晨 / 白天 / 黄昏 / 夜晚 / 凌晨 / 不确定",
                "light_dir": "主光从画面哪一侧来：左侧/右侧/顶部/背面/正面/环境漫射",
                "focus": "这一格的视觉焦点，一句话，如'她攥紧手机的手'",
                "action": "这一格正在发生的**一个**动作瞬间，一句话",
                "expression": "主要角色的表情与眼神，一句话",
                "description": (
                    "给画师的镜头描述，24-80 字，精辟、只保留核心："
                    "①谁 ②一个动作瞬间（到哪一帧）③表情/视线 ④场景里必须出现的一个关键物 "
                    "⑤光从哪来。不要堆环境装饰、不要写景别/机位、不要台词/心理/比喻/原文。"
                ),
                "mood": f"情绪氛围，从这些里选一个：{'、'.join(config.MOOD_OPTIONS)}",
                "beat": "这一格的叙事功能：建立/对白/反应/动作/情绪/转场/揭示/蒙太奇/日常/幽默/细节",
                "pacing": "这一格的节奏：慢/中/快",
                "panel_emphasis": "这一格在整页里的视觉权重：normal（常规）/ main（重点）/ splash（满版冲击）",
                "caption": "如果这一格适合'上图下文'的说明型版式，写一句 ≤40 字的说明文字；否则留空",
                "silent": "该格是否完全没有文字（台词、旁白、音效都没有）true/false",
                "hook": "是否为本章最后一个悬念格（true/false）",
                "dialogues": [
                    {
                        "speaker": "说话人姓名；旁白填'旁白'；拟声词填'音效'",
                        "text": "只保留说话内容本身，不要带引号与'某某说'，单条 ≤20 字",
                        "tone": "语气，如 平静/低沉/急促/冷笑/哽咽",
                        "kind": "speech（对话）/ narration（旁白）/ sfx（拟声词）",
                        "slot": f"气泡槽位，从这些里选一个：{'、'.join(config.BUBBLE_SLOTS)}",
                        "offscreen": "说话人是否不在画面里（电话/画外音）true/false",
                    }
                ],
                "source_text": "该格对应的原文短句（≤60 字，可留空）",
                "source_quote": "该格对应的原文依据，≤30 字，逐字引用；优先复用精修 25 格里的 quote",
            }
        ]
    }

    user = f"""请把下面这段小说原文改编成**可以直接开画的条漫分镜脚本**。
你的身份是快看漫画的分镜主笔，不是插画师：读者要靠这些格子读懂剧情、感到情绪、想往下滑。

【作品信息】
整体画风：{style_brief(project)}
风格细化：{project.style_detail or "无"}
故事梗概：{project.summary or "无"}
情节线：{truncate(project.plotline, 700) or "无"}
精修核心任务：{truncate(getattr(project, "core_task", ""), 260) or "无"}
精修视觉锚点：{"；".join(str(a.get("event", "")) for a in (getattr(project, "visual_anchors", []) or [])[:10]) or "无"}
精修 25 格连续故事板（按顺序展开，不得跳过）：
{refine_note}

【已有角色清单（speaker / characters 只能填这里的名字）】
每个画格都要在 character_actions 里给每个可见角色写一个动作/姿态短语（例如"右手撑门框，身体前倾"），供姿态骨架选择。
如果 characters 为空，这一格是纯道具/环境镜头：description 只写道具和承托面，不写人物、身体部位、衣服下摆或“有人走过的痕迹”。
除关键冲突/群像/建立镜头外，每格最多一个完整人物；两人对话优先拆成单人反应、斜侧、插入物、正反打，不要在同一格硬塞两个完整人物。
3~4 人只用于群像、对峙、建立镜头或大场面：必须写清每人的位置、动作、视线，
并给每个人在 character_actions 里单独写一个动作短语。
{char_list}

【精修 25 格的使用方式（重要）】
上面的 25 格是本章已经确认过的叙事骨架，不是参考建议：
- 输出分镜必须覆盖 25 格的顺序与关键动作，不得跳过、不得打乱；一段原文明明对应第 12 格，不要提前画进第 3 格。
- 一格精修格通常展开成 1~2 个分镜：如果这格对白密集，就拆成"说话人 → 听者反应"两格；如果只有一个动作，就保留一格。
- 精修格的 quote 是原文依据：source_quote 优先逐字复用；不要自己另编一句原文。
- 精修格里的 shot_type / environment / emotion 只作为起点；如果连续 3 格同景别，必须按镜头规则改机位或景别。
- 精修没有覆盖到的原文细节，只有在"不补就会看不懂"时才增加分镜；不要为了凑格数复述原文。

【已有场景清单】
{scene_list}

【本段目标格数：{target_shots} 格（{target_low}~{target_high} 格），硬上限 {target_high} 格】
- 请按"叙事 beat"分组，**不是按句子逐句拆**。一段连续描写（同一时间、同一地点、
  同一件事）通常只对应 1 格；只有动作/视角/时间/地点真的变了，才拆下一格。
- 格数明显超过上限会把整话拖成"流水账"，也会导致 JSON 被截断；
  格数低于下限则信息会挤在一起。请把格数控制在上面这个区间内。

【硬配额（必须满足，超出会被程序强制合并/改镜头）】
- 总格数 ≤ {target_high} 格；
- 双人同框 ≤ {two_cap} 格（约 25%）：对白优先"单人/斜侧"，双人只用于对峙、建立镜头；
- 3~4 人群像 ≤ {multi_cap} 格（约 10%）：只用于群像/对峙/建立镜头，每格最多 1 条台词；
- 近景 + 特写 ≤ {close_cap} 格（约 30%）：大特写是情绪牌，不要连着打；
- 单人格 ≥ 一半；完全无文字的纯画面 15%~30%；
- 同一场景内不得连续 3 格同景别/同机位。

【第一原则：一格 = 一个 beat = 一个瞬间】
- 一格只发生一件事、只有一个视觉焦点、只有一个动作瞬间。
- 如果原文一句话里有"起身→走到窗边→推开窗"三个动作，就拆成三格；
  如果连续几段都在写同一个静止画面，就合并成一格。
- **禁止**把一段原文原样抄进 description。description 是给画师的指令，不是小说。
- 禁止在 description 里出现引号、台词、心理活动、比喻堆砌、原文整段。

【第二原则：镜头要有变化，画面要能"演"】
- 每场戏的第一格必须是远景或全景（建立镜头），交代"这是哪、什么时候"。
- 同一场景内不得连续 3 格使用同一景别；相邻两格不得同景别 + 同机位。
- **近景 + 特写合计占 24%~30%**，只留给关键台词与情绪转折；不要全篇大特写，
  也不要全篇中景。
- **完全没有文字（台词/旁白/音效）的纯画面格控制在 15%~30%**，不要超过 40%；
  每 6~8 格至少 1 格纯画面反应格即可，不要连续 3 格以上都没有文字。
- 连续有台词的格不超过 3 格，第 4 格必须插反应格或环境格。
- **单一场景不要超过全篇的 65%**；同一空间里每 8~10 格换一次机位或景别方向。
- panel_scale 至少用到 3 种：动作/快节奏用 S，常规对白用 M，情绪/冲击用 L，
  换场/大场面用 XL。不要全篇都是 M。
- 对话双方在同一场景内保持左右位置稳定（char_sides），不要随意跳轴。

【第三原则：谁在画面里，谁只是声音】
- characters **只填画面里看得见的人**。打电话时，电话另一端的人不在画面里，
  不要填进 characters；他的台词用 "offscreen": true 表示画外音。
- **一格最多 4 个人**；5 人以上必须拆格。对白优先"单人/斜侧"，
  双人同框只用于对峙、建立镜头、大场面；3~4 人只用于群像/对峙/建立镜头。
- 双人同框时，characters 只放 2 人，char_sides 必须一左一右，
  且**最多 1 条台词**；3~4 人同框时，char_positions 必须写清从左到右的顺序，
  且**最多 1 条台词**，其余台词转画外音或拆到反应格。
- 说话人必须在 characters 里；不在画面里就 `offscreen: true`。
- 描述里只写**姓名**，不要重复外貌（发色/服装由统一的"身份卡"注入，
  这里再写一遍会和身份卡打架，还会把另一个人的特征带进来）。
- 旁白（kind=narration）不是台词，只做三件事：交代时间地点、写人物内心、桥接因果。
  每条 ≤24 字，不得复述画面已经能看见的内容。
- 拟声词（kind=sfx）只写声音本身，如"咔哒""砰"，≤6 字。

【第三原则补充：单人与双人的写法（直接决定会不会多画人）】
- **单人镜头**：description 必须给出排他信号，写成"X 独自……"或"画面里只有 X 一个人"；
  场景必须写成空场景，例如"空无一人的夜晚街巷""吧台空置的酒馆""空荡的走廊"。
  禁止在单人镜头里写：两人、二人、双人、对方、对手、同伴、人群、路人、行人、
  围观者、观众、群众、另一个人、另一个角色、肩背、半张脸、人影。
  单人的 gaze 只写"看向画面左侧/右侧/上方/下方/深处"，不写"看向画外的人/对方"。
  单人优先用近景、半身或特写；只有交代空间时才用远景/全景，
  且必须补一句"人物占画面主体"，不要让画面边缘留出大片空地。
- **双人镜头**：char_sides 必须一左一右；description 先写"左侧的 A 在做什么"，
  再写"右侧的 B 在做什么"，最后写两人之间的空间关系（例如"两人之间隔着一张桌子"）。
  不要写"两人长得像/都是黑发/都穿深色外套"这类把特征合并的句子；
  两人的差异由程序按身份卡注入，你只需要把位置、动作、视线写清楚。
- **单人格出现"过肩"时**：改为斜侧或平视。过肩镜头的前景肩背在纯文生图里
  会被画成第二个人；双人格同样降斜侧（模型会把肩背画成独立的第三个人）。

【第三原则补充：描述必须"可拍"】
每格 description 必须**短而精**（24-80 字），只写：
  ① 谁：角色姓名
  ② 做什么：**一个**动作瞬间（手已经推出去/还没碰到）
  ③ 表情/视线：看哪、什么表情
  ④ 场景关键物：这一格必须出现的一个东西（不要罗列整间屋子）
  ⑤ 光：从哪个方向来
不要堆背景装饰、不要写景别/机位、不要心理/比喻/原文；分镜乱通常是因为描述太满。
- **禁止**在 description 里写景别/机位（"俯视全景""特写""过肩"）——那是独立字段，
  写两遍会让提示词自相矛盾。
- **禁止**写"她看向画外的人"这种会诱导模型把画外人补进画面的句子；
  改写成"看向画面右侧"。
- 物件/屏幕/空镜格：characters 留空数组，描述只写物件与空间，
  不要为了"有主角"硬塞一个人进去。

【第四原则：台词量按手机屏控制】
- 每格最多 3 条台词；单条 ≤20 字；超过就拆到相邻格（说话人近景 / 听者反应交替）。
- 同一人在同一轮里连说的几句，合并成一条。
- 不要改写原文台词的意思，也不要凭空添加原文没有的对话。
- 每格给每条台词分配 slot：说话人在画面左侧 → top-left / mid-left / bottom-left
  （按台词顺序从上到下）；在右侧 → top-right / mid-right / bottom-right；
  画外音 → top-center；旁白固定放画面顶部。
  槽位要避开人物脸部：近景/特写优先 top-*，中景可用 mid-*。

【第五原则：叙事完整，节奏像一话】
- 前 3 格内必须出现钩子（冲突、异常、悬念）。
- 每个场景按"建立 → 目标 → 冲突 → 转折"四拍推进，格子之间是"因此/但是"，
  不是"然后…然后…"。
- 章末最后 2~3 格是悬念落点，最后一格 hook=true，并给一个让人想追更的画面。
- 原文的每个情节点都要被至少一格覆盖；不要漏掉关键动作、物件、时间提示。
- 不要凭空添加原文没有的情节。

【第六原则：为"一页多格"做好准备（重要）】
- 成品的格不是全部单独成条：连续的 2~6 格会被编进同一页漫画，
  版式（网格 / 主次格 / 正反打 / 满版 / 上图下文…）由程序按内容自动选。
- 所以每格必须只承担**一个** beat：`beat` 从建立/对白/反应/动作/情绪/转场/
  揭示/蒙太奇/日常/幽默/细节里选一个；同一段连续对白要拆成
  "说话人 → 听者反应 → 再说话"的交替格，让它们能拼进同一页。
- `pacing` 标清慢/中/快：连续 3 格同节奏会让页面没有起伏。
- `panel_emphasis` 只给真正的重点格标 main / splash（钩子、反转、情绪爆发），
  其余写 normal。**不要每格都标 splash**，否则整页没有主次。
- 每个场景的开头 1 格用远景/全景建立空间；同一场景内至少留 1 格反应特写，
  它们会被分到不同页面，形成"建立页 → 对白页 → 情绪页"的翻页节奏。
- `caption` 只在"这一格是说明型画面（时间流逝、环境交代、日常流程）"时写，
  一句 ≤40 字；对白格、动作高潮格留空，避免和气泡重复。
- 单格画面要**自成构图**：每格单独出图后再拼页，所以不要写"接上一格""见左图"
  这类依赖相邻格的描述。

【拆解粒度】
- 以本段目标格数为准（{target_low}~{target_high} 格），不要为了"细"而把一句话拆成一格。
- 一般每 70~110 字原文对应 1 格；对白密集、动作转折、关键发现处可以更细，
  但要用"合并相邻同场景静止画面"把总数拉回目标区间。
{min_hint}
【输出前自检（必须满足）】
1. 每格 description 里没有引号、没有台词、没有"她想/他觉得"这类心理描写。
2. 每格 description 开头没有景别/机位词（俯视/全景/特写/过肩…）。
2.1 单人镜头里没有"两人/对方/人群/路人/观众/同伴/肩背/半张脸"这类召唤词，
    且场景写成了空场景；过肩已经改成斜侧。
3. characters 里没有"只在电话里出现"的人；说话人要么在 characters 里，要么 offscreen=true。
4. 每格 ≤4 人；双人格/3~4 人群像 ≤1 条台词；5 人以上已拆。
5. 同一场景内没有连续 3 格同景别，没有连续 3 格同机位。
6. 全篇至少 3 种 panel_scale，至少 1 个 hook=true。
7. 每格台词 ≤3 条、每条 ≤20 字。
8. 物件/屏幕格 characters 为空。
9. 每格都有 gaze；有道具的格子都填了 props。
10. 双人同框 ≤ {two_cap} 格；3~4 人群像 ≤ {multi_cap} 格；
    近景+特写 ≤ {close_cap} 格；总格数 ≤ {target_high}。

【严格输出以下 JSON 结构】
{json.dumps(schema, ensure_ascii=False, indent=2)}

【本段原文（{chunk_label}）开始】
{chunk_text}
【本段原文结束】

注意：这是整部漫画第 {start_index} 格之后的连续内容，请保持剧情顺序与人物位置连贯。"""
    return [{"role": "system", "content": SYSTEM_STORYBOARD},
            {"role": "user", "content": user}]


def storyboard_dialogue_split_prompt(project: Project, shots_json: list[dict],
                                     max_per_shot: int = 2) -> list[dict]:
    """
    把"一格塞了太多对话"的分镜再拆细。

    一张图里对话太多会把画面盖住，因此把多轮对话拆成多格
    （说话人特写 / 听者反应 / 动作瞬间交替表现）。
    """
    user = f"""下面是漫画分镜列表。其中部分分镜把多轮对话塞进了同一格，
会导致这一格要叠很多对话气泡、把画面完全盖住，必须拆开。

【你的任务】
把每一个"对话条数超过 {max_per_shot} 条"或"包含多个连续动作"的分镜拆成若干格，使得：
1. 每一格的对话条数不超过 {max_per_shot} 条。
2. 每一格仍然是一个清晰的独立画面（说话人近景/特写、听者反应、动作瞬间等）。
3. 原画面描述的细节不要丢：把原描述拆开分配给拆分后的各格，可适当补充镜头语言，
   但不要新增原文没有的情节。
4. 对话条数不超过 {max_per_shot} 条的分镜，原样保留，不要改动。
5. 所有分镜保持原有剧情顺序。
6. characters 只能使用这些角色名：{"、".join(project.character_names()) or "（无）"}。
7. shot_type 属于 {"/".join(config.SHOT_TYPES)}，
   mood 属于 {"/".join(config.MOOD_OPTIONS)}。

【当前分镜列表】
{json.dumps(shots_json, ensure_ascii=False, indent=2)}

【输出结构：与输入完全相同的 {"{shots: [...]}"} 结构】
只输出 JSON。"""
    return [{"role": "system", "content": SYSTEM_STORYBOARD},
            {"role": "user", "content": user}]


def storyboard_merge_prompt(shots_json: list[dict], project: Project) -> list[dict]:
    """合并、去重、排序所有分镜，形成最终分镜列表。"""
    user = f"""下面是同一部小说分段拆解得到的分镜列表（已按剧情顺序拼接）。
请你做一次整体梳理，输出最终的分镜列表。

【最重要的一条：只删重复，不要减少信息】
输出列表必须覆盖原文的全部剧情推进与画面细节。**允许输出比输入更多的分镜**，
但不允许因为"合并"而丢掉任何一个情节动作、场景转换或画面细节。
默认应当保留绝大多数输入分镜。

【梳理规则】
1. 只有"完全重复"或"画面高度雷同（同一个瞬间的同一机位）"才删除，保留信息更全的那一条。
2. 只有相邻两条**确实在描述同一个连续画面**时才合并；只要描述里有不同的动作、
   不同的表情、不同的视角、或不同的场景，就必须保留为两格，不要合并。
3. **无对话的纯画面分镜（环境交代、动作细节、物件特写、时间地点提示）同样重要，
   必须完整保留**，不要因为没有台词就删掉。
4. 如果发现某个情节节点在输入里缺失（例如关键动作、物件出现、时间提示），
   可以按原文补出对应分镜，但不要新增原文没有的情节。
5. 保持剧情时间顺序不变，序号由 1 连续递增（不要出现跳跃或重复）。
6. 修正 characters 字段：只能使用这些角色名：{"、".join(project.character_names()) or "（无）"}。
   只保留画面里看得见的人；电话/画外音的人不要放进 characters。
7. 修正 shot_type / mood：必须分别落在 {"/".join(config.SHOT_TYPES)} 和
   {"/".join(config.MOOD_OPTIONS)} 之内。
8. 保持每一格的 dialogue 条数尽量不超过 3 条；超过就把该格拆成两格，
   而不是删掉台词。
9. **保留并修正导演字段**：camera / composition / lighting / panel_scale /
   transition / focus / action / expression / char_sides / time_of_day /
   silent / hook。缺了就按画面内容补，不要留空。
10. 同一场景内不得连续 3 格同景别或同机位；panel_scale 至少保留 3 种。
11. 台词单条超过 20 字就拆到相邻格；旁白超过 24 字就精简。
12. 每场戏的第一格必须是远景或全景；章末最后一格 hook=true。

【分段结果 JSON】
{json.dumps(shots_json, ensure_ascii=False, indent=2)}

【输出结构：与输入完全相同的 {"{shots: [...]}"} 结构，shots 为最终列表】
只输出 JSON。"""
    return [{"role": "system", "content": SYSTEM_STORYBOARD},
            {"role": "user", "content": user}]


def shot_fix_prompt(shot: Shot, project: Project) -> list[dict]:
    """把一条信息缺失的分镜补全（用于本地兜底后修复）。"""
    user = f"""下面是一条漫画分镜信息，请你补全并润色。

【作品风格】{project.style}
【已有角色】{"、".join(project.character_names()) or "（无）"}
【已有场景】{"、".join(project.scene_names()) or "（无）"}

【当前分镜】
{json.dumps(shot.to_dict(), ensure_ascii=False, indent=2)}

请输出补全后的单条分镜 JSON（结构不变），要求：
- description 必须是具体、可画的镜头描述（40-90 字），只写看得见的东西，
  不要台词、心理描写或原文摘抄
- characters 只能使用已有角色名；画面里看不见的人（电话/画外音）不要填
- shot_type 属于 {"/".join(config.SHOT_TYPES)}，mood 属于 {"/".join(config.MOOD_OPTIONS)}
- 补齐 camera（{"/".join(config.SHOT_ANGLES)}）、composition、
  lighting、panel_scale、transition、focus、action、expression、char_sides
- dialogues 保留原有的对话内容，可补充标点与语气，不得改变原意；
  单条台词 ≤20 字，单格 ≤3 条
只输出 JSON。"""
    return [{"role": "system", "content": SYSTEM_STORYBOARD},
            {"role": "user", "content": user}]


# =============================================================================
# 第四阶段补充：旁白补全
# =============================================================================

def narration_fill_prompt(project: Project, shots_json: list[dict],
                          max_chars: int = 24) -> list[dict]:
    """
    让模型给指定分镜补旁白。

    旁白是条漫的"叙事黏合剂"：交代时间地点、写人物内心、桥接因果。
    规则兜底只能从原文里摘句子，质量一般；这里让模型写得更贴画面。
    """
    user = f"""下面是漫画分镜列表，请你为其中需要旁白的格子补写旁白。

【旁白只做这三件事】
1. 交代时间地点（例如"凌晨两点十七分，楼下卷帘门响了。"）
2. 写人物内心（例如"她早就习惯了这种声音。"）
3. 桥接因果（例如"三天前，她把同一个问题问过另一个人。"）

【绝对不要】
- 不要复述画面里已经看得见的东西（"她拿起手机"这种不要再写）。
- 不要写成对话，旁白不是台词。
- 不要新增原文没有的情节。

【输出要求】
- 每条旁白 ≤ {max_chars} 字，一条只说一件事。
- 只为下面列表里真的需要的格子写；不需要的格子返回空字符串。
- 每场戏的开头、以及出现内心活动 / 时间跳跃的格子最需要旁白。
- 一部短篇里旁白总数控制在 3-8 条，不要每格都加。

【作品信息】画风：{project.style}；梗概：{truncate(project.summary, 200) or "无"}

【分镜列表】
{json.dumps(shots_json, ensure_ascii=False, indent=2)}

【输出结构】
{{"narration": [{{"index": 序号, "text": "旁白内容（不需要就留空字符串）"}}]}}
只输出 JSON。"""
    return [{"role": "system", "content": SYSTEM_STORYBOARD},
            {"role": "user", "content": user}]


# =============================================================================
# 第五阶段：分镜图像提示词
# =============================================================================

def _de_realize(text: str) -> str:
    """
    把旧版人设里残留的"写实"倾向改成条漫插画语汇。

    旧工程的人设是上一代提示词写的，常带"精致写实""比例偏写实"；
    这些词会被图像模型放大成照片/3D 质感，必须在出口处洗掉。
    """
    t = text or ""
    for old, new in (
        ("精致写实的五官", "精致的二次元五官"),
        ("精致写实", "精致的二次元插画"),
        ("比例偏写实", "比例偏插画"),
        ("写实照片", "2D插画"),
        ("写实", "插画"),
        ("照片", "插画"),
        ("3D渲染", "2D手绘"),
    ):
        t = t.replace(old, new)
    return t


# ---------------------------------------------------------------------------
# 出图提示词：按 rules/data/shot_prompt_rules.json 的 order 组装
# ---------------------------------------------------------------------------
#
# 组装顺序（rules 里可改）：
#   画风锚 → 画风 → 人数 → 角色(左/右) → 说话人 → 镜头 → 竖幅 →
#   动作 → 表情 → 焦点 → 画面内容 → 场景 → 时间 → 情绪 → 气泡 → 收尾
#
# 三条硬约束（详见 rules/03_提示词工程规则.md）：
#   1. 只写正面描述（cfg=1 时负向被零化，写"不要 X"等于把 X 喂给模型）；
#   2. 人数与"谁在左/右"必须前置，且与 shot.characters / char_sides 一致；
#   3. 总长度受 PROMPT_MAX_CHARS 约束，超了按 drop_order 整段丢弃，绝不切半句。

_SIDE_ORDER = ("左", "右", "中")


def _rules_mod():
    """延迟导入规则模块，避免 prompts ←→ models 的循环依赖。"""
    try:
        from comicgen import rules as rules_mod
        return rules_mod
    except Exception:  # noqa: BLE001
        return None


def _clean_clause(text: str, limit: int = 0) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip()).strip("。；，、 ")
    if limit and len(t) > limit:
        cut = t[:limit]
        pos = max((cut.rfind(p) for p in "，、；：。"), default=-1)
        t = (cut[:pos] if pos >= max(6, limit // 2) else cut[:limit]).strip("，、；：。 ")
    return t


def _sanitize_prompt(text: str) -> str:
    """替换写实词、丢掉明确的否定句（对当前工作流既无效又有害）。

    不只删整句：角色卡里常见"……；外套长度锁定：短款，不要长风衣/过膝大衣"，
    否定子句藏在句尾。这里按逗号/分号再切一层，把"不要/别/禁止"开头的子句
    整段删掉，避免把"长风衣/第二个人"这类词喂给模型。
    """
    rules_mod = _rules_mod()
    repl = (rules_mod.forbidden().get("replace", {}) or {}) if rules_mod else {}
    for old, new in repl.items():
        if old and new:
            text = text.replace(old, new)
    kept_sentences: list[str] = []
    for sentence in re.split(r"(?<=[。；;])", text or ""):
        if re.match(r"^\s*(不要|别|禁止|严禁|不得|非画)", sentence):
            continue
        subs = re.split(r"(?<=[，,])", sentence)
        kept_subs = [s for s in subs if not re.match(r"^\s*(不要|别|禁止|严禁|不得)", s)]
        kept_sentences.append("".join(kept_subs))
    return "".join(kept_sentences).strip()


def _unique_parts(parts: Sequence[str]) -> list[str]:
    """去掉重复段落（画风词重复两遍会放大该风格，也白占预算）。"""
    out: list[str] = []
    for raw in parts:
        p = (raw or "").strip().strip("。；，、 ")
        if not p:
            continue
        dup = False
        for i, q in enumerate(out):
            if p == q or (len(p) >= 8 and p in q) or (len(q) >= 8 and q in p):
                dup = True
                if len(p) > len(q):
                    out[i] = p
                break
        if not dup:
            out.append(p)
    return out


def _normalize_cast(character_briefs: Sequence[str] | None,
                    cast: Sequence[dict] | None) -> list[dict]:
    """统一两种入参：新代码传 cast（含左右/位置），旧代码传 "名字：人设" 字符串。"""
    items: list[dict] = []
    if cast:
        for item in cast:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip()
            card = str(item.get("card", "") or "").strip()
            if not name and not card:
                continue
            # 身份卡自带"名字："前缀时去掉，避免"右侧是陆鸣：陆鸣：…"
            for sep in ("：", ":"):
                if name and card.startswith(name + sep):
                    card = card[len(name) + len(sep):].strip()
                    break
            items.append({
                "name": name, "card": card,
                "side": str(item.get("side", "") or "").strip(),
                "position": str(item.get("position", "") or "").strip(),
                "outfit": str(item.get("outfit", "") or "").strip(),
                "age": str(item.get("age", "") or "").strip(),
                "build": str(item.get("build", "") or "").strip(),
            })
        return items
    for brief in character_briefs or []:
        text = str(brief or "").strip()
        if not text:
            continue
        name, card = "", text
        for sep in ("：", ":"):
            if sep in text:
                head, rest = text.split(sep, 1)
                if 0 < len(head) <= 12:
                    name, card = head.strip(), rest.strip()
                break
        items.append({"name": name, "card": card, "side": "", "position": "",
                      "age": "", "build": ""})
    return items


def _cast_relation_text(items: Sequence[dict]) -> str:
    """多人同框的“年龄/身高关系”句。

    只写角色卡里的年龄/体型，不猜；用于防止“少年被画成成年人”、
    “矮壮角色被画成高个”这类多人场景的比例漂移。
    """
    if len(items) < 2:
        return ""
    parts: list[str] = []
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if not a.get("name") or not b.get("name"):
                continue
            a_age = str(a.get("age", "") or "").strip()
            b_age = str(b.get("age", "") or "").strip()
            if a_age and b_age and a_age != b_age:
                parts.append(f"{a['name']}是{a_age}，{b['name']}是{b_age}，两人年龄差明显")
            elif a_age and "岁" in a_age and not b_age:
                parts.append(f"{a['name']}是{a_age}，{b['name']}是成年人，"
                             f"{a['name']}比{b['name']}小得多")
            elif b_age and "岁" in b_age and not a_age:
                parts.append(f"{b['name']}是{b_age}，{a['name']}是成年人，"
                             f"{b['name']}比{a['name']}小得多")
            a_build = str(a.get("build", "") or "")
            b_build = str(b.get("build", "") or "")
            a_tall = any(w in a_build for w in ("高大", "魁梧", "高挑"))
            b_tall = any(w in b_build for w in ("高大", "魁梧", "高挑"))
            a_short = any(w in a_build for w in ("瘦小", "矮小", "瘦弱"))
            b_short = any(w in b_build for w in ("瘦小", "矮小", "瘦弱"))
            if a_tall and b_short:
                parts.append(f"{a['name']}比{b['name']}高出一头，{b['name']}只到{a['name']}胸口")
            elif b_tall and a_short:
                parts.append(f"{b['name']}比{a['name']}高出一头，{a['name']}只到{b['name']}胸口")
    return "；".join(dict.fromkeys(parts))


def _scrub_ghost_names(text: str, project: Project, visible: Sequence[str]) -> str:
    """把画面外角色的名字换成"画外"：提示词里出现谁，模型就画谁（rules/03 D5）。"""
    if not text:
        return text
    visible = set(visible or [])
    for c in project.characters:
        if c.name and c.name not in visible and c.name in text:
            text = text.replace(c.name, "画外")
    return text


_OUTERWEAR_WORDS = ("风衣", "大衣", "夹克", "外套", "披风", "斗篷", "羽绒服",
                    "西装外套", "开衫", "马甲", "雨披")


def _canonical_outfit(text: str) -> str:
    """
    同一角色只保留**一件外层服装**。

    人设里常同时写"工装夹克 + 羊毛大衣"，主视觉只画其中一件、三视图却把两件
    叠在一起（甚至出现半透明外套）。这里按描述顺序保留第一件外层，后续外层
    从提示词里去掉；完整人设仍保留在工程/导出文档里，不影响人工查看。
    """
    if not text or not bool(getattr(config, "CHARACTER_SINGLE_OUTERWEAR", True)):
        return text
    parts = [p.strip() for p in re.split(r"[、，；,;]", str(text)) if p.strip()]
    if not parts:
        return text
    seen_outer = False
    kept: list[str] = []
    for part in parts:
        is_outer = any(w in part for w in _OUTERWEAR_WORDS)
        if is_outer:
            if seen_outer:
                continue
            seen_outer = True
        if any(w in part for w in ("薄纱", "透明", "透视", "半透明")):
            continue
        kept.append(part)
    return "、".join(kept) if kept else text


def _character_outfit(character: Any) -> str:
    """只取人设里的【服装风格】段，再压成一件外层；不要把整段人设塞进图像提示词。"""
    if character is None:
        return ""
    design = str(getattr(character, "design", "") or "")
    m = re.search(r"【服装风格】([\s\S]*?)(?=【|$)", design)
    outfit = m.group(1).strip() if m else design
    if not outfit:
        # 只跑过分析、还没生成人设：从 appearance 里取带服装词的分句。
        appearance = str(getattr(character, "appearance", "") or "")
        picked = [p.strip() for p in re.split(r"[，、；。]", appearance)
                  if p.strip() and any(w in p for w in ("衫", "袄", "袍", "衣",
                                                        "裤", "裙", "褂"))]
        outfit = "、".join(picked)
    return _scrub_eye_makeup(_canonical_outfit(outfit))


# 服装色词比通用色词多一层"旧衣/土布"色，用于把描述里的撞色拉回身份卡。
_OUTFIT_COLOR_WORDS = (
    "赭石", "灰蓝", "靛青", "月白", "酱色", "土黄", "藏青", "墨绿", "炭黑",
    "冷灰", "雾蓝", "米白", "驼色", "深棕", "暗红", "银灰", "灰绿", "橄榄绿",
) + _COLOR_WORDS
_GARMENT_WORDS = ("长衫", "短衫", "棉袄", "袍", "外套", "风衣", "夹克", "马甲",
                  "坎肩", "裙", "裤", "褂", "袄")


def _scrub_outfit_conflict(text: str, project: Project,
                           visible: Sequence[str]) -> str:
    """把描述里和身份卡冲突的服装颜色拉回身份卡。

    实测最容易出问题的是"身份卡写赭石色旧长衫，分镜描述却写灰蓝长衫"：
    两个颜色都进了正向，模型会合成一件灰蓝长衫，跨格颜色漂移。
    只替换"颜色 + 服装词"紧邻的短语，不碰环境色与光色。
    """
    if not text:
        return text
    visible_set = set(visible or [])
    garments = "|".join(_GARMENT_WORDS)
    for c in project.characters:
        if not c.name or c.name not in visible_set:
            continue
        outfit = str((c.hard_features or {}).get("outfit", "") or "") or _character_outfit(c)
        canon = next((w for w in _OUTFIT_COLOR_WORDS if w in outfit), "")
        if not canon:
            continue
        for col in _OUTFIT_COLOR_WORDS:
            if col == canon or col in outfit:
                continue
            if re.search(re.escape(col) + r"[^，；。]{0,6}(?:" + garments + ")", text):
                text = text.replace(col, canon)
    return text


def _character_design_section(character: Any, title: str) -> str:
    """从人设正文里取某个【段】的内容，供没有 hard_features 的分析结果兜底。"""
    design = str(getattr(character, "design", "") or "")
    if not design:
        return ""
    m = re.search(rf"【{re.escape(title)}】([\s\S]*?)(?=【|$)", design)
    return (m.group(1).strip() if m else "").strip("。；， ")


def _character_hard_features(character: Any) -> dict[str, str]:
    """
    身份硬特征。

    分析阶段产出的角色往往只有 design / keywords，没有 hard_features；
    旧版只从中抠出“短发”“细长眼”，把“花白、稀疏、圆脸、双下巴、
    左眉尾小痣”这些真正决定一致性的特征丢了，导致每格重新长脸。
    这里优先用 hard_features，缺失时从人设正文分段补齐；连人设都没生成时
    （只跑过第 2/3 步），退回分析阶段提取的 ``appearance`` 原文短句。
    """
    hf = dict(getattr(character, "hard_features", {}) or {})
    if not hf.get("face"):
        hf["face"] = _character_design_section(character, "基本形象")
    if not hf.get("hair"):
        hf["hair"] = _character_design_section(character, "发型与发色")
    if not hf.get("signature"):
        hf["signature"] = _character_design_section(character, "标志性特征")
    if not hf.get("palette"):
        hf["palette"] = _character_design_section(character, "色彩方案")

    appearance = str(getattr(character, "appearance", "") or "").strip()
    if appearance and not any(hf.get(k) for k in ("face", "hair", "outfit", "signature")):
        parts = [p.strip() for p in re.split(r"[，、；。]", appearance) if p.strip()]
        for part in parts:
            if not hf.get("hair") and any(w in part for w in ("发", "胡", "须")):
                hf["hair"] = part
            if not hf.get("face") and any(w in part for w in
                                          ("脸", "面", "皱纹", "颧", "肤色")):
                hf["face"] = part
            if not hf.get("outfit") and any(w in part for w in
                                            ("衫", "袄", "袍", "衣", "裤", "裙", "褂")):
                hf["outfit"] = part
            if not hf.get("signature") and any(w in part for w in
                                               ("伤", "疤", "眼镜", "袖口", "满手", "泥")):
                hf["signature"] = part
        if not hf.get("build"):
            for part in parts:
                if any(w in part for w in ("高大", "清瘦", "瘦削", "微胖",
                                            "魁梧", "佝偻", "矮小", "壮实")):
                    hf["build"] = part
                    break
    return hf


def _character_hair(character: Any) -> str:
    """取发型硬特征；如果是马尾/低马尾，追加“全部收进”的锁定描述。"""
    if character is None:
        return ""
    hf = _character_hard_features(character)
    text = str(hf.get("hair", "") or "").strip()
    # 关键词通常比人设正文更短、更适合直接进提示词，优先用关键词。
    kw_hair = ""
    for kw in (getattr(character, "keywords", []) or []):
        kw = str(kw or "").strip()
        if kw and any(w in kw for w in ("发", "髻", "辫", "寸头", "光头", "刘海")):
            kw_hair = kw
            break
    if kw_hair:
        text = kw_hair
    if not text:
        blob = "、".join([*(getattr(character, "keywords", []) or []),
                          str(getattr(character, "design", "") or "")])
        for kw in ("及肩黑发低马尾", "低马尾", "高马尾", "齐耳短发", "及腰黑长直发",
                   "短发", "长发", "盘发", "双马尾"):
            if kw in blob:
                text = kw
                break
    text = _clean_clause(text, 36)
    if not text:
        return ""
    if any(w in text for w in ("马尾", "盘发")):
        if "全部" not in text and "收进" not in text:
            text += "，全部头发向后收进发髻，额前与鬓角干净，不散发"
    return text


def _character_eyes(character: Any) -> str:
    """眼型 + 双眼对称锁定；眼型只取一个短句，避免和“大眼/小眼”冲突。"""
    if character is None:
        return ""
    hf = getattr(character, "hard_features", {}) or {}
    text = str(hf.get("eyes", "") or "").strip()
    if not text:
        for kw in (getattr(character, "keywords", []) or []):
            if any(w in str(kw) for w in ("眼", "瞳")):
                text = str(kw)
                break
    text = _clean_clause(text, 24)
    return (text + "；双眼大小一致、瞳孔居中、看向同一方向") if text else \
        "双眼大小一致、瞳孔居中、看向同一方向"


def _normalize_age(character: Any) -> str:
    """把“十二岁起当伙计，叙述时已成年”这类年龄描述压成可视化年龄。

    只从已有 age 字段里取“N岁/中年/少年”等，不凭空补年龄——
    主视图才是视觉权威，文本年龄不能和主视图打架。
    """
    raw = str(getattr(character, "age", "") or "").strip()
    if raw not in ("未知", "不确定", "?", ""):
        m = re.search(r"([一二三四五六七八九十百零两\d]{1,3}岁(?:上下|左右|出头)?)", raw)
        if m:
            return m.group(1)
        for word in ("少年", "青年", "中年偏老", "中年", "老年", "孩童"):
            if word in raw:
                return word
    # age 字段常是“未知”；只从人设补“年龄带”（不补数字年龄，避免和主视图打架）
    design = str(getattr(character, "design", "") or "")
    for word in ("少年", "青年", "中年偏老", "中年", "老年", "孩童"):
        if word in design:
            return word
    return _clean_clause(raw, 12) if raw not in ("未知", "不确定", "?", "") else ""


def character_feature_lock(character: Any) -> str:
    """跨图逐字复用的身份锁：年龄/体型/脸/发/眼/服装/记忆点。

    主视图、姿态总图、分镜都使用这一段，避免模型每格重新解释角色。
    只写可视化特征，不写性格/身世；优先使用分析阶段的关键词，
    保证“花白、稀疏、圆脸、双下巴、小痣”这类特征不会在拼接时丢失。
    """
    if character is None:
        return ""
    hf = _character_hard_features(character)
    name = str(getattr(character, "name", "") or "").strip()
    age = _normalize_age(character)
    gender = _clean_clause(str(getattr(character, "gender", "") or ""), 8)
    keywords = [str(k or "").strip() for k in (getattr(character, "keywords", []) or [])
                if str(k or "").strip()]
    build = next((k for k in keywords
                  if any(w in k for w in ("清瘦", "瘦削", "瘦小", "敦实", "微胖",
                                          "魁梧", "佝偻", "高挑", "矮小", "壮实"))), "")
    face_raw = next((k for k in keywords
                     if any(w in k for w in ("脸", "颧骨", "下巴", "眉骨", "眼窝"))), "")
    face = face_raw
    if face_raw:
        # 关键词常把“脸型”和“眼型”写在一起；只取“脸型”那一段
        fm = re.search(r"([\u4e00-\u9fa5]{1,8}脸(?:盘|颊|型)?(?:双下巴)?)", face_raw)
        if fm:
            face = fm.group(1)
    if not face:
        face = _clean_clause(str(hf.get("face", "") or ""), 26)
    hair = _character_hair(character)
    eyes = _character_eyes(character)
    # 眼型后面已经统一追加“双眼对称”，避免重复
    eyes = (eyes.replace("双眼大小一致、瞳孔居中、看向同一方向", "")
            .replace("；双眼对称", "").strip("；，、 "))
    eyes = re.sub(r"[；，、\s]*双眼对称$", "", eyes).strip("；，、 ")
    # 关键词里“瘦长脸细长下垂眼…”会把脸型带进眼睛；从开头去掉脸型
    if face and eyes.startswith(face):
        eyes = eyes[len(face):].strip("，、； ")
    elif face_raw and eyes.startswith(face_raw):
        eyes = eyes[len(face_raw):].strip("，、； ")
    # 人设明确写了“右眼略小/不对称”时，不能再用“双眼对称”覆盖它
    asym_blob = " ".join([face, eyes, str(getattr(character, "design", "") or "")])
    eye_lock = ("双眼位置正确、瞳孔同向"
                if any(w in asym_blob for w in ("右眼略小", "左眼略小", "不对称", "大小眼"))
                else "双眼对称")
    outfit = _clean_clause(_character_outfit(character), 46)
    sig = _clean_clause(str(getattr(character, "identity_tag", "") or ""), 30)
    if not sig:
        sig = _clean_clause(str(hf.get("signature", "") or ""), 30)
    parts = []
    if name:
        parts.append(f"角色：{name}")
    if gender:
        parts.append(f"性别：{gender}")
    if age:
        parts.append(f"年龄：{age}")
    if build:
        parts.append(f"体型：{build}")
    if face:
        parts.append(f"脸型：{face}")
    if hair:
        parts.append(f"发型：{hair}")
    if eyes:
        parts.append(f"眼睛：{eyes}；{eye_lock}")
    if outfit:
        parts.append(f"服装：{outfit}")
        if ("短款" in outfit) and not any(w in outfit for w in ("长风衣", "长外套", "大衣")):
            parts.append("外套长度锁定：短款，衣摆到腰或髋部")
    if not outfit:
        palette = _clean_clause(str(hf.get("palette", "") or ""), 32)
        if not palette:
            palette = "、".join(_character_color_words(character)[:3])
        if palette:
            parts.append(f"配色：{palette}")
    lower = character_lower_body(character)
    if lower:
        parts.append(f"下装锁定：{lower}")
    if sig:
        parts.append(f"记忆点：{sig}")
    if not parts:
        return ""
    return "【身份锁定】" + "；".join(parts) + "。"


def _scrub_person_text(text: str, project: Project,
                       visible: Sequence[str]) -> str:
    """画面内人物描述的出口清洗：去画外名 → 修眼镜 → 拉回服装色 → 去眼妆。"""
    return _scrub_eye_makeup(
        _scrub_outfit_conflict(
            _scrub_mismatched_glasses(
                _scrub_ghost_names(text, project, visible),
                project, visible),
            project, visible))


def _character_keywords(character: Any) -> list[str]:
    """关键词也要压掉第二件外层（如"米白薄开衫"），否则侧视图会换外套。"""
    if character is None:
        return []
    out: list[str] = []
    seen_outer = False
    for raw in getattr(character, "keywords", []) or []:
        kw = str(raw or "").strip()
        if not kw:
            continue
        if any(w in kw for w in ("薄纱", "透明", "透视", "半透明")):
            continue
        is_outer = any(w in kw for w in _OUTERWEAR_WORDS)
        if is_outer:
            if seen_outer:
                continue
            seen_outer = True
        out.append(kw)
    return out


def _character_has_glasses(character: Any) -> bool:
    if character is None:
        return False
    blob = " ".join([str(getattr(character, "design", "") or ""),
                     "、".join(getattr(character, "keywords", []) or [])])
    return any(w in blob for w in ("眼镜", "墨镜", "镜片", "镜框", "单片镜"))


def _has_glasses(project: Project, visible: Sequence[str]) -> bool:
    names = set(visible or [])
    for c in project.characters:
        if c.name not in names:
            continue
        blob = " ".join([str(c.design or ""), str(c.identity_tag or ""),
                         "、".join(c.keywords or [])])
        if any(k in blob for k in ("眼镜", "墨镜", "镜片", "镜框", "单片镜")):
            return True
    return False


def _scrub_scar(text: str) -> str:
    """把"细疤/疤痕"降成"浅淡旧痕"：Z-Image 会把眉尾疤画成眼罩/单片镜。"""
    if not text:
        return text
    for a, b in (("右眉尾细疤", "眉形清晰"),
                 ("左眉尾细疤", "眉形清晰"),
                 ("眉尾细疤", "眉形清晰"),
                 ("细疤", "浅淡纹理"),
                 ("疤痕", "旧痕")):
        text = text.replace(a, b)
    return text


def _scrub_eye_makeup(text: str) -> str:
    """
    把"眼下淡青影/青影"改成"眼周自然肤色"。

    Z-Image 会把"青影"理解成绿色眼影：冷绿主题色下，眼睛下方出现一圈明显绿色。
    这是文本歧义，不是画风；提示词里只保留"自然肤色/素颜"。
    """
    if not text:
        return text
    for a, b in (("眼下淡青影", "眼周自然肤色"),
                 ("眼下有淡淡青影", "眼周自然肤色"),
                 ("眼下淡青色阴影", "眼周自然肤色"),
                 ("眼下青影", "眼周自然肤色"),
                 ("淡青影", "眼周自然肤色"),
                 ("青影", "自然阴影")):
        text = text.replace(a, b)
    return text


def _scrub_mismatched_glasses(text: str, project: Project,
                              visible: Sequence[str]) -> str:
    """
    角色没戴眼镜，分镜里却写"镜片反光"——Z-Image 会把它画成单片眼镜/眼罩。

    分镜表是旧数据或文本模型脑补时常见这种道具冲突；生成前按角色设定做一次
    定向清理，只改"镜片/眼镜"相关措辞，不动其它画面描述。
    """
    if not text or _has_glasses(project, visible):
        return text
    for a, b in (
        ("镜片反光遮住半只眼", "一侧眼睛被阴影压住"),
        ("镜片反出一道光", "眼底反出一道光"),
        ("越过镜片", "抬眼"),
        ("镜片后", ""),
        ("镜片", "眼睛"),
        ("眼镜", "眼睛"),
        ("镜框", "眼眶"),
        ("单片镜", "眼睛"),
    ):
        text = text.replace(a, b)
    return _scrub_scar(text)


def _visible_speaker(shot: Shot, cast: Sequence[dict]) -> tuple[str, list[str]]:
    """找出画面里唯一在说话的人；多人同时说话返回空（避免指错人）。"""
    names = [c["name"] for c in cast if c["name"]]
    speakers: list[str] = []
    for d in shot.dialogues:
        if d.kind != "speech" or not (d.text or "").strip() or d.offscreen:
            continue
        if d.speaker and d.speaker in names and d.speaker not in speakers:
            speakers.append(d.speaker)
    return (speakers[0] if len(speakers) == 1 else ""), speakers


def _lens_block(shot: Shot, object_only: bool, rules_mod) -> str:
    type_map = ((getattr(config, "SHOT_TYPE_PROMPT_MAP_OBJECT", None)
                 or config.SHOT_TYPE_PROMPT_MAP) if object_only
                else config.SHOT_TYPE_PROMPT_MAP)
    lens = [type_map.get(shot.shot_type, "") or f"{shot.shot_type}镜头"]
    cam = comp = light = None
    if rules_mod:
        cam = rules_mod.lens_map("camera_object" if object_only else "camera").get(shot.camera or "")
        comp = rules_mod.lens_map("composition").get(shot.composition or "")
        light = rules_mod.lens_map("lighting_object" if object_only else "lighting").get(
            shot.lighting or "") or rules_mod.lens_map("lighting").get(shot.lighting or "")
    if shot.camera:
        lens.append(cam or f"{shot.camera}机位")
    if shot.composition:
        lens.append(comp or shot.composition)
    if shot.lighting and not object_only:
        lens.append(light or shot.lighting)
    elif shot.lighting and object_only:
        lens.append(light or f"{shot.lighting}")
    return "；".join(x for x in lens if x)


def _bubble_block(project: Project, shot: Shot,
                  visible: Sequence[str] | None = None,
                  draw_bubbles: bool | None = None) -> str:
    """
    气泡绘制指令（默认让图像模型按坐标画，程序只负责填字）。

    为什么写这么细：图像模型不会"猜"气泡该放哪。数量、每个气泡的槽位、
    外接框（左/上/宽/高，百分比）、形状、尾巴朝向、内部纯白，全部写出来，
    出图后的 detect_blank_bubbles() 才有稳定目标可匹配。
    """
    plan = list(getattr(shot, "bubble_plan", []) or [])
    if not plan:
        return ""
    rules_mod = _rules_mod()
    keep_clear = 0.08
    if rules_mod:
        try:
            keep_clear = float(rules_mod.shot_prompt().get("bubble", {}).get(
                "keep_top_clear_ratio", 0.08))
        except (TypeError, ValueError):
            keep_clear = 0.08
    has_narration = any(d.kind == "narration" and (d.text or "").strip()
                        for d in shot.dialogues)

    if draw_bubbles is None:
        draw_bubbles = bool(getattr(config, "IMAGE_DRAW_BUBBLES", False))
    if draw_bubbles:
        slot_cn = {
            "top-left": "左上角", "top-center": "顶部居中", "top-right": "右上角",
            "mid-left": "画面中部偏左", "mid-right": "画面中部偏右",
            "bottom-left": "左下角", "bottom-right": "右下角",
        }
        count = len(plan)
        head = str(getattr(config, "IMAGE_BUBBLE_SUFFIX", "") or "")
        try:
            head = head.format(count=count)
        except (KeyError, IndexError, ValueError):
            pass
        parts = [head] if head else []
        for i, b in enumerate(plan, start=1):
            try:
                x, y = float(b.get("x", 0)), float(b.get("y", 0))
                w, h = float(b.get("w", 0)), float(b.get("h", 0))
            except (TypeError, ValueError):
                continue
            slot = str(b.get("slot", "") or "")
            speaker = str(b.get("speaker", "") or "")
            side = (shot.char_sides or {}).get(speaker, "")
            style = str(b.get("style", "") or "圆角矩形")
            shape = "方框" if style == "旁白框" else ("椭圆" if style == "椭圆" else "圆角矩形")
            tail = "，没有尾巴" if b.get("tail") in (None, "", "none") else \
                f"，尾巴指向画面{'左侧' if side == '左' else '右侧' if side == '右' else '说话人'}" \
                + (f"的{speaker}" if speaker and (not visible or speaker in visible) else "")
            parts.append(
                f"气泡 {i}：位于{slot_cn.get(slot, '画面内')}，"
                f"外接框左边缘约 {x:.0%}、上边缘约 {y:.0%}、宽约 {w:.0%}、高约 {h:.0%}，"
                f"{shape}，内部纯白无字{tail}")
        parts.append(f"本格气泡总数 {count} 个，画面里只有这 {count} 个气泡")
        if count == 1:
            b0 = plan[0]
            parts.append(
                f"这个气泡固定在{slot_cn.get(str(b0.get('slot', '')), '画面内')}，"
                f"不移动到画面其他位置，尾巴指向说话人")
        if has_narration:
            parts.append(f"画面最顶部约 {keep_clear:.0%} 的区域留给旁白条，"
                         f"气泡从它下方开始，人物脸部放在更下方")
        return "；".join(x for x in parts if x)

    # ---- 备用模式（IMAGE_DRAW_BUBBLES=False）：只预留干净背景，不提"气泡/文字" ----
    try:
        if (getattr(shot, "panel_orientation", "") or getattr(shot, "bubble_programmatic", False)) \
                and not bool(getattr(config, "BUBBLE_RESERVE_PAGE_ENABLED", False)):
            return ""
        top = float(getattr(config, "BUBBLE_RESERVE_TOP_RATIO", 0.045))
        # 预留区只覆盖气泡实际占用的高度，不要动不动空出 30%~40% 画面
        try:
            plan_bottom = max(float(b.get("y", 0)) + float(b.get("h", 0))
                              for b in plan)
        except (TypeError, ValueError):
            plan_bottom = 0.28
        bottom = max(0.16, plan_bottom + 0.025)
        if has_narration:
            bottom = max(bottom, top + 0.14)
        bottom = min(bottom, float(getattr(config, "BUBBLE_RESERVE_MAX_RATIO", 0.42)))
        template = str(getattr(config, "IMAGE_BUBBLE_RESERVE_PROMPT", "") or "")
        if not template:
            return ""
        return template.format(top=int(round(top * 100)), bottom=int(round(bottom * 100)))
    except (KeyError, IndexError, ValueError):
        return ""


def negative_prompt(project: Project, shot: Shot | None = None,
                    kind: str = "shot",
                    character: Any = None) -> str:
    """
    组合负向提示词（真正送进 KSampler.negative 的文本）。

    注意：Z-Image-Turbo 是蒸馏模型，cfg=1 时负向条件在数学上被忽略；
    本项目默认 COMFY_CFG=1.4，并让工作流把 negative 接到 CLIPTextEncode，
    负向词才会开始起作用。负向词按"镜头 / 主视觉 / 三视图 / 风格"分开，
    避免把三视图本身的"并排三角度"误当成多格漫画压掉。
    """
    parts: list[str] = []
    # 人数对应的负向清单放最前：负向总长有 900 字上限，放后面会被风格词挤掉。
    if kind == "shot" and shot is not None:
        parts.append(castguard.negative_clause(shot, project))
    parts.append(str(getattr(config, "IMAGE_NEGATIVE_PROMPT", "") or ""))
    style_id = str(getattr(project, "style_id", "") or "")
    prefix = style_id.split("-")[0].upper() if "-" in style_id else ""
    if kind == "shot":
        parts.append(str(getattr(config, "NEGATIVE_SHOT", "") or ""))
        parts.append("换脸, 脸不一致, 发色偏移, 服装颜色偏移, 身份漂移")
        # 人数锁的负向对应已放在函数开头（见 parts 初始化）。
        # 单人格且角色是女性时，明确压掉“画成男性”的偏移；
        # 男女同框时不能加，否则会把男角色也压掉。
        try:
            chars = [c for c in (getattr(shot, "characters", []) or [])]
            women = []
            for nm in chars:
                c = None
                if project is not None and hasattr(project, "find_character"):
                    c = project.find_character(str(nm))
                if c is not None and str(getattr(c, "gender", "") or "") == "女":
                    women.append(c)
            if women and len(women) == len(chars):
                parts.append("男性化, 男相, 男性面孔, 喉结, 胡须")
        except Exception:  # noqa: BLE001
            pass
        if shot is not None and getattr(shot, "bubble_programmatic", False):
            parts.append("对话气泡, 空白气泡, 气泡框")
        # 短款外套的角色，禁止模型改成长风衣/过膝大衣
        try:
            outfits = []
            for name in (getattr(shot, "characters", None) or []):
                c = project.find_character(name) if project else None
                if c:
                    outfits.append(_character_outfit(c))
            if outfits and all(("短款" in o and "长风衣" not in o and "大衣" not in o)
                               for o in outfits if o):
                parts.append("长风衣, 长外套, 过膝大衣, 风衣下摆, 大衣")
        except Exception:  # noqa: BLE001
            pass
        # 下装是裤装的角色，禁止模型在侧身/走路格换成裙子或高跟鞋
        try:
            for name in (getattr(shot, "characters", None) or []):
                c = project.find_character(name) if project else None
                if c and ("裤" in character_lower_body(c)):
                    parts.append("裙子, 半裙, 连衣裙, 高跟鞋, 换装")
                    break
        except Exception:  # noqa: BLE001
            pass
        dark_only = []
        for name in (getattr(shot, "characters", None) or []):
            c = project.find_character(name) if project else None
            if c:
                dark_only.append(_character_outfit(c))
        if dark_only and all(
                not any(w in o for w in ("奶油白", "米白", "白色", "浅色"))
                for o in dark_only):
            parts.append(str(getattr(config, "NEGATIVE_OUTFIT_LIGHT", "") or ""))
    elif kind == "character_triple":
        parts.append(str(getattr(config, "NEGATIVE_TRIPLE", "") or ""))
    elif kind == "character_back":
        parts.append(str(getattr(config, "NEGATIVE_TRIPLE", "") or ""))
        parts.append(str(getattr(config, "NEGATIVE_BACK", "") or ""))
    elif kind == "character_pose":
        parts.append(str(getattr(config, "NEGATIVE_POSE", "") or ""))
    elif kind == "character_main":
        parts.append(str(getattr(config, "NEGATIVE_CHARACTER", "") or ""))
    elif kind == "character_top":
        parts.append(str(getattr(config, "NEGATIVE_TOP", "") or ""))
        parts.append(str(getattr(config, "NEGATIVE_CHARACTER", "") or ""))
    if kind.startswith("character") and not _character_has_glasses(character):
        parts.append("眼镜, 墨镜, 单片眼镜, 眼罩, 镜片反光")
    if kind.startswith("character") and character is not None:
        outfit = _character_outfit(character)
        if outfit and not any(w in outfit for w in ("奶油白", "米白", "白色", "浅色")):
            parts.append(str(getattr(config, "NEGATIVE_OUTFIT_LIGHT", "") or ""))
    if prefix == "FI":
        parts.append(str(getattr(config, "NEGATIVE_STYLE_CARTOON", "") or ""))
    elif prefix in ("FA", "FB", "FD", "FE", "FG"):
        parts.append(str(getattr(config, "NEGATIVE_STYLE_PHOTO", "") or ""))
    elif prefix == "FF":
        parts.append(str(getattr(config, "NEGATIVE_STYLE_3D", "") or ""))
    if prefix in ("FA", "FB", "FD", "FE"):
        # 这些风格最容易被"韩漫/日系萌系"模板带跑；负向只压模板，不压风格本身。
        parts.append("韩漫画风, 日系萌系, 平滑渐变, 大眼睛, 低幼卡通")
    if prefix == "FA":
        parts.append("干净矢量线, 均匀线宽, 平滑渐变, 正常头身比, 赛璐璐平涂, 数码平涂")
    elif prefix == "FB":
        parts.append("硬边矢量线, 塑料感3D, 均匀渐变, 照片")
    elif prefix == "FD":
        parts.append("写实照片, 厚涂油画, 3D渲染, 均匀渐变")
    elif prefix == "FE":
        parts.append("照片, 3D渲染, 塑料高光, 厚涂")
    seen: list[str] = []
    for chunk in parts:
        for item in str(chunk or "").replace("，", ",").split(","):
            item = item.strip()
            if item and item not in seen:
                seen.append(item)
    text = ", ".join(seen)
    return text[:900]


def shot_image_prompt(project: Project, shot: Shot,
                      character_briefs: Sequence[str] | None = None,
                      include_bubbles: bool = False,
                      ref_names: Sequence[str] | None = None,
                      ref_roles: Sequence[dict] | None = None,
                      cast: Sequence[dict] | None = None,
                      trigger: str = "",
                      style_ref: bool = False,
                      style_ref_mode: str = "style",
                      draw_bubbles: bool | None = None) -> str:
    """
    组装一张分镜图的最终提示词。

    parameters
    ----------
    character_briefs : 旧接口，"名字：人设" 列表（兼容 selftest/旧调用）
    cast             : 新接口，``[{name, card, side, position}]``；优先级高于 briefs
    ref_names        : 只有工作流真的支持参考图时才传（否则 prompt 里不出现"参考图"）
    ref_roles        : 与 ref_names 一一对应，``[{name, role}]``；
                       role = face/main/side/back/top/style，决定“只取什么”
    """
    _ = include_bubbles
    rules_mod = _rules_mod()
    limit = int(getattr(config, "PROMPT_MAX_CHARS", 0) or 0)
    if rules_mod:
        try:
            limit = int(rules_mod.prompt_max_chars())
        except (TypeError, ValueError):
            pass
    limit = limit or 760
    subjects = rules_mod.subjects() if rules_mod else {}

    cast_items = _normalize_cast(character_briefs, cast)
    max_cast = int(getattr(config, "MAX_CHARACTERS_PER_SHOT", 4) or 4)
    if rules_mod:
        try:
            max_cast = int(rules_mod.cast_policy().get("max_characters", max_cast) or max_cast)
        except (TypeError, ValueError):
            pass
    n_cast = len(cast_items)
    # 3~4 人给额外预算：每多一人 +80 字，避免为了塞下角色卡把画面内容丢光。
    # 双人仍按 900 字上限；三人 980、四人 1060。
    if n_cast > 2:
        limit += (n_cast - 2) * 80
    # 纯文生图最多画 max_cast 个人：超过上限的由导演层拆格，这里做最后兜底。
    cast_items = cast_items[:max(1, max_cast)]
    n_cast = len(cast_items)
    object_only = n_cast == 0

    # ---- 1) 画风 ----
    # 优先用 handraw-style 的编号化风格（365 种 + 主题色），没有才回落到
    # config 里的 7 个中文预设。handraw 风格块必须原样保留：
    # _sanitize_prompt 会把"写实/3D/照片"替换掉，那会直接毁掉风格本身。
    handraw_text = ""
    if bool(getattr(config, "HANDRAW_ENABLED", True)):
        hs = stylelib.project_style(project)
        if hs.get("style_id"):
            handraw_text = stylelib.style_prompt_block(
                hs["style_id"], hs.get("color_ids") or [],
                include_reference_note=bool(style_ref) and "__style__" not in (ref_names or ()),
                ref_mode=style_ref_mode,
                tone=getattr(project, "style_tone", ""),
                trigger=hs.get("trigger", ""),
                text=f"{project.summary} {project.style_detail}")
    style_key = project.style if project.style in config.STYLE_PROMPT_MAP else config.DEFAULT_STYLE
    style_text = config.STYLE_PROMPT_MAP.get(style_key, config.STYLE_PROMPT_MAP[config.DEFAULT_STYLE])
    style_detail = ""
    if project.style_detail and not handraw_text:
        style_detail = f"风格补充：{_de_realize(_clean_clause(project.style_detail, rules_mod.style_detail_max_chars() if rules_mod else 40))}"

    # ---- 2) 人数 + 角色绑定（前置，决定"画几个人、谁在左"）----
    # castguard 负责四件事：
    #   1. 人数锁：正向只写"只有一位/两位人物"，不写"没有第二个人"；
    #   2. 一人一段：先写完 A 的全部特征，再写 B，绝不交叉；
    #   3. 双人差异对照 + 左右分区 + 中间留空，防止两张脸被平均化；
    #   4. 单人场景去人群、过肩一律降斜侧，堵住"画面边缘再长一个人"。
    # 景别先算出来：身份锁的详略由 shot_type 决定（远景只留轮廓，特写保留记忆点）。
    level = shot_compiler.detail_level(getattr(shot, "shot_type", ""))
    guard = castguard.prepare(project, shot, cast_items, object_only=object_only,
                              detail_level=level)
    view = guard.shot or shot
    count_text = guard.count_text
    cast_text_value = guard.cast_text or ""
    isolation_text = guard.isolation_text or ""
    compiled = shot_compiler.compile_shot(
        project, shot, cast_items,
        style_hint=str(getattr(project, "style", "") or ""))
    if compiled:
        # 按景别裁身份锁：远景保留轮廓，特写保留全量；不改写人设字段。
        cast_text_value = shot_compiler.trim_identity(
            cast_text_value, compiled.get("level", level), n_cast)

    # ---- 3) 说话人绑定（谁张嘴、谁闭嘴）----
    speaker, _speakers = _visible_speaker(shot, cast_items)
    speaker_text = ""
    if speaker:
        others = [c["name"] for c in cast_items if c["name"] and c["name"] != speaker]
        if others:
            speaker_text = subjects.get(
                "speaker", "这一格里说话的是{speaker}，只有{speaker}张嘴；{others}闭着嘴，看向{speaker}"
            ).format(speaker=speaker, others="、".join(others))
        else:
            speaker_text = subjects.get(
                "speaker_solo", "这一格里说话的是{speaker}，嘴微微张开"
            ).format(speaker=speaker)

    # ---- 4) 其它块 ----
    # lens 用 guard 的出图视图：过肩已降成斜侧，避免前景被画成第二/第三个人
    lens_text = _lens_block(view, object_only, rules_mod)
    # 构图：分格页里每格按自己的格子比例取构图指令；原条漫仍用竖幅满版
    orientation = (shot.panel_orientation or "").lower()
    if orientation == "wide":
        vertical = str(getattr(config, "PAGE_PANEL_COMPOSITION_WIDE", "") or "")
    elif orientation == "tall":
        vertical = str(getattr(config, "PAGE_PANEL_COMPOSITION_TALL", "") or "")
    elif orientation == "square":
        vertical = str(getattr(config, "PAGE_PANEL_COMPOSITION_SQUARE", "") or "")
    elif getattr(config, "WEBTOON_MODE", False):
        vertical = _clean_clause(getattr(config, "WEBTOON_COMPOSITION_PROMPT", ""), 90)
    else:
        vertical = ""
    if vertical and shot.panel_scale in ("L", "XL") \
            and getattr(config, "WEBTOON_MODE", False) and not orientation:
        vertical = f"{vertical}；画幅偏高，纵向空间充足"
    # 单人近景/特写必须把"下方延伸到地面"改掉：那是全身构图指令，
    # 会和"取胸部以上"打架，实测会把特写画成全身站姿（也更容易在脚边补人）。
    if not object_only and str(getattr(shot, "shot_type", "") or "") in ("特写", "近景"):
        _close = str(getattr(shot, "shot_type", "")) == "特写"
        _part = "脸部（头顶到下颌）" if _close else "胸部以上（头顶到胸口）"
        _cut = "下颌以下在画面之外" if _close else "腰部以下在画面之外"
        if orientation == "wide":
            vertical = (f"单幅横向近景构图：只画人物{_part}，{_cut}，"
                        f"人物占画面主体，背景只保留少量空间，满版出血")
        else:
            vertical = (f"单幅竖向近景构图：只画人物{_part}，{_cut}，"
                        f"人物填满画面，背景只保留少量空间，满版出血")
    elif not object_only and str(getattr(shot, "shot_type", "") or "") == "中景":
        if orientation == "wide":
            vertical = ("单幅横向中景构图：取人物腰部以上，人物占画面主体，"
                        "背景保留必要环境，满版出血")
        else:
            vertical = ("单幅竖向中景构图：取人物腰部以上，人物占画面主体，"
                        "背景保留必要环境，满版出血")
    emphasis_text = ""
    emp_key = shot.panel_emphasis if shot.panel_emphasis in ("main", "splash") else ""
    if not emp_key and shot.panel_role == "inset":
        emp_key = "inset"
    if emp_key:
        emphasis_text = config.PANEL_EMPHASIS_PROMPTS.get(emp_key, "")

    visible_names = [c["name"] for c in cast_items if c["name"]]
    action = ""
    if view.action:
        action = f"动作瞬间：{_scrub_person_text(_clean_clause(view.action, 40), project, visible_names)}"
    expression = ""
    if view.expression and not object_only:
        expression = f"表情与眼神：{_scrub_person_text(_clean_clause(view.expression, 40), project, visible_names)}"
    focus = ""
    if view.focus:
        focus = f"视觉焦点：{_scrub_person_text(_clean_clause(view.focus, 30), project, visible_names)}"
    content = ""
    if view.description:
        content = (f"画面内容："
                   f"{_de_realize(_scrub_person_text(_clean_clause(view.description, 110), project, visible_names))}")
    elif object_only:
        content = subjects.get("object_subject", "")
    if object_only:
        mode = str(getattr(shot, "object_mode", "") or "")
        desc = shot.description or ""
        if mode == "物证照":
            content += ("；物证记录照：俯拍，物件平放在金属台面上，旁边放一把比例尺，"
                        "屏幕是黑色玻璃，表面只有裂纹、血迹与顶灯的长条反光；"
                        "画面里只有物件、台面、比例尺与反光")
        elif mode == "屏幕界面" or any(k in desc for k in ("屏幕", "手机", "显示器")):
            if any(k in desc for k in ("显示", "记录", "界面", "时间", "号码")):
                content += ("；屏幕亮着，显示冷色调的抽象色块、进度条与数字，"
                            "表面有裂纹与玻璃反光")
            else:
                content += ("；屏幕是黑色玻璃，表面只有裂纹、血迹与顶灯的长条反光")
        elif mode == "环境细节":
            content += "；画面是环境的一个局部，物件之外只有承托它的桌面、墙面与地面"
    gaze = _scrub_mismatched_glasses(
        _scrub_ghost_names(str(getattr(view, "gaze", "") or "").strip(),
                           project, visible_names), project, visible_names)
    if gaze and cast_items:
        gaze_clause = f"视线：{_clean_clause(gaze, 24)}"
        content = f"{content}；{gaze_clause}" if content else gaze_clause
    if n_cast > 1:
        relation = _cast_relation_text(cast_items)
        if relation:
            content = (f"{content}；年龄与身高关系：{relation}"
                       if content else f"年龄与身高关系：{relation}")

    scene_text = ""
    if object_only:
        if shot.scene:
            scene_obj = next((s for s in project.scenes if s.name == shot.scene), None)
            note = shot.scene
            # 物件格不要把"陆鸣工作室"里的角色名带进去，否则模型会在屏幕/墙上画人
            for c in project.characters:
                if c.name and c.name in note:
                    note = note.replace(c.name, "")
            if scene_obj and scene_obj.features:
                note += f"（{_clean_clause(scene_obj.features, 24)}）"
            scene_text = f"场景环境：{note}"
    elif guard.scene_text:
        scene_text = f"场景环境：{_clean_clause(guard.scene_text, 70)}"
    time_text = f"时间：{shot.time_of_day}" if shot.time_of_day else ""
    _mood = (compiled or {}).get("mood") or shot.mood
    mood_text = f"情绪与光影：{_mood}" if _mood else ""
    bubble_text = _bubble_block(project, shot, visible_names, draw_bubbles=draw_bubbles)
    bubble_text = prompt_overrides.text(
        project, "bubble.block", f"shot:{shot.id}", bubble_text)

    ref_text = ""
    if ref_names:
        roles_seq = list(ref_roles or [])
        lines: list[str] = []
        for i, name in enumerate(ref_names, start=1):
            info = roles_seq[i - 1] if i - 1 < len(roles_seq) else {}
            role = str(info.get("role") or ("style" if name == "__style__" else "main"))
            if name == "__style__" or role == "style":
                lines.append(f"第{i}张参考图是画风参考，只取线条、笔触、媒介、材质、"
                             f"色彩倾向与整体视觉语言，画面内容按本格描述")
            elif role == "face":
                lines.append(f"第{i}张参考图是「{name}」的面部特写，"
                             f"只取脸型、五官、发型、发色与年龄感")
            elif role == "side":
                lines.append(f"第{i}张参考图是「{name}」的侧面参考，"
                             f"只取侧脸轮廓、鼻梁、下颌线、后脑与侧面服装结构")
            elif role == "back":
                lines.append(f"第{i}张参考图是「{name}」的背面参考，"
                             f"只取后脑、后发、背部服装与背面轮廓")
            elif role == "top":
                lines.append(f"第{i}张参考图是「{name}」的正上方俯视结构参考，"
                             f"只取发旋、发缝、肩线、衣领与头顶轮廓")
            else:
                lines.append(f"第{i}张参考图是「{name}」的角色主视图，"
                             f"只取脸型、发型、发色、服装款式与配色")
        ref_text = ("参考图使用说明：" + "；".join(lines)
                    + "；所有参考图只取外观，不取站姿、构图、背景、光影与画框；"
                      "本格的姿势、机位、人数与场景以文字描述为准。")

    draw_now = (bool(getattr(config, "IMAGE_DRAW_BUBBLES", False))
                if draw_bubbles is None else bool(draw_bubbles))
    if object_only:
        tail = getattr(config, "IMAGE_QUALITY_SUFFIX_NO_CHARACTER", "")
    elif bubble_text and draw_now:
        tail = getattr(config, "IMAGE_QUALITY_SUFFIX_WITH_BUBBLES", "")
    else:
        tail = getattr(config, "IMAGE_QUALITY_SUFFIX", "")
    tail_final = subjects.get("tail", "这一张图是一个完整画面：一个镜头、一层景、一个瞬间")
    if tail:
        tail_final = f"{tail}；{tail_final}"

    def build(drop: set[str] | None = None,
              content_limit: int = 0) -> str:
        drop = drop or set()
        content_block = content
        if content_limit and view.description:
            content_block = (f"画面内容：{_de_realize(_clean_clause(view.description, content_limit))}"
                             + (f"；视线：{_clean_clause(gaze, 24)}" if gaze and cast_items else ""))
        raw_keys: set[str] = set()
        if handraw_text:
            # handraw 风格块逐字保留（里面的"写实/3D/照片"是风格本体，不能被洗掉）
            trigger_txt = str(trigger).strip()
            blocks_style_anchor = (f"{trigger_txt}。" if trigger_txt else "") + handraw_text
            blocks_style = ""
            raw_keys.add("style_anchor")
        else:
            # 画风锚点与画风正文只去重这两段，不要做全局去重
            # （全局"长句覆盖短句"会把质量收尾顶到前面去）
            style_pair = _unique_parts([getattr(config, "IMAGE_STYLE_ANCHOR", ""), style_text])
            blocks_style_anchor = ((str(trigger).strip() + "。") if str(trigger).strip() else "") \
                + (style_pair[0] if style_pair else "")
            blocks_style = style_pair[1] if len(style_pair) > 1 else ""
        blocks: dict[str, str] = {
            "frame": (compiled or {}).get("frame", ""),
            "style_anchor": blocks_style_anchor,
            "style": blocks_style,
            "count": count_text,
            "cast": ref_text + ("；" if ref_text and cast_items else "") + cast_text_value,
            "crowd": (compiled or {}).get("crowd", ""),
            "isolation": isolation_text,
            "speaker": speaker_text,
            "lens": lens_text,
            "emphasis": emphasis_text,
            "vertical": vertical,
            "action": action,
            "expression": expression,
            "focus": focus,
            "content": content_block,
            "scene": scene_text,
            "time": time_text,
            "mood": mood_text,
            "bubble": bubble_text,
            "tail": tail_final,
        }
        if drop:
            for key in drop:
                blocks.pop(key, None)
        order = list((rules_mod.shot_prompt().get("order") if rules_mod else None) or [
            "frame", "style_anchor", "style", "count", "cast", "crowd", "isolation",
            "speaker", "lens", "emphasis", "vertical", "action", "expression",
            "focus", "content", "scene", "time", "mood", "bubble", "tail",
        ])
        # 首句框架必须第一；旧规则 order 不含 frame/crowd 时补进去。
        if "frame" not in order:
            order.insert(0, "frame")
        else:
            order.remove("frame"); order.insert(0, "frame")
        if "crowd" not in order:
            pos = order.index("cast") + 1 if "cast" in order else len(order)
            order.insert(pos, "crowd")
        if "emphasis" not in order:
            pos = order.index("lens") + 1 if "lens" in order else len(order)
            order.insert(pos, "emphasis")
        if "isolation" not in order:
            pos = order.index("cast") + 1 if "cast" in order else len(order)
            order.insert(pos, "isolation")
        parts: list[str] = []
        for key in order:
            text_part = blocks.get(key, "")
            if not text_part:
                continue
            if key not in raw_keys:
                text_part = _sanitize_prompt(text_part)
            text_part = text_part.strip().strip("。；，、 ")
            if text_part and text_part not in parts:
                parts.append(text_part)
        if style_detail and "style_detail" not in drop:
            parts.insert(min(2, len(parts)), _sanitize_prompt(style_detail).strip("。；，、 "))
        return "。".join(p for p in parts if p) + "。"

    def finish(result: str) -> str:
        """兜底：预算被画风/身份锁吃光后，保证本格仍有自己的动作/画面。

        双人镜头的风格块 + 身份锁经常就占满 900 字，超预算裁剪会把
        "动作瞬间 / 画面内容 / 场景"整段丢掉，导致同场景的两格提示词完全相同，
        出图变成几乎一样的"两个人站着"。这里在截断后再补一句最短的本格画面，
        宁可略超预算，也不让不同镜头退化成同一张图。
        """
        result = result.strip()
        # 注意这里必须匹配带冒号的整段标题：参考图说明里也有“画面内容按本格描述”，
        # 只查“画面内容”会误判成已包含画面内容，结果补的是动作而不是画面。
        if view.description and "画面内容：" not in result:
            result += f"；本格画面：{_clean_clause(view.description, 48)}"
        elif view.action and "动作瞬间" not in result:
            result += f"；本格动作：{_clean_clause(view.action, 36)}"
        return result

    text = build()
    if len(text) <= limit:
        return finish(text)

    # 超预算：按 drop_order 整段丢弃 → 缩画面内容 → 按句硬收。
    # 注意：**绝不再切角色卡**。切在"眼睛"后面会把人物卡变成病句，
    # 模型会把它当成"另一个人"的一部分（本次撞脸修复的重点之一）。
    drop_order = ((rules_mod.shot_prompt().get("drop_order") if rules_mod else None)
                  or ["style_detail", "focus", "scene", "action", "expression",
                      "vertical", "mood", "time"])
    # 首句框架、身份锁、画风锚点不参与整段丢弃。
    drop_order = [k for k in drop_order
                  if k not in ("frame", "crowd", "cast", "style_anchor")]
    drop: set[str] = set()
    for key in drop_order:
        drop.add(key)
        text = build(drop)
        if len(text) <= limit:
            return finish(text)
    for content_limit in (90, 70, 55):
        text = build(drop, content_limit=content_limit)
        if len(text) <= limit:
            return finish(text)
    # 最后手段：按句截断（再不济也不会切在词中间）
    sentences = re.split(r"(?<=[。；;])", text)
    buf = ""
    for sent in sentences:
        if len(buf) + len(sent) > limit:
            break
        buf += sent
    return finish((buf or text[:limit]).strip())


# =============================================================================
# 导演优化（对已有分镜做一次"重拍"）
# =============================================================================

def director_pass_prompt(project: Project, shots_json: list[dict],
                         novel_excerpt: str = "") -> list[dict]:
    """
    把已有的"原文搬运式"分镜重写成可拍的镜头脚本。

    与初次拆解不同：这次保留原有的剧情顺序、台词与场景，只重做镜头语言：
    补机位/构图/光线/画幅、把心理描写改成动作、把画外音从画面人物里剔掉、
    把一段话拆成多个 beat。用于修复旧工程，也用于用户点"导演优化"。
    """
    schema = {
        "shots": [
            {
                "shot_type": f"景别：{'、'.join(config.SHOT_TYPES)}",
                "camera": f"机位：{'、'.join(config.SHOT_ANGLES)}",
                "composition": f"构图：{'、'.join(config.SHOT_COMPOSITIONS)}",
                "lighting": f"光线：{'、'.join(config.SHOT_LIGHTINGS)}",
                "panel_scale": "S / M / L / XL",
                "transition": f"转场：{'、'.join(config.TRANSITION_TYPES)}",
                "characters": ["画面里看得见的角色名；一格最多 4 人"],
                "char_sides": {"角色名": "左 / 右 / 中"},
                "char_positions": {"角色名": "画面位置，如'左侧中景，侧身对门'"},
                "gaze": "主要人物看向哪：画面左侧/右侧/画外/对方",
                "props": ["这一格必须出现的道具"],
                "scene": "场景",
                "time_of_day": "清晨 / 白天 / 黄昏 / 夜晚 / 凌晨 / 不确定",
                "focus": "视觉焦点，一句话",
                "action": "一个动作瞬间，一句话",
                "expression": "表情与眼神，一句话",
                "description": "24-80 字：①谁 ②一个动作瞬间 ③表情/视线 ④场景关键物 ⑤光从哪来；不写景别/机位",
                "mood": f"情绪：{'、'.join(config.MOOD_OPTIONS)}",
                "beat": "建立/对白/反应/动作/情绪/转场/揭示/蒙太奇/日常/幽默/细节",
                "pacing": "慢/中/快",
                "panel_emphasis": "normal / main / splash",
                "caption": "说明型画面的 ≤40 字说明（对白/高潮格留空）",
                "silent": "true/false",
                "hook": "true/false",
                "dialogues": [
                    {"speaker": "姓名/旁白/音效", "text": "≤20 字",
                     "tone": "语气", "kind": "speech/narration/sfx",
                     "slot": f"{'、'.join(config.BUBBLE_SLOTS)}",
                     "offscreen": "true/false"}
                ],
                "source_text": "对应原文短句（≤60 字，可空）",
            }
        ]
    }

    user = f"""下面是一份已经拆好的漫画分镜，但它是"原文搬运式"的：画面描述像小说、
缺少机位与构图、把打电话的对方也画进了同一间屋子、台词与旁白分工不清。
请你以快看漫画分镜主笔的身份，把每一格**重拍**成可以直接开画的镜头。

【作品信息】
画风：{style_brief(project, 70)}
梗概：{truncate(project.summary, 200) or "无"}
角色：{"、".join(project.character_names()) or "（无）"}
场景：{"、".join(project.scene_names()) or "（无）"}

【重拍规则（按优先级）】
1. **保留剧情与台词**：不要删掉情节节点，不要改写台词原意；只重做镜头与描述。
   **总格数控制在原格数的 ±20% 以内**，不要为了"细"把一格拆成四五格。如果是修复
   旧工程，原格数通常已经是目标值，优先原样保留格数。
2. **一格一个 beat**：原格里如果写了多个连续动作，拆成多格；如果两格是同一瞬间
   的同一机位，合并成一格。拆分/合并后保持剧情顺序。
3. **画面里只留看得见的人**：电话、画外音、回忆里的声音，说话人用
   offscreen=true，不要放进 characters。一格最多 4 人；3~4 人只用于群像/对峙/
   建立镜头，5 人以上拆格。
4. **description 只写看得见的东西**：①谁 ②画面位置 ③一个动作瞬间 ④视线表情
   ⑤前后景 ⑥光从哪来。禁止引号、台词、心理描写、原文摘抄；
   **禁止写景别/机位**（俯视/全景/特写/过肩…那是独立字段）。28~110 字。
5. **补齐字段**：每格都必须有 shot_type / camera / composition / lighting /
   panel_scale / transition / focus / action / expression / char_sides /
   char_positions / gaze / props / time_of_day / silent / hook。
6. **节奏**：每场第一格建立镜头；同场景不连续 3 格同景别或同机位；
   近景+特写 20%~30%；每 6~8 格至少 1 格无台词；连续台词格 ≤3；
   panel_scale 至少 3 种。
7. **台词量**：每格 ≤3 条、每条 ≤20 字；长了就拆到相邻格。
   旁白 ≤24 字，只交代时空/内心/因果，不复述画面。
   说话人必须在 characters 里，否则 offscreen=true。
8. **槽位**：说话人在左 → top-left / mid-left / bottom-left（按台词顺序往下）；
   在右 → top-right / mid-right / bottom-right；画外音 → top-center；
   旁白在顶部。槽位避开人物脸部。
9. 最后一格 hook=true，作为本章悬念。
10. 不要新增原文没有的情节。
11. **物件/屏幕格 characters 必须为空**，不要为了"有主角"塞一个人进去。
12. **为多格页服务**：每格标 beat（建立/对白/反应/动作/情绪/转场/揭示/蒙太奇/
    日常/幽默/细节）与 pacing（慢/中/快）；只在钩子、反转、情绪爆发格标
    panel_emphasis=main/splash，其余 normal；说明型画面可给 ≤40 字 caption，
    对白格与高潮格留空。同一段对白拆成"说话人 → 听者反应"的交替格，
    方便程序把 2~6 格编进同一页。

【原文摘录（仅供核对剧情，不要抄进 description）】
{truncate(novel_excerpt, 2400) or "（无）"}

【当前分镜 JSON】
{json.dumps(shots_json, ensure_ascii=False, indent=2)}

【输出结构：与输入相同的 {{"shots": [...]}}，但每格补齐上述字段】
只输出 JSON。"""
    return [{"role": "system", "content": SYSTEM_STORYBOARD},
            {"role": "user", "content": user}]


def panel_repair_prompt(project: Project, shot_json: dict,
                        issues: list[str]) -> list[dict]:
    """只修复单格的镜头字段（导演自检不过关时用）。"""
    user = f"""下面这一格漫画分镜存在问题，请只重写这一格。

【作品】{project.title}／画风：{style_brief(project, 60)}
【角色】{"、".join(project.character_names()) or "（无）"}
【场景】{"、".join(project.scene_names()) or "（无）"}
【问题】{"；".join(issues)}

【当前分镜】
{json.dumps(shot_json, ensure_ascii=False, indent=2)}

【要求】
- 保留原有剧情与台词，只修镜头：description 按①谁②画面位置③一个动作瞬间
  ④视线表情⑤前后景⑥光从哪来 来写，28~110 字，**不要写景别/机位**；
- characters 最多 4 人；只留画面里看得见的人，画外音用 offscreen=true；
- 补齐 camera / composition / lighting / panel_scale / transition / focus /
  action / expression / char_sides / char_positions / gaze / props / time_of_day；
- 台词单条 ≤20 字、单格 ≤3 条；旁白 ≤24 字；
- 输出单条分镜 JSON（结构不变），不要数组、不要解释。
只输出 JSON。"""
    return [{"role": "system", "content": SYSTEM_STORYBOARD},
            {"role": "user", "content": user}]


# =============================================================================
# 绘制风格推荐（handraw-style）
# =============================================================================

def style_recommend_prompt(project: Project, shortlist: Sequence[str],
                           colors_table: str = "") -> list[dict]:
    """
    让文本模型从候选风格里挑一套最贴故事的【风格 + 主题色】。

    365 种风格不可能全塞进提示词，所以先用本地关键词打分收敛到 20~30 个候选，
    再由模型结合题材/情绪/受众做最终审美判断（对应 handraw-style 的"实时动态研判"）。
    只输出 JSON：{"style_id": "FA-001", "color_ids": ["C-01"], "rationale": "..."}。
    """
    styles_table = stylelib.style_brief_table(list(shortlist))
    schema = {
        "style_id": "从候选清单里选一个编号，如 FA-012",
        "color_ids": ["1~2 个主题色编号，如 C-01；第二个作为点缀色"],
        "rationale": "一句话说明为什么这套风格+配色契合这个故事（结合题材、时代、情绪、受众）",
    }
    user = f"""你是这部漫画的美术总监。请为它选一套最合适的**绘制风格 + 主题色**。

【作品信息】
标题：{project.title or "未命名"}
梗概：{truncate(project.summary, 600) or "无"}
情节线：{truncate(project.plotline, 500) or "无"}
关键点：{"；".join(project.key_points[:8]) or "无"}
角色：{"、".join(f"{c.name}（{c.identity or c.role or '角色'}）" for c in project.characters[:8]) or "无"}
场景：{"、".join(project.scene_names()[:8]) or "无"}
题材与气质（文本分析结果）：{project.style_detail or project.style or "无"}

【候选画风（编号｜风格名｜参考作者/风格｜核心视觉特征）】
{styles_table or "（风格库不可用，style_id 留空）"}

【主题色（编号｜中文名（英文名）｜色系）】
{colors_table or "（不指定）"}

【选择规则】
1. 只能从上面的候选画风里选一个，输出它的编号；不要发明新编号。
2. 结合故事的**题材、时代、情绪基调、读者与画面介质**判断，不要机械按关键词对号入座。
   同一题材可以有很多种美学解法（如水墨、木刻、粗粒油画棒、包豪斯几何）。
3. 主题色选 1~2 个：一个主色统领画面，必要时再加一个点缀色；要能和该风格的
   笔触、媒介、明暗关系共存，而不是盖掉它。
4. rationale 要具体到"这套风格的哪种视觉语言（线条/笔触/媒介/光影/色彩）如何承载
   这个故事的情绪"，不要写套话。
5. 这是漫画分镜（人物 + 场景 + 对白气泡），风格要能稳定复现人物与连续场景。

【严格输出以下 JSON】
{json.dumps(schema, ensure_ascii=False, indent=2)}
只输出 JSON。"""
    return [{"role": "system", "content": "你是漫画美术总监，只输出结构化 JSON。"},
            {"role": "user", "content": user}]


# =============================================================================
# 分镜版式规划（一页多格）
# =============================================================================

def page_plan_prompt(project: Project, layouts_table: str,
                     shot_lines: Sequence[str]) -> list[dict]:
    """
    让文本模型把已有分镜编成"页"，并为每页挑一个 handraw 漫画分镜版式。

    输出必须是**按顺序、无遗漏、无重复**地覆盖全部分镜，且每页的格数
    必须等于所选版式的格数（程序会严格校验，不合法就退回本地规划）。
    """
    n = len(project.shots)
    target_pages = max(1, int(round(n / 3.6)))
    schema = {
        "pages": [
            {
                "layout_id": "版式编号，如 SB-001 / WB-001",
                "shots": ["按顺序的分镜序号，如 1,2,3,4；数量必须等于该版式格数"],
                "rationale": "一句话：为什么这几格合成一页、为什么用这个版式",
                "captions": ["仅当版式带字幕栏时填写：每格 ≤40 字说明；否则留空数组"],
            }
        ]
    }
    user = f"""你是这部漫画的版面导演。请把下面的分镜编成漫画页，并逐页选择版式。

【作品】{project.title or "未命名"}／画风：{style_brief(project, 60)}
【分镜总数】{n} 格；建议约 {target_pages} 页（每页 2~5 格为主，重点页可 1 格）

【分镜清单（序号｜场景｜景别/机位｜beat｜节奏｜视觉权重｜台词数｜画面摘要）】
{chr(10).join(shot_lines)}

【可用版式（编号｜名称｜格数｜强项）】
{layouts_table}

【编页规则（按优先级）】
1. **完整覆盖**：所有分镜按 1..{n} 的顺序各出现一次，不能漏、不能重复、不能乱序。
2. **格数匹配**：每页 shots 的数量必须等于该版式的格数。
3. **一页一个场景**：同一页尽量只放同一场景的连续格；确需跨场景时，用
   SB-034/SB-035/SB-044 这类"并行/交叉/时间轴"版式，一页最多跨 2 个场景。
4. **按内容选版式**，不要全篇一个模板：
   - 日常对白、信息平稳 → 规则网格 / 正反打 / Z 字阅读；
   - 场景开场、大空间 → 顶部大格 / 宽银幕 / 建立镜头版式；
   - 情绪落点、反转 → 底部大格 / 满版冲击 / 留白版式；
   - 动作、冲突 → 对角切割 / 碎片面板 / 动作分解；
   - 回忆、想象、电话、屏幕、时间流逝 → 闪回嵌套 / 手机聊天 / 时间轴；
   - 章末钩子或全页定格 → 1 格满版 / 跨页 / 海报式；
   - 原生条漫（WB-*）用于想保持"手机上一路下滑"手感的连续情绪/对白段。
5. **重点格落在大格**：标了 splash/main 的格优先放进该页最大的画格；
   不要把满版冲击格塞进 6 格小格里。
6. **画风适配版式**：按当前画风选气质，不要所有画风都用同一套：
   - 绘本 / 水墨（FB/FE）→ 少格、大留白、安静情绪版式；
   - 社论 / 日漫（FA/FD）→ 可碎片、对角、动作分解、冷幽默停顿；
   - 电影写实（FI）→ 宽银幕、建立镜头、悬疑压暗的大格；
   - 3D / 动画（FF/FG）→ 大格材质展示、动作冲击、喜剧节奏。
6. **不要连续 3 页用同一版式**；也不要为了多样而选不适合内容的花哨版式。
7. 只有 SB-069/070/071 这类"上图下文"版式需要 captions（每格一句说明，
   ≤40 字，写画面正在发生的事，不要和台词重复）；其它版式 captions 给空数组 []。

【严格输出以下 JSON】
{json.dumps(schema, ensure_ascii=False, indent=2)}
只输出 JSON。"""
    return [{"role": "system", "content": SYSTEM_STORYBOARD},
            {"role": "user", "content": user}]




def comic_style_lock(project: Any) -> str:
    """按项目选择的画风锁定，不预设某一种风格。

    项目选 2D 韩漫就写 2D 韩漫；选电影感/水彩/黑白线稿就按对应风格，
    不再把“2D 韩漫”硬编码成唯一答案。
    """
    style = str(getattr(project, "style", "") or "")
    mapped = config.STYLE_PROMPT_MAP.get(style, "")
    detail = str(getattr(project, "style_detail", "") or "")
    base = mapped or detail or style or "按项目设定画风"
    return (
        "【本项目画风锁定】" + base +
        "；人物、场景、道具、材质与光线必须服从这一套项目画风；"
        "同一角色跨格保持同一套线条、配色与五官比例。"
    )

def _refine_char_hint(project: Any) -> str:
    """给精修提示词的"已有角色"清单：只给姓名/身份/可画外貌，不给整张人设卡。

    精修的任务是提取事件与视觉锚点，不是重写人设；把 220 字身份锁塞进来
    会挤掉原文、让模型把注意力放在外貌而不是"这一格发生了什么"。
    """
    rows: list[str] = []
    for c in (getattr(project, "characters", []) or [])[:16]:
        bits = [str(getattr(c, "name", "") or ""),
                str(getattr(c, "gender", "") or ""),
                str(getattr(c, "age", "") or ""),
                str(getattr(c, "identity", "") or ""),
                str(getattr(c, "appearance", "") or "")[:40]]
        line = "；".join(x for x in bits if x)
        if line:
            rows.append(line)
    return "\n".join(rows) or "（尚未分析角色；只使用原文出现的姓名，不要新造人名）"


def novel_refine_schema() -> dict:
    """精修输出的 JSON 结构（单段 / 分段共用）。"""
    return {
        "core_task": "一句话核心戏剧任务：谁想要什么、最大阻碍、结尾不可逆变化；≤60 字",
        "digest": {
            "protagonist": "主角姓名，必须与原文一致",
            "goal": "主角在本章最想得到/保住的东西，≤24 字",
            "obstacle": "最大阻碍，≤24 字",
            "turn": "最关键的一次转折，≤28 字",
            "ending_state": "结尾人物/关系/处境发生了什么不可逆变化，≤30 字",
            "tone": "整体基调，2-4 个词，如'冷灰、克制、荒诞'",
            "motifs": ["反复出现、可画的视觉母题，2-4 个，如'长衫'、'茴香豆'、'柜台'"],
        },
        "anchors": [
            {
                "id": "A1",
                "event": "人物+动作+环境+情绪，必须能用一个静态画面传递关键信息",
                "function": "建立/揭示/升级/转折/留白",
                "quote": "原文依据，≤30 字，必须逐字来自原文",
                "characters": ["画面里看得见的人名"],
                "place": "地点",
                "time": "时间/光线",
                "shot_hint": "远景/全景/中景/近景/特写",
            }
        ],
        "cells": [
            {
                "cell": 1,
                "beat_id": "对应 anchors 的 id",
                "shot_type": "远景/全景/中景/近景/特写",
                "subject_action": "谁在做什么、朝向、手部/手中物品；只写一个动作瞬间",
                "environment": "地点、时间、光源方向、关键陈设",
                "light": "主光方向与色温，如'左侧窗光、冷白'",
                "emotion": "1-2 个能被看见的情绪词",
                "function": "建立/揭示/升级/转折/留白",
                "characters": ["画面里看得见的人名"],
                "props": ["这一格必须出现的道具"],
                "gaze": "看向画面左/右/上/下/深处",
                "transition": "切/叠/同构图跳切/留白",
                "quote": "对应原文短句，≤30 字，逐字引用",
            }
        ],
    }


def novel_refine_prompt(project: Any, chunk_text: str | None = None,
                        chunk_index: int = 1, chunk_total: int = 1,
                        prior_context: str = "") -> list[dict]:
    """第 2 步：小说精修 + 连续故事板（8-12 锚点 -> 25 格）。

    ``chunk_text`` 不为空时只处理这一段：输出本段候选锚点/候选格，
    由 :func:`novel_refine_merge_prompt` 统一压成 25 格。
    这样长篇小说不会因为"一口气读全文"而跳读后半段，也不会在分段处
    丢掉人物与场景的连续性。
    """
    text = project.novel_text if chunk_text is None else str(chunk_text)
    part_mode = int(chunk_total or 1) > 1
    part_note = ""
    if part_mode:
        part_note = (
            f"\n【分段边界】这是第 {chunk_index}/{chunk_total} 段，只提取本段发生的画面。"
            f"不要写后文，不要补结局，不要复述前情；人物的连续性以【前文摘要】为准。")
    prior_note = f"\n【前文摘要（只用于保持人名/地点/时间连续，不要重复提取）】\n{prior_context}" \
        if prior_context else ""
    anchor_hint = ("4-10 个本段候选锚点" if part_mode else "8-12 个视觉锚点")
    cell_hint = ("12-22 个本段候选镜头格" if part_mode else "恰好 25 个连续镜头格")

    system = (
        "你是漫画分镜编剧，负责把小说文字转成可单独成立、能被画出来的镜头单元。"
        "你不复述小说、不写影评、不解释主题；你只做视觉化提取，并给每个镜头留下"
        "可核对的原文依据。只输出 JSON。"
    )

    user = f"""【作品】{project.title or "未命名"}
【画风】{getattr(project, "style", "") or "按项目默认"}；{getattr(project, "style_detail", "") or ""}
【已有角色（姓名必须逐字使用，不要新造人名）】
{_refine_char_hint(project)}{prior_note}{part_note}

【任务】只输出 JSON，不要解释。你要做的是“情节减法 → 视觉锚点 → 连续镜头格”。

第一步：一句话核心戏剧任务
- core_task：主角想要什么、最大阻碍、结尾不可逆变化，三件事压进一句 ≤60 字的话。
- digest：把主角/目标/阻碍/转折/落点/基调/母题拆成结构化字段，方便后续分镜直接引用。

第二步：{anchor_hint}
每个锚点都必须满足：
- 能用一个静态画面把关键信息传出去（谁、在哪、正在做什么、和谁对峙）；
- 有明确的叙事功能（建立/揭示/升级/转折/留白），连续两个锚点功能重复且无信息增量就合并；
- 带 quote：从原文逐字抄一句 ≤30 字的依据，不得改写、不得用概括句；
- 跳过纯心理、重复动作、无冲突寒暄、纯过渡段落。

第三步：{cell_hint}
把锚点展开成连续镜头格。每格只做一件事：
- 一格 = 一个 beat = 一个动作瞬间；不要在一格里写"先…然后…又…"；
- shot_type 要有变化：同一场景内不得连续 3 格同景别；相邻两格不得同景别+同机位；
- 每场戏的第一格必须是远景/全景建立镜头，交代"这是哪、什么时候"；
- 近景+特写合计不超过 8 格，只留给关键台词、情绪转折、关键物件；
- 至少 3 格是无台词/无旁白的纯画面（环境、手部、道具、空镜）；
- 每格必须填 characters（画面里看得见的人）与 props（必须出现的道具）；
- 画面里不出现任何中文文字、气泡、标题、旁白框；这些由程序在后处理叠加。

【抽象语义必须转译成可见元素】
禁止把"悲凉/压抑/命运/麻木/看客的笑"直接留在 subject_action 或 emotion 里；
把它们拆成：人物姿态（低头/缩肩/背对）、空间（留白/拥挤/高低差）、
光影（冷灰/侧光/顶光/逆光）、道具（长衫/酒碗/门槛/铜钱）。
emotion 只保留 1-2 个"能被看见"的词。

【原文依据是硬约束】
- quote 必须逐字来自本段原文；找不到依据的镜头不要写。
- 不新增原文没有的事件、人物、亲属关系、结局。
- 人物称呼用【已有角色】里的 name；原文用"他/那人"时，根据上下文归到已有 name。

【25 格节奏（整章模式必须满足）】
- 1-3 格建立空间与人物处境；4-18 格推进冲突；19-24 格推向转折；第 25 格留一个可追更的落点。
- 中间至少安排 2 组"动作特写 → 反应近景 → 环境留白"的减速。
- 不允许 4 格以上连续对白；不允许连续 3 格同一场景同一机位。

【输出前自检】
1. cells 数量与要求一致（整章模式必须正好 25；分段模式在 12-22 之间）；
2. 每个 cell 都有 beat_id、quote、characters、props、gaze；
3. 没有把台词、心理活动、比喻写进 subject_action；
4. 没有出现"分镜/一格/漫画格/画面里不要"这类制作术语；
5. 没有抽象词残留在 subject_action / environment；emotion 只有 1-2 个词。

【严格输出以下 JSON 结构】
{json.dumps(novel_refine_schema(), ensure_ascii=False, indent=2)}

【本段原文开始】
{text}
【本段原文结束】"""
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def novel_refine_merge_prompt(partials: list[dict], project: Any,
                              chunk_total: int = 1) -> list[dict]:
    """把多段精修结果压成一份：8-12 锚点 + 恰好 25 格。"""
    system = (
        "你是漫画分镜主编。你要把同一章的分段精修结果合并成一份连续故事板，"
        "保留全部关键转折与原文依据，只删重复、只并同 beat。只输出 JSON。"
    )
    char_hint = _refine_char_hint(project)
    user = f"""下面是同一章按 {chunk_total} 段提取的精修结果（已按剧情顺序排列）。
请合并成一份最终结果：8-12 个视觉锚点 + 恰好 25 个连续镜头格。

【合并规则（按顺序执行）】
1. 合并 core_task / digest：取信息最全的一份；digest 的 goal/obstacle/turn/ending_state
   必须能串成一条完整因果链。
2. anchors 去重：event 或 quote 高度重复的只保留信息更全的一条；其余按剧情顺序保留。
   合并后锚点数控制在 8-12；超过 12 个时优先保留"转折/揭示"锚点。
3. cells 先全部保留，再压成 25 格：
   - 每个锚点至少保留 1 格；
   - 优先保留 function=转折/揭示 的格、以及承担关键动作的格；
   - 相邻且同一 beat、同一机位、无信息增量的格合并；
   - 不足 25 格时，把张力最高的锚点拆成"动作特写 → 反应近景 → 环境留白"补足；
   - 不允许为了凑数复制同一格。
4. 保持剧情时间顺序；cell 从 1 连续编号到 25。
5. quote 必须保留原文依据；合并后的格如果丢了 quote，从同锚点的原文句补回。
6. 人名只使用【已有角色】里的 name，不新增人物。

【25 格节奏检查（输出前逐条过）】
- 每场戏第一格是远景/全景；
- 同一场景内没有连续 3 格同景别；
- 近景+特写 ≤ 8 格；
- 至少 3 格纯画面；
- 第 25 格是钩子/落点；
- 每格都有 characters、props、gaze、quote。

【已有角色】
{char_hint}

【分段结果 JSON】
{json.dumps(partials, ensure_ascii=False, indent=2)}

【严格输出与下面完全相同的 JSON 结构（cells 必须正好 25 个）】
{json.dumps(novel_refine_schema(), ensure_ascii=False, indent=2)}

现在只输出合并后的 JSON。"""
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def character_lower_body(character: Any) -> str:
    """从人设里提取下装，避免侧身/走路格被模型换成裙子。"""
    design = str(getattr(character, "design", "") or "")
    m = re.search(r"下装[：:]?([^；。\n]+)", design)
    return (m.group(1).strip() if m else "")[:40]
