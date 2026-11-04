# -*- coding: utf-8 -*-
"""导出各阶段提示词到 ``prompts/`` 目录（文档化 + 可回溯）。

这个脚本用一组固定的样例数据，把每个 prompt builder 真正渲染一遍，
把 **系统提示 + 用户提示 + 输入变量 + 输出结构** 写进：

    prompts/<阶段>/<序号>_<名称>.md

这样：
  · 文档里的提示词就是代码实际会发出去的文本，不会"文档写一套、代码跑另一套"；
  · 改完提示词重跑本脚本，diff 就能看出改了哪一句；
  · 运行期的真实提示词还会另存到 data/projects/<id>/prompts/（见 prompt_trace.py）。

用法：
    env\\Scripts\\python.exe export_prompts.py
"""

from __future__ import annotations

import json
from pathlib import Path

import config
from comicgen import bubble, prompts
from comicgen import pagelayout
from comicgen import stylelib
from comicgen.models import Character, Dialogue, Project, Scene, Shot

ROOT = Path(config.BASE_DIR) / "prompts"

SAMPLE_NOVEL = (config.SAMPLE_NOVEL or
                "雨夜，林晚回到城南老宅。门虚掩着，屋里没有开灯。\n"
                "「你终于来了。」沈砚站在楼梯口，手里握着一把断骨黑伞。\n"
                "林晚没有回答。她看见桌上放着一只旧怀表，指针停在十一点四十七分。")

STAGES = {
    "01_analysis": "小说内容分析（角色 / 场景 / 情节 / 画风）",
    "02_characters": "角色人设与跨角色差异化",
    "03_storyboard": "分镜拆解与整理",
    "04_director": "导演层重拍与单格修镜头",
    "05_narration": "旁白补全",
    "06_image": "分镜出图提示词（含气泡坐标）",
    "07_bubble": "气泡绘制、检测、回填与返修",
    "08_repair": "出图自检与返修",
    "09_style": "绘制风格推荐（handraw-style 365 种画风 + 36 主题色 + 16 阶调）",
    "10_layout": "分页与版式规划（SB-001–SB-071 + 原生 WB-*）",
}


def sample_project() -> Project:
    project = Project(title="雨夜的老宅", novel_text=SAMPLE_NOVEL,
                      summary="林晚雨夜回到老宅，遇见沈砚，发现一只停在十一点四十七分的怀表。",
                      plotline="雨夜回宅 → 遇见沈砚 → 发现怀表", style="悬疑暗调韩漫",
                      style_detail="冷色调、低照度、强调雨夜质感")
    project.characters = [
        Character(name="林晚", gender="女", age="二十四岁", identity="旧宅继承人",
                  personality="冷静克制", role="主角", is_extra=False,
                  design="【基本形象】女性，二十四岁，清瘦，鹅蛋脸，细长杏眼。\n"
                         "【发型与发色】及腰黑长直发，偏分。\n"
                         "【服装风格】米色旧风衣，深色高领毛衣。\n"
                         "【标志性特征】左耳一枚细银耳线。\n"
                         "【色彩方案】米色/黑/冷灰。",
                  keywords=["及腰黑长直发", "鹅蛋脸", "细长杏眼", "米色旧风衣", "左耳细银耳线"],
                  identity_tag="左耳细银耳线"),
        Character(name="沈砚", gender="男", age="三十岁上下", identity="旧宅访客",
                  personality="寡言", role="关键人物", is_extra=False,
                  design="【基本形象】男性，三十岁上下，清瘦，长方脸，深眼窝。\n"
                         "【发型与发色】黑色三七分短发。\n"
                         "【服装风格】深灰长风衣，白衬衫。\n"
                         "【标志性特征】右手无名指素圈银戒。\n"
                         "【色彩方案】深灰/白/冷蓝。",
                  keywords=["黑色三七分短发", "长方脸", "深眼窝", "深灰长风衣", "素圈银戒"],
                  identity_tag="右手无名指素圈银戒"),
    ]
    project.scenes = [Scene(name="城南老宅", features="木质楼梯、旧家具、雨夜",
                            atmosphere="阴冷压抑", frequency="高频")]
    shot = Shot(index=1, shot_type="中景", camera="平视", composition="三分构图",
                lighting="侧光", panel_scale="M", characters=["林晚"],
                char_sides={"林晚": "左"}, scene="城南老宅", time_of_day="夜晚",
                action="林晚推门进来，停在门口", expression="眼睛盯着楼梯口",
                focus="她握在门把上的手",
                description="林晚在画面左侧门口停下，右手还搭在门把上，眼睛盯着楼梯口；"
                            "前景是半开的木门，后景是暗下来的楼梯；门外的雨光从她身后打进来。",
                mood="悬疑", dialogues=[Dialogue(speaker="沈砚", text="你终于来了。",
                                              kind="speech", offscreen=True, slot="top-center")])
    shot2 = Shot(index=2, shot_type="近景", camera="斜侧", composition="前景遮挡",
                 lighting="低调光", panel_scale="S", characters=["林晚"],
                 char_sides={"林晚": "左"}, scene="城南老宅", time_of_day="夜晚",
                 action="林晚低头看向桌面", expression="瞳孔缩了一下",
                 focus="桌上的旧怀表",
                 description="林晚在画面左侧低头，视线落在桌面；前景是桌沿，"
                             "后景是窗外的雨；台灯从右前方照亮怀表。",
                 mood="紧张", dialogues=[Dialogue(speaker="林晚", text="十一点四十七分……",
                                               kind="speech", slot="top-left")])
    project.shots = [shot, shot2]
    # 版式样例：按内容自动分页（与运行期同一套本地动态规划）
    if stylelib.available():
        rec = stylelib.recommend(f"{project.summary} {project.plotline} {project.style_detail}")
        project.style_id = rec.get("style_id") or ""
        project.style_color_ids = list(rec.get("color_ids") or [])
        project.style_rationale = rec.get("rationale") or ""
    try:
        pagelayout.annotate_shots(project)
        pages = pagelayout.plan_local(project, mode="hybrid")
        pagelayout.apply_plans(project, pages, "样例：本地动态规划")
    except Exception:  # noqa: BLE001
        pass
    return project


