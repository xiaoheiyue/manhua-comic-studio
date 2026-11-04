"""角色预设生成测试：用副本角色跑一遍主视图/三视图/俯视图派生。

不改动原角色资产；新角色名为「孔乙己测试」。
运行：
    env\\Scripts\\python.exe tools\\preset_test.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from comicgen.models import Project  # noqa: E402
from comicgen.pipeline import Pipeline  # noqa: E402
from comicgen.qwencomic import QwenComicClient  # noqa: E402

SRC = ROOT / "data" / "projects" / "caa29540ff.json"


def main() -> None:
    src = Project.from_dict(json.loads(SRC.read_text(encoding="utf-8")))
    c = src.find_character("孔乙己")
    if c is None:
        raise SystemExit("找不到孔乙己")
    c = type(c).from_dict(c.to_dict())
    c.name = "孔乙己测试"
    c.main_visual = ""
    c.face_ref = ""
    c.views = {}
    c.view_quality = {}
    c.triple_view = ""
    c.pose_sheet = ""
    c.top_view = ""
    c.top_skeleton = ""
    c.top_status = "待生成"
    c.pose_status = "待生成"
    c.image_status = "待生成"
    c.seed = 456440361

    pr = Project(id="ctestpre1", title="角色预设生成测试", style=src.style,
                 style_id=src.style_id, style_detail=src.style_detail,
                 style_color_ids=list(src.style_color_ids or []),
                 style_tone=src.style_tone, summary=src.summary,
                 settings={})
    pr.characters = [c]
    pr.shots = []
    pr.pages = []
    pr.save()

    pipe = Pipeline(image_client=QwenComicClient())
    started = time.time()
    pipe.generate_character_images(pr, c, "默认造型")
    pr.save()
    print("done", round(time.time() - started, 1), flush=True)
    print(json.dumps({
        "main": c.main_visual,
        "face": c.face_ref,
        "triple": c.triple_view,
        "top": c.top_view,
        "views": c.views,
        "quality": c.view_quality,
        "status": c.image_status,
    }, ensure_ascii=False, indent=1), flush=True)


if __name__ == "__main__":
    main()
