# -*- coding: utf-8 -*-
"""三人/四人同框压测：验证 3+ 人不是被拆光，而是真的进入同一张图。

用法：
    env\\Scripts\\python.exe tools\\cast_multi_bench.py --count 3
    env\\Scripts\\python.exe tools\\cast_multi_bench.py --count 4
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from comicgen import models, pipeline, prompts  # noqa: E402
from comicgen.imageclient import ImageClient  # noqa: E402

OUT = ROOT / "outputs" / "guard_bench" / "multi"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=3)
    args = ap.parse_args()
    n = max(3, min(4, args.count))

    if n == 3:
        src = "29fe831811"
        names = ["林晚", "沈砚", "林雨"]
        desc = ("三人在老宅正屋的八仙桌旁对峙：林晚站在左侧，右手握着断骨伞；"
                "沈砚在中央提着煤油灯；林雨在右侧抱着旧相片。")
        sides = {"林晚": "左", "沈砚": "中", "林雨": "右"}
        pos = {"林晚": "左侧前景", "沈砚": "中央中景", "林雨": "右侧中景"}
    else:
        src = "0665ea5faa"
        names = ["老港", "陆鸣", "高星", "运维人员"]
        desc = ("四人在机房门口形成对峙：老港在左前，陆鸣在左中，高星在右中，"
                "运维人员在右后，四人之间隔着两步距离。")
        sides = {"老港": "左", "陆鸣": "中", "高星": "中", "运维人员": "右"}
        pos = {"老港": "左侧前景", "陆鸣": "中左", "高星": "中右", "运维人员": "右侧后景"}

    p = models.Project.load(ROOT / "data" / "projects" / f"{src}.json")
    missing = [x for x in names if not p.find_character(x)]
    if missing:
        raise SystemExit(f"缺少角色：{missing}")
    shot = models.Shot(index=1, shot_type="全景", characters=names,
                       char_sides=sides, char_positions=pos,
                       character_actions={x: "站定，看向画面中央" for x in names},
                       description=desc, scene=p.scenes[0].name if p.scenes else "",
                       camera="斜侧", composition="三分构图", lighting="侧光",
                       time_of_day="夜晚", mood="紧张")
    pipe = pipeline.Pipeline(image_client=ImageClient(),
                             log_cb=lambda m: print("  " + m, flush=True))
    prompt = pipe.build_shot_prompt(p, shot, include_bubbles=False)
    neg = prompts.negative_prompt(p, shot, kind="shot")
    refs = pipe.build_shot_references(p, shot)
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"{n} 人提示词 {len(prompt)} 字，参考图 {len(refs)} 张")
    print(prompt)
    res = pipe.image.generate(prompt, reference_paths=refs, size=(1024, 1024),
                              seed=20261027, negative_prompt=neg, use_cache=False)
    png = OUT / f"cast{n}.png"
    res.image.save(png)
    (OUT / f"cast{n}.json").write_text(
        json.dumps({"count": n, "characters": names, "prompt": prompt,
                    "negative": neg, "image": str(png)},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"-> {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