def fmt_messages(messages: list[dict]) -> str:
    out = []
    for msg in messages:
        out.append(f"### {msg.get('role', '')}\n")
        out.append("```text")
        out.append(str(msg.get("content", "")))
        out.append("```\n")
    return "\n".join(out)


def write_doc(stage: str, name: str, title: str, purpose: str, source: str,
              messages: list[dict], output: str, notes: str = "") -> None:
    folder = ROOT / stage
    folder.mkdir(parents=True, exist_ok=True)
    body = [
        f"# {title}", "",
        f"- **阶段**：{stage}",
        f"- **代码位置**：`{source}`",
        f"- **用途**：{purpose}", "",
        "## 输入变量", "",
        notes or "（见下方提示词正文中的占位说明）", "",
        "## 提示词正文", "",
        fmt_messages(messages),
        "## 期望输出", "",
        output, "",
        "> 本文由 `export_prompts.py` 用固定样例渲染生成；改代码后重跑脚本即可更新。",
        "",
    ]
    (folder / f"{name}.md").write_text("\n".join(body), encoding="utf-8")


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    project = sample_project()
    c = project.characters[0]
    c2 = project.characters[1]
    shot = project.shots[0]
    shot.bubble_plan = bubble.plan_layout(shot, *shot.panel_size())

    # 01 分析
    write_doc("01_analysis", "01_novel_overview", "小说整体分析",
              "一次读一段小说，识别角色/场景/情节/画风。",
              "prompts.novel_overview_prompt", prompts.novel_overview_prompt(SAMPLE_NOVEL[:1200], 1, 1),
              "JSON：summary / plotline / key_points / style / style_detail / characters[] / scenes[]",
              "变量：`novel`（本段正文）、`chunk_index`、`chunk_total`。")
    write_doc("01_analysis", "02_merge_analysis", "分段分析合并",
              "把多段分析合并去重成一份完整设定。",
              "prompts.merge_analysis_prompt",
              prompts.merge_analysis_prompt([{"summary": "样例", "characters": [], "scenes": []}],
                                            SAMPLE_NOVEL[:400]),
              "与 analysis 相同的 JSON 结构", "变量：`partials`（分段结果）、`novel_head`。")

    # 02 人设
    write_doc("02_characters", "01_character_design", "角色人设生成",
              "为单个角色生成人设、硬特征、唯一记忆点与关键词。",
              "prompts.character_design_prompt", prompts.character_design_prompt(c, project),
              "JSON：design / hard_features / identity_tag / keywords / main_visual_prompt / triple_view_prompt",
              "变量：`character`（分析结果）、`project`（含已有角色清单，用于差异化）。")
    write_doc("02_characters", "02_distinct_rewrite", "角色撞车重写",
              "两个角色过于相似时，定向重写后出现的那个。",
              "prompts.character_distinct_rewrite_prompt",
              prompts.character_distinct_rewrite_prompt(
                  c2, project, [{"other": c.name, "shared_labels": ["脸型", "发型"]}]),
              "JSON：design / hard_features / identity_tag / keywords / reason",
              "变量：`character`、`project`、`collisions`（撞车明细）。")

    # 03 分镜
    write_doc("03_storyboard", "01_decompose", "分镜拆解",
              "把一段小说拆成可拍的镜头脚本（一格一个 beat）。",
              "prompts.storyboard_prompt",
              prompts.storyboard_prompt(project, SAMPLE_NOVEL, 1, "第 1 批", target_shots=30),
              "JSON：shots[]（景别/机位/构图/光线/画幅/人数/站位/视线/道具/描述/台词）",
              "变量：`project`、`chunk_text`、`start_index`、`chunk_label`、`target_shots`。")
    write_doc("03_storyboard", "02_merge", "分镜合并去重",
              "合并分段拆解的结果，补全字段、去重、保持剧情顺序。",
              "prompts.storyboard_merge_prompt",
              prompts.storyboard_merge_prompt([shot.to_dict()], project),
              "JSON：shots[]", "变量：`shots_json`、`project`。")
    write_doc("03_storyboard", "03_dialogue_split", "对话拆格",
              "一格台词过多时拆成多格（说话人/听者/动作交替）。",
              "prompts.storyboard_dialogue_split_prompt",
              prompts.storyboard_dialogue_split_prompt(project, [shot.to_dict()], 2),
              "JSON：shots[]", "变量：`shots_json`、`max_per_shot`。")
    write_doc("03_storyboard", "04_shot_fix", "单条分镜补全",
              "补齐一条分镜缺失的镜头字段（本地兜底后修复）。",
              "prompts.shot_fix_prompt", prompts.shot_fix_prompt(shot, project),
              "单条 shot JSON", "变量：`shot`、`project`。")

    # 04 导演
    write_doc("04_director", "01_pass", "导演重拍",
              "把已有分镜重写成可拍镜头：补机位/构图/光线/画幅，剔掉画外音误入画面。",
              "prompts.director_pass_prompt",
              prompts.director_pass_prompt(project, [shot.to_dict()], SAMPLE_NOVEL[:600]),
              "JSON：shots[]（字段同分镜拆解）", "变量：`shots_json`、`novel_excerpt`。")
    write_doc("04_director", "02_panel_repair", "单格修镜头",
              "自检不过关时只重写这一格。",
              "prompts.panel_repair_prompt",
              prompts.panel_repair_prompt(project, shot.to_dict(), ["描述过短", "说话人不在画面"]),
              "单条 shot JSON", "变量：`shot_json`、`issues`。")

    # 05 旁白
    write_doc("05_narration", "01_fill", "旁白补全",
              "为需要旁白的格子补写旁白（时空/内心/因果）。",
              "prompts.narration_fill_prompt",
              prompts.narration_fill_prompt(project, [shot.to_dict()], 24),
              'JSON：{"narration":[{"index":1,"text":"…"}]}', "变量：`shots_json`、`max_chars`。")

    # 06 出图
    cast = [{"name": c.name, "card": c.identity_card(),
             "side": shot.char_sides.get(c.name, ""),
             "position": shot.char_positions.get(c.name, "")}]
    write_doc("06_image", "01_shot_prompt", "分镜出图提示词",
              "组装一张分镜图的最终提示词：人数 → 身份卡 → 说话人 → 镜头 → 画面内容 → 气泡坐标 → 收尾。",
              "prompts.shot_image_prompt",
              [{"role": "system", "content": "图像生成（ComfyUI · Z-Image-Turbo int8）"},
               {"role": "user", "content": prompts.shot_image_prompt(project, shot, cast=cast)}],
              "纯文本提示词（送入 ComfyUI 的 CLIPTextEncode）",
              "变量：`project`、`shot`、`cast`、`ref_names`；气泡坐标来自 `shot.bubble_plan`。")

    # 07 气泡
    write_doc("07_bubble", "01_bubble_prompt", "气泡绘制指令（出图时）",
              "把每个气泡的槽位、外接框、形状、尾巴、内部留白写清楚，让模型按坐标画。",
              "prompts._bubble_block",
              [{"role": "system", "content": "气泡绘制指令（拼在图像提示词末尾）"},
               {"role": "user", "content": prompts._bubble_block(project, shot, [c.name])}],
              "自然语言：数量 + 每个气泡的坐标/形状/尾巴 + 总数约束",
              "变量：`shot.bubble_plan`（x/y/w/h/slot/speaker/tail）。")

    # 09 绘制风格
    story_text = f"{project.summary} {project.plotline} {project.style_detail}"
    shortlist = stylelib.shortlist_style_ids(story_text, 12)
    write_doc("09_style", "01_recommend", "绘制风格推荐",
              "从 365 种 handraw 画风里收敛候选，再由模型结合题材/情绪/时代选一套风格 + 主题色。",
              "prompts.style_recommend_prompt",
              prompts.style_recommend_prompt(project, shortlist, stylelib.color_brief_table()),
              'JSON：{"style_id": "FA-001", "color_ids": ["C-01"], "rationale": "…"}',
              "变量：`project`、`shortlist`（本地关键词收敛后的候选编号）、`colors_table`。")

    # 10 分页与版式
    write_doc("10_layout", "01_page_plan", "分页与版式规划",
              "把连续分镜编成漫画页，并逐页选择 SB-*/WB-* 版式（格数必须匹配）。",
              "prompts.page_plan_prompt",
              prompts.page_plan_prompt(project, stylelib.layout_brief_table(16),
                                       pagelayout.shot_brief_lines(project)),
              'JSON：{"pages": [{"layout_id": "SB-001", "shots": [1,2,3,4], "rationale": "…"}]}',
              "变量：`project`、`layouts_table`（版式清单）、`shot_lines`（分镜摘要）。")

    # 各阶段 README
    for stage, desc in STAGES.items():
        folder = ROOT / stage
        folder.mkdir(parents=True, exist_ok=True)
        files = sorted(p.name for p in folder.glob("*.md") if p.name != "README.md")
        lines = [f"# {stage} · {desc}", "",
                 "本目录的提示词由 `export_prompts.py` 从代码实际渲染生成。", "",
                 "| 文件 | 说明 |", "| --- | --- |"]
        for name in files:
            lines.append(f"| [{name}]({name}) | 见文件内标题 |")
        (folder / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 总 README
    index = ["# prompts/ · 全流程提示词库", "",
             "> 目标：**每一阶段的提示词都能单独查看、对比、回溯**。", "",
             "## 目录", "",
             "| 阶段 | 内容 |", "| --- | --- |"]
    for stage, desc in STAGES.items():
        index.append(f"| [{stage}]({stage}/README.md) | {desc} |")
    index += ["",
              "## 两种存储", "",
              "1. **模板/样例（本目录）**：`export_prompts.py` 用固定样例把每个 builder 渲染一遍，",
              "   保证文档 = 代码实际发出的文本；",
              "2. **运行期真实提示词**：`comicgen/prompt_trace.py` 在每次调用时把",
              "   system/user、气泡计划、检测结果写到 `data/projects/<工程id>/prompts/<阶段>/`，",
              "   工程目录下还有 `index.md` 汇总。", "",
              "## 重新生成", "",
              "```powershell",
              "env\\Scripts\\python.exe export_prompts.py",
              "```", "",
              "## 回溯一张图", "",
              "1. 在 `data/projects/<id>/prompts/06_image/` 找到该格的提示词；",
              "2. 在 `07_bubble/` 看当时的气泡计划（plan）与检测结果（debug）；",
              "3. 在 `02_characters/` 看人设/身份卡版本；",
              "4. 对比 `prompts/` 里当前模板，就能判断问题出在规则、数据还是模型。", ""]
    (ROOT / "README.md").write_text("\n".join(index), encoding="utf-8")
    print(f"已导出提示词文档：{ROOT}")


if __name__ == "__main__":
    main()
