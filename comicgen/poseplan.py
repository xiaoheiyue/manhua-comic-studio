# -*- coding: utf-8 -*-
"""分镜姿态规划：把镜头/角色动作映射到姿态骨架。

不调用模型，纯规则。目的：
1. 分镜分析阶段已经有 `characters` + `action` + `character_actions`；
2. 出图前按景别/机位/动作选一个骨架，交给 Pose ControlNet；
3. 用户手动上传的 `Shot.pose_image` 永远优先。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import config

_LIB = Path(config.BASE_DIR) / "data" / "pose_library"
_VIEWS = _LIB / "views"
_ACTIONS = _LIB / "actions"

# 已确认不可用的骨架：action_04 的画面里有**两个重叠的头**，
# 单人镜头一挂它，模型就会画出"前后两张脸"（本次单人乱入的根因之一）。
# 新骨架请先用 ``validate_pose`` 自检，再放进库。
_INVALID_NAMES = {"action_04.png"}


def _p(path: Path) -> str:
    return str(path) if path.exists() else ""


def _view(name: str) -> str:
    return _p(_VIEWS / name)


def _action(i: int) -> str:
    return _p(_ACTIONS / f"action_{i:02d}.png")


def validate_pose(path: str | Path) -> tuple[bool, str]:
    """检查骨架是否适合单人分镜。

    两个硬条件：
    1. 文件存在，且不在已知坏图名单里；
    2. 画面上方 32% 至少有一簇头部关键点（OpenPose 彩色骨架即可）。
       当前 ``views/`` 下的图大多是从参考图里裁出来的"无头"骨架，
       挂上去会把脸画到画布顶上，甚至补出第二个人。
    """
    p = Path(path)
    if not p.exists():
        return False, "文件不存在"
    if p.name in _INVALID_NAMES:
        return False, "已知多头骨架"
    try:
        from PIL import Image
        import numpy as np
        im = Image.open(p).convert("RGB")
        w, h = im.size
        if w < 32 or h < 64:
            return False, "尺寸过小"
        arr = np.asarray(im)
        top = arr[:max(1, int(h * 0.32))]
        # OpenPose 风格骨架的头部关键点是彩色（粉/红/橙），不是白色。
        # 旧逻辑只数近白像素，导致所有内置骨架都被判"缺少头部关键点"，
        # 第 7 步全部退化成同一张"正面兜底"。这里改为统计亮色关键点。
        bright = (top.max(axis=2) > 140) & (top.min(axis=2) < 140)
        white = ((top[:, :, 0] > 175) & (top[:, :, 1] > 175)
                 & (top[:, :, 2] > 175))
        if int(bright.sum()) + int(white.sum()) < 40:
            return False, "缺少头部关键点"
    except Exception as exc:  # noqa: BLE001
        return False, f"无法读取：{exc}"
    return True, ""


def _validated(path: str, reason: str, dynamic: bool) -> tuple[str, str, bool]:
    ok, why = validate_pose(path)
    if not ok:
        return "", f"{reason}（骨架不可用：{why}）", False
    return path, reason, dynamic


def choose_pose(shot: Any) -> tuple[str, str, bool]:
    """返回 (骨架路径, 选择原因, 是否动态)。

    规则优先级：俯视 > 过肩 > 背影 > 特写 > 动作 > 景别。
    """
    # 没有可见人物的纯道具/环境镜头，不挂人物姿态骨架；
    # 否则模型会按骨架硬画一个完整人物（例如“楼梯上的皮鞋”变成长裙女人）。
    if not (getattr(shot, "characters", None) or []):
        return "", "无人物镜头，不挂姿态", False
    text = " ".join([
        str(getattr(shot, "camera", "") or ""),
        str(getattr(shot, "shot_type", "") or ""),
        str(getattr(shot, "action", "") or ""),
        str(getattr(shot, "description", "") or ""),
        " ".join(str(v) for v in (getattr(shot, "character_actions", {}) or {}).values()),
    ])
    st = str(getattr(shot, "shot_type", "") or "")

    if any(w in text for w in ("俯视", "高机位", "航拍", "顶视", "鸟瞰", "从上往下")):
        # Z-Image 目前没有稳定的顶视条件；不自动塞骨架，避免画成正面/巨手。
        return "", "俯视/高机位（需真实顶视参考，暂不自动挂骨架）", False
    if "过肩" in text:
        if "左" in text:
            return _validated(_view("13左过肩镜头.png"), "左过肩", False)
        return _validated(_view("12右过肩镜头.png"), "右过肩", False)
    if any(w in text for w in ("背影", "背对", "背面", "回头", "回眸")):
        return _validated(_view("4全身-背面.png"), "背影/回眸", False)
    if st == "特写" or "特写" in text:
        return _validated(_view("10特写正面.png"), "特写", False)
    if st == "近景":
        return _validated(_view("5半身正面.png"), "近景半身", False)

    # 动作：优先用多动作骨架，避免所有格都站桩。
    # 注意：动作词必须带上下文，不能拿单字去匹配场景名——
    # "走廊"里的"走"、"打开"里的"打"、"冲突"里的"冲"都会误挂骨架。
    if re.search(r"奔跑|跑动|跑来|跑去|跑向|逃跑", text):
        return _validated(_action(1), "奔跑动作", True)
    if re.search(r"挥手|挥拳|挥动|打斗|出拳|击打|推开|拉开|伸手|举起|抓住|抓取|推门|拉门", text):
        return _validated(_action(4), "上肢动作", True)
    if re.search(r"坐下|坐着|坐到|蹲下|蹲着|跪|蜷", text):
        return _validated(_action(3), "坐/蹲动作", True)
    if re.search(r"走(?!廊)|迈步|走向|走过去|往前走|转身|回头", text):
        return _validated(_action(6), "行走/转身", True)
    if re.search(r"跳起|跳下|跳跃|踢|跃起", text):
        return _validated(_action(2), "跳跃/踢腿", True)

    if st in ("远景", "全景"):
        return _validated(_view("1全身正面.png"), "远景全身", False)
    if "侧" in text:
        return _validated(_view("2全身右侧面.png"), "侧面全身", False)
    if st == "中景":
        return _validated(_view("6半身右侧45°.png"), "中景半身", False)
    return _validated(_view("5半身正面.png"), "默认半身", False)


def should_auto_pose(shot: Any, mode: str | None = None) -> bool:
    """是否自动挂姿态：off / key / all。

    key 只对“动态动作 / 俯视 / 过肩”启用，避免 8GB 上每格都跑 ControlNet。
    """
    if getattr(shot, "pose_image", ""):
        return False                 # 用户自定义优先，由调用方处理
    mode = (mode or getattr(config, "AUTO_POSE_SHOTS", "key") or "key").lower()
    if mode == "off":
        return False
    if mode == "all":
        return True
    # 特写/近景不挂全身骨架：全身骨架会把女性角色画成男性体型，
    # 也会把脸部结构带偏；这些镜头靠 prompt/机位就够了。
    if str(getattr(shot, "shot_type", "") or "") in ("特写", "近景"):
        return False
    # 多人物镜头只给一张全身骨架，模型会把两个人挤进同一副骨架，
    # 造成服装/脸互相污染；多人镜头交给分镜 prompt 和角色卡。
    if len([n for n in (getattr(shot, "characters", None) or []) if n]) > 1:
        return False
    _path, reason, dynamic = choose_pose(shot)
    return bool(dynamic or reason in ("俯视/高机位", "左过肩", "右过肩"))

