# -*- coding: utf-8 -*-
"""阶段五：分镜图像批量生成与气泡叠加。

功能覆盖需求 4.6 / 4.9 / 4.10：
- 按分镜顺序逐张生成，实时回调进度与预览
- 随时暂停 / 继续 / 取消
- 跳过指定分镜、对单张重新生成
- 失败记录错误并继续后续分镜，最后汇总
- 生成后自动叠加对话气泡（可关闭，输出纯画面）
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Callable, Sequence

from PIL import Image

import config
from . import bubble as bubble_mod
from . import pagelayout
from . import prompt_trace
from . import quality as quality_mod
from .logger import get_logger
from .models import Project, Shot
from .pipeline import Pipeline
from .textutil import truncate

log = get_logger(__name__)

ProgressCb = Callable[[str, float], None]
LogCb = Callable[[str], None]
ShotDoneCb = Callable[[Shot], None]


def _noop_progress(msg: str, value: float) -> None:  # pragma: no cover
    log.info("%s (%.0f%%)", msg, value * 100)


def _noop_log(msg: str) -> None:  # pragma: no cover
    log.info(msg)


# =============================================================================
# 单张分镜的成品输出
# =============================================================================

def shot_output_path(project: Project, shot: Shot, suffix: str = "") -> Path:
    """分镜成品的标准输出路径。"""
    out_dir = Path(config.SHOT_DIR) / project.id
    out_dir.mkdir(parents=True, exist_ok=True)
    name = config.SHOT_FILENAME_TEMPLATE.format(index=shot.index) + suffix
    ext = config.OUTPUT_IMAGE_FORMAT.lower()
    if ext == "jpg":
        ext = "jpeg"
    return out_dir / f"{name}.{'jpg' if ext == 'jpeg' else ext}"


def finalize_shot(project: Project, shot: Shot,
                  bubbles_enabled: bool = True,
                  bubble_position: str | None = None,
                  layouts: Sequence[dict] | None = None) -> Shot:
    """
    把 shot.raw_image_path（纯画面）加工成 shot.image_path（带气泡成品）。

    bubbles_enabled=False 时直接输出纯画面（需求 4.8：可关闭气泡）。
    """
    if not shot.raw_image_path or not Path(shot.raw_image_path).exists():
        shot.status = "失败"
        shot.error = "缺少原始画面文件"
        return shot

    # 模型偶尔会在方图里画竖构图、左右留白；先在原图上裁掉白边，
    # 再重算气泡计划，避免文字按旧画布坐标错位。
    try:
        raw = Image.open(shot.raw_image_path).convert("RGB")
        cleaned, borders = bubble_mod.trim_white_borders(raw)
        if borders:
            cleaned.save(shot.raw_image_path, format="PNG")
            if getattr(shot, "bubble_plan", None):
                shot.bubble_plan = bubble_mod.plan_layout(shot, cleaned.width, cleaned.height)
            log.info("分镜 %s 裁掉白边 %s", shot.index, borders)
    except Exception as exc:  # noqa: BLE001
        log.debug("分镜 %s 白边处理失败：%s", shot.index, exc)

    target = shot_output_path(project, shot)
    if not bubbles_enabled or not shot.has_dialogue:
        shutil.copyfile(shot.raw_image_path, target)
        shot.image_path = str(target)
        shot.bubble_layout = []
    else:
        path, layout = bubble_mod.apply_bubbles(
            shot.raw_image_path, shot, output_path=target,
            layouts=layouts, position=bubble_position, enabled=True)
        shot.image_path = path
        shot.bubble_layout = layout
    shot.status = "已完成"
    shot.error = ""
    try:
        prompt_trace.record_data(project.id, "07_bubble", f"shot_{shot.index:03d}", {
            "plan": shot.bubble_plan or [],
            "debug": shot.bubble_debug or {},
            "layouts": shot.bubble_layout or [],
            "quality_issues": shot.quality_issues or [],
            "image": str(shot.image_path or ""),
        })
    except Exception:  # noqa: BLE001
        pass
    return shot


def regenerate_bubbles(project: Project, shot: Shot,
                       layouts: Sequence[dict] | None = None,
                       position: str | None = None) -> Shot:
    """只重绘气泡，不重新出图（气泡编辑后保存用）。"""
    if not shot.raw_image_path or not Path(shot.raw_image_path).exists():
        return finalize_shot(project, shot, bubbles_enabled=True,
                             bubble_position=position, layouts=layouts)
    target = shot_output_path(project, shot)
    path, layout = bubble_mod.apply_bubbles(
        shot.raw_image_path, shot, output_path=target,
        layouts=layouts, position=position, enabled=True)
    shot.image_path = path
    shot.bubble_layout = layout
    if shot.status != "已完成":
        shot.status = "已完成"
    return shot


# =============================================================================
# 批量生成器
# =============================================================================

class ShotBatchGenerator:
    """可暂停、可取消、可跳过的分镜批量生成器。"""

    def __init__(self, pipeline: Pipeline,
                 progress_cb: ProgressCb | None = None,
                 log_cb: LogCb | None = None,
                 shot_done_cb: ShotDoneCb | None = None):
        self.pipe = pipeline
        self.progress = progress_cb or _noop_progress
        self.log = log_cb or _noop_log
        self.shot_done = shot_done_cb

        self._pause = threading.Event()
        self._pause.set()          # set = 运行中，clear = 暂停
        self._cancel = threading.Event()
        self._pause_requested = False
        self.auto_repair_override: bool | None = None

    # ---- 控制 ----
    def pause(self) -> None:
        self._pause_requested = True
        self._pause.clear()
        self.log("已暂停生成，可查看已完成图片并调整后续分镜")

    def resume(self) -> None:
        self._pause.set()
        self.log("已继续生成")

    def cancel(self) -> None:
        self._cancel.set()
        self._pause.set()
        self.log("已取消生成流程")

    @property
    def paused(self) -> bool:
        return not self._pause.is_set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _wait_if_paused(self) -> bool:
        """返回 False 表示已取消。"""
        while self.paused:
            if self._cancel.is_set():
                return False
            time.sleep(0.2)
        return not self._cancel.is_set()

    # ---- 单格：生成 + 自检 + 自动返修 ----
    def _generate_with_repair(self, project: Project, shot: Shot,
                              bubbles_enabled: bool,
                              bubble_position: str | None,
                              size: tuple[int, int] | None) -> quality_mod.QualityReport:
        retries = int(getattr(config, "IMAGE_AUTO_REPAIR_RETRIES", 2)) \
            if (config.IMAGE_AUTO_REPAIR if self.auto_repair_override is None
                else self.auto_repair_override) else 0
        retries = max(0, retries)
        qa_dir = Path(config.SHOT_DIR) / project.id / "_qa"
        qa_dir.mkdir(parents=True, exist_ok=True)
        best: dict | None = None
        last: quality_mod.QualityReport | None = None
        shot.image_note = ""

        for attempt in range(1 + retries):
            if attempt:
                note = quality_mod.repair_note(last, shot) if last else ""
                shot.image_note = note or "按上一轮的问题修正构图、气泡与光线"
                self.log(f"第 {shot.index} 格自检 {last.score if last else 0} 分，"
                         f"自动返修 {attempt}/{retries}：{shot.image_note}")

            self.pipe.generate_one_shot(project, shot,
                                        include_bubbles=bubbles_enabled,
                                        size=size)
            finalize_shot(project, shot, bubbles_enabled=bubbles_enabled,
                          bubble_position=bubble_position)
            report = quality_mod.inspect_shot(project, shot)
            shot.quality_score = report.score
            shot.quality_issues = report.codes
            last = report
            codes = "、".join(report.codes)
            bubble_hard = any(c in report.codes for c in
                              ("bubble_detect_fail", "bubble_count_mismatch", "bubble_fallback",
                               "object_face_hallucination"))
            if report.passed and not bubble_hard:
                self.log(f"第 {shot.index} 格自检 {report.score} 分，通过")
            else:
                self.log(f"第 {shot.index} 格自检 {report.score} 分"
                         f"{'（' + codes + '）' if codes else ''}")

            if shot.image_path and Path(shot.image_path).exists():
                if best is None or report.score > best["score"]:
                    best_final = qa_dir / f"best_{shot.index:03d}_final.png"
                    best_raw = qa_dir / f"best_{shot.index:03d}_raw.png"
                    shutil.copyfile(shot.image_path, best_final)
                    if shot.raw_image_path and Path(shot.raw_image_path).exists():
                        shutil.copyfile(shot.raw_image_path, best_raw)
                    else:
                        best_raw = None
                    best = {"score": report.score, "issues": list(report.codes),
                            "final": best_final, "raw": best_raw,
                            "layout": list(shot.bubble_layout or [])}
            if report.passed and not bubble_hard:
                break
            if self._cancel.is_set():
                break

        # 保留最高分的一版（返修可能越修越差）
        if best and shot.image_path and Path(shot.image_path).exists():
            try:
                shutil.copyfile(best["final"], shot.image_path)
                if best["raw"] and Path(best["raw"]).exists():
                    shutil.copyfile(best["raw"], shot.raw_image_path)
                shot.bubble_layout = best["layout"]
                shot.quality_score = best["score"]
                shot.quality_issues = best["issues"]
            except Exception as exc:  # noqa: BLE001
                log.debug("恢复最佳版本失败：%s", exc)
        shot.image_note = ""
        accept = int(getattr(config, "IMAGE_QUALITY_ACCEPT_SCORE", 55))
        bubble_only = bool(shot.quality_issues) and all(
            c in ("bubble_detect_fail", "bubble_count_mismatch", "bubble_fallback",
                  "stray_balloon_covered", "extra_balloon")
            for c in shot.quality_issues)
        if shot.quality_score < accept and not bubble_only and not self._cancel.is_set():
            shot.status = "失败"
            shot.error = (f"自检 {shot.quality_score} 分，低于可交付阈值 {accept}；"
                          f"问题：{'、'.join(shot.quality_issues or []) or '未知'}")
            self.log(f"第 {shot.index} 格自检 {shot.quality_score} 分，标记失败，"
                     f"避免带入成品")
        return last or quality_mod.QualityReport(score=0)

    # ---- 主流程 ----
    def run(self,
            project: Project,
            shot_ids: Sequence[str] | None = None,
            bubbles_enabled: bool | None = None,
            bubble_position: str | None = None,
            size: tuple[int, int] | None = None,
            retry_failed_only: bool = False,
            skip_done: bool = True,
            auto_repair: bool | None = None,
            auto_pose: bool | None = None) -> Project:
        """
        批量生成分镜图。

        shot_ids      指定只生成这些分镜（None 表示全部）
        retry_failed_only  只重试失败的分镜
        skip_done     已完成的自动跳过
        """
        bubbles_enabled = config.BUBBLE_ENABLED_BY_DEFAULT \
            if bubbles_enabled is None else bubbles_enabled
        self.auto_repair_override = auto_repair
        self.auto_pose_override = auto_pose

        # 分格页：出图前确保每一格都知道自己在哪一页、什么比例
        try:
            self.pipe.ensure_pages(project)
        except Exception as exc:  # noqa: BLE001
            log.warning("分页规划失败（按原条漫画幅继续）：%s", exc)

        targets = list(project.shots)
        if shot_ids:
            wanted = set(shot_ids)
            targets = [s for s in targets if s.id in wanted]
        if retry_failed_only:
            targets = [s for s in targets if s.status == "失败"]
        elif skip_done:
            targets = [s for s in targets if s.status != "已完成"]
        targets = [s for s in targets if not s.skip]

        total = len(targets)
        if total == 0:
            self.log("没有需要生成的分镜（可能都已完成或都被跳过）")
            self.progress("无待生成分镜", 1.0)
            return project

        self._cancel.clear()
        self.log(f"开始生成 {total} 格分镜（气泡叠加：{'开启' if bubbles_enabled else '关闭'}）")
        done_count = 0
        errors: list[str] = []
        started = time.time()

        for i, shot in enumerate(targets, start=1):
            if self._cancel.is_set():
                self.log("生成流程已取消")
                break
            if not self._wait_if_paused():
                self.log("生成流程已取消")
                break
            if shot.skip:
                self.log(f"分镜 {shot.index} 已标记跳过，忽略")
                continue

            # 硬超时保护
            if time.time() - started > config.GENERATION_HARD_TIMEOUT:
                self.log("已达最长运行时间，自动停止生成")
                break

            overall = 0.05 + 0.90 * (i - 1) / total
            self.progress(f"正在生成第 {shot.index} 格（{i}/{total}）…", overall)
            shot.status = "生成中"
            try:
                self._generate_with_repair(project, shot,
                                           bubbles_enabled=bubbles_enabled,
                                           bubble_position=bubble_position,
                                           size=size)
                if shot.status == "失败":
                    raise RuntimeError(shot.error or f"第 {shot.index} 格未通过自检")
                done_count += 1
                score = getattr(shot, "quality_score", 0)
                self.log(f"第 {shot.index} 格生成完成（{i}/{total}，自检 {score} 分）")
                if self.shot_done:
                    try:
                        self.shot_done(shot)
                    except Exception as exc:  # noqa: BLE001
                        log.debug("回调异常：%s", exc)
            except Exception as exc:  # noqa: BLE001
                shot.status = "失败"
                shot.error = truncate(str(exc), 300)
                msg = f"第 {shot.index} 格生成失败：{shot.error}"
                errors.append(msg)
                self.log(msg)
                if not config.SKIP_ON_ERROR:
                    self.log("配置为遇错中止，停止生成")
                    break
                self.log("已跳过该格，继续生成后续分镜")
                if self.shot_done:
                    try:
                        self.shot_done(shot)
                    except Exception:  # noqa: BLE001
                        pass

            self.progress(f"已完成 {done_count} 格", 0.05 + 0.90 * i / total)
            if config.INTER_SHOT_DELAY > 0:
                time.sleep(config.INTER_SHOT_DELAY)

        # ---- 汇总 ----
        # 分格页：所有格出完后按版式拼页（缺图的页会自动跳过，等补图后再合）
        if project.pages and bool(getattr(config, "PAGE_COMPOSE_ON_GENERATE", True)):
            try:
                self.progress("正在按版式合成漫画页…", 0.97)
                stats = pagelayout.compose_all_pages(
                    project, only_pending=True, progress=self.log)
                self.log(f"分格页：{stats['done']}/{stats['pages']} 页已合成"
                         + (f"，{stats['pending']} 页待补图" if stats["pending"] else "")
                         + (f"，{stats['failed']} 页失败" if stats["failed"] else ""))
            except Exception as exc:  # noqa: BLE001
                log.warning("页面合成失败：%s", exc)
                self.log(f"页面合成失败（分镜图已保留）：{exc}")

        done, all_total = project.progress()
        if errors:
            self.log(f"生成结束，共 {len(errors)} 格失败，明细如下：")
            for e in errors[:50]:
                self.log("  · " + e)
        self.log(f"全部结束：成功 {done}/{all_total} 格")
        self.progress(f"生成完成：{done}/{all_total}", 1.0)
        return project


# =============================================================================
# 工具
# =============================================================================

def thumbnail_grid(project: Project, max_edge: int | None = None,
                   include_pending: bool = True) -> list[tuple[Image.Image, str]]:
    """
    生成缩略图网格数据：[(缩略图, 标题), ...]，供界面 gr.Gallery 展示。
    """
    max_edge = max_edge or config.THUMBNAIL_MAX_EDGE
    items: list[tuple[Image.Image, str]] = []
    for shot in project.shots:
        path = shot.image_path or shot.raw_image_path
        if path and Path(path).exists():
            try:
                img = Image.open(path).convert("RGB")
                img.thumbnail((max_edge, max_edge), Image.LANCZOS)
                items.append((img, f"{shot.index:03d} {shot.shot_type} {shot.status}"))
            except Exception as exc:  # noqa: BLE001
                log.debug("缩略图失败 %s：%s", path, exc)
        elif include_pending:
            items.append((_placeholder(shot, max_edge),
                          f"{shot.index:03d} {shot.shot_type} {shot.status}"))
    return items


def _placeholder(shot: Shot, max_edge: int) -> Image.Image:
    """未生成分镜的占位缩略图。"""
    from PIL import ImageDraw
    size = (max_edge, max_edge)
    img = Image.new("RGB", size, (238, 240, 250))
    draw = ImageDraw.Draw(img)
    font = bubble_mod.load_font(max(14, max_edge // 18))
    lines = [f"{shot.index:03d}", shot.shot_type, shot.status]
    y = size[1] // 2 - len(lines) * 14
    for line in lines:
        w = bubble_mod.measure(line, font)[0]
        draw.text(((size[0] - w) // 2, y), line, font=font, fill=(140, 148, 176))
        y += 28
    return img


def character_gallery(project: Project, max_edge: int | None = None) -> list[tuple[Image.Image, str]]:
    """角色参考图网格：主视图 + 姿态总图。"""
    max_edge = max_edge or config.THUMBNAIL_MAX_EDGE
    items: list[tuple[Image.Image, str]] = []
    for c in project.characters:
        for label, path in (("主视图", c.main_visual), ("姿态总图", c.pose_sheet)):
            if path and Path(path).exists():
                try:
                    img = Image.open(path).convert("RGB")
                    img.thumbnail((max_edge, max_edge), Image.LANCZOS)
                    items.append((img, f"{c.name} · {label}"))
                except Exception as exc:  # noqa: BLE001
                    log.debug("角色缩略图失败 %s：%s", path, exc)
    return items


