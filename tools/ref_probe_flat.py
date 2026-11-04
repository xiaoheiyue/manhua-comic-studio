"""参考图路由探针（修正 Autogrow 键格式版）。

背景：`TextEncodeQwenImage21.images` 是 COMFY_AUTOGROW_V3 输入，ComfyUI 的
API 需要扁平键 `images.image_1`；旧代码写成嵌套 `{"images": {"image_1": ...}}`，
参考图实际没有进入模型（同提示词/种子下换参考图，输出哈希完全相同）。

本脚本用扁平键重建同一张图，比较：
A 主视图 / B 主视图+三视图 / C 主视图+俯视图 / D 三张全送
E 特写·仅主视图 / F 特写·面部参考

运行：env\\Scripts\\python.exe tools\\ref_probe_flat.py
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import time
import uuid
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
CLOSEUP = (
    "漫画分镜，面部特写：一位白发白须、清瘦的老人，脸型瘦长、颧骨微凸，"
    "细长下垂眼、花白眉毛，穿洗得发白的灰蓝旧长衫，深褐色夹克领口；"
    "背景虚化；画面里只有这一位人物；水墨线稿、平涂上色，五官清楚。"
)
NEG = "多个人物, 第二个人, 重复人脸, 拼图, 分格, 画中画, 文字, 水印, 低质量, 模糊"
SEED = 20261103


def generate_flat(client: QwenEditClient, prompt: str, refs: list[Path], *,
                  size: int = 1024, seed: int = SEED) -> Image.Image:
    """与 QwenEditClient.generate 相同的图，但 images 用扁平 Autogrow 键。"""
    names = [client._upload(str(p)) for p in refs if p and p.exists()]
    load_nodes = {str(20 + i): {"class_type": "LoadImage", "inputs": {"image": n}}
                  for i, n in enumerate(names)}
    if not load_nodes:
        load_nodes["20"] = {"class_type": "LoadImage", "inputs": {"image": ""}}
    enc_inputs = {
        "clip": ["2", 0], "prompt": prompt,
        "negative_prompt": NEG,
        "resolution": int(size), "vae": ["3", 0],
    }
    for i in range(len(names)):
        enc_inputs[f"images.image_{i+1}"] = [str(20 + i), 0]
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": "qwen_image_2.1_turbo_int8_convrot.safetensors",
            "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen3vl_8b_w4a8.safetensors",
            "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {
            "vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
        **load_nodes,
        "5": {"class_type": "TextEncodeQwenImage21", "inputs": enc_inputs},
        "6": {"class_type": "KSampler", "inputs": {
            "seed": int(seed), "steps": 8, "cfg": 1.0,
            "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
            "model": ["1", 0], "positive": ["5", 0], "negative": ["5", 1],
            "latent_image": ["5", 2]}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}},
        "8": {"class_type": "SaveImage", "inputs": {
            "filename_prefix": "qwen_ref_probe", "images": ["7", 0]}},
    }
    r = client.session.post(f"{client.host}/prompt",
                            json={"prompt": graph, "client_id": uuid.uuid4().hex},
                            timeout=120)
    r.raise_for_status()
    pid = r.json()["prompt_id"]
    started = time.time()
    entry = None
    while time.time() - started < 300:
        h = client.session.get(f"{client.host}/history/{pid}", timeout=30).json()
        if h:
            entry = h.get(pid) or next(iter(h.values()), None)
            break
        time.sleep(1.5)
    if not entry:
        raise TimeoutError("Qwen Edit 超时")
    for node in (entry.get("outputs") or {}).values():
        for img in (node.get("images") or []):
            q = client.session.get(f"{client.host}/view", params={
                "filename": img["filename"], "subfolder": img.get("subfolder", ""),
                "type": img.get("type", "output")}, timeout=120)
            q.raise_for_status()
            return Image.open(io.BytesIO(q.content)).convert("RGB")
    raise RuntimeError("Qwen Edit 没有输出图片")


def face_crop(src: Path, dst: Path) -> Path:
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


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    face = face_crop(MAIN, OUT / "参考_面部.png")
    client = QwenEditClient()
    try:
        client.session.post(f"{client.host}/free",
                            json={"unload_models": True, "free_memory": True}, timeout=30)
    except Exception:
        pass

    jobs = [
        ("fixed_A_仅主视图", [MAIN], PROMPT),
        ("fixed_B_主视图_三视图", [MAIN, TRIPLE], PROMPT),
        ("fixed_C_主视图_俯视图", [MAIN, TOP], PROMPT),
        ("fixed_D_三张全送", [MAIN, TRIPLE, TOP], PROMPT),
        ("fixed_E_特写_仅主视图", [MAIN], CLOSEUP),
        ("fixed_F_特写_面部参考", [face], CLOSEUP),
    ]
    rows = []
    for name, refs, prompt in jobs:
        started = time.time()
        try:
            img = generate_flat(client, prompt, refs)
            out = OUT / f"{name}.png"
            img.save(out, format="PNG")
            rows.append({"name": name, "refs": [p.name for p in refs],
                         "image": str(out.relative_to(ROOT)),
                         "size": list(img.size),
                         "seconds": round(time.time() - started, 1),
                         "sha256": hashlib.sha256(out.read_bytes()).hexdigest().upper()})
            print("done", name, img.size, round(time.time() - started, 1), flush=True)
        except Exception as exc:  # noqa: BLE001
            rows.append({"name": name, "error": repr(exc)})
            print("fail", name, repr(exc), flush=True)
    (OUT / "result_fixed.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
