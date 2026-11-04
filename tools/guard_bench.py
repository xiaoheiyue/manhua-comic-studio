# -*- coding: utf-8 -*-
"""分镜提示词压测台：固定角色、固定种子、固定画幅，A/B 对照"撞脸/乱入"。

用法：
    env\\Scripts\\python.exe tools\\guard_bench.py --tag baseline
    env\\Scripts\\python.exe tools\\guard_bench.py --tag guard

输出：
    outputs/guard_bench/<tag>/case_XX.png
    outputs/guard_bench/<tag>/cases.json

注意：不写回工程文件，只读 data/projects 下的测试工程。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from comicgen import models, pipeline, prompts  # noqa: E402
from comicgen.imageclient import ImageClient  # noqa: E402

PROJECT = ROOT / "data" / "projects" / "29fe831811.json"
SIZE = (832, 1216)
SEED_BASE = 770100


def _shot(index: int, shot_type: str, names: list[str], desc: str,
          scene: str = "", side: dict[str, str] | None = None,
          pos: dict[str, str] | None = None, action: str = "",
          gaze: str = "", expr: str = "", camera: str = "平视",
          composition: str = "中心构图", lighting: str = "低调光",
          time_of_day: str = "夜晚", mood: str = "压抑") -> models.Shot:
    return models.Shot(
        index=index, shot_type=shot_type, characters=names,
        description=desc, scene=scene, char_sides=side or {},
        char_positions=pos or {}, action=action or desc,
        gaze=gaze, expression=expr or "神情克制", camera=camera,
        composition=composition, lighting=lighting,
        time_of_day=time_of_day, mood=mood, panel_scale="M",
    )


def _cases() -> list[dict]:
    """四类最容易翻车的镜头，覆盖单人/双人、近景/远景、同色系角色。"""
    return [
        {
            "id": "single_close",
            "note": "单人近景：只该有一张脸",
            "shot": _shot(
                1, "近景", ["林晚"],
                "林晚在雨夜的老宅门前，抬起脸看向门楣，雨水顺着发梢往下滴，"
                "右手攥着断骨黑伞的伞柄。",
                scene="城南老宅门前", side={"林晚": "中"},
                pos={"林晚": "画面中央近景"}, gaze="看向画面上方",
                expr="眼神清冷、嘴唇抿紧", camera="平视",
                composition="中心构图", lighting="侧光"),
        },
        {
            "id": "single_wide_street",
            "note": "单人远景 + 召唤词场景：最容易凭空多出人",
            "shot": _shot(
                2, "远景", ["林晚"],
                "深夜的街头，林晚撑着一把断骨黑伞，独自沿湿漉漉的青石路往前走，"
                "路灯把她的影子拉得很长，远处店铺已经打烊。",
                scene="夜晚的街头", side={"林晚": "中"},
                pos={"林晚": "画面中央偏下"}, gaze="看向画面深处",
                expr="疲惫但警觉", camera="平视",
                composition="对角线构图", lighting="低调光"),
        },
        {
            "id": "two_confront",
            "note": "双人对峙：两张脸最容易融合/互换服装",
            "shot": _shot(
                3, "全景", ["林晚", "沈砚"],
                "林晚站在门槛外，沈砚提着煤油灯从正屋阴影里迈出一步，两人隔着门槛对视，"
                "灯光在两人之间铺开一小块暖色。",
                scene="老宅正屋", side={"林晚": "左", "沈砚": "右"},
                pos={"林晚": "画面左侧前景，背对镜头", "沈砚": "画面右侧中景，面对林晚"},
                action="林晚脚步顿住，右手握紧伞柄；沈砚提灯停步，抬眼看向林晚",
                gaze="两人对视", expr="林晚紧绷，沈砚平静",
                camera="过肩", composition="对角线构图", lighting="低调光"),
        },
        {
            "id": "two_sisters",
            "note": "同色系姐妹双人：撞脸的最高危场景",
            "shot": _shot(
                4, "中景", ["林晚", "林雨"],
                "林晚和林雨并肩坐在老宅八仙桌旁，林雨把一张旧相片推到林晚面前，"
                "两人同时低头看着相片，煤油灯从桌面照亮两人的下半张脸。",
                scene="老宅正屋", side={"林晚": "左", "林雨": "右"},
                pos={"林晚": "画面左侧中景，低头", "林雨": "画面右侧中景，低头"},
                action="林雨右手推相片，林晚左手扶住相片边缘",
                gaze="两人都看向桌面相片", expr="林晚克制，林雨平静",
                camera="平视", composition="三分构图", lighting="顶光"),
        },
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="baseline")
    ap.add_argument("--only", default="", help="只跑某个 case id，逗号分隔")
    ap.add_argument("--no-image", action="store_true", help="只生成提示词，不出图")
    args = ap.parse_args()

    project = models.Project.load(PROJECT)
    pipe = pipeline.Pipeline(image_client=ImageClient(),
                             log_cb=lambda m: print("  " + m, flush=True))
    out_dir = ROOT / "outputs" / "guard_bench" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    only = {x.strip() for x in args.only.split(",") if x.strip()}

    records: list[dict] = []
    for i, case in enumerate(_cases()):
        if only and case["id"] not in only:
            continue
        shot: models.Shot = case["shot"]
        prompt = pipe.build_shot_prompt(project, shot, include_bubbles=False)
        negative = prompts.negative_prompt(project, shot, kind="shot")
        refs = pipe.build_shot_references(project, shot)
        record = {
            "id": case["id"], "note": case["note"], "shot": shot.index,
            "shot_type": shot.shot_type, "characters": shot.characters,
            "prompt_chars": len(prompt), "prompt": prompt,
            "negative": negative, "negative_chars": len(negative),
            "refs": [str(x) for x in refs], "size": list(SIZE),
            "seed": SEED_BASE + i,
        }
        print(f"\n[{case['id']}] prompt={len(prompt)}字 negative={len(negative)}字 "
              f"refs={len(refs)}", flush=True)
        if not args.no_image:
            t0 = time.time()
            res = pipe.image.generate(
                prompt, reference_paths=refs, size=SIZE,
                seed=record["seed"], negative_prompt=negative, use_cache=False)
            png = out_dir / f"case_{i:02d}_{case['id']}.png"
            res.image.save(png, format="PNG")
            record["image"] = str(png)
            record["seconds"] = round(time.time() - t0, 1)
            print(f"  -> {png} ({record['seconds']}s)", flush=True)
        records.append(record)

    (out_dir / "cases.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成：{len(records)} 例 -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
