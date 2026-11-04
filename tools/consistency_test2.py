"""一致性测试第二轮：10 格，覆盖单人/双人/三人/四人、儿童/成人、侧/背/远。

运行：env\\Scripts\\python.exe tools\\consistency_test2.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comicgen.models import Project, Shot  # noqa: E402
from comicgen.pipeline import Pipeline  # noqa: E402
from comicgen.qwencomic import QwenComicClient  # noqa: E402
from tools.consistency_test import copy_single, group_shot  # noqa: E402

SRC = ROOT / "data" / "projects" / "caa29540ff.json"
OUT = ROOT / "outputs" / "consistency_test2"
TEST_ID = "ctestg02"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    src = Project.from_dict(json.loads(SRC.read_text(encoding="utf-8")))
    pr = Project.from_dict(src.to_dict())
    pr.id = TEST_ID
    pr.title = "一致性第二轮：单/双/三/四人与儿童"
    pr.pages = []
    pr.grid25_image = ""
    pr.grid25_shots = []
    pr.grid25_status = ""
    pr.settings = {}

    cases: list[Shot] = [
        copy_single(src, 8, 1, description=(
            "孔乙己的脸占满画面，嘴唇紧抿，额上青筋凸起，顶光压出阴影；"
            "背景是空无一人的咸亨酒店柜台与酒坛。"), camera="平视"),
        copy_single(src, 28, 2, description=(
            "掌柜低头结账，手指点在账本上，粉板放在手边，右侧光斜照；"
            "背景是空无一人的柜台。"), camera="平视"),
        copy_single(src, 13, 3, description=(
            "我独自站在柜台左侧，双手端着酒碗，转头看向画面右侧，"
            "身材瘦小，十二岁少年，背景是空荡的酒店。"), camera="平视"),
        copy_single(src, 19, 4, description=(
            "孔乙己独自在街道上双手撑地慢慢移出，逆光拉长灯影，街道空荡。"),
            camera="平视"),
        copy_single(src, 2, 5, description=(
            "长衫主顾抬脚迈过门槛，背对画面，背影没入里间。"), camera="背影"),
        copy_single(src, 13, 6, description=(
            "我侧身站在柜台左侧，双手端着酒碗，侧脸看向画面右侧，"
            "十二岁瘦小少年。"), camera="侧面", view_hint="side"),
        group_shot(7, ["掌柜", "短衣主顾"], "中景",
                   "掌柜在画面左侧，矮壮敦实、圆方脸，手拿账本；"
                   "短衣主顾在画面右侧，矮壮敦实、方中带圆脸，手举酒碗；"
                   "两人之间留出一臂宽的空隙。",
                   {"掌柜": "左", "短衣主顾": "右"}),
        group_shot(8, ["我", "掌柜"], "近景",
                   "我在画面左侧，只有十二岁、个子瘦小，头顶只到掌柜胸口，"
                   "双手端着酒碗抬头看向掌柜；掌柜在画面右侧，中年偏老、矮壮，"
                   "低头看我；两人之间留出一臂宽的空隙。",
                   {"我": "左", "掌柜": "右"}),
        group_shot(9, ["孔乙己", "我", "掌柜"], "全景",
                   "孔乙己在画面左侧，高大佝偻；我在中间，十二岁瘦小少年；"
                   "掌柜在右侧，中年偏老、矮壮；三人之间各留半步空隙。",
                   {"孔乙己": "左", "我": "中", "掌柜": "右"}),
        group_shot(10, ["孔乙己", "掌柜", "短衣主顾", "我"], "全景",
                   "孔乙己在最左，掌柜在左二，短衣主顾在右二，我在最右；"
                   "四人排成横列、各留半步空隙，背景是空荡的咸亨酒店。",
                   {"孔乙己": "左", "掌柜": "左二", "短衣主顾": "右二", "我": "右"}),
    ]
    pr.shots = cases

    pipe = Pipeline(image_client=QwenComicClient())
    rows: list[dict] = []
    for shot in cases:
        plan = pipe.plan_shot_refs(pr, shot)
        prompt = pipe.build_shot_prompt(pr, shot, include_bubbles=False)
        row = {
            "index": shot.index, "shot_type": shot.shot_type,
            "characters": list(shot.characters), "view": pipe._shot_view(shot),
            "plan": [{"character": p["character"], "role": p["role"],
                      "file": Path(p["path"]).name} for p in plan],
            "prompt_len": len(prompt),
        }
        started = time.time()
        try:
            pipe.generate_one_shot(pr, shot, include_bubbles=False)
            shot.status = "已完成"
            row.update({"ok": True, "seconds": round(time.time() - started, 1),
                        "raw": shot.raw_image_path,
                        "size": list(Image.open(shot.raw_image_path).size)})
            print("done", shot.index, row["seconds"], row["size"], flush=True)
        except Exception as exc:  # noqa: BLE001
            row.update({"ok": False, "error": repr(exc),
                        "seconds": round(time.time() - started, 1)})
            print("fail", shot.index, repr(exc), flush=True)
        rows.append(row)
        pr.save()

    (OUT / "summary.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("summary", OUT / "summary.json", flush=True)


if __name__ == "__main__":
    main()
