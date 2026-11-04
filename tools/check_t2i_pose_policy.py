# -*- coding: utf-8 -*-
"""回归检查：Qwen T2I 分镜不得自动挂 pose_library 骨架。

背景（2026-11-04）：
    最终分镜出现“无头长衫 / 头陷进胸口 / 背后多一个巨人身体”。
    根因不是提示词，而是 ``Pipeline.generate_one_shot`` 在
    ``QWEN_STORYBOARD_USE_CHAR_REFS=True`` 时仍把自动姿态骨架送进
    Qwen ControlNet；内置骨架没有肩→髋躯干段，且竖骨架被按横格
    center-crop 会切掉头脚。25 格一直是纯 T2I，所以同一提示词正常。

这个脚本用假出图客户端跑 ``generate_one_shot``，不占 GPU：
    1. 自动骨架（data/projects/<id>/poses/）必须被忽略；
    2. 用户上传姿态（data/poses/）必须保留。

用法：
    .\\env\\Scripts\\python.exe tools\\check_t2i_pose_policy.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from comicgen.imageclient import GenResult  # noqa: E402
from comicgen.models import Project, Shot  # noqa: E402
from comicgen.pipeline import Pipeline  # noqa: E402
from comicgen.qwenedit import QwenEditClient  # noqa: E402
from comicgen.qwencomic import QwenComicClient  # noqa: E402


class RecordingQwen(QwenComicClient):
    """只记录参数，不连 ComfyUI。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, prompt: str, **kwargs):  # type: ignore[override]
        self.calls.append({"prompt": prompt, **kwargs})
        return GenResult(image=Image.new("RGB", (64, 64), "white"),
                         seed=int(kwargs.get("seed") or 1), size=(64, 64),
                         reference_paths=list(kwargs.get("reference_paths") or []),
                         workflow="fake", prompt_id="", protocol="fake")


def _shot(project: Project, index: int) -> Shot:
    return Shot(index=index, shot_type="全景", characters=["孔乙己"],
                description="孔乙己站在柜台前。", action="站着")


def main() -> int:
    project = Project(id="pose_policy_test", title="pose policy")
    project.characters = []
    shot = _shot(project, 1)
    project.shots = [shot]

    pipe = Pipeline(image_client=RecordingQwen())
    assert pipe.storyboard_t2i, "假客户端必须被识别为 Qwen T2I"

    old_shot_dir = config.SHOT_DIR
    old_data_dir = config.DATA_DIR
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        config.SHOT_DIR = tmp / "shots"
        config.DATA_DIR = tmp / "data"
        try:
            # 1) 自动骨架：放在 data/projects/<id>/poses/ 下，必须被忽略
            auto_pose = tmp / "data" / "projects" / project.id / "poses" / "pose_01_shot1.png"
            auto_pose.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (64, 128), "black").save(auto_pose)
            shot.pose_image = str(auto_pose)
            shot.pose_skeleton = str(auto_pose)
            pipe.generate_one_shot(project, shot, include_bubbles=False,
                                   size=(64, 64))
            call = pipe.image.calls[-1]
            assert call.get("pose_image") is None, (
                "自动骨架仍然被送进 Qwen T2I：" + str(call.get("pose_image")))

            # 2) 用户上传姿态：data/poses/ 下必须保留
            user_pose = tmp / "data" / "poses" / "my_pose.png"
            user_pose.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (64, 128), "black").save(user_pose)
            shot.pose_image = str(user_pose)
            shot.pose_skeleton = ""
            pipe.generate_one_shot(project, shot, include_bubbles=False,
                                   size=(64, 64))
            call = pipe.image.calls[-1]
            assert call.get("pose_image") == str(user_pose), (
                "用户上传姿态被误删：" + str(call.get("pose_image")))
            # 3) 竖姿态 -> 横格必须等比补黑边，不能裁掉头/脚
            portrait = tmp / "portrait_pose.png"
            im = Image.new("RGB", (64, 128), "black")
            im.putpixel((32, 2), (255, 0, 0))     # 头
            im.putpixel((32, 125), (0, 255, 0))   # 脚
            im.save(portrait)
            padded = Path(QwenEditClient()._pad_pose(str(portrait), (128, 64)))
            arr = np.asarray(Image.open(padded).convert("RGB"))
            assert arr.shape[:2] == (64, 128), f"姿态补边尺寸错误：{arr.shape}"
            assert (arr[:5, :, 0] > 0).any(), "姿态补边后头被裁掉了"
            assert (arr[-5:, :, 1] > 0).any(), "姿态补边后脚被裁掉了"
        finally:
            config.SHOT_DIR = old_shot_dir
            config.DATA_DIR = old_data_dir

    print("[+] Qwen T2I 姿态策略 OK：自动骨架忽略、用户姿态保留、竖骨架不裁头脚")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
