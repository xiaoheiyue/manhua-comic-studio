# -*- coding: utf-8 -*-
"""出图自检与自动返修。

这一层不判断"画得好不好看"（那需要视觉大模型），而是把**可程序化验证的硬伤**
找出来并给出返修指令：

* 白边：模型在方图里画竖构图，左右留白；
* 文字溢出：最小字号仍放不进气泡；
* 多余空白气泡：模型多画了对话框；
* 气泡兜底：模型没画气泡，程序自己补了一个；
* 全黑/全白/过暗：画面没有有效内容；
* 有台词却没有气泡布局。

`inspect_shot()` 返回 0~100 分与问题列表；`repair_note()` 把问题翻译成下一轮
出图提示词。`imggen.ShotBatchGenerator` 会在分数低于阈值时自动重试，保留最高
分的一版。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from PIL import Image, ImageStat

import config
from comicgen import bubble as bubble_mod
from comicgen.models import Project, Shot


@dataclass
class QualityReport:
    score: int = 0
    issues: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.score >= int(getattr(config, "IMAGE_QUALITY_PASS_SCORE", 82))

    @property
    def codes(self) -> list[str]:
        return [str(i.get("code", "")) for i in self.issues]

    def to_dict(self) -> dict[str, Any]:
        return {"score": self.score, "passed": self.passed,
                "issues": self.issues, "details": self.details}


def _speech_layouts(shot: Shot) -> list[dict]:
    out = []
    for item in shot.bubble_layout or []:
        if isinstance(item, dict) and item.get("kind", "speech") == "speech":
            out.append(item)
    return out


def inspect_shot(project: Project | None, shot: Shot) -> QualityReport:
    """对一格成品做结构化自检。"""
    report = QualityReport(score=100)
    path = shot.image_path
    if not path or not Path(path).exists():
        report.score = 0
        report.issues.append({"code": "image_missing", "level": "danger",
                              "message": "成品图不存在"})
        return report
    try:
        img = Image.open(path).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        report.score = 0
        report.issues.append({"code": "image_broken", "level": "danger",
                              "message": f"成品图无法读取：{exc}"})
        return report

    W, H = img.size
    report.details["size"] = [W, H]
    gray = img.convert("L")
    stat = ImageStat.Stat(gray)
    mean = float(stat.mean[0])
    std = float(stat.stddev[0])
    report.details.update({"mean": round(mean, 2), "std": round(std, 2)})

    def penalty(code: str, score: int, message: str, **extra: Any) -> None:
        report.score -= score
        item = {"code": code, "level": "warn", "message": message, "penalty": score}
        item.update(extra)
        report.issues.append(item)

    # ---- 1) 全白 / 全黑 / 过暗 ----
    if std < 4.0:
        penalty("blank_image", 70, "画面几乎是纯色，可能生成失败")
    elif mean < 8.0:
        penalty("too_dark", 22, "画面过暗，主体和背景几乎看不清")
    elif std < 12.0:
        penalty("low_contrast", 12, "画面对比度过低，层次不清")

    # ---- 2) 白边（正常情况下 apply_bubbles 已经裁掉）----
    borders = bubble_mod.detect_white_borders(img)
    if borders:
        penalty("white_border", 28, f"四边仍有白边：{borders}", borders=borders)
    elif "white_border_trimmed" in (shot.quality_issues or []):
        penalty("white_border_trimmed", 3, "检测到白边并已自动裁掉")

    # ---- 3) 文字溢出 ----
    layouts = _speech_layouts(shot)
    overflow = [b for b in layouts if b.get("overflow")]
    if overflow:
        penalty("text_overflow", 36,
                f"{len(overflow)} 个气泡在最小字号下仍放不下文字", count=len(overflow))

    # ---- 4) 多余空白气泡 ----
    if "extra_balloon" in (shot.quality_issues or []):
        penalty("extra_balloon", 6, "模型多画了空白气泡，已自动填“……”或盖掉")

    # ---- 5) 气泡兜底 / 数量不符 / 定位失败 ----
    # 主链路是"模型画气泡、程序回填"：程序补画 = 模型没画到位，要扣分并返修。
    # 注意：色彩迁移工作流会把气泡染色，此时提示词里**没有**气泡计划
    # （shot.bubble_plan 为空），气泡本来就该由程序画，不能按"兜底"扣分。
    if bool(getattr(config, "IMAGE_DRAW_BUBBLES", False)) \
            and (shot.bubble_plan or []) \
            and not bool(getattr(shot, "bubble_programmatic", False)):
        fallback = [b for b in layouts if not b.get("drawn_in_art")]
        if fallback:
            penalty("bubble_fallback", min(18, 6 * len(fallback)),
                    f"{len(fallback)} 个气泡是程序兜底画的，模型没按坐标画",
                    count=len(fallback))
    debug = dict(getattr(shot, "bubble_debug", {}) or {})
    if "bubble_detect_fail" in (shot.quality_issues or []):
        penalty("bubble_detect_fail", 14,
                f"计划 {debug.get('expected', 0)} 个气泡，画面里一个都没定位到")
    if "bubble_count_mismatch" in (shot.quality_issues or []):
        penalty("bubble_count_mismatch", 6,
                f"模型多画了 {debug.get('extra', 0)} 个气泡，已补成背景",
                count=debug.get("extra", 0))
    # 模型自绘的空白气泡被盖掉：扣分很少，但要让用户知道发生过
    if "stray_balloon_covered" in (shot.quality_issues or []):
        penalty("stray_balloon_covered", 4,
                "模型自己画了气泡，已用邻近背景盖掉（重出图可减少）")

    # ---- 6) 有台词却没有气泡布局 ----
    speech_dialogues = [d for d in shot.dialogues
                        if (d.text or "").strip() and d.kind == "speech"]
    if speech_dialogues and not layouts:
        penalty("no_bubble", 24,
                f"有 {len(speech_dialogues)} 条台词，但画面里没有可用气泡")

    # ---- 6.5) 物件格凭空长脸/长手 ----
    # 手机+血迹、屏幕、纸张这类物件特写，Z-Image 会按语义补一个人像或一只手。
    # 用低分辨率肤色热区做粗筛：物件格里出现大面积肤色，基本就是"人"了。
    if not shot.characters and shot.shot_type in ("特写", "近景"):
        skin = bubble_mod._skin_grid(img)
        if skin is not None:
            ratio = float(skin["grid"].mean())
            report.details["object_skin_ratio"] = round(ratio, 3)
            strong = float((skin["grid"] > 0.8).mean())
            if ratio > 0.015 and strong > 0.002:
                penalty("object_face_hallucination", 18,
                        f"物件特写里出现 {ratio:.1%} 肤色区域（强命中 {strong:.1%}），"
                        f"模型可能自己加了人脸/手",
                        ratio=round(ratio, 3), strong=round(strong, 3))

    # ---- 7) 角色参考图是否齐全（当前 ComfyUI 工作流不接收图像输入，
    #         这里只检查"有没有可用来对照的参考图"，一致性本身靠提示词兜）----
    if shot.characters and not shot.reference_paths:
        penalty("no_reference", 8,
                "这格有角色，但角色还没有参考图（一致性只能靠人设描述）")

    report.score = max(0, min(100, report.score))
    report.details["issue_codes"] = report.codes
    return report


def repair_note(report: QualityReport, shot: Shot) -> str:
    """把自检问题翻译成下一轮出图要追加的返修要求。"""
    codes = set(report.codes)
    parts: list[str] = []
    if "white_border" in codes:
        parts.append("画面满版出血到四边，构图完整占满画布，人物与背景一直延伸到画面边缘")
    if "text_overflow" in codes:
        if bool(getattr(config, "IMAGE_DRAW_BUBBLES", False)):
            parts.append("气泡要画大一号，给文字留足空间；文字必须完整放在气泡里")
        else:
            # 程序画气泡时，模型能帮的是"多留一点干净背景"，而不是画大气泡
            parts.append("画面上方保留更完整的浅色背景空间，人物与道具整体往下让一点")
        # 同时放大气泡计划，给模型更明确的落点
        for p in shot.bubble_plan or []:
            try:
                p["w"] = min(0.92, float(p.get("w", 0.3)) * 1.14)
                p["h"] = min(0.55, float(p.get("h", 0.12)) * 1.16)
                p["x"] = min(max(0.01, float(p.get("x", 0.2)) - 0.02), 1.0 - p["w"] - 0.01)
                p["y"] = min(max(0.01, float(p.get("y", 0.05)) - 0.01), 1.0 - p["h"] - 0.01)
            except (TypeError, ValueError):
                continue
    if "bubble_detect_fail" in codes or "bubble_count_mismatch" in codes \
            or "bubble_fallback" in codes:
        n = max(1, len([d for d in shot.dialogues
                        if (d.text or "").strip() and d.kind == "speech"]))
        parts.append(f"画面里只有 {n} 个空白气泡，严格按上面每个气泡的坐标与尺寸画，"
                     f"气泡纯白、黑色描边、内部没有文字，彼此不重叠")
    if "extra_balloon" in codes:
        if bool(getattr(config, "IMAGE_DRAW_BUBBLES", False)):
            n = max(1, len([d for d in shot.dialogues
                            if (d.text or "").strip() and d.kind == "speech"]))
            parts.append(f"画面中只画 {n} 个空白气泡，位置与尺寸严格按上面的说明")
        else:
            parts.append("画面里只保留人物、道具与场景，背景干净完整")
    if "stray_balloon_covered" in codes:
        parts.append("画面里只保留人物、道具与场景，背景干净完整")
    if "bubble_fallback" in codes and not bool(getattr(config, "IMAGE_DRAW_BUBBLES", False)):
        parts.append("画面上方保留更完整的浅色背景空间，人物与道具整体往下让一点")
    if "no_bubble" in codes and bool(getattr(config, "IMAGE_DRAW_BUBBLES", False)):
        parts.append("台词对应的空白气泡必须画出来，不能省略")
    if "blank_image" in codes or "too_dark" in codes or "low_contrast" in codes:
        parts.append("画面不能全黑或全白，主体、人物和背景都要清晰可辨")
    if "no_reference" in codes:
        parts.append("严格按上面的人设描述锁定人物长相、发型、发色与服装")
    if "object_face_hallucination" in codes:
        parts.append("画面里只有物件与承托它的桌面/空间：物件的表面只有材质、裂纹、"
                     "血迹与反光，物件之外没有任何人、脸或手")
    return "；".join(parts)


def needs_repair(report: QualityReport) -> bool:
    if not bool(getattr(config, "IMAGE_AUTO_REPAIR", True)):
        return False
    return report.score < int(getattr(config, "IMAGE_QUALITY_PASS_SCORE", 82))
