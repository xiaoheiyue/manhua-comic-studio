# -*- coding: utf-8 -*-
"""提示词翻译层 / 精修收敛 / 姿态校验 的离线自检。

不联网、不调模型，直接跑规则：
    .\\env\\Scripts\\python.exe tools\\prompt_pipeline_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from comicgen import castguard, poseplan  # noqa: E402
from comicgen import prompts  # noqa: E402
from comicgen import shot_prompt_compiler as compiler  # noqa: E402
from comicgen.models import Character, Project, Scene, Shot  # noqa: E402
from comicgen.pipeline import Pipeline  # noqa: E402

ERRORS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[+] {name}")
    else:
        ERRORS.append(f"{name}：{detail}")
        print(f"[-] {name}：{detail}")


def build_project() -> Project:
    p = Project(title="孔乙己·离线自检", novel_text="孔乙己是站着喝酒而穿长衫的唯一的人。" * 40)
    p.style = config.DEFAULT_STYLE
    p.characters = [
        Character(name="孔乙己", gender="男", age="四十岁上下", identity="落魄书生",
                  appearance="花白短发、赭石色旧长衫",
                  hard_features={"face": "瘦长脸", "hair": "花白短发及耳",
                                 "eyes": "细长下垂眼", "outfit": "赭石色打补丁旧长衫",
                                 "build": "清瘦", "signature": "右袖口墨渍"}),
        Character(name="掌柜", gender="男", age="中年", identity="酒店掌柜",
                  hard_features={"face": "圆脸", "hair": "黑色短寸",
                                 "eyes": "细长眼", "outfit": "深灰棉袍",
                                 "build": "微胖", "signature": "腰间铜钥匙"}),
    ]
    p.scenes = [Scene(name="咸亨酒店", features="曲尺形大柜台", objects=["柜台", "粉板"],
                      light="门口暖黄灯")]
    return p


def main() -> int:
    p = build_project()

    # 1) 景别分级
    check("远景 -> wide", compiler.detail_level("远景") == "wide")
    check("中景 -> medium", compiler.detail_level("中景") == "medium")
    check("大特写 -> extreme", compiler.detail_level("大特写") == "extreme")
    check("近景 -> close", compiler.detail_level("近景") == "close")

    # 2) 首句框架：≤120 字，含版式/景别/机位/场景/时间/光
    shot = Shot(index=1, shot_type="中景", camera="平视", scene="咸亨酒店",
                time_of_day="傍晚", lighting="侧光", light_dir="左侧",
                panel_orientation="tall", panel_role="main",
                description="孔乙己独自站在柜台前，右手按着茴香豆。",
                action="右手按豆，身体微驼", expression="眼皮耷拉",
                focus="按着茴香豆的手指", mood="窘迫",
                characters=["孔乙己"], char_sides={"孔乙己": "左"})
    frame = compiler.frame_clause(shot, p)
    check("首句 ≤120 字", len(frame) <= 120, frame)
    for word in ("竖向", "主格", "中景", "平视机位", "咸亨酒店", "傍晚", "左侧"):
        check(f"首句包含 {word}", word in frame, frame)

    # 3) 抽象词翻译
    visual = compiler.visualize_abstract("看客的笑里透着悲凉")
    check("看客的笑 -> 可见元素", "看客的笑" not in visual and "画面边缘" in visual, visual)
    check("悲凉 -> 可见元素", "悲凉" not in visual and "留白" in visual, visual)

    # 4) 身份锁裁剪：双人时不能把第二个人整段切掉
    guard = castguard.prepare(p, shot, [
        {"name": "孔乙己", "side": "左"},
        {"name": "掌柜", "side": "右"},
    ], detail_level="wide")
    trimmed = compiler.trim_identity(guard.cast_text, "wide", 2)
    check("双人身份锁保留两人姓名", "孔乙己" in trimmed and "掌柜" in trimmed, trimmed)
    check("双人身份锁不超预算",
          len(trimmed) <= compiler.identity_budget("wide", 2),
          f"{len(trimmed)} > {compiler.identity_budget('wide', 2)}")

    # 5) 出图 prompt：服装色拉回身份卡、无否定词、无抽象词
    text = prompts.shot_image_prompt(p, shot, cast=[
        {"name": "孔乙己", "side": "左", "position": "画面左侧三分之一"},
    ])
    check("描述里的灰蓝长衫被拉回赭石", "灰蓝" not in text, text)
    check("正向无“不要”", "不要" not in text, text)
    check("正向无“悲凉”", "悲凉" not in text, text)
    check("prompt 在预算内", len(text) <= config.PROMPT_MAX_CHARS + 120,
          str(len(text)))

    # 6) 校验器能报风险
    issues = compiler.validate_prompt("一张中景，画面里不要出现第二个人，透着悲凉", shot,
                                      p, [{"name": "孔乙己"}])
    check("校验器报否定词", any("不要" in x for x in issues), str(issues))
    check("校验器报抽象词", any("悲凉" in x for x in issues), str(issues))

    # 7) 精修输出收敛：多余格裁到 25，缺字段补齐
    raw = {
        "core_task": "孔乙己想保住体面。",
        "digest": {"protagonist": "孔乙己", "goal": "体面", "obstacle": "哄笑",
                   "turn": "偷书", "ending_state": "消失", "tone": "冷灰",
                   "motifs": ["长衫"]},
        "anchors": [{"event": f"锚点{i}", "function": "建立", "quote": f"原文{i}"}
                    for i in range(1, 15)],
        "cells": [{"subject_action": f"动作{i}", "quote": f"原文{i}"}
                  for i in range(1, 31)],
    }
    pipes = Pipeline(text_client=None, image_client=None)
    anchors, cells, digest = pipes._normalize_refine(raw, p, [])
    check("锚点裁到 12", len(anchors) == 12, str(len(anchors)))
    check("镜头格裁到 25", len(cells) == 25, str(len(cells)))
    check("镜头格重新编号", [c["cell"] for c in cells] == list(range(1, 26)))
    check("digest 保留", digest["protagonist"] == "孔乙己")

    # 8) 姿态校验：内置正面骨架必须可用（否则第 7 步全退化成同一张）
    front = Path(config.BASE_DIR) / "data" / "pose_library" / "views" / "1全身正面.png"
    ok, why = poseplan.validate_pose(front)
    check("内置正面骨架通过校验", ok, why)

    print("=" * 60)
    if ERRORS:
        print(f"失败 {len(ERRORS)} 项：")
        for e in ERRORS:
            print("  -", e)
        return 1
    print("提示词/精修/姿态自检全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
