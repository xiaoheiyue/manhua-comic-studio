"""参考图路由探针（旧路径 / 用于复现 P0）。

注意：本脚本调用生产代码 `QwenEditClient.generate()`。当前生产代码把
`TextEncodeQwenImage21.images` 写成嵌套对象，参考图不会进入模型；因此
A–D 的输出哈希会完全相同。这是**预期中的失败复现**，不是脚本 bug。
修复后的对照实验见 `tools/ref_probe_flat.py`。

运行：env\\Scripts\\python.exe tools\\ref_probe.py
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

from comicgen.qwenedit import QwenEditClient  # noqa: E402

CHAR = ROOT / "data" / "characters" / "孔乙己" / "默认造型"
MAIN = CHAR / "主视图.png"
TRIPLE = CHAR / "三视图.png"
TOP = CHAR / "俯视图.png"
OUT = ROOT / "outputs" / "ref_probe"

PROMPT = (
    "漫画分镜，中景：一位白发白须、清瘦微驼的老人，穿洗得发白的灰蓝旧长衫，"
    "外罩深褐色破旧夹克，站在旧式木柜台前，低头摊开手掌看掌心的几枚铜钱；"
    "背景是昏暗的酒馆室内；画面里只有这一位人物；"
    "水墨线稿、平涂上色，线条干净，画面完整。"
)
NEG = "多个人物, 第二个人, 重复人脸, 拼图, 分格, 画中画, 文字, 水印, 低质量, 模糊"
SEED = 20261103


def face_crop(src: Path, dst: Path) -> Path:
    """从主视图裁一个头肩参考：脸在参考图中的占比从 ~10% 提到 ~35%。"""
    import cv2
    import numpy as np

    rgb = np.array(Image.open(src).convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    cas = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = cas.detectMultiScale(gray, 1.08, 5, minSize=(32, 32))
    if len(faces) == 0:
        raise RuntimeError("主视图没有检测到人脸")
    x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
    side = int(max(w, h) * 2.4)
    cx, cy = x + w // 2, y + h // 2 + int(h * 0.15)
    box = (max(0, cx - side // 2), max(0, cy - side // 2),
           min(rgb.shape[1], cx + side // 2), min(rgb.shape[0], cy + side // 2))
    Image.fromarray(rgb).crop(box).save(dst, format="PNG")
    return dst


def run(client: QwenEditClient, name: str, refs: list[Path],
        prompt: str = PROMPT) -> dict:
    started = time.time()
    img = client.generate(prompt, [str(p) for p in refs], negative=NEG,
                          size=1024, seed=SEED, steps=8)
    out = OUT / f"{name}.png"
    img.save(out, format="PNG")
    return {"name": name, "refs": [p.name for p in refs],
            "image": str(out.relative_to(ROOT)), "seconds": round(time.time() - started, 1)}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    face = face_crop(MAIN, OUT / "参考_面部.png")
    client = QwenEditClient()

    jobs = [
        ("A_仅主视图", [MAIN], PROMPT),
        ("B_主视图_三视图", [MAIN, TRIPLE], PROMPT),
        ("C_主视图_俯视图", [MAIN, TOP], PROMPT),
        ("D_三张全送", [MAIN, TRIPLE, TOP], PROMPT),
    ]
    closeup = (
        "漫画分镜，面部特写：一位白发白须、清瘦的老人，脸型瘦长、颧骨微凸，"
        "细长下垂眼、花白眉毛，穿洗得发白的灰蓝旧长衫，深褐色夹克领口；"
        "背景虚化；画面里只有这一位人物；水墨线稿、平涂上色，五官清楚。"
    )
    jobs += [
        ("E_特写_仅主视图", [MAIN], closeup),
        ("F_特写_面部参考", [face], closeup),
    ]

    rows = []
    for name, refs, prompt in jobs:
        try:
            row = run(client, name, refs, prompt)
            print("done", name, row["seconds"], flush=True)
        except Exception as exc:  # noqa: BLE001
            row = {"name": name, "error": repr(exc)}
            print("fail", name, repr(exc), flush=True)
        rows.append(row)

    (OUT / "result.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
