"""一致性聚焦测试：角色预设资产 + 分镜单格 + 双人/三人群像。

不跑完整流程，只验证：
1. 参考图路由（face/main/side/back/top）是否按景别/人数生效；
2. 输出画幅是否等于分镜格；
3. 单人/双人/三人的人物一致性、人数、服装。

运行：
    env\\Scripts\\python.exe tools\\consistency_test.py
输出：
    data/projects/ctestg01.json
    data/shots/ctestg01/*.png
    outputs/consistency_test/summary.json
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

SRC = ROOT / "data" / "projects" / "caa29540ff.json"
OUT = ROOT / "outputs" / "consistency_test"
TEST_ID = "ctestg01"


def copy_single(src: Project, index: int, new_index: int, **overrides) -> Shot:
    s = Shot.from_dict(src.shots[index - 1].to_dict())
    s.id = f"ct{new_index:03d}"
    s.index = new_index
    s.status = "待生成"
    s.raw_image_path = ""
    s.image_path = ""
    s.pose_image = ""
    s.pose_skeleton = ""
    s.pose_draft = ""
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def group_shot(index: int, characters: list[str], shot_type: str,
               description: str, sides: dict[str, str]) -> Shot:
    def pos(name: str) -> str:
        side = sides.get(name, "")
        if side == "左":
            return "画面左侧"
        if side == "右":
            return "画面右侧"
        return "画面中间"

    return Shot(
        id=f"ct{index:03d}", index=index, shot_type=shot_type,
        characters=characters, scene="咸亨酒店", description=description,
        camera="平视", lighting="顶光", time_of_day="白天",
        char_sides=sides,
        char_positions={n: pos(n) for n in characters},
        action="", mood="对峙",
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    src = Project.from_dict(json.loads(SRC.read_text(encoding="utf-8")))
    pr = Project.from_dict(src.to_dict())
    pr.id = TEST_ID
    pr.title = "一致性聚焦测试·单格与群像"
    pr.pages = []
    pr.grid25_image = ""
    pr.grid25_shots = []
    pr.grid25_status = ""
    pr.settings = {}

    cases: list[Shot] = [
        copy_single(src, 8, 1, description=(
            "孔乙己的脸占满画面，嘴唇紧抿，额上青筋凸起，顶光压出阴影；"
            "背景是空无一人的咸亨酒店柜台与酒坛。"), camera="平视"),
        copy_single(src, 7, 2, description=(
            "孔乙己独自面向画面右侧，睁大眼睛，手指微抬，背景虚化，右侧光打在脸上。")),
        copy_single(src, 14, 3, description=(
            "孔乙己弯腰伸开五指罩住碟子，仰头望着碟子，顶光洒下。"), camera="斜侧"),
        copy_single(src, 2, 4, description=(
            "长衫主顾抬脚迈过门槛，背对画面，背影没入里间，柜台的粉板在左侧。"),
            camera="背影"),
        copy_single(src, 19, 5, description=(
            "孔乙己独自在街道上双手撑地慢慢移出，逆光拉长灯影，街道空荡，"
            "孔乙己是画面主体。"), camera="平视"),
        copy_single(src, 6, 6, description=(
            "孔乙己的手指将铜钱一枚枚排在柜台上，铜钱反光，木纹清晰，右侧光斜照。"),
            camera="俯视"),
        group_shot(7, ["孔乙己", "掌柜"], "中景",
                   "孔乙己站在柜台左侧，掌柜站在柜台右侧，两人之间隔着一臂宽的空隙；"
                   "孔乙己手里捏着几枚铜钱，掌柜低头看粉板；背景是空无一人的咸亨酒店。",
                   {"孔乙己": "左", "掌柜": "右"}),
        group_shot(8, ["孔乙己", "我"], "近景",
                   "孔乙己在画面左侧，微微弯腰把茴香豆放进碟子；我在画面右侧，"
                   "双手端着酒碗，转头看向孔乙己；两人之间留出一臂宽的空隙。",
                   {"孔乙己": "左", "我": "右"}),
        group_shot(9, ["孔乙己", "掌柜", "短衣主顾"], "全景",
                   "孔乙己站在画面左侧，掌柜站在画面中间柜台后，短衣主顾站在画面右侧；"
                   "三人之间各留出半步空隙，孔乙己面向掌柜，短衣主顾侧身看向孔乙己；"
                   "背景是空无一人的咸亨酒店。",
                   {"孔乙己": "左", "掌柜": "中", "短衣主顾": "右"}),
    ]
    pr.shots = cases

    pipe = Pipeline(image_client=QwenComicClient())
    rows: list[dict] = []
    for shot in cases:
        plan = pipe.plan_shot_refs(pr, shot)
        prompt = pipe.build_shot_prompt(pr, shot, include_bubbles=False)
        row = {
            "index": shot.index,
            "shot_type": shot.shot_type,
            "characters": list(shot.characters),
            "view": pipe._shot_view(shot),
            "plan": [{"character": p["character"], "role": p["role"],
                      "file": Path(p["path"]).name} for p in plan],
            "prompt_head": prompt[:300],
        }
        started = time.time()
        try:
            pipe.generate_one_shot(pr, shot, include_bubbles=False)
            shot.status = "已完成"
            row.update({"ok": True, "seconds": round(time.time() - started, 1),
                        "raw": shot.raw_image_path,
                        "size": list(Image.open(shot.raw_image_path).size),
                        "refs": [Path(p).name for p in shot.reference_paths]})
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
