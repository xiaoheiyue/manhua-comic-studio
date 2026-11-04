# -*- coding: utf-8 -*-
"""真实链路抽检：走 Pipeline.generate_one_shot，验证姿态/参考图/提示词一起工作。

用法：
    env\\Scripts\\python.exe tools\\pipeline_guard_check.py

只读取测试工程，克隆成 guardpipe_* 工程后出图，不改原工程。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from comicgen import models, pipeline, prompts  # noqa: E402
from comicgen.imageclient import ImageClient  # noqa: E402

OUT = ROOT / "outputs" / "guard_bench" / "pipeline"
OUT.mkdir(parents=True, exist_ok=True)


def _clone(src: str, new_id: str, shot_indexes: list[int]) -> models.Project:
    p = models.Project.load(ROOT / "data" / "projects" / f"{src}.json")
    p.id = new_id
    p.title = f"guardpipe-{src}"
    p.shots = [s for s in p.shots if s.index in shot_indexes]
    for s in p.shots:
        s.raw_image_path = ""
        s.image_path = ""
        s.pose_image = ""       # 让 poseplan 自己选，验证坏骨架会被跳过
        s.status = "待生成"
    p.reindex_shots()
    p.save()
    return p


def main() -> int:
    pipe = pipeline.Pipeline(image_client=ImageClient(),
                             log_cb=lambda m: print("  " + m, flush=True))
    cases = [
        ("29fe831811", "guardpipe_29fe", [1, 5]),
        ("b43a3b821a", "guardpipe_b43a", [8]),
    ]
    records = []
    for src, new_id, indexes in cases:
        p = _clone(src, new_id, indexes)
        for s in p.shots:
            prompt = pipe.build_shot_prompt(p, s, include_bubbles=False)
            shot = pipe.generate_one_shot(p, s, include_bubbles=False)
            png = OUT / f"{new_id}_shot{s.index}.png"
            if shot.raw_image_path and Path(shot.raw_image_path).exists():
                Path(shot.raw_image_path).replace(png)
            records.append({
                "project": new_id, "shot": s.index,
                "characters": s.characters, "pose": s.pose_image,
                "prompt_chars": len(prompt), "prompt": prompt,
                "image": str(png), "exists": png.exists(),
            })
            print(f"  -> {png} (prompt {len(prompt)} 字)", flush=True)
        p.save()
    (OUT / "records.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"完成：{len(records)} 格 -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
