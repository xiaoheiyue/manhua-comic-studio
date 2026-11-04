# -*- coding: utf-8 -*-
"""漫画生成器 —— Web 后端（FastAPI）。

设计要点：
* 纯 REST + JSON，前端是原生 HTML/CSS/JS（见 web/ 目录），不依赖 Gradio。
* 耗时任务（分析、人设、拆解、出图、导出）在后台线程执行，
  前端通过 /api/job/{id} 轮询进度与结果，界面永远不会卡住。
* 所有图片通过 /api/image?path=... 统一读取，路径被限制在项目目录内。
* 中间结果实时落盘到 data/，重启后可继续。

启动：
    env\\Scripts\\python.exe server.py
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,  # noqa: E402
                               Response)
from fastapi.staticfiles import StaticFiles  # noqa: E402
from PIL import Image  # noqa: E402

import config  # noqa: E402
from comicgen import bubble as bubble_mod  # noqa: E402
from comicgen import exporter, imggen, models, pipeline, textutil  # noqa: E402
from comicgen import director as director_mod  # noqa: E402
from comicgen import episode as episode_mod  # noqa: E402
from comicgen import pagelayout  # noqa: E402
from comicgen import keyframes as keyframes_mod  # noqa: E402
from comicgen import poseplan  # noqa: E402
from comicgen import prompt_trace  # noqa: E402
from comicgen import prompt_studio  # noqa: E402
from comicgen import resource_studio  # noqa: E402
from comicgen import workflow_studio  # noqa: E402
from comicgen import prompts  # noqa: E402
from comicgen import storyboard as storyboard_mod  # noqa: E402
from comicgen import stylelib  # noqa: E402
from comicgen import studio as studio_mod  # noqa: E402
from comicgen.imageclient import ImageClient
from comicgen.qwencomic import QwenComicClient  # noqa: E402
from comicgen.logger import get_logger, log_lines  # noqa: E402
from comicgen.models import Character, Dialogue, Project, Scene, Shot  # noqa: E402
from comicgen.pipeline import Pipeline  # noqa: E402
from comicgen.textclient import TextClient  # noqa: E402

log = get_logger("comicgen.server")
config.ensure_dirs()

app = FastAPI(title=config.APP_TITLE, docs_url="/api/docs", redoc_url=None)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# =============================================================================
# 运行态
# =============================================================================

@dataclass
class State:
    """进程内共享状态。"""

    project: Project | None = None
    pipe: Pipeline | None = None
    batch: imggen.ShotBatchGenerator | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)

    def get_project(self) -> Project:
        with self.lock:
            if self.project is None:
                restored = self._restore_last_project()
                self.project = restored or Project()
                if restored is not None:
                    self._remember_project(restored)
            return self.project

    @staticmethod
    def _remember_project(project: Project) -> None:
        """把当前工程写进 last_project.txt；服务重启后按它恢复。"""
        try:
            pointer = Path(getattr(config, "LAST_PROJECT_FILE",
                                   Path(config.DATA_DIR) / "last_project.txt"))
            target = Path(config.PROJECT_DIR) / f"{project.id}.json"
            if not target.exists():
                project.save(target)
            pointer.parent.mkdir(parents=True, exist_ok=True)
            pointer.write_text(str(target.resolve()), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            log.debug("写入 last_project 指针失败：%s", exc)

    @staticmethod
    def _restore_last_project() -> Project | None:
        """服务重启后恢复上次打开的工程。

        旧行为是直接返回一个空白 Project：界面刷新后所有"精修 / 25 格 / 姿态"
        都变成 0，用户会以为历史结果丢了。这里优先读 last_project.txt，
        再退回"最近修改的合法工程文件"。
        """
        candidates: list[Path] = []
        pointer = Path(getattr(config, "LAST_PROJECT_FILE",
                               Path(config.DATA_DIR) / "last_project.txt"))
        try:
            if pointer.exists():
                text = pointer.read_text(encoding="utf-8").strip()
                if text:
                    candidates.append(Path(text))
        except Exception as exc:  # noqa: BLE001
            log.debug("读取 last_project 指针失败：%s", exc)
        for path in Project.list_projects():
            if re.fullmatch(r"[0-9a-f]{6,12}\.json", path.name):
                candidates.append(path)
        seen: set[str] = set()
        for path in candidates:
            key = str(path.resolve()) if path.exists() else str(path)
            if key in seen:
                continue
            seen.add(key)
            if not path.exists() or not path.is_file():
                continue
            try:
                project = Project.load(path)
            except Exception as exc:  # noqa: BLE001
                log.warning("恢复工程失败 %s：%s", path, exc)
                continue
            if not (project.novel_text or project.shots or project.visual_anchors):
                continue
            log.info("已自动恢复上次工程：%s（%s）", project.title, project.id)
            return project
        return None

    def set_project(self, project: Project) -> None:
        with self.lock:
            self.project = project
            self.pipe = None
            self._remember_project(project)

    def pipeline(self) -> Pipeline:
        with self.lock:
            if self.pipe is None:
                if str(getattr(config, "IMAGE_FAMILY", "zimage")).lower() == "qwen":
                    img_client = QwenComicClient()
                else:
                    img_client = ImageClient()
                self.pipe = Pipeline(
                    text_client=TextClient(),
                    image_client=img_client,
                    progress_cb=lambda m, v: None,
                    log_cb=lambda m: None,
                    stop_cb=lambda: bool(self.batch and self.batch.cancelled),
                )
            return self.pipe

    def save(self) -> None:
        with self.lock:
            if self.project is not None:
                self.project.save()


STATE = State()


# =============================================================================
# 后台任务
# =============================================================================

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.RLock()
JOB_RETENTION = 40          # 最多保留多少个已完成任务


class JobBusy(RuntimeError):
    """已有任务在运行。"""


def _running_job() -> dict[str, Any] | None:
    with JOBS_LOCK:
        for job in JOBS.values():
            if job["state"] == "running":
                return job
    return None


def attach_job(pipe: Pipeline, job_id: str, base: float = 0.02,
               span: float = 0.95) -> None:
    """把任务进度接到流水线上。"""
    def report(msg: str, value: float) -> None:
        update_job(job_id, msg, base + value * span)

    pipe.bind_reporter(report)


def new_job(kind: str, title: str) -> str:
    """注册一个后台任务。已有任务在跑时抛 JobBusy。"""
    busy = _running_job()
    if busy:
        raise JobBusy(f"已有任务正在运行：{busy['title']}，请等待完成或先取消")
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        # 清理过期任务
        finished = [k for k, v in JOBS.items() if v["state"] != "running"]
        for key in finished[:max(0, len(finished) - JOB_RETENTION)]:
            JOBS.pop(key, None)
        JOBS[job_id] = {
            "id": job_id, "kind": kind, "title": title, "state": "running",
            "progress": 0.0, "message": "准备中…", "result": None,
            "error": "", "started": time.time(), "ended": None,
        }
    return job_id


def update_job(job_id: str, message: str = "", progress: float | None = None) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        if message:
            job["message"] = message
        if progress is not None:
            job["progress"] = max(0.0, min(1.0, float(progress)))


def finish_job(job_id: str, result: Any = None, error: str = "") -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job["state"] = "failed" if error else "done"
        job["error"] = error
        job["result"] = result
        job["progress"] = 1.0
        job["ended"] = time.time()
        if error:
            job["message"] = error


def run_job(job_id: str, fn: Callable[[], Any]) -> None:
    """在后台线程里执行任务函数，函数返回的结果作为 job.result。"""

    def runner() -> None:
        try:
            result = fn()
            finish_job(job_id, result=result)
        except JobBusy as exc:
            finish_job(job_id, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            log.error("任务失败：%s", exc)
            log.error(traceback.format_exc(limit=4))
            finish_job(job_id, error=textutil.truncate(str(exc), 500) or "未知错误")

    threading.Thread(target=runner, name=f"job-{job_id}", daemon=True).start()


def job_payload(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job["id"], "kind": job["kind"], "title": job["title"],
        "state": job["state"], "progress": round(job["progress"], 4),
        "message": job["message"], "error": job["error"],
        "result": job["result"], "elapsed": round((job["ended"] or time.time())
                                                 - job["started"], 1),
    }


# =============================================================================
# 序列化辅助
# =============================================================================

def serialize_character(c: Character) -> dict[str, Any]:
    pose_sheet = c.pose_sheet or c.triple_view
    pose_status = c.pose_status if c.pose_sheet else (
        "已完成" if c.triple_view else c.pose_status)
    return {
        "id": c.id, "name": c.name, "gender": c.gender, "age": c.age,
        "age_anchor": prompts._normalize_age(c),
        "identity": c.identity, "personality": c.personality, "role": c.role,
        "is_extra": c.is_extra, "design": c.design, "keywords": list(c.keywords),
        "identity_card": c.identity_card(),
        "appearance": c.appearance, "aliases": list(c.aliases or []),
        "first_appearance": c.first_appearance,
        "hard_features": dict(c.hard_features or {}),
        "identity_tag": c.identity_tag,
        "main_visual": c.main_visual, "face_ref": c.face_ref,
        "views": dict(c.views or {}), "view_quality": dict(c.view_quality or {}),
        "pose_sheet": pose_sheet, "top_view": c.top_view,
        "top_source": c.top_source, "top_skeleton": c.top_skeleton,
        "top_note": c.top_note,
        "pose_status": pose_status, "top_status": c.top_status,
        # 旧字段保留，前端不再展示
        "triple_view": c.triple_view,
        "view_paths": dict(c.view_paths or {}), "active_look": c.active_look,
        "looks": c.look_options(), "extra_looks": dict(c.extra_looks or {}),
        "design_status": c.design_status, "image_status": c.image_status,
        "error": c.error, "has_reference": c.has_reference(),
    }


def serialize_scene(s: Scene) -> dict[str, Any]:
    return {"id": s.id, "name": s.name, "features": s.features,
            "atmosphere": s.atmosphere, "frequency": s.frequency,
            "objects": list(s.objects or []), "light": s.light}


def serialize_dialogue(d: Dialogue) -> dict[str, Any]:
    return {"speaker": d.speaker, "text": d.text, "tone": d.tone,
            "bubble_style": d.bubble_style, "kind": d.kind,
            "slot": d.slot, "tail": d.tail, "offscreen": d.offscreen,
            "size": d.size,
            "resolved_style": d.resolved_style()}


def serialize_shot(s: Shot, project: Project | None = None) -> dict[str, Any]:
    size = list(s.panel_size())
    label = s.panel_label()
    if project is not None:
        page = project.page_for_shot(s.id)
        if page is not None and page.mode == "page" \
                and 0 <= s.panel_index < len(page.cells):
            try:
                size = list(pagelayout.panel_generation_size(page.cells[s.panel_index], page))
                label = f"P{page.index}-{s.panel_index + 1} {size[0]}×{size[1]}"
            except Exception:  # noqa: BLE001
                pass
    return {
        "id": s.id, "index": s.index, "shot_type": s.shot_type,
        "camera": s.camera, "composition": s.composition,
        "lighting": s.lighting, "panel_scale": s.panel_scale,
        "transition": s.transition, "focus": s.focus,
        "action": s.action, "expression": s.expression,
        "char_sides": dict(s.char_sides or {}),
        "character_actions": dict(s.character_actions or {}),
        "time_of_day": s.time_of_day, "silent": s.silent, "hook": s.hook,
        "source_quote": s.source_quote, "light_dir": s.light_dir,
        # ---- 分镜内容分析 / 版式 ----
        "page_id": s.page_id, "panel_index": s.panel_index,
        "panel_role": s.panel_role, "panel_orientation": s.panel_orientation,
        "beat": s.beat, "pacing": s.pacing,
        "panel_emphasis": s.panel_emphasis, "caption": s.caption,
        "layout_hint": s.layout_hint,
        "characters": list(s.characters), "scene": s.scene,
        "description": s.description, "mood": s.mood,
        "source_text": s.source_text,
        "dialogues": [serialize_dialogue(d) for d in s.dialogues],
        "image_path": s.image_path, "raw_image_path": s.raw_image_path,
        "prop_image": s.prop_image,
        "pose_image": s.pose_image,
        "pose_skeleton": s.pose_skeleton,
        "pose_draft": s.pose_draft,
        "bubble_layout": list(s.bubble_layout or []),
        "bubble_plan": list(s.bubble_plan or []),
        "status": s.status, "error": s.error, "skip": s.skip,
        "attempts": s.attempts, "seed": s.seed,
        "reference_count": len(s.reference_paths or []),
        # 出图自检与自动返修
        "quality_score": s.quality_score,
        "quality_issues": list(s.quality_issues or []),
        "image_note": s.image_note,
        # 条漫画幅：0/0 表示按镜头类型自动分档
        "panel_w": s.panel_w, "panel_h": s.panel_h,
        "panel_size": size,
        "panel_label": label,
    }


def serialize_project(project: Project, full: bool = True) -> dict[str, Any]:
    issues = storyboard_mod.validate(project) if project.shots else []
    try:
        issues = list(issues) + director_mod.validate(project)
    except Exception:  # noqa: BLE001
        pass
    style_meta = stylelib.style_meta(project.style_id) if project.style_id else {}
    style_tone = project.style_tone or (
        stylelib.tone_for(project.style_id, f"{project.summary} {project.style_detail}")
        if project.style_id else "")
    auto_trigger = (stylelib.trigger_phrase(
        project.style_id, style_tone,
        f"{project.summary} {project.style_detail}") if project.style_id else "")
    style_trigger = (project.style_trigger
                     if bool(getattr(project, "style_trigger_custom", False)) and project.style_trigger
                     else auto_trigger)
    # 旧工程的触发词可能是"家族 + 阶调"的老格式；非手改时统一回填为新格式，
    # 让界面和提示词都真正带上该风格的 traits。
    if project.style_id and not bool(getattr(project, "style_trigger_custom", False)):
        project.style_trigger = auto_trigger
    data: dict[str, Any] = {
        "id": project.id, "title": project.title,
        "source_filename": project.source_filename,
        "chapter_no": project.chapter_no,
        "chapter_title": project.chapter_title,
        "tagline": project.tagline,
        "next_teaser": project.next_teaser,
        "author": project.author,
        "novel_length": len(project.novel_text or ""),
        "summary": project.summary, "plotline": project.plotline,
        "key_points": list(project.key_points),
        "style": project.style, "style_detail": project.style_detail,
        "core_task": project.core_task,
        "visual_anchors": list(project.visual_anchors or []),
        "continuous_cells": list(project.continuous_cells or []),
        "continuous_storyboard": project.continuous_storyboard,
        "refine_status": project.refine_status,
        "refine_digest": dict(project.refine_digest or {}),
        "refine_meta": dict(project.refine_meta or {}),
        # ---- handraw-style 绘制风格 ----
        "style_id": project.style_id,
        "style_color_ids": list(project.style_color_ids or []),
        "style_auto": bool(project.style_auto),
        "style_rationale": project.style_rationale,
        "style_ref_path": project.style_ref_path,
        "style_tone": style_tone,
        "style_tone_meta": stylelib.tone_meta(style_tone) if style_tone else {},
        "style_trigger": style_trigger,
        "style_trigger_custom": bool(getattr(project, "style_trigger_custom", False)),
        "style_medium": stylelib.style_medium(style_meta) if style_meta else "",
        "style_comic_features": stylelib.comic_feature_words(style_meta) if style_meta else [],
        "style_meta": style_meta,
        # ---- 分镜形态 / 分格页 ----
        "render_mode": project.render_mode or config.render_mode(),
        "render_modes": [
            {"id": "webtoon", "name": "原条漫", "desc": "一格一张图，竖屏无缝长条（旧行为）"},
            {"id": "page", "name": "分镜页", "desc": "2~6 格拼成一页，按内容选 SB-* 版式"},
            {"id": "hybrid", "name": "混排", "desc": "条漫长格 + 分镜页，按内容自动分流"},
        ],
        "page_plan_note": project.page_plan_note,
        "pages": pagelayout.page_plan_payload(project),
        "page_summary": pagelayout.summary(project),
        "output_dir": project.output_dir,
        "output_folder_name": project.output_folder_name,
        "zip_path": project.zip_path,
        "statistics": project.statistics(),
        # 顺序提示：主角还没有主视图/三视图时，25 格与出图缺身份锁。
        # 只统计真正出现在分镜里的角色，避免"只被提到、没出场"的角色误报。
        "characters_missing_images": [
            c.name for c in project.characters
            if not c.is_extra and not (c.main_visual or c.triple_view)
            and (not project.shots or any(c.name in s.characters for s in project.shots))
        ],
        # 分镜质量自检（台词是否被抄进描述、节奏/情绪/场景是否退化…），界面用来提示
        "storyboard_issues": issues,
        "created_at": project.created_at, "updated_at": project.updated_at,
    }
    if full:
        data["novel_text"] = project.novel_text
        data["characters"] = [serialize_character(c) for c in project.characters]
        data["scenes"] = [serialize_scene(s) for s in project.scenes]
        data["shots"] = [serialize_shot(s, project) for s in project.shots]
        data["grid25_image"] = project.grid25_image
        data["grid25_image_exists"] = bool(
            project.grid25_image and Path(project.grid25_image).exists())
        data["grid25_shots"] = list(project.grid25_shots or [])
        data["grid25_status"] = project.grid25_status
        data["pose_count"] = sum(1 for s in project.shots
                                 if s.pose_skeleton or s.pose_image)
        data["pose_total"] = len(project.shots)
    else:
        data["character_count"] = len(project.characters)
        data["scene_count"] = len(project.scenes)
        data["shot_count"] = len(project.shots)
    return data


def _safe_path(path: str) -> Path:
    """把前端传来的路径解析为绝对路径，并限制在允许的目录内。"""
    if not path:
        raise HTTPException(status_code=400, detail="缺少 path 参数")
    p = Path(path)
    if not p.is_absolute():
        p = (config.BASE_DIR / p).resolve()
    else:
        p = p.resolve()
    allowed_roots = [config.DATA_DIR.resolve(), config.OUTPUT_ROOT.resolve(),
                     config.BASE_DIR.resolve()]
    if not any(str(p).startswith(str(root)) for root in allowed_roots):
        raise HTTPException(status_code=403, detail="不允许访问该路径")
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return p


# =============================================================================
# 基础接口
# =============================================================================

@app.get("/api/studio/catalog")
def api_studio_catalog() -> dict[str, Any]:
    """创作工坊：365 风格 / 36 主题色 / 167 版式（按用途分组）。"""
    return studio_mod.catalog()


@app.get("/api/studio/history")
def api_studio_history() -> dict[str, Any]:
    return {"items": studio_mod.history(30)}


@app.get("/api/studio/stickers")
def api_studio_stickers(image: str = Query(...)) -> Response:
    """把某次表情包的每一格透明 PNG 打成一个 zip 下载。"""
    import io
    import zipfile
    root = (Path(config.OUTPUT_ROOT) / "创作工坊").resolve()
    p = Path(image)
    if not p.is_absolute():
        p = root / p
    try:
        p = p.resolve()
        p.relative_to(root)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="非法的作品路径") from exc
    meta_path = p.parent / "meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="没有找到该作品的 meta.json")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"读取作品信息失败：{exc}") from exc
    stickers = [Path(x) for x in (meta.get("stickers") or []) if Path(x).exists()]
    if not stickers:
        raise HTTPException(status_code=404,
                            detail="这次作品还没有单格透明图，请重新生成一次表情包")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        n = 0
        for sp in stickers:
            try:
                sp = sp.resolve()
                sp.relative_to(root)
            except Exception:  # noqa: BLE001
                continue
            n += 1
            z.write(sp, arcname=f"emoji_sticker_{n:02d}.png")
    data = buf.getvalue()
    filename = f"emoji_stickers_{p.parent.name}.zip"
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
    })


@app.post("/api/studio/upload-ref")
async def api_studio_upload_ref(file: UploadFile = File(...)) -> dict[str, Any]:
    """创作工坊参考图：上传后去背景，作为 img2img/Edit 底图，保留主体结构。"""
    import uuid as _uuid
    from PIL import Image as _Img
    from comicgen import bgremove
    out = Path(config.OUTPUT_ROOT) / "创作工坊" / "refs"
    out.mkdir(parents=True, exist_ok=True)
    raw = out / f"ref_{_uuid.uuid4().hex[:10]}.png"
    raw.write_bytes(await file.read())
    try:
        _Img.open(raw).convert("RGB").save(raw, format="PNG")
    except Exception as exc:  # noqa: BLE001
        raw.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"参考图无法读取：{exc}") from exc
    nobg = out / f"{raw.stem}_nobg.png"
    path = bgremove.remove_background(raw, nobg, mode="white")
    return {"path": str(path), "message": "参考图已上传并去背景；生成时会作为参考底图"}


@app.post("/api/studio/generate")
def api_studio_generate(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload or {}
    if not str(data.get("topic") or "").strip():
        raise HTTPException(status_code=400, detail="请先填写主题")
    try:
        job_id = new_job("studio", f"创作工坊 · {data.get('mode') or 'poster'}")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    def work() -> dict[str, Any]:
        update_job(job_id, "正在准备创作…", 0.08)
        meta = studio_mod.generate(
            data, progress=lambda m: update_job(job_id, m, 0.65))
        update_job(job_id, "完成", 1.0)
        return {"meta": meta, "items": studio_mod.history(30)}

    run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/studio/rewrite-preview")
def api_studio_rewrite_preview(payload: dict[str, Any]) -> dict[str, Any]:
    """只做语义编译，不出图；用于界面预览改写结果。"""
    data = payload or {}
    if not str(data.get("topic") or "").strip():
        raise HTTPException(status_code=400, detail="请先填写主题")
    try:
        return studio_mod.preview_prompt(data)
    except Exception as exc:  # noqa: BLE001
        log.exception("提示词编译失败：%s", exc)
        raise HTTPException(status_code=500, detail=f"提示词编译失败：{exc}") from exc


_SETTINGS_FILE = Path(config.DATA_DIR) / "settings.json"


def _load_family_setting() -> None:
    try:
        raw = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        fam = str(raw.get("image_family") or "").lower()
        if fam in ("zimage", "qwen"):
            config.IMAGE_FAMILY = fam
    except Exception:
        pass


def _save_family_setting(fam: str) -> None:
    try:
        _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SETTINGS_FILE.write_text(json.dumps({"image_family": fam}, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    except Exception:
        pass


_load_family_setting()


@app.get("/api/model/family")
def api_model_family() -> dict[str, Any]:
    return {"family": str(getattr(config, "IMAGE_FAMILY", "zimage")),
            "options": [
                {"id": "zimage", "name": "Z-Image-Turbo",
                 "desc": "8GB 稳定；ControlNet 姿态；无真正角色参考"},
                {"id": "qwen", "name": "Qwen-Image-2.1 Turbo",
                 "desc": "参考图 Edit 锁角色/服装；8GB 需先卸载 Z-Image；速度较慢"},
            ]}


@app.post("/api/model/family")
def api_model_family_set(payload: dict[str, Any]) -> dict[str, Any]:
    fam = str(payload.get("family") or "").lower()
    if fam not in ("zimage", "qwen"):
        raise HTTPException(status_code=400, detail="未知模型族")
    config.IMAGE_FAMILY = fam
    try:
        STATE.pipe = None
    except Exception:
        pass
    _save_family_setting(fam)
    return {"message": f"已切换为 {fam}", "family": fam}


@app.get("/api/config")
def api_config() -> dict[str, Any]:
    webtoon = bool(getattr(config, "WEBTOON_MODE", False))
    if webtoon:
        # 条漫模式：画幅按镜头类型分档，只列实际会用到的几种
        seen: list[tuple[int, int]] = []
        for st in config.SHOT_TYPES:
            size = config.webtoon_panel_size(st)
            if size not in seen:
                seen.append(size)
        size_presets = ["自动（按镜头分档）"] + [f"{w}×{h}" for w, h in seen]
        default_size = "自动（按镜头分档）"
    else:
        size_presets = ["512×512", "768×768", "1024×1024", "1344×1344"]
        default_size = f"{config.IMAGE_SIZE[0]}×{config.IMAGE_SIZE[1]}"
    family = str(getattr(config, "IMAGE_FAMILY", "zimage")).lower()
    qwen = family == "qwen"
    image_workflow = "qwen_image_2.1_turbo" if qwen else config.COMFY_WORKFLOW_NAME
    image_model_display = "Qwen-Image-2.1 Turbo" if qwen else "Z-Image-Turbo"
    return {
        "app_title": config.APP_TITLE,
        "app_subtitle": config.APP_SUBTITLE,
        "stages": config.STAGE_TITLES,
        "style_presets": config.STYLE_PRESETS,
        # 内置画风按家族分组（日系/国风与华语/欧美/欧洲/独立/媒介/韩漫），
        # 前端下拉用 optgroup 展示，避免 47 个选项平铺成一长条。
        "style_preset_groups": config.STYLE_PRESET_GROUPS,
        # ---- handraw-style 绘制风格库 ----
        "handraw": {
            "enabled": bool(getattr(config, "HANDRAW_ENABLED", True)),
            "available": stylelib.available(),
            "styles": len(stylelib.style_entries()),
            "colors": len(stylelib.color_entries()),
            "layouts": len(stylelib.layout_entries()),
            "style_ref_enabled": bool(getattr(config, "HANDRAW_STYLE_REF_ENABLED", True)),
            "default_style": config.HANDRAW_DEFAULT_STYLE,
            "default_colors": config.HANDRAW_DEFAULT_COLORS,
        },
        "render_mode": config.render_mode(),
        "page_canvas_width": config.PAGE_CANVAS_WIDTH,
        "page_max_panels": config.PAGE_MAX_PANELS,
        "page_caption_enabled": bool(config.PAGE_CAPTION_ENABLED),
        "shot_beats": list(config.SHOT_BEATS),
        "pacings": list(config.PACING_OPTIONS),
        "emphasis_options": list(config.EMPHASIS_OPTIONS),
        "workflow_presets": [
            {"id": "auto", "name": "自动（有画风参考用 style_color）"},
            {"id": "base", "name": "基础文生图 image_z_image_turbo_int8"},
            {"id": "style_color", "name": "画风色彩迁移（只迁色，推荐）"},
            {"id": "style_ref", "name": "Z-Image Omni 参考图（实验）"},
            {"id": "style_img2img", "name": "参考图 img2img（兜底）"},
        ],
        "workflow_preset": config.COMFY_WORKFLOW_PRESET,
        "workflow_style": config.COMFY_WORKFLOW_STYLE_NAME,
        "workflow_style_ref": config.COMFY_WORKFLOW_STYLE_REF_NAME,
        "shot_types": config.SHOT_TYPES,
        "shot_angles": config.SHOT_ANGLES,
        "compositions": config.SHOT_COMPOSITIONS,
        "lightings": config.SHOT_LIGHTINGS,
        "panel_scales": config.PANEL_SCALE_OPTIONS,
        "transitions": config.TRANSITION_TYPES,
        "bubble_slots": config.BUBBLE_SLOTS,
        "moods": config.MOOD_OPTIONS,
        "bubble_styles": config.BUBBLE_STYLES,
        "bubble_position": config.BUBBLE_DEFAULT_POSITION,
        "bubble_enabled": config.BUBBLE_ENABLED_BY_DEFAULT,
        "size_presets": size_presets,
        "default_size": default_size,
        # ---- 条漫（竖幅分格 + 无缝长条）----
        "webtoon_mode": webtoon,
        "webtoon_panel_width": config.WEBTOON_PANEL_WIDTH,
        "webtoon_panel_sizes": {k: list(v)
                                for k, v in config.WEBTOON_PANEL_SIZE_MAP.items()},
        "webtoon_panel_default": list(config.WEBTOON_PANEL_SIZE_DEFAULT),
        "webtoon_strip_max_height": config.WEBTOON_STRIP_MAX_HEIGHT,
        "narration_plain": bool(getattr(config, "NARRATION_PLAIN", True)),
        "narration_style": getattr(config, "NARRATION_STYLE", "caption"),
        "image_draw_bubbles": bool(getattr(config, "IMAGE_DRAW_BUBBLES", False)),
        "image_auto_repair": bool(getattr(config, "IMAGE_AUTO_REPAIR", True)),
        "auto_pose_shots": str(getattr(config, "AUTO_POSE_SHOTS", "key")),
        "image_auto_repair_retries": int(getattr(config, "IMAGE_AUTO_REPAIR_RETRIES", 2)),
        "image_quality_pass_score": int(getattr(config, "IMAGE_QUALITY_PASS_SCORE", 82)),
        "auto_pipeline_enabled": bool(getattr(config, "AUTO_PIPELINE_ENABLED", True)),
        "auto_export_after_generate": bool(getattr(config, "AUTO_EXPORT_AFTER_GENERATE", True)),
        "panel_border": bool(getattr(config, "PANEL_BORDER_ENABLED", True)),
        "panel_gutter": int(getattr(config, "PANEL_GUTTER", 0)),
        "director_enabled": bool(getattr(config, "DIRECTOR_ENABLED", True)),
        "size_multiple": config.IMAGE_DIMENSION_MULTIPLE,
        "poll_interval": config.POLL_INTERVAL_MS,
        "output_root": str(config.OUTPUT_ROOT),
        "default_output_name": config.default_output_name(),
        "api_key_status": config.api_key_status(),
        "text_key_status": config.text_api_key_status(),
        "api_host": config.COMFY_API_HOST,
        "text_host": config.TEXT_API_HOST,
        "image_host": config.COMFY_API_HOST,
        "image_backend": config.IMAGE_BACKEND,
        "text_needs_key": config.TEXT_API_NEEDS_KEY,
        "text_model": config.TEXT_MODEL,
        "text_model_display": config.TEXT_MODEL_DISPLAY,
        "text_model_path": config.TEXT_MODEL_PATH,
        "image_model": image_workflow,
        "image_model_display": image_model_display,
        "image_workflow": image_workflow,
        "image_family": family,
        "image_workflow_style": ("qwen_image_2.1_turbo_t2i_local" if qwen
                                 else config.COMFY_WORKFLOW_STYLE_NAME),
        "image_model_path": image_workflow,
        "image_workflow_dir": str(config.COMFY_WORKFLOW_DIR),
        "protocols": [],                 # 旧字段：ComfyUI 不再有"协议"概念
        "max_reference_images": config.MAX_REFERENCE_IMAGES,
        "min_novel_length": config.MIN_NOVEL_LENGTH,
        "max_novel_length": config.MAX_NOVEL_LENGTH,
        "sample_novel_length": len(config.SAMPLE_NOVEL),
    }


@app.get("/api/sample")
def api_sample() -> dict[str, Any]:
    """返回内置示例小说。"""
    return {"text": config.SAMPLE_NOVEL, "length": len(config.SAMPLE_NOVEL)}


@app.get("/api/rules")
def api_rules() -> dict[str, Any]:
    """规则库自检（rules/ 目录）：给界面/命令行确认规则文件是否可用。"""
    from comicgen import rules as rules_mod
    return rules_mod.self_check()


# =============================================================================
# 提示词工作台（prompts/ 目录 · 当前工程可编辑版本）
# =============================================================================

@app.get("/api/prompts/overview")
def api_prompts_overview() -> dict[str, Any]:
    """各步骤提示词数量与已自定义数量（界面上的小徽标用）。"""
    return {"steps": prompt_studio.overview(STATE.get_project())}


@app.get("/api/prompts")
def api_prompts(step: str = Query("1")) -> dict[str, Any]:
    """某个步骤下的提示词目录（不渲染正文，避免一次返回太多）。"""
    try:
        return prompt_studio.catalog(STATE.get_project(), step)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/prompts/detail")
def api_prompt_detail(id: str = Query(...), item: str = Query("")) -> dict[str, Any]:
    """渲染一条提示词的当前工程版本；有覆盖时返回覆盖后的文本。"""
    try:
        return prompt_studio.detail(STATE.get_project(), id, item)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("渲染提示词失败：%s", exc)
        raise HTTPException(status_code=500, detail=f"渲染提示词失败：{exc}") from exc


@app.post("/api/prompts/save")
def api_prompt_save(payload: dict[str, Any]) -> dict[str, Any]:
    """保存用户编辑后的提示词（按工程 / 文段 / 角色 / 分镜分别保存）。"""
    project = STATE.get_project()
    prompt_id = str(payload.get("id", "") or "")
    item = str(payload.get("item", "") or "")
    try:
        result = prompt_studio.save(
            project, prompt_id, item,
            system=payload.get("system"),
            user=payload.get("user"),
            text=payload.get("text"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    STATE.save()
    return result


@app.post("/api/prompts/reset")
def api_prompt_reset(payload: dict[str, Any]) -> dict[str, Any]:
    """清除一条提示词覆盖，恢复按当前工程实时渲染的默认版本。"""
    project = STATE.get_project()
    try:
        result = prompt_studio.reset(
            project, str(payload.get("id", "") or ""),
            str(payload.get("item", "") or ""))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    STATE.save()
    return result


# =============================================================================
# 规则与工具工作台（rules/ + tools/）
# =============================================================================

@app.get("/api/resources")
def api_resources(step: str = Query("2")) -> dict[str, Any]:
    """某个步骤需要的规则文档 / 运行期 JSON / 工具脚本。"""
    try:
        return resource_studio.catalog(step)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/resources/detail")
def api_resource_detail(id: str = Query(...)) -> dict[str, Any]:
    try:
        return resource_studio.detail(id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/resources/save")
def api_resource_save(payload: dict[str, Any]) -> dict[str, Any]:
    """保存规则 / 文档 / 工具脚本（写回磁盘，保存前校验 JSON / Python 语法）。"""
    try:
        return resource_studio.save(
            str(payload.get("id", "") or ""),
            str(payload.get("content", "") or ""))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/resources/restore")
def api_resource_restore(payload: dict[str, Any]) -> dict[str, Any]:
    """恢复该文件上次保存前的版本。"""
    try:
        return resource_studio.restore(str(payload.get("id", "") or ""))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# =============================================================================
# 工作流工作台（comfy_workflows/ + data/workflows/）
# =============================================================================

@app.get("/api/workflows")
def api_workflows() -> dict[str, Any]:
    return workflow_studio.catalog(STATE.get_project())


@app.get("/api/workflows/detail")
def api_workflow_detail(id: str = Query(...)) -> dict[str, Any]:
    try:
        return workflow_studio.detail(STATE.get_project(), id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("读取工作流失败：%s", exc)
        raise HTTPException(status_code=500, detail=f"读取工作流失败：{exc}") from exc


@app.post("/api/workflows/save")
def api_workflow_save(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return workflow_studio.save_graph(
            STATE.get_project(), str(payload.get("id", "") or ""),
            payload.get("nodes") or [], payload.get("links") or [],
            payload.get("positions") or {})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/workflows/doc")
def api_workflow_doc(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return workflow_studio.save_doc(
            str(payload.get("id", "") or ""), str(payload.get("content", "") or ""))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/workflows/comfyui")
def api_workflow_comfyui() -> dict[str, Any]:
    return workflow_studio.comfyui_catalog()


@app.get("/api/workflows/node-types")
def api_workflow_node_types(q: str = "", limit: int = 120) -> dict[str, Any]:
    return workflow_studio.node_catalog(q, limit)


@app.post("/api/workflows/create")
def api_workflow_create(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return workflow_studio.create_workflow(
            STATE.get_project(), str(payload.get("name", "") or ""),
            mode=str(payload.get("mode", "duplicate") or "duplicate"),
            base_id=str(payload.get("base_id", "") or ""),
            graph_json=str(payload.get("graph_json", "") or ""),
            comfyui_name=str(payload.get("comfyui_name", "") or ""))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/workflows/delete")
def api_workflow_delete(payload: dict[str, Any]) -> dict[str, Any]:
    wid = str(payload.get("id", "") or "")
    try:
        result = workflow_studio.delete_workflow(wid)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project = STATE.get_project()
    bindings = (project.settings or {}).get("workflow_bindings") or {}
    if wid in bindings.values():
        project.settings["workflow_bindings"] = {
            k: v for k, v in bindings.items() if v != wid}
        STATE.save()
    return result


@app.post("/api/workflows/apply")
def api_workflow_apply(payload: dict[str, Any]) -> dict[str, Any]:
    project = STATE.get_project()
    try:
        result = workflow_studio.apply_workflow(
            project, str(payload.get("id", "") or ""),
            payload.get("targets") or [])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    STATE.save()
    return result


@app.post("/api/workflows/restore")
def api_workflow_restore(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return workflow_studio.restore_default(
            STATE.get_project(), str(payload.get("id", "") or ""))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# =============================================================================
# 绘制风格（handraw-style）
# =============================================================================

def _color_image_rel(color_id: str) -> str:
    path = Path(config.HANDRAW_IMAGE_DIR) / "colors" / f"{color_id}.webp"
    if not path.exists():
        return ""
    try:
        return path.resolve().relative_to(Path(config.BASE_DIR).resolve()).as_posix()
    except Exception:  # noqa: BLE001
        return str(path)


@app.get("/api/style/list")
def api_style_list(q: str = "", limit: int = 400) -> dict[str, Any]:
    """风格库目录（365 种风格 + 36 种主题色 + 当前工程的选择）。"""
    query = (q or "").strip().lower()
    styles: list[dict[str, Any]] = []
    for s in stylelib.style_entries():
        hay = " ".join(str(s.get(k, "")) for k in
                       ("number", "generation_name", "reference", "traits", "group"))
        if query and query not in hay.lower():
            continue
        sid = s["number"]
        meta = stylelib.style_meta(sid)
        styles.append({
            "id": sid,
            "name": s.get("generation_name", ""),
            "name_zh": meta.get("name_zh") or stylelib.style_name_zh(meta),
            "name_en": s.get("generation_name", ""),
            "reference": s.get("reference", ""),
            "reference_zh": meta.get("reference_zh", ""),
            "reference_display": meta.get("reference_display") or stylelib.style_reference_display(s),
            "group": s.get("group", ""),
            "group_zh": meta.get("group_zh") or stylelib.style_group_zh(s),
            "traits": stylelib.positive_traits(s.get("traits", ""), 70),
            "image": stylelib.style_image_rel(sid),
        })
        if len(styles) >= max(1, min(limit, 500)):
            break
    colors = [{
        "id": c["id"], "name": c.get("name_zh", ""),
        "name_en": c.get("name_en", ""),
        "category": c.get("category_zh", ""),
        "image": _color_image_rel(c["id"]),
    } for c in stylelib.color_entries()]
    tones = [{
        "id": t["id"], "name": t.get("name_zh", ""),
        "desc": t.get("desc_zh", ""), "category": t.get("category_zh", ""),
    } for t in stylelib.tone_entries()]
    project = STATE.get_project()
    current = stylelib.project_style(project) if project else {}
    return {
        "available": stylelib.available(),
        "styles": styles,
        "total": len(stylelib.style_entries()),
        "colors": colors,
        "tones": tones,
        "current": {
            "style_id": current.get("style_id", ""),
            "color_ids": current.get("color_ids", []),
            "auto": current.get("auto", True),
            "rationale": current.get("rationale", ""),
            "style": current.get("style", {}),
            "tone_id": current.get("tone_id", ""),
            "trigger": current.get("trigger", ""),
        },
        "render_mode": ((project.render_mode or config.render_mode())
                        if project else config.render_mode()),
    }


@app.post("/api/style/recommend")
def api_style_recommend(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """按当前故事内容重新推荐风格（AI 可用时由模型在候选里定夺）。"""
    data = payload or {}
    project = STATE.get_project()
    if not project.summary and not project.characters:
        raise HTTPException(status_code=400, detail="请先完成内容分析，再推荐风格")
    pipe = STATE.pipeline()
    use_llm = data.get("llm")
    try:
        info = pipe.recommend_style(project, force=True,
                                    use_llm=None if use_llm is None else bool(use_llm))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    STATE.save()
    return {"message": "已重新推荐绘制风格", "style": info,
            "project": serialize_project(project, full=False)}


@app.post("/api/style/set")
def api_style_set(payload: dict[str, Any]) -> dict[str, Any]:
    """手动指定风格 / 主题色（也可以只改主题色）。"""
    project = STATE.get_project()
    raw_style = payload.get("style_id")
    if raw_style in (None, ""):
        style_id = ""
    else:
        style_id = stylelib.normalize_style_id(raw_style)
        if not style_id and str(raw_style).strip():
            raise HTTPException(status_code=400, detail=f"未知风格编号：{raw_style}")
    colors = stylelib.parse_colors(payload.get("color_ids")
                                   if payload.get("color_ids") is not None
                                   else payload.get("colors"))
    old_style = project.style_id
    had_color_payload = ("color_ids" in payload) or ("colors" in payload)
    if payload.get("color_ids") == []:
        colors = []
    project.style_id = style_id
    if had_color_payload:
        project.style_color_ids = colors
    project.style_auto = bool(payload.get("auto", False))
    project.style_rationale = str(payload.get("rationale") or "手动选择")
    text = f"{project.summary} {project.style_detail}"
    forced_tone = str(payload.get("tone") or payload.get("tone_id") or "").strip()
    if not style_id:
        project.style_tone = ""
    elif forced_tone and stylelib.tone_meta(forced_tone):
        project.style_tone = forced_tone.upper()
    elif style_id and (style_id != old_style or not project.style_tone):
        project.style_tone = stylelib.tone_for(style_id, text)
    # 用户显式改了触发词才原样用；否则只要切了风格或换了阶调，就重算自动触发词，
    # 避免出现"阶调是剪影、触发词还写着暗沉"。
    raw_trigger = str(payload.get("trigger") or "").strip()
    project.style_trigger_custom = bool(raw_trigger)
    if raw_trigger:
        project.style_trigger = raw_trigger
    elif style_id and (style_id != old_style or forced_tone or not project.style_trigger):
        project.style_trigger = stylelib.trigger_phrase(style_id, project.style_tone, text)
    elif not style_id:
        project.style_trigger = ""
    try:
        project.style_ref_path = stylelib.style_reference_png(style_id) if style_id else ""
    except Exception:  # noqa: BLE001
        project.style_ref_path = ""
    project.touch()
    STATE.save()
    meta = stylelib.style_meta(style_id) if style_id else {}
    label = (f"#{style_id} {meta.get('generation_name', '')}" if style_id
             else "已清除 handraw 风格，回到内置画风")
    return {"message": f"绘制风格已更新：{label}",
            "style": stylelib.project_style(project),
            "project": serialize_project(project, full=False)}


# =============================================================================
# 分镜版式 / 分格页
# =============================================================================

@app.get("/api/layout/list")
def api_layout_list(category: str = "comic-storyboard",
                    panels: str = "") -> dict[str, Any]:
    """可用漫画分镜版式（SB-001–SB-071 + 原生 WB-*）。"""
    wanted = {int(x) for x in re.findall(r"\d+", panels or "")}
    out: list[dict[str, Any]] = []
    for item in stylelib.layout_entries():
        if wanted and int(item.get("panels") or 0) not in wanted:
            continue
        out.append({
            "id": item["id"], "name": item.get("name", ""),
            "name_en": item.get("name_en", ""),
            "panels": item.get("panels"),
            "geometry": item.get("geometry", ""),
            "beats": item.get("beats", []),
            "keywords": item.get("keywords", []),
            "fit": item.get("fit", {}),
            "native": bool(item.get("native")),
            "approx": bool(item.get("approx")),
            "image": item.get("image_rel", ""),
            "prompt": stylelib.layout_prompt(item["id"]),
        })
    project = STATE.get_project()
    return {"layouts": out, "total": len(out),
            "current": pagelayout.page_plan_payload(project) if project else [],
            "summary": pagelayout.summary(project) if project else {}}


@app.post("/api/layout/plan")
def api_layout_plan(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """重新分页 + 选版式（AI 优先，失败自动退回本地规划）。"""
    data = payload or {}
    project = STATE.get_project()
    if not project.shots:
        raise HTTPException(status_code=400, detail="还没有分镜，请先拆解分镜")
    mode = str(data.get("mode") or project.render_mode or config.render_mode()).lower()
    if mode not in ("webtoon", "page", "hybrid"):
        raise HTTPException(status_code=400, detail=f"未知分镜形态：{mode}")
    project.render_mode = mode
    try:
        job_id = new_job("layout_plan", "规划分镜版式")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    pipe = STATE.pipeline()
    use_llm = data.get("use_llm")
    force = bool(data.get("force", True))

    def work() -> dict[str, Any]:
        update_job(job_id, "正在按内容规划分页与版式…", 0.1)
        pipe.plan_pages(project, use_llm=None if use_llm is None else bool(use_llm),
                        force=force)
        # 重新规划后不要立刻拿旧分镜图合成：旧图是上一版风格/版式的，
        # 先让页面显示"待生成"，等新图出来再按新 cells 合成。
        if bool(data.get("compose", False)):
            update_job(job_id, "正在合成已有页面…", 0.7)
            try:
                pagelayout.compose_all_pages(project)
            except Exception as exc:  # noqa: BLE001
                log.warning("规划后合成页面失败：%s", exc)
        STATE.save()
        update_job(job_id, "分页规划完成", 0.95)
        return {"project": serialize_project(project, full=True),
                "summary": pagelayout.summary(project)}

    run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/project/render-mode")
def api_render_mode(payload: dict[str, Any]) -> dict[str, Any]:
    """切换分镜形态：原条漫 / 分镜页 / 混排。"""
    mode = str(payload.get("mode") or "").strip().lower()
    if mode not in ("webtoon", "page", "hybrid"):
        raise HTTPException(status_code=400, detail=f"未知分镜形态：{mode}")
    project = STATE.get_project()
    old = (project.render_mode or config.render_mode())
    project.render_mode = mode
    project.clear_pages()
    if mode != "webtoon" and project.shots:
        try:
            STATE.pipeline().plan_pages(project, use_llm=False, force=True)
            # 不在切形态时拿旧图合成：旧图按旧 cells 出，先显示待生成。
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    else:
        project.page_plan_note = "原条漫模式：一格一张图，竖屏无缝长条"
    project.touch()
    STATE.save()
    return {"message": f"分镜形态已切换：{old} → {mode}",
            "project": serialize_project(project, full=True)}


@app.post("/api/page/set-layout")
def api_page_set_layout(payload: dict[str, Any]) -> dict[str, Any]:
    """手动更换某一页的版式（格数必须一致）。"""
    project = STATE.get_project()
    page = project.page_by_id(str(payload.get("page_id") or ""))
    if page is None:
        raise HTTPException(status_code=404, detail="找不到该页")
    layout_id = str(payload.get("layout_id") or "")
    try:
        new_page = pagelayout.relayout(project, page, layout_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    project.touch()
    STATE.save()
    return {"message": f"第 {new_page.index} 页已换成 {layout_id} {new_page.layout_name}",
            "project": serialize_project(project, full=True)}


@app.post("/api/images/clear")
def api_images_clear(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    只清图片，不动文本。

    scope:
      - shot / shots：指定 id 或全部；清成品/原图/气泡计划，页面标脏
      - page / pages：清页图
      - character / characters：清主视觉、三视图、所有造型图
      - all：以上全部
    """
    data = payload or {}
    scope = str(data.get("scope") or "all").strip().lower()
    target_id = str(data.get("id") or "").strip()
    delete_files = bool(data.get("delete_files", True))
    project = STATE.get_project()

    def _drop(path: str) -> None:
        p = str(path or "").strip()
        if not p or not delete_files:
            return
        try:
            f = Path(p).resolve()
            roots = (Path(config.DATA_DIR).resolve(), Path(config.OUTPUT_ROOT).resolve())
            if any(f == r or r in f.parents for r in roots):
                f.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    def clear_shot(shot) -> None:
        for p in (shot.image_path, shot.raw_image_path):
            _drop(p)
        shot.image_path = ""
        shot.raw_image_path = ""
        shot.status = "待生成"
        shot.error = ""
        shot.quality_issues = []
        shot.bubble_plan = []
        shot.image_note = ""
        shot.attempts = 0
        page = project.page_by_id(shot.page_id) if shot.page_id else None
        if page is not None:
            _drop(page.image_path)
            page.image_path = ""
            page.status = "待生成"
            page.error = ""

    def clear_page(page) -> None:
        _drop(page.image_path)
        page.image_path = ""
        page.status = "待生成"
        page.error = ""

    def clear_character(c) -> None:
        for p in [c.main_visual, c.face_ref, c.pose_sheet, c.top_view, c.triple_view,
                  *(c.views or {}).values(),
                  *(c.view_paths or {}).values()]:
            _drop(p)
        c.main_visual = ""
        c.face_ref = ""
        c.views = {}
        c.view_quality = {}
        c.pose_sheet = ""
        c.pose_status = "待生成"
        c.top_status = "待生成"
        c.triple_view = ""
        c.view_paths = {}
        for look in (c.extra_looks or {}).values():
            if not isinstance(look, dict):
                continue
            for key in ("main_visual", "pose_sheet", "top_view", "triple_view"):
                _drop(look.get(key, ""))
                look[key] = ""
        c.image_status = "待生成"
        c.error = ""

    n_shots = n_pages = n_chars = 0
    if scope in ("shot", "shots", "all"):
        for s in project.shots:
            if target_id and str(s.id) != target_id:
                continue
            clear_shot(s)
            n_shots += 1
    if scope in ("page", "pages", "all"):
        for pg in project.pages:
            if target_id and str(pg.id) != target_id:
                continue
            clear_page(pg)
            n_pages += 1
    if scope in ("character", "characters", "all"):
        for c in project.characters:
            if target_id and str(c.id) != target_id:
                continue
            clear_character(c)
            n_chars += 1
    project.touch()
    STATE.save()
    return {"message": f"已清空：分镜图 {n_shots} 格、页图 {n_pages} 页、角色图 {n_chars} 个；文本未改动",
            "counts": {"shots": n_shots, "pages": n_pages, "characters": n_chars},
            "project": serialize_project(project, full=True)}


@app.post("/api/page/compose")
def api_page_compose(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """按当前版式重新合成页面（只合成缺图/过期页）。"""
    data = payload or {}
    project = STATE.get_project()
    page_id = str(data.get("page_id") or "").strip()
    force = bool(data.get("force", False))
    if page_id:
        page = project.page_by_id(page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="找不到该页")
        if force:
            page.image_path = ""
            page.status = "待生成"
        try:
            pagelayout.compose_page(project, page)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        stats = {"pages": 1, "done": int(page.status == "已合成"),
                 "pending": int(page.status == "待生成"),
                 "failed": int(page.status == "失败")}
    else:
        stats = pagelayout.compose_all_pages(project, only_pending=not force)
    project.touch()
    STATE.save()
    return {"message": f"页面合成：{stats['done']}/{stats['pages']} 页就绪",
            "stats": stats,
            "pages": pagelayout.page_plan_payload(project),
            "summary": pagelayout.summary(project)}


@app.get("/api/service/status")
def api_service_status() -> dict[str, Any]:
    """检查密钥、文本模型、图像模型状态。"""
    family = str(getattr(config, "IMAGE_FAMILY", "zimage")).lower()
    default_workflow = ("qwen_image_2.1_turbo" if family == "qwen"
                        else config.COMFY_WORKFLOW_NAME)
    out: dict[str, Any] = {
        # ComfyUI 本地服务默认不鉴权，这里恒为"可用"；只有套了反代才需要密钥
        "key": {"ok": True, "message": config.api_key_status()},
        "text_key": {"ok": bool(config.text_api_key()),
                     "message": config.text_api_key_status()},
        "text": {"ok": False, "message": "未检查"},
        "image": {"ok": False, "message": "未检查"},
        "image_model": {},
        "image_family": family,
        "protocol": "",
        "workflow": default_workflow,
    }

    try:
        client = TextClient()
        ok, msg = client.health()
        out["text"] = {"ok": ok, "message": msg}
        st = client.text_model_status()
        out["text_model"] = {
            "loaded": st.get("loaded") or [], "serving": st.get("serving") or [],
            "active": st.get("active_model"), "context": st.get("context_length"),
            "path": config.TEXT_MODEL_PATH,
            "local": client.is_local,
        }
    except Exception as exc:  # noqa: BLE001
        out["text"] = {"ok": False, "message": textutil.truncate(str(exc), 300)}

    try:
        ic = QwenComicClient() if family == "qwen" else ImageClient()
        ok2, msg2 = ic.health()
        out["image"] = {"ok": ok2, "message": msg2}
        out["protocol"] = config.IMAGE_BACKEND
        st2 = ic.image_model_status()
        if isinstance(st2, dict):
            out["image_model"] = {
                "loaded": st2.get("loaded"), "workflow": st2.get("workflow"),
                "source": st2.get("source"), "engine": st2.get("engine"),
                "version": st2.get("version"), "device": st2.get("device"),
                "nodes": st2.get("nodes"), "steps": st2.get("steps"),
                "cfg": st2.get("cfg"), "sampler_name": st2.get("sampler_name"),
                "models": st2.get("models"),
                "supports_reference_images": st2.get("supports_reference_images"),
                "reference_slots": st2.get("reference_slots"),
                "color_matched": st2.get("color_matched"),
                "supports_negative_prompt": st2.get("supports_negative_prompt"),
                "negative_enabled": st2.get("negative_enabled"),
                "negative_effective": st2.get("negative_effective"),
                "host": st2.get("host"), "error": st2.get("error"),
            }
            out["workflow"] = st2.get("workflow") or default_workflow
    except Exception as exc:  # noqa: BLE001
        out["image"] = {"ok": False, "message": textutil.truncate(str(exc), 300)}
    return out


@app.get("/api/logs")
def api_logs(limit: int = 400) -> dict[str, Any]:
    return {"logs": log_lines(limit)}


@app.post("/api/service/load-text-model")
def api_load_text_model(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """加载服务端文本模型。"""
    path = str((payload or {}).get("model_path") or config.TEXT_MODEL_PATH)
    try:
        job_id = new_job("load_text_model", "加载文本模型")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    client = TextClient()

    def work() -> dict[str, Any]:
        update_job(job_id, f"正在加载文本模型：{path}", 0.1)
        ok, msg = client.load_text_model(path)
        if not ok:
            raise RuntimeError(msg)
        return {"message": msg}

    run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/service/load-image-model")
def api_load_image_model(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """检查图像工作流是否就绪（ComfyUI 不需要手动加载模型）。"""
    family = str(getattr(config, "IMAGE_FAMILY", "zimage")).lower()
    if family == "qwen":
        workflow = "qwen_image_2.1_turbo"
    else:
        workflow = str((payload or {}).get("model_path")
                       or (payload or {}).get("workflow")
                       or config.COMFY_WORKFLOW_NAME).strip()
    try:
        job_id = new_job("check_workflow", "检查图像工作流")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    def work() -> dict[str, Any]:
        update_job(job_id, f"正在检查工作流：{workflow}", 0.1)
        if family == "qwen":
            ok, msg = QwenComicClient().health()
            if not ok:
                raise RuntimeError(msg)
            update_job(job_id, msg, 1.0)
            return {"message": msg}

        client = ImageClient()

        def on_progress(node: dict[str, Any]) -> None:
            msg = str(node.get("message") or "")
            try:
                pct = float(node.get("percent") or 0) / 100.0
            except (TypeError, ValueError):
                pct = 0.1
            update_job(job_id, msg or "检查中…", max(0.05, min(0.98, pct)))

        ok, msg = client.load_image_model(workflow, on_progress=on_progress)
        if not ok:
            raise RuntimeError(msg)
        return {"message": msg}

    run_job(job_id, work)
    return {"job_id": job_id}


@app.get("/api/service/models")
def api_models() -> dict[str, Any]:
    try:
        return {"models": ImageClient().list_models()}
    except Exception as exc:  # noqa: BLE001
        return {"models": [], "error": textutil.truncate(str(exc), 200)}


# =============================================================================
# 图片读取
# =============================================================================

@app.get("/api/image")
def api_image(path: str = Query(...), thumb: int = 0) -> Response:
    """读取项目内的图片（可选生成缩略图）。"""
    target = _safe_path(path)
    if not thumb:
        return FileResponse(target)
    try:
        img = Image.open(target).convert("RGB")
        img.thumbnail((max(64, thumb), max(64, thumb)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return Response(content=buf.getvalue(), media_type="image/jpeg")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"缩略图生成失败：{exc}") from exc


@app.get("/api/download")
def api_download(path: str = Query(...)) -> Response:
    """下载单个文件。"""
    target = _safe_path(path)
    return FileResponse(target, filename=target.name)


# =============================================================================
# 任务查询
# =============================================================================

@app.get("/api/job/{job_id}")
def api_job(job_id: str) -> dict[str, Any]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在或已过期")
        return job_payload(job)


@app.post("/api/job/{job_id}/cancel")
def api_job_cancel(job_id: str) -> dict[str, Any]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if STATE.batch is not None:
        STATE.batch.cancel()
    log.info("用户请求取消任务：%s", job["title"])
    return {"message": "已发送取消请求，正在停止…"}


# =============================================================================
# 工程
# =============================================================================

@app.get("/api/project")
def api_get_project(full: bool = True) -> dict[str, Any]:
    return serialize_project(STATE.get_project(), full=full)


@app.post("/api/project/new")
def api_new_project(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    title = str((payload or {}).get("title") or "未命名作品")
    project = Project(title=title, novel_text=str((payload or {}).get("text") or ""))
    project.save()
    STATE.set_project(project)
    log.info("已新建工程：%s", project.id)
    return serialize_project(project)


@app.get("/api/project/list")
def api_project_list() -> dict[str, Any]:
    items = []
    paths = [p for p in Project.list_projects()
             if re.fullmatch(r"[0-9a-f]{6,12}\.json", p.name)]
    for path in paths[:80]:
        try:
            project = Project.load(path)
            pose_path = next((s.pose_skeleton or s.pose_image
                              for s in project.shots
                              if s.pose_skeleton or s.pose_image), "")
            grid_image = project.grid25_image if (
                project.grid25_image and Path(project.grid25_image).exists()) else ""
            items.append({
                "file": str(path), "id": project.id, "title": project.title,
                "updated_at": project.updated_at,
                "characters": len(project.characters),
                "shots": len(project.shots),
                "done": project.progress()[0],
                "anchors": len(project.visual_anchors or []),
                "cells": len(project.continuous_cells or []),
                "refine_status": project.refine_status,
                "core_task": textutil.truncate(project.core_task or "", 80),
                "digest": dict(project.refine_digest or {}),
                "grid25_count": len(project.grid25_shots or []),
                "grid25_status": project.grid25_status,
                "grid25_image": grid_image,
                "pose_count": sum(1 for s in project.shots
                                  if s.pose_skeleton or s.pose_image),
                "pose_thumb": pose_path,
                "novel_length": len(project.novel_text or ""),
            })
        except Exception as exc:  # noqa: BLE001
            log.debug("读取工程失败 %s：%s", path, exc)
    return {"projects": items}


@app.post("/api/project/load")
def api_load_project(payload: dict[str, Any]) -> dict[str, Any]:
    file_path = str(payload.get("file", "") or "")
    if not file_path:
        raise HTTPException(status_code=400, detail="缺少 file 参数")
    target = Path(file_path)
    if not target.is_absolute():
        target = Path(config.PROJECT_DIR) / target
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"工程文件不存在：{target}")
    try:
        project = Project.load(target)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"载入失败：{exc}") from exc
    STATE.set_project(project)
    log.info("已载入工程：%s（%d 角色 / %d 分镜）", project.title,
             len(project.characters), len(project.shots))
    return serialize_project(project)


@app.post("/api/project/save")
def api_save_project(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project = STATE.get_project()
    data = payload or {}
    if "title" in data:
        project.title = str(data["title"] or project.title)
    if "novel_text" in data:
        project.novel_text = str(data["novel_text"] or "")
    if "style" in data:
        project.style = str(data["style"] or project.style)
    if "style_detail" in data:
        project.style_detail = str(data["style_detail"] or "")
    path = project.save()
    return {"message": f"工程已保存：{path.name}", "file": str(path),
            "project": serialize_project(project, full=False)}


# =============================================================================
# 阶段一 / 二：文本与内容分析
# =============================================================================

@app.post("/api/text/upload")
async def api_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    name = file.filename or "uploaded.txt"
    ext = Path(name).suffix.lower()
    if ext and ext not in config.SUPPORTED_TEXT_EXTENSIONS:
        raise HTTPException(status_code=400,
                            detail=f"仅支持文本格式文件，当前类型 {ext}")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件内容为空")
    try:
        text, enc = textutil.detect_and_decode(raw)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"文件解码失败：{exc}") from exc
    text = textutil.normalize_novel(text)
    if not text:
        raise HTTPException(status_code=400, detail="文件内容为空")
    # 编码识别错误时会出现大量 U+FFFD（�），这里统计出来告诉前端，
    # 免得用户看到"读进来的字比文件少"却不知道是编码猜错了。
    replacement = text.count("\ufffd")
    save_path = Path(config.UPLOAD_DIR) / f"{int(time.time())}_{name}"
    try:
        save_path.write_bytes(raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("保存上传文件失败：%s", exc)
    log.info("上传文件 %s（编码 %s，%d 字节 → %d 字 / %d 行）",
             name, enc, len(raw), len(text), text.count("\n") + 1)
    return {
        "text": text,
        "filename": name,
        "encoding": enc,
        "length": len(text),
        "bytes": len(raw),
        "lines": text.count("\n") + 1,
        "replacement_chars": replacement,
        # 分析阶段只取前 MAX_NOVEL_LENGTH 字（超出部分仍保留在文本框与工程里）
        "analysis_limit": config.MAX_NOVEL_LENGTH,
        "complete": replacement == 0,
    }


@app.post("/api/refine/start")
def api_refine_start(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """第 2 步：小说精修 -> 8-12 视觉锚点 -> 25 格连续故事板（只存文本）。"""
    project = STATE.get_project()
    data = payload or {}
    # 输入页的文本框是前端本地状态；点“开始精修”时先落盘，
    # 否则 project.novel_text 仍为空，会出现“明明输入了却说没输入”。
    text = str(data.get("text") or "").strip()
    if text:
        project.novel_text = text
    title = str(data.get("title") or "").strip()
    if title:
        project.title = title
    if text or title:
        project.save()
    if len(project.novel_text or "") < config.MIN_NOVEL_LENGTH:
        raise HTTPException(status_code=400, detail="请先输入小说文本")
    pipe = STATE.pipeline()
    try:
        job_id = new_job("refine", "小说精修 / 连续故事板")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    attach_job(pipe, job_id, 0.02, 0.94)

    def work() -> dict[str, Any]:
        project.refine_status = "生成中"
        result = pipe.refine_novel(project)
        project.touch(); project.save()
        update_job(job_id,
                   f"精修完成：{len(project.visual_anchors)} 锚点 / "
                   f"{len(project.continuous_cells)} 格", 1.0)
        return {"message": "小说精修完成",
                "core_task": project.core_task,
                "digest": project.refine_digest,
                "anchors": project.visual_anchors,
                "cells": project.continuous_cells,
                "meta": project.refine_meta,
                "warnings": result.get("warnings") or [],
                "project": serialize_project(project)}

    run_job(job_id, work)
    return {"job_id": job_id}


def _refine_anchor(raw: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "id": str(raw.get("id") or f"A{index + 1}"),
        "event": textutil.truncate(str(raw.get("event") or ""), 80),
        "function": str(raw.get("function") or "推进"),
        "quote": textutil.truncate(str(raw.get("quote") or ""), 40),
        "characters": [str(x) for x in (raw.get("characters") or [])
                       if str(x).strip()],
        "place": str(raw.get("place") or ""),
        "time": str(raw.get("time") or ""),
        "shot_hint": str(raw.get("shot_hint") or ""),
    }


def _refine_cell(raw: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "cell": index + 1,
        "beat_id": str(raw.get("beat_id") or ""),
        "shot_type": str(raw.get("shot_type") or "中景"),
        "subject_action": textutil.truncate(str(raw.get("subject_action") or ""), 80),
        "environment": textutil.truncate(str(raw.get("environment") or ""), 80),
        "light": textutil.truncate(str(raw.get("light") or ""), 30),
        "emotion": textutil.truncate(str(raw.get("emotion") or ""), 16),
        "function": str(raw.get("function") or "推进"),
        "characters": [str(x) for x in (raw.get("characters") or [])
                       if str(x).strip()],
        "props": [str(x) for x in (raw.get("props") or []) if str(x).strip()],
        "gaze": str(raw.get("gaze") or ""),
        "transition": str(raw.get("transition") or "切"),
        "quote": textutil.truncate(str(raw.get("quote") or ""), 40),
    }


def _refine_storyboard_text(cells: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{c.get('cell', i)}. {c.get('shot_type', '')}｜{c.get('subject_action', '')}｜"
        f"{c.get('environment', '')}｜{c.get('emotion', '')}｜{c.get('function', '')}"
        for i, c in enumerate(cells, start=1))


@app.post("/api/refine/save")
def api_refine_save(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """保存第 2 步的手动修改（核心任务 / 摘要 / 锚点 / 25 格）。"""
    project = STATE.get_project()
    data = payload or {}
    if "core_task" in data:
        project.core_task = str(data.get("core_task") or "").strip()
    if isinstance(data.get("digest"), dict):
        d = data["digest"]
        project.refine_digest = {
            "protagonist": str(d.get("protagonist") or ""),
            "goal": str(d.get("goal") or ""),
            "obstacle": str(d.get("obstacle") or ""),
            "turn": str(d.get("turn") or ""),
            "ending_state": str(d.get("ending_state") or ""),
            "tone": str(d.get("tone") or ""),
            "motifs": [str(x) for x in (d.get("motifs") or []) if str(x).strip()][:6],
        }
    if isinstance(data.get("anchors"), list):
        anchors = [_refine_anchor(a, i) for i, a in enumerate(data["anchors"])
                   if isinstance(a, dict) and str(a.get("event") or "").strip()]
        project.visual_anchors = anchors[:12]
    if isinstance(data.get("cells"), list):
        cells = [_refine_cell(c, i) for i, c in enumerate(data["cells"])
                 if isinstance(c, dict) and str(c.get("subject_action") or "").strip()]
        project.continuous_cells = cells[:25]
    project.continuous_storyboard = _refine_storyboard_text(project.continuous_cells or [])
    project.refine_status = ("已修改" if (project.visual_anchors or
                                         project.continuous_cells) else "待生成")
    meta = dict(project.refine_meta or {})
    meta.update({
        "edited": True,
        "chars": len(project.novel_text or ""),
        "anchors": len(project.visual_anchors or []),
        "cells": len(project.continuous_cells or []),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    project.refine_meta = meta
    if project.continuous_cells:
        project.page_plan_note = "精修内容已手动修改；如结构变化请重新拆解分镜"
    project.touch(); project.save()
    return {"message": "第 2 步修改已保存", "project": serialize_project(project)}


@app.post("/api/refine/delete")
def api_refine_delete(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """删除第 2 步内容：单个锚点 / 单格 / 全部。"""
    project = STATE.get_project()
    data = payload or {}
    scope = str(data.get("scope") or "all").lower()
    try:
        index = int(data.get("index", -1))
    except (TypeError, ValueError):
        index = -1
    if scope == "anchor":
        if 0 <= index < len(project.visual_anchors):
            project.visual_anchors.pop(index)
    elif scope == "cell":
        if 0 <= index < len(project.continuous_cells):
            project.continuous_cells.pop(index)
            for i, c in enumerate(project.continuous_cells, start=1):
                c["cell"] = i
    else:
        project.visual_anchors = []
        project.continuous_cells = []
        project.core_task = ""
        project.refine_digest = {}
    project.continuous_storyboard = _refine_storyboard_text(project.continuous_cells or [])
    project.refine_status = ("已修改" if (project.visual_anchors or
                                         project.continuous_cells) else "待生成")
    meta = dict(project.refine_meta or {})
    meta.update({"edited": True, "anchors": len(project.visual_anchors or []),
                 "cells": len(project.continuous_cells or []),
                 "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
    project.refine_meta = meta
    project.touch(); project.save()
    return {"message": "已删除", "project": serialize_project(project)}


@app.post("/api/analyze")
def api_analyze(payload: dict[str, Any]) -> dict[str, Any]:
    """阶段二：开始分析小说。"""
    text = str(payload.get("text", "") or "").strip()
    title = str(payload.get("title", "") or "").strip() or "未命名作品"
    if len(text) < config.MIN_NOVEL_LENGTH:
        raise HTTPException(status_code=400, detail="请先输入或上传小说文本")

    project = STATE.get_project()
    project.title = title
    project.novel_text = text
    project.save()
    pipe = STATE.pipeline()
    try:
        job_id = new_job("analyze", "小说内容分析")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    attach_job(pipe, job_id, 0.02, 0.93)

    def work() -> dict[str, Any]:
        pipe.analyze_novel(project)
        project.save()
        update_job(job_id, "分析完成", 1.0)
        return serialize_project(project)

    run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/auto/start")
def api_auto_start(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    一键全流程：分析 → 人设/三视图 → 分镜 → 出图（自检+自动返修）→ 导出。

    已有阶段结果会自动复用；用户仍然可以在任意阶段停下来手工修改设定，
    再点一次继续。reset=true 时从空白工程重新开始。
    """
    payload = payload or {}
    text = str(payload.get("text", "") or "").strip()
    title = str(payload.get("title", "") or "").strip() or "未命名作品"
    reset = bool(payload.get("reset", False))
    export = bool(payload.get("export", getattr(config, "AUTO_EXPORT_AFTER_GENERATE", True)))

    if reset:
        if len(text) < config.MIN_NOVEL_LENGTH:
            raise HTTPException(status_code=400, detail="请先输入或上传小说文本")
        project = Project(title=title, novel_text=text)
        STATE.set_project(project)
    else:
        project = STATE.get_project()
        if text:
            project.novel_text = text
        if title and (not project.title or project.title == "未命名作品"):
            project.title = title

    if len((project.novel_text or "").strip()) < config.MIN_NOVEL_LENGTH:
        raise HTTPException(status_code=400, detail="请先输入或上传小说文本")

    pipe = STATE.pipeline()
    try:
        job_id = new_job("auto", "一键全流程")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    attach_job(pipe, job_id, 0.01, 0.98)

    def work() -> dict[str, Any]:
        pipe.auto_run(project, export=export)
        project.save()
        update_job(job_id, "全流程完成", 1.0)
        return serialize_project(project)

    run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/analysis/save")
def api_save_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    """阶段二：保存编辑后的角色 / 场景 / 风格。"""
    project = STATE.get_project()
    chars = payload.get("characters")
    if isinstance(chars, list):
        new_chars: list[Character] = []
        for item in chars:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip()
            if not name:
                continue
            old = project.find_character(str(item.get("id", "") or "")) \
                or next((c for c in project.characters if c.id == item.get("id")), None)
            c = old or Character()
            c.name = name
            c.gender = str(item.get("gender", "") or "")
            c.age = str(item.get("age", "") or "")
            c.identity = str(item.get("identity", "") or "")
            c.personality = str(item.get("personality", "") or "")
            c.role = str(item.get("role", "") or "")
            c.is_extra = bool(item.get("is_extra", False))
            new_chars.append(c)
        project.characters = new_chars

    scenes = payload.get("scenes")
    if isinstance(scenes, list):
        new_scenes: list[Scene] = []
        for item in scenes:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip()
            if not name:
                continue
            old = next((s for s in project.scenes if s.id == item.get("id")), None)
            s = old or Scene()
            s.name = name
            s.features = str(item.get("features", "") or "")
            s.atmosphere = str(item.get("atmosphere", "") or "")
            s.frequency = str(item.get("frequency", "") or "")
            new_scenes.append(s)
        project.scenes = new_scenes

    if "style" in payload and payload["style"]:
        project.style = str(payload["style"])
    if "style_detail" in payload:
        project.style_detail = str(payload["style_detail"] or "")
    # handraw 风格（界面上的风格选择器）
    if "style_id" in payload:
        raw = str(payload.get("style_id") or "")
        sid = stylelib.normalize_style_id(raw) if raw else ""
        if raw and not sid:
            raise HTTPException(status_code=400, detail=f"未知风格编号：{raw}")
        project.style_id = sid
        project.style_auto = bool(payload.get("style_auto", False))
        if "style_color_ids" in payload:
            project.style_color_ids = stylelib.parse_colors(payload.get("style_color_ids"))
        if "style_rationale" in payload:
            project.style_rationale = str(payload.get("style_rationale") or "")
        try:
            project.style_ref_path = stylelib.style_reference_png(sid) if sid else ""
        except Exception:  # noqa: BLE001
            project.style_ref_path = ""
    project.save()
    log.info("已保存分析结果：角色 %d、场景 %d", len(project.characters),
             len(project.scenes))
    return {"message": "分析结果已保存", "project": serialize_project(project)}


# =============================================================================
# 阶段三：人设与参考图
# =============================================================================

@app.post("/api/character/design")
def api_character_design(payload: dict[str, Any]) -> dict[str, Any]:
    """生成单个角色的人设。"""
    project = STATE.get_project()
    cid = str(payload.get("id", "") or "")
    look = str(payload.get("look", "") or "默认造型")
    character = next((c for c in project.characters if c.id == cid), None)
    if character is None:
        raise HTTPException(status_code=404, detail="未找到该角色")
    pipe = STATE.pipeline()
    try:
        job_id = new_job("design", f"生成「{character.name}」人设")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    attach_job(pipe, job_id, 0.05, 0.9)

    def work() -> dict[str, Any]:
        update_job(job_id, f"正在为「{character.name}」设计人设…", 0.2)
        pipe.generate_character_design(project, character, look)
        project.save()
        return {"character": serialize_character(character)}

    run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/character/images")
def api_character_images(payload: dict[str, Any]) -> dict[str, Any]:
    """生成单个角色的主视觉图 + 三视图（可指定造型）。"""
    project = STATE.get_project()
    cid = str(payload.get("id", "") or "")
    look = str(payload.get("look", "") or "默认造型")
    character = next((c for c in project.characters if c.id == cid), None)
    if character is None:
        raise HTTPException(status_code=404, detail="未找到该角色")
    pipe = STATE.pipeline()
    try:
        job_id = new_job("char_images", f"生成「{character.name}」主视图与姿态图")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    attach_job(pipe, job_id, 0.02, 0.95)

    def work() -> dict[str, Any]:
        pipe.generate_character_images(project, character, look)
        project.save()
        return {"character": serialize_character(character)}

    run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/character/all-images")
def api_all_character_images(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """一键为所有角色生成人设与参考图。"""
    project = STATE.get_project()
    if not project.characters:
        raise HTTPException(status_code=400, detail="请先完成内容分析")
    pipe = STATE.pipeline()
    try:
        job_id = new_job("all_char_images", "批量生成全部角色参考图")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    attach_job(pipe, job_id, 0.02, 0.95)

    def work() -> dict[str, Any]:
        pipe.generate_all_characters(project)
        project.save()
        return {"characters": [serialize_character(c) for c in project.characters]}

    run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/character/regenerate")
def api_character_regenerate(payload: dict[str, Any]) -> dict[str, Any]:
    """只重做主视觉图 / 三视图 / 某个单视图。"""
    project = STATE.get_project()
    cid = str(payload.get("id", "") or "")
    which = str(payload.get("which", "主视觉图"))
    character = next((c for c in project.characters if c.id == cid), None)
    if character is None:
        raise HTTPException(status_code=404, detail="未找到该角色")
    pipe = STATE.pipeline()
    try:
        job_id = new_job("char_regen", f"重做「{character.name}」的{which}")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    attach_job(pipe, job_id, 0.05, 0.9)

    def work() -> dict[str, Any]:
        update_job(job_id, f"正在重做「{character.name}」的{which}…", 0.3)
        if which in ("姿态图", "三视图"):
            pipe.regenerate_character_pose_sheet(project, character)
        elif which in ("主视图", "主视觉图"):
            pipe.regenerate_character_main_visual(project, character)
        else:
            pipe.regenerate_character_pose_sheet(project, character)
        project.save()
        return {"character": serialize_character(character)}

    run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/character/update")
def api_character_update(payload: dict[str, Any]) -> dict[str, Any]:
    """保存角色的人设文本与关键词。"""
    project = STATE.get_project()
    cid = str(payload.get("id", "") or "")
    character = next((c for c in project.characters if c.id == cid), None)
    if character is None:
        raise HTTPException(status_code=404, detail="未找到该角色")
    if "design" in payload:
        character.design = str(payload["design"] or "")
    if "keywords" in payload:
        kws = payload["keywords"]
        if isinstance(kws, str):
            kws = [x.strip() for x in kws.replace("，", "\n").replace("、", "\n")
                   .split("\n") if x.strip()]
        character.keywords = [str(k) for k in (kws or []) if str(k).strip()]
    if "active_look" in payload:
        character.active_look = str(payload["active_look"] or "默认造型")
    if not character.keywords:
        character.keywords = pipeline.extract_keywords(character.design)
    project.save()
    return {"message": f"已保存「{character.name}」的人设",
            "character": serialize_character(character)}


# =============================================================================
# 自定义姿态（角色姿态总图 / 单格姿态）
# =============================================================================

def _pose_upload_dir() -> Path:
    d = Path(config.DATA_DIR) / "poses"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _save_pose_upload(file: UploadFile, stem: str, convert: bool) -> str:
    suffix = Path(file.filename or "pose.png").suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
        suffix = ".png"
    raw = _pose_upload_dir() / f"{stem}_raw{suffix}"
    raw.write_bytes(await file.read())
    if not convert:
        return str(raw)
    try:
        from comicgen import poseutil
        if poseutil.looks_like_skeleton(raw):
            return str(raw)
        out = _pose_upload_dir() / f"{stem}_skeleton.png"
        return poseutil.photo_to_skeleton(raw, out)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/character/pose-upload")
async def api_character_pose_upload(
        id: str = Form(...), convert: bool = Form(True),
        file: UploadFile = File(...)) -> dict[str, Any]:
    project = STATE.get_project()
    character = next((c for c in project.characters if c.id == id), None)
    if character is None:
        raise HTTPException(status_code=404, detail="未找到该角色")
    path = await _save_pose_upload(file, f"char_{id}", convert)
    character.pose_source = path
    character.pose_status = "待生成"
    character.error = ""
    project.touch()
    STATE.save()
    return {"message": "姿态图已上传，可重做姿态总图", "character": serialize_character(character),
            "pose_source": path}


@app.post("/api/character/top-upload")
async def api_character_top_upload(
        id: str = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
    """上传俯视参考：自动转成骨架，再用骨架生成角色俯视图。

    旧行为只是把上传图去背景后直接当 ``top_view``；用户看到的是"参考图"
    而不是"这个角色的俯视图"。现在按姿态图同一套流程走：
    上传图 → OpenPose 骨架 → ControlNet + 身份锁 → 俯视图.png。
    """
    project = STATE.get_project()
    character = next((c for c in project.characters if c.id == id), None)
    if character is None:
        raise HTTPException(status_code=404, detail="未找到该角色")
    from comicgen.pipeline import _safe_name
    out_dir = Path(config.CHARACTER_DIR) / _safe_name(character.name) / \
        _safe_name(character.active_look or "默认造型")
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "top.png").suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
        suffix = ".png"
    raw = out_dir / f"俯视图_上传{suffix}"
    raw.write_bytes(await file.read())
    path = out_dir / "俯视图_参考.png"
    # 上传文件可能扩展名和真实格式不一致（例如 .png 实际是 WebP），统一转 PNG。
    try:
        from PIL import Image as _Img
        _Img.open(raw).convert("RGB").save(path, format="PNG")
        raw.unlink(missing_ok=True)
    except Exception:
        raw.replace(path)
    # 去背景，只保留人物结构；再把参考图转成 OpenPose 骨架。
    try:
        from comicgen import bgremove
        path = bgremove.remove_background(path, out_dir / "俯视图_参考_去背景.png",
                                          mode="white")
    except Exception as exc:  # noqa: BLE001
        log.warning("俯视参考去背景失败：%s", exc)
    character.top_source = str(path)
    character.top_view = ""
    character.top_status = "待生成"
    try:
        from comicgen import poseutil
        sk = out_dir / "俯视图_骨架.png"
        character.top_skeleton = str(poseutil.photo_to_skeleton(path, sk))
        character.error = ""
    except Exception as exc:  # noqa: BLE001
        character.top_skeleton = ""
        character.top_note = f"参考图未提取到骨架，将改用文本俯视：{textutil.truncate(str(exc), 120)}"
        log.warning("俯视参考骨架提取失败：%s", exc)
    project.touch(); STATE.save()

    # 上传后自动生成俯视图（后台任务）；已有任务时返回 need_generate，由前端手动触发。
    pipe = STATE.pipeline()
    try:
        job_id = new_job("top_view", f"生成「{character.name}」俯视图")
    except JobBusy as exc:
        return {"message": "俯视参考已上传；当前有任务在跑，请稍后点「生成俯视图」",
                "character": serialize_character(character),
                "top_source": character.top_source,
                "top_skeleton": character.top_skeleton,
                "need_generate": True, "busy": str(exc)}
    attach_job(pipe, job_id, 0.05, 0.9)

    def work() -> dict[str, Any]:
        pipe.generate_character_top_view(project, character)
        project.save()
        return {"character": serialize_character(character)}

    run_job(job_id, work)
    return {"message": "俯视参考已上传，正在生成俯视图…",
            "character": serialize_character(character),
            "top_source": character.top_source,
            "top_skeleton": character.top_skeleton,
            "job_id": job_id}


@app.post("/api/character/top-generate")
def api_character_top_generate(payload: dict[str, Any]) -> dict[str, Any]:
    """用已上传的俯视参考/骨架重新生成俯视图。"""
    project = STATE.get_project()
    character = next((c for c in project.characters
                      if c.id == str(payload.get("id", "") or "")), None)
    if character is None:
        raise HTTPException(status_code=404, detail="未找到该角色")
    # 没有上传参考时也允许"纯文本俯视"：对配角/群众角色足够用，
    # 主角仍建议上传真实参考走 参考→骨架→生成。
    pipe = STATE.pipeline()
    try:
        job_id = new_job("top_view", f"生成「{character.name}」俯视图")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    attach_job(pipe, job_id, 0.05, 0.9)

    def work() -> dict[str, Any]:
        pipe.generate_character_top_view(project, character)
        project.save()
        return {"character": serialize_character(character)}

    run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/character/top-default")
def api_character_top_default(payload: dict[str, Any]) -> dict[str, Any]:
    """把 ComfyUI input 的默认 俯视图.png 设为该角色的俯视参考。"""
    project = STATE.get_project()
    character = next((c for c in project.characters
                      if c.id == str(payload.get("id", "") or "")), None)
    if character is None:
        raise HTTPException(status_code=404, detail="未找到该角色")
    from comicgen.pipeline import _safe_name
    candidates = list(getattr(config, "CHARACTER_TOP_DEFAULT_REFS", []) or [])
    candidates.insert(0, Path(getattr(config, "CHARACTER_TOP_DEFAULT_REF", Path(""))))
    src = next((p for p in candidates if p and Path(p).exists()), None)
    if src is None:
        raise HTTPException(status_code=404, detail="默认俯视图不存在：ComfyUI-Shared/input/俯视图.png")
    out_dir = Path(config.CHARACTER_DIR) / _safe_name(character.name) / \
        _safe_name(character.active_look or "默认造型")
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / "俯视图.png"
    try:
        from PIL import Image as _Img
        _Img.open(src).convert("RGB").save(dst, format="PNG")
    except Exception:
        shutil.copyfile(src, dst)
    from comicgen import bgremove
    top_path = bgremove.remove_background(dst, out_dir / "俯视图_去背景.png", mode="white")
    character.top_view = str(top_path)
    character.top_status = "已完成"
    project.touch(); STATE.save()
    return {"message": "已使用默认俯视图", "top_view": str(dst),
            "character": serialize_character(character)}


@app.post("/api/character/top-clear")
def api_character_top_clear(payload: dict[str, Any]) -> dict[str, Any]:
    project = STATE.get_project()
    character = next((c for c in project.characters
                      if c.id == str(payload.get("id", "") or "")), None)
    if character is None:
        raise HTTPException(status_code=404, detail="未找到该角色")
    character.top_view = ""
    character.top_source = ""
    character.top_skeleton = ""
    character.top_status = "待生成"
    (character.views or {}).pop("top", None)
    (character.view_quality or {}).pop("top", None)
    project.touch()
    STATE.save()
    return {"message": "已清除俯视参考", "character": serialize_character(character)}


@app.post("/api/character/pose-clear")
def api_character_pose_clear(payload: dict[str, Any]) -> dict[str, Any]:
    project = STATE.get_project()
    cid = str(payload.get("id", "") or "")
    character = next((c for c in project.characters if c.id == cid), None)
    if character is None:
        raise HTTPException(status_code=404, detail="未找到该角色")
    character.pose_source = ""
    character.pose_status = "待生成"
    project.touch()
    STATE.save()
    return {"message": "已恢复内置姿态总图", "character": serialize_character(character)}


@app.post("/api/shot/split")
def api_shot_split(payload: dict[str, Any]) -> dict[str, Any]:
    """把双人镜头拆成两个单人格，避免 Z-Image 画出第三个人/服装互染。"""
    project = STATE.get_project()
    shot = project.shot_by_id(str(payload.get("shot_id", "") or ""))
    if shot is None:
        raise HTTPException(status_code=404, detail="未找到该分镜")
    names = [n for n in (shot.characters or []) if n]
    if len(names) < 2:
        raise HTTPException(status_code=400, detail="这个镜头不是双人镜头")
    from comicgen.models import Shot as _Shot
    second = _Shot()
    # 复制场景/镜头信息，但只保留一个角色
    for f in ("scene", "time", "mood", "shot_type", "camera", "composition",
              "lighting", "beat", "pacing", "panel_scale", "panel_emphasis",
              "caption", "silent"):
        if hasattr(shot, f):
            setattr(second, f, getattr(shot, f))
    second.characters = [names[1]]
    second.character_actions = {names[1]: (shot.character_actions or {}).get(names[1], "单人反应")}
    second.char_positions = {names[1]: "画面中心"}
    second.char_sides = {names[1]: "正面"}
    second.description = (f"单人反应镜头：{names[1]}，"
                          f"{(shot.character_actions or {}).get(names[1], '保持当前动作')}；"
                          f"画面里只有 {names[1]} 一个人。")
    second.raw_image_path = ""; second.image_path = ""; second.prop_image = ""
    second.pose_image = ""; second.pose_skeleton = ""; second.status = "待生成"
    # 原格只保留第一个人
    shot.characters = [names[0]]
    shot.character_actions = {names[0]: (shot.character_actions or {}).get(names[0], "单人反应")}
    shot.char_positions = {names[0]: "画面中心"}
    shot.char_sides = {names[0]: "正面"}
    shot.description = (f"单人反应镜头：{names[0]}，"
                        f"{(shot.character_actions or {}).get(names[0], '保持当前动作')}；"
                        f"画面里只有 {names[0]} 一个人。")
    shot.raw_image_path = ""; shot.image_path = ""; shot.prop_image = ""
    shot.pose_image = ""; shot.pose_skeleton = ""; shot.status = "待生成"
    pos = project.shots.index(shot)
    project.shots.insert(pos + 1, second)
    project.reindex_shots()
    project.pages = []
    project.grid25_image = ""; project.grid25_status = "待生成"
    project.touch(); STATE.save()
    return {"message": f"已拆成两格：{names[0]} / {names[1]}，请分别重新出图",
            "shots": [serialize_shot(shot, project), serialize_shot(second, project)]}


@app.post("/api/shot/prop-upload")
async def api_shot_prop_upload(shot_id: str = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
    """纯道具镜头：上传一张道具参考图，直接裁成该格画面，不再走文生图。"""
    project = STATE.get_project()
    shot = project.shot_by_id(shot_id)
    if shot is None:
        raise HTTPException(status_code=404, detail="未找到该分镜")
    out_dir = Path(config.DATA_DIR) / "props"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "prop.png").suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
        suffix = ".png"
    raw = out_dir / f"shot_{shot.id}_raw{suffix}"
    raw.write_bytes(await file.read())
    dst = out_dir / f"shot_{shot.id}.png"
    try:
        from PIL import Image as _Img
        _Img.open(raw).convert("RGB").save(dst, format="PNG")
        raw.unlink(missing_ok=True)
    except Exception:
        raw.replace(dst)
    # 道具参考去背景：输出只保留物件，避免桌面/环境被当成画面内容
    from comicgen import bgremove
    nobg = out_dir / f"shot_{shot.id}_nobg.png"
    prop_path = bgremove.remove_background(dst, nobg, mode="white")
    shot.prop_image = str(prop_path)
    project.touch(); STATE.save()
    return {"message": "道具参考已上传并去背景；这一格会直接使用它，不再生成人物", 
            "shot": serialize_shot(shot, project)}


@app.post("/api/shot/prop-clear")
def api_shot_prop_clear(payload: dict[str, Any]) -> dict[str, Any]:
    project = STATE.get_project()
    shot = project.shot_by_id(str(payload.get("shot_id", "") or ""))
    if shot is None:
        raise HTTPException(status_code=404, detail="未找到该分镜")
    shot.prop_image = ""
    project.touch(); STATE.save()
    return {"message": "已清除道具参考", "shot": serialize_shot(shot, project)}


@app.post("/api/shot/pose-upload")
async def api_shot_pose_upload(
        shot_id: str = Form(...), convert: bool = Form(True),
        file: UploadFile = File(...)) -> dict[str, Any]:
    project = STATE.get_project()
    shot = project.shot_by_id(shot_id)
    if shot is None:
        raise HTTPException(status_code=404, detail="未找到该分镜")
    path = await _save_pose_upload(file, f"shot_{shot_id}", convert)
    shot.pose_image = path
    project.touch()
    STATE.save()
    return {"message": "本格姿态参考已上传；重新生成会按这个姿态出图",
            "shot_id": shot_id, "pose_image": path}


@app.post("/api/shot/pose-clear")
def api_shot_pose_clear(payload: dict[str, Any]) -> dict[str, Any]:
    project = STATE.get_project()
    shot = project.shot_by_id(str(payload.get("shot_id", "") or ""))
    if shot is None:
        raise HTTPException(status_code=404, detail="未找到该分镜")
    shot.pose_image = ""
    project.touch()
    STATE.save()
    return {"message": "已清除本格姿态参考", "shot_id": shot.id}


@app.post("/api/character/add-look")
def api_character_add_look(payload: dict[str, Any]) -> dict[str, Any]:
    """为角色生成一套新造型参考图。"""
    project = STATE.get_project()
    cid = str(payload.get("id", "") or "")
    look = str(payload.get("look", "") or "").strip()
    if not look:
        raise HTTPException(status_code=400, detail="请填写新造型名称")
    character = next((c for c in project.characters if c.id == cid), None)
    if character is None:
        raise HTTPException(status_code=404, detail="未找到该角色")
    pipe = STATE.pipeline()
    try:
        job_id = new_job("char_look", f"生成「{character.name}」造型「{look}」")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    attach_job(pipe, job_id, 0.05, 0.9)

    def work() -> dict[str, Any]:
        update_job(job_id, f"正在生成造型「{look}」的主视觉图…", 0.05)
        pipe.generate_character_images(project, character, look)
        project.save()
        update_job(job_id, f"造型「{look}」生成完成", 1.0)
        return {"character": serialize_character(character)}

    run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/character/add")
def api_character_add(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project = STATE.get_project()
    name = str((payload or {}).get("name") or "新角色")
    project.characters.append(Character(name=name))
    project.save()
    return {"project": serialize_project(project)}


@app.post("/api/character/delete")
def api_character_delete(payload: dict[str, Any]) -> dict[str, Any]:
    project = STATE.get_project()
    cid = str(payload.get("id", "") or "")
    before = len(project.characters)
    project.characters = [c for c in project.characters if c.id != cid]
    if len(project.characters) == before:
        raise HTTPException(status_code=404, detail="未找到该角色")
    project.save()
    return {"project": serialize_project(project)}


# =============================================================================
# 阶段四：分镜拆解与编辑
# =============================================================================

@app.post("/api/storyboard/decompose")
def api_decompose(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project = STATE.get_project()
    if not project.novel_text:
        raise HTTPException(status_code=400, detail="请先输入或上传小说文本")
    if not project.characters:
        raise HTTPException(status_code=400, detail="请先完成第二阶段的内容分析")
    pipe = STATE.pipeline()
    try:
        job_id = new_job("decompose", "分镜拆解")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    attach_job(pipe, job_id, 0.02, 0.95)

    def work() -> dict[str, Any]:
        pipe.decompose_storyboard(project)
        project.save()
        return {"project": serialize_project(project),
                "notice": getattr(pipe, "last_notice", "")}

    run_job(job_id, work)
    return {"job_id": job_id}


_POSE_ACTION_WORDS = (
    "跑", "冲", "跳", "踢", "坐", "蹲", "跪", "爬", "推", "伸", "抓",
    "举", "端", "递", "指", "靠", "趴", "转身", "回头", "挥手", "倒",
    "扶", "撑", "按", "攥", "捧", "仰面", "俯身",
)


def _grid_pose_needed(shot: Any) -> bool:
    """25 格里只给"关键动作镜头"挂姿态，不再每格都跑 ControlNet。

    用户明确要求：优先分析最关键的动作场景，只给这些场景人物参考姿态图；
    静态对白/反应/纯道具格用提示词 + 人数锁即可，省时也避免骨架把构图锁死。
    """
    names = [n for n in (getattr(shot, "characters", None) or []) if n]
    if len(names) != 1:
        return False
    if str(getattr(shot, "shot_type", "") or "") in ("特写",):
        return False
    text = " ".join([
        str(getattr(shot, "action", "") or ""),
        str(getattr(shot, "description", "") or ""),
        " ".join(str(v) for v in (getattr(shot, "character_actions", {}) or {}).values()),
    ])
    if str(getattr(shot, "panel_emphasis", "") or "") in ("main", "splash"):
        return True
    return any(w in text for w in _POSE_ACTION_WORDS)


def _pose_priority(shot: Any) -> int:
    text = " ".join([
        str(getattr(shot, "action", "") or ""),
        str(getattr(shot, "description", "") or ""),
        " ".join(str(v) for v in (getattr(shot, "character_actions", {}) or {}).values()),
    ])
    score = 0
    if str(getattr(shot, "panel_emphasis", "") or "") in ("main", "splash"):
        score += 4
    score += sum(2 for w in ("跑", "冲", "跳", "踢", "爬", "推", "伸", "抓",
                             "举", "端", "递", "指", "转身", "回头", "挥手",
                             "倒", "扶", "撑", "仰面", "俯身") if w in text)
    score += sum(1 for w in ("坐", "蹲", "跪", "靠", "趴", "按", "攥", "捧") if w in text)
    return score


def _select_pose_shots(key_shots: list[Any], max_n: int = 10) -> list[Any]:
    """只保留最关键的动作镜头挂姿态（默认最多 10 格）。"""
    cands = [s for s in key_shots if _grid_pose_needed(s)]
    cands.sort(key=lambda s: (-_pose_priority(s), int(getattr(s, "index", 0))))
    return cands[:max(1, int(max_n))]


def _ensure_shot_poses(project: Any, key_shots: list[Any]) -> int:
    """只给最关键的动作镜头补姿态（用户上传的优先）。

    AUTO_POSE_SHOTS=off 时完全不动：自动骨架只应在显式开启时参与，
    否则会把内置骨架的坏比例带进 25 格/最终图。
    """
    if str(getattr(config, "AUTO_POSE_SHOTS", "off") or "off").lower() == "off":
        return 0
    out_dir = Path(config.PROJECT_DIR) / project.id / "poses"
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = _select_pose_shots(key_shots)
    made = 0
    for i, s in enumerate(key_shots, start=1):
        if s not in targets:
            continue
        if (getattr(s, "pose_skeleton", "") or getattr(s, "pose_image", "")):
            continue
        pose, reason, _dynamic = poseplan.choose_pose(s)
        if not pose or not Path(pose).exists():
            continue
        dst = out_dir / f"pose_{i:02d}_shot{s.index}.png"
        try:
            shutil.copyfile(pose, dst)
            s.pose_skeleton = str(dst)
            if not s.pose_image:
                s.pose_image = str(dst)
            made += 1
            log.info("25 格前置姿态：第 %s 格 %s（%s）", s.index, reason, Path(pose).name)
        except Exception as exc:  # noqa: BLE001
            log.warning("25 格前置姿态失败：第 %s 格 %s", s.index, exc)
    if made:
        project.touch()
    return made


def _grid_cell_size(shot: Any, max_side: int = 512) -> tuple[int, int]:
    """25 格按镜头比例生成，避免全部方图后裁切/留白。"""
    try:
        w, h = shot.panel_size()
    except Exception:  # noqa: BLE001
        w, h = 16, 9
    scale = max_side / max(w, h)
    nw, nh = max(256, int(round(w * scale / 8) * 8)), max(256, int(round(h * scale / 8) * 8))
    return nw, nh


@app.post("/api/storyboard/grid25")
def api_storyboard_grid25(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """第 5 步：按关键镜头提示词生成 25 格场景分镜，再拼成 5×5。"""
    project = STATE.get_project()
    if not project.shots:
        raise HTTPException(status_code=400, detail="请先完成分镜拆解")
    used = {n for s in project.shots for n in (s.characters or []) if n}
    missing = [c.name for c in project.characters
               if not c.is_extra and c.name in used
               and not (c.main_visual or c.triple_view)]
    if missing and not bool((payload or {}).get("force", False)):
        raise HTTPException(
            status_code=400,
            detail=("以下主角还没有主视图/面部特写/单视图：" + "、".join(missing)
                    + "。请先在阶段 3 完成角色图；确认要跳过时传 force=true。"))
    key_shots = keyframes_mod.select_key_shots(project, 25, require_image=False)
    if not key_shots:
        raise HTTPException(status_code=400, detail="没有可用的关键镜头")
    oversized = [s.index for s in key_shots
                 if len([n for n in (s.characters or []) if n]) > 3]
    if oversized and not bool((payload or {}).get("force", False)):
        raise HTTPException(
            status_code=400,
            detail=(f"第 {', '.join(map(str, oversized))} 格超过 3 人；"
                    "Qwen 群像实测 4 人会多画。请先在分镜拆解拆成 3+1/2+2，"
                    "确认要硬出时传 force=true。"))
    # 不足 25 格时不再循环补足：重复同一镜头会生成一模一样的画面。
    # 版面上的空位由 compose_contact_sheet 画成占位格，保持 5×5 但不伪造重复。
    pipe = STATE.pipeline()
    try:
        job_id = new_job("grid25", "生成 25 格分镜")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    attach_job(pipe, job_id, 0.02, 0.94)

    def work() -> dict[str, Any]:
        out_dir = Path(config.PROJECT_DIR) / project.id / "grid25_cells"
        out_dir.mkdir(parents=True, exist_ok=True)
        # 第 6 步顺序：先姿态、后 25 格。缺姿态的关键镜头在这里自动补，
        # 这样每一格都有骨架可用，不再只是"文生图碰运气"。
        # Qwen T2I 不挂自动姿态：25 格与最终出图同一条路径，避免所有单人镜头
        # 被同一张全身骨架固定成同一站姿。
        if getattr(pipe, "storyboard_t2i", False):
            made = 0
            log.info("25 格：Qwen T2I 模式跳过前置姿态，按提示词/参考图生成")
        else:
            made = _ensure_shot_poses(project, key_shots)
        if made:
            project.save()
            update_job(job_id, f"已补齐 {made} 个前置姿态，开始生成 25 格…", 0.04)
        pose_indexes = {s.index for s in _select_pose_shots(key_shots)}
        paths, labels, cells = [], [], []
        total = len(key_shots)
        for i, s in enumerate(key_shots, start=1):
            update_job(job_id, f"生成 25 格分镜 {i}/{total}…", 0.02 + 0.92 * (i - 1) / total)
            # 纯道具格直接用上传的道具图；不要再用文生图补人
            if not (s.characters or []) and s.prop_image and Path(s.prop_image).exists():
                try:
                    from PIL import Image as _Img, ImageOps as _ImgOps
                    cell = out_dir / f"cell_{i:02d}_shot{s.index}.png"
                    im = _ImgOps.fit(_Img.open(s.prop_image).convert("RGB"),
                                     (512, 512), method=_Img.LANCZOS, centering=(0.5, 0.5))
                    im.save(cell)
                    paths.append(str(cell)); labels.append(f"{i:02d}")
                    cells.append({"shot": s.index, "prompt": "纯道具格：使用上传道具图",
                                  "image": str(cell)})
                    prompt_trace.record_data(project.id, "05_grid25",
                                             f"cell_{i:02d}_shot{s.index}",
                                             {"shot": s.index, "prompt": "纯道具格：使用上传道具图",
                                              "image": str(cell)})
                    continue
                except Exception as exc:  # noqa: BLE001
                    log.warning("25 格道具格 %s 使用道具图失败：%s", s.index, exc)
            # 25 格附图直接复用该镜头的无气泡原图，避免 Qwen 在格子里
            # 又画空白气泡/文字；没有原图才重新生成。
            _raw = str(getattr(s, "raw_image_path", "") or "")
            if _raw and Path(_raw).exists() and not getattr(s, "has_dialogue", False):
                try:
                    from PIL import Image as _Img, ImageOps as _ImgOps
                    cell = out_dir / f"cell_{i:02d}_shot{s.index}.png"
                    _cw, _ch = _grid_cell_size(s)
                    im = _ImgOps.fit(_Img.open(_raw).convert("RGB"), (_cw, _ch),
                                     method=_Img.LANCZOS, centering=(0.5, 0.45))
                    im.save(cell)
                    paths.append(str(cell)); labels.append(f"{i:02d}")
                    cells.append({"shot": s.index, "prompt": "复用无气泡原图", "image": str(cell)})
                    prompt_trace.record_data(project.id, "05_grid25",
                                             f"cell_{i:02d}_shot{s.index}",
                                             {"shot": s.index, "prompt": "复用无气泡原图", "image": str(cell)})
                    continue
                except Exception as exc:  # noqa: BLE001
                    log.warning("25 格复用原图失败：%s", exc)
            prompt = pipe.build_shot_prompt(project, s, include_bubbles=False,
                                            draw_bubbles=False)
            prompt += ("；这是 25 格分镜附图：画面里只有人物、道具与场景，"
                       "画面干净完整，四边满版。")
            try:
                refs = pipe.build_shot_references(project, s)
                pose = ""
                if not getattr(pipe, "storyboard_t2i", False) and s.index in pose_indexes:
                    pose = str(getattr(s, "pose_skeleton", "") or
                               getattr(s, "pose_image", "") or "")
                    if (pose and Path(pose).exists()
                            and str(getattr(s, "shot_type", "") or "") not in ("特写", "近景")
                            and len([n for n in (s.characters or []) if n]) <= 1):
                        pass
                    else:
                        pose = ""
                try:
                    res = pipe.image.generate(
                        prompt,
                        reference_paths=refs,
                        pose_image=pose or None,
                        negative_prompt=(prompts.negative_prompt(project, s, kind="shot")
                                         + ", 对话气泡, 空白气泡, 气泡框, 对话文字, 文字"),
                        size=_grid_cell_size(s), seed=20263000 + s.index)
                except Exception as exc:  # noqa: BLE001
                    # 少数 ComfyUI 版本不接受"ControlNet + 多参考"同图，
                    # 退回只送参考图（身份优先），姿势仍由文字描述约束。
                    log.warning("25 格第 %s 格 姿态+参考失败，退回参考图：%s", s.index, exc)
                    res = pipe.image.generate(
                        prompt,
                        reference_paths=refs,
                        negative_prompt=(prompts.negative_prompt(project, s, kind="shot")
                                         + ", 对话气泡, 空白气泡, 气泡框, 对话文字, 文字"),
                        size=_grid_cell_size(s), seed=20263000 + s.index)
                cell = out_dir / f"cell_{i:02d}_shot{s.index}.png"
                res.image.save(cell)
                paths.append(str(cell)); labels.append(f"{i:02d}")
                cells.append({"shot": s.index, "prompt": prompt, "image": str(cell)})
                prompt_trace.record_data(project.id, "05_grid25",
                                         f"cell_{i:02d}_shot{s.index}",
                                         {"shot": s.index, "prompt": prompt, "image": str(cell)})
            except Exception as exc:  # noqa: BLE001
                log.warning("25 格第 %s 格失败：%s", s.index, exc)
        if not paths:
            raise RuntimeError("25 格全部生成失败")
        # 旧版循环补足会留下 cell_15..25 这类重复文件，重生成后一并清掉
        keep = {Path(p).name for p in paths}
        for old in out_dir.glob("cell_*.png"):
            if old.name not in keep:
                old.unlink(missing_ok=True)
        out = Path(config.PROJECT_DIR) / project.id / "25宫格_分镜结果.png"
        keyframes_mod.compose_contact_sheet(paths, str(out), 5, 512, labels,
                                            total_slots=25)
        project.grid25_image = str(out)
        project.grid25_shots = [int(s.index) for s in key_shots]
        project.grid25_status = "已完成"
        project.touch(); project.save()
        update_job(job_id, f"25 格分镜完成（{len(paths)}/25）", 1.0)
        return {"message": f"25 格分镜已生成（{len(paths)}/25 格，其余为占位格）",
                "image": str(out), "count": len(paths), "cells": cells}

    run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/storyboard/poses25")
def api_storyboard_poses25() -> dict[str, Any]:
    """第 6 步：为 25 个关键镜头生成对应的姿态骨架，并落盘保存。"""
    project = STATE.get_project()
    if not project.shots:
        raise HTTPException(status_code=400, detail="请先完成分镜拆解")
    key_shots = keyframes_mod.select_key_shots(project, 25, require_image=False)
    out_dir = Path(config.PROJECT_DIR) / project.id / "poses"
    out_dir.mkdir(parents=True, exist_ok=True)
    # 重新生成前先清掉上一轮自动骨架；用户自己上传到 data/poses 的
    # 参考图不能误删，只清项目 poses 目录下的自动文件。
    for old in out_dir.glob("pose_*.png"):
        old.unlink(missing_ok=True)
    for s in project.shots:
        if s.pose_skeleton:
            s.pose_skeleton = ""
        if s.pose_image and str(s.pose_image).startswith(str(out_dir)):
            s.pose_image = ""
    pipe = STATE.pipeline()
    if getattr(pipe, "storyboard_t2i", False):
        project.touch()
        STATE.save()
        return {
            "message": "Qwen T2I 模式：自动姿态已停用；需要动作锁定时请在格子上传自定义姿态",
            "items": [],
            "project": serialize_project(project),
        }
    rows = []
    pose_targets = _select_pose_shots(key_shots)
    for i, s in enumerate(key_shots, start=1):
        if s not in pose_targets:
            s.pose_skeleton = ""
            if s.pose_image and str(s.pose_image).startswith(str(out_dir)):
                s.pose_image = ""
            rows.append({"shot": s.index, "skeleton": "",
                         "reason": "非关键动作/静态对白，不挂姿态（省时）"})
            continue
        if len([n for n in (s.characters or []) if n]) > 1:
            s.pose_skeleton = ""
            if s.pose_image and str(s.pose_image).startswith(str(out_dir)):
                s.pose_image = ""
            rows.append({"shot": s.index, "skeleton": "", "reason": "多人物镜头不挂单骨架"})
            continue
        if str(getattr(s, "shot_type", "") or "") in ("特写", "近景"):
            s.pose_skeleton = ""
            if s.pose_image and str(s.pose_image).startswith(str(out_dir)):
                s.pose_image = ""
            rows.append({"shot": s.index, "skeleton": "", "reason": "特写/近景不挂全身骨架"})
            continue
        if not (s.characters or []):
            s.pose_skeleton = ""
            if s.pose_image and str(s.pose_image).startswith(str(out_dir)):
                s.pose_image = ""
            prompt_trace.record_data(project.id, "06_pose", f"shot_{s.index}",
                                     {"shot": s.index, "skeleton": "",
                                      "pose_reason": "无人物镜头，不挂姿态"})
            rows.append({"shot": s.index, "skeleton": "", "reason": "无人物镜头"})
            continue
        if not _grid_pose_needed(s):
            s.pose_skeleton = ""
            if s.pose_image and str(s.pose_image).startswith(str(out_dir)):
                s.pose_image = ""
            rows.append({"shot": s.index, "skeleton": "",
                         "reason": "非关键动作/静态对白，不挂姿态（省时）"})
            continue
        pose, reason, _dynamic = poseplan.choose_pose(s)
        if not pose or not Path(pose).exists():
            pose = str(Path(config.BASE_DIR) / "data" / "pose_library" / "views" / "1全身正面.png")
            reason = "正面兜底"
        dst = out_dir / f"pose_{i:02d}_shot{s.index}.png"
        shutil.copyfile(pose, dst)
        s.pose_skeleton = str(dst)
        if not s.pose_image:
            s.pose_image = str(dst)
        prompt_trace.record_data(project.id, "06_pose", f"shot_{s.index}",
                                 {"shot": s.index, "skeleton": str(dst),
                                  "pose_reason": reason,
                                  "character_actions": dict(s.character_actions or {})})
        rows.append({"shot": s.index, "skeleton": str(dst), "reason": reason})
    project.touch()
    STATE.save()
    return {"message": f"姿态骨架已生成（{len(rows)} 格）", "items": rows,
            "project": serialize_project(project)}


@app.post("/api/storyboard/enhance")
def api_storyboard_enhance(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    整理已有分镜（不重新拆解）。

    专门修复"原文搬运式"分镜：台词被抄进画面描述、dialogues 为空、
    镜头/情绪/场景全部退化成同一个值。整个过程确定性执行，不依赖模型听话。
    """
    project = STATE.get_project()
    if not project.shots:
        raise HTTPException(status_code=400, detail="还没有分镜，请先拆解分镜")
    data = payload or {}
    pipe = STATE.pipeline()
    use_llm = bool(data.get("llm_narration", True))
    try:
        job_id = new_job("enhance", "整理分镜")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    attach_job(pipe, job_id, 0.02, 0.96)

    def work() -> dict[str, Any]:
        report = pipe.enhance_storyboard(project, use_llm_narration=use_llm)
        project.save()
        return {"report": report, "project": serialize_project(project)}

    run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/storyboard/director")
def api_storyboard_director(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    导演优化（重拍镜头）。

    与"整理分镜"不同：整理分镜只做确定性规则；导演优化会让文本模型按快看
    条漫的镜头语言把每一格重写一遍（补机位/构图/光线/画幅，剔掉画外音误入
    画面的人物，把小说体描述改成可画的镜头）。
    """
    project = STATE.get_project()
    if not project.shots:
        raise HTTPException(status_code=400, detail="还没有分镜，请先拆解分镜")
    data = payload or {}
    pipe = STATE.pipeline()
    try:
        job_id = new_job("enhance", "导演优化")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    attach_job(pipe, job_id, 0.02, 0.96)

    def work() -> dict[str, Any]:
        try:
            changed = pipe.director_rewrite(project, reason="manual",
                                            raise_on_error=True)
        except Exception:
            # AI 不可用时至少把本地规则整理落盘，再把失败原因抛给前端，
            # 不能像以前那样静默返回 0 却提示“已重拍”。
            try:
                director_mod.normalize(project)
                project.save()
            except Exception:  # noqa: BLE001
                pass
            raise
        report = director_mod.normalize(project)
        project.save()
        return {"changed": changed, "report": report,
                "project": serialize_project(project)}

    run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/storyboard/save")
def api_storyboard_save(payload: dict[str, Any]) -> dict[str, Any]:
    """整体保存分镜列表（前端表格编辑后提交）。"""
    project = STATE.get_project()
    shots = payload.get("shots")
    if not isinstance(shots, list):
        raise HTTPException(status_code=400, detail="缺少 shots 参数")
    existing = {s.id: s for s in project.shots}
    new_shots: list[Shot] = []
    for item in shots:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id", "") or "")
        shot = existing.get(sid) or Shot()
        shot.shot_type = str(item.get("shot_type") or shot.shot_type)
        chars = item.get("characters") or []
        if isinstance(chars, str):
            chars = [x.strip() for x in chars.replace("，", "、").replace(",", "、")
                     .split("、") if x.strip()]
        shot.characters = [str(x).strip() for x in chars if str(x).strip()]
        shot.scene = str(item.get("scene", "") or "")
        desc = str(item.get("description", "") or "").strip()
        if desc and desc != shot.description:
            shot.description = desc
            shot.bubble_plan = []
            if shot.status == "已完成":
                shot.status = "待生成"
                shot.image_path = ""
                shot.raw_image_path = ""
                shot.bubble_layout = []
        shot.mood = str(item.get("mood") or shot.mood)
        shot.source_text = str(item.get("source_text", "") or "")
        # 导演层字段（表格里可直接改；空值表示交给自动整理）
        for attr in ("camera", "composition", "lighting", "transition",
                     "panel_scale", "focus", "action", "expression",
                     "time_of_day", "beat", "pacing", "panel_emphasis",
                     "caption", "layout_hint"):
            if attr in item:
                setattr(shot, attr, str(item.get(attr) or "").strip())
        if "char_sides" in item and isinstance(item.get("char_sides"), dict):
            shot.char_sides = {str(k): str(v) for k, v in item["char_sides"].items()}
        if "silent" in item:
            shot.silent = bool(item.get("silent"))
        if "hook" in item:
            shot.hook = bool(item.get("hook"))
        dialogues = item.get("dialogues")
        if isinstance(dialogues, list):
            old_sig = [(d.speaker, d.text, d.kind, d.slot, d.offscreen)
                       for d in shot.dialogues]
            parsed: list[Dialogue] = []
            for d in dialogues:
                if not isinstance(d, dict):
                    continue
                text = str(d.get("text", "") or "").strip()
                if not text:
                    continue
                kind = str(d.get("kind") or "speech")
                parsed.append(Dialogue(
                    speaker=str(d.get("speaker", "") or ""), text=text,
                    tone=str(d.get("tone", "") or ""),
                    bubble_style=str(d.get("bubble_style", "") or ""),
                    kind=kind if kind in ("speech", "narration", "sfx") else "speech",
                    slot=str(d.get("slot", "") or ""),
                    tail=str(d.get("tail", "") or ""),
                    offscreen=bool(d.get("offscreen", False)),
                    size=str(d.get("size", "") or "")))
            shot.dialogues = parsed[:config.MAX_BUBBLES_PER_SHOT * 2]
            new_sig = [(d.speaker, d.text, d.kind, d.slot, d.offscreen) for d in shot.dialogues]
            if new_sig != old_sig:
                # 台词变了，出图时预留的空白气泡位置也就作废；下次出图重新规划
                shot.bubble_plan = []
        new_shots.append(shot)
    project.shots = new_shots
    project.reindex_shots()
    # 分镜改动后页面编组必然失效：清空并按新分镜做本地重排（很快，不调模型）
    project.clear_pages()
    if project.shots:
        try:
            STATE.pipeline().plan_pages(project, use_llm=False, force=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("保存分镜后重排页面失败：%s", exc)
    project.save()
    return {"message": f"已保存 {len(project.shots)} 格分镜",
            "project": serialize_project(project)}


@app.post("/api/storyboard/operate")
def api_storyboard_operate(payload: dict[str, Any]) -> dict[str, Any]:
    """分镜操作：新增 / 删除 / 上移 / 下移 / 合并 / 拆分。"""
    project = STATE.get_project()
    sid = str(payload.get("id", "") or "")
    op = str(payload.get("op", "") or "")
    shot = project.shot_by_id(sid)
    if shot is None and sid.isdigit():
        idx = int(sid) - 1
        if 0 <= idx < len(project.shots):
            shot = project.shots[idx]
    if shot is None:
        raise HTTPException(status_code=404, detail=f"未找到分镜：{sid}")

    if op == "delete":
        index = shot.index
        project.delete_shot(shot.id)
        message = f"已删除第 {index} 格"
    elif op == "up":
        message = f"第 {shot.index} 格已上移" if project.move_shot(shot.id, -1) \
            else "已经是第一格，无法上移"
    elif op == "down":
        message = f"第 {shot.index} 格已下移" if project.move_shot(shot.id, 1) \
            else "已经是最后一格，无法下移"
    elif op == "merge":
        message = f"第 {shot.index} 格已与下一格合并" if project.merge_shots(shot.id) \
            else "最后一格无法向下合并"
    elif op == "split":
        message = f"第 {shot.index} 格已拆分为两格" if project.split_shot(shot.id) \
            else "拆分失败"
    elif op == "add":
        new_shot = project.add_shot(shot.id)
        message = f"已在第 {shot.index} 格后新增第 {new_shot.index} 格"
    else:
        raise HTTPException(status_code=400, detail=f"未知操作：{op}")
    project.reindex_shots()
    project.clear_pages()
    if project.shots:
        try:
            STATE.pipeline().plan_pages(project, use_llm=False, force=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("分镜操作后重排页面失败：%s", exc)
    project.save()
    log.info(message)
    return {"message": message, "project": serialize_project(project)}


@app.post("/api/storyboard/shot")
def api_storyboard_shot(payload: dict[str, Any]) -> dict[str, Any]:
    """新增一个空白分镜（放在末尾或指定格之后）。"""
    project = STATE.get_project()
    after = str(payload.get("after", "") or "")
    shot = project.add_shot(after or None)
    try:
        STATE.pipeline().plan_pages(project, use_llm=False, force=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("新增分镜后重排页面失败：%s", exc)
    project.save()
    return {"message": f"已新增第 {shot.index} 格",
            "project": serialize_project(project), "shot_id": shot.id}


# =============================================================================
# 阶段五：出图
# =============================================================================

@app.post("/api/generate/start")
def api_generate_start(payload: dict[str, Any]) -> dict[str, Any]:
    """批量生成分镜图。"""
    project = STATE.get_project()
    if not project.shots:
        raise HTTPException(status_code=400, detail="请先完成分镜拆解")
    pipe = STATE.pipeline()
    bubbles = bool(payload.get("bubbles", config.BUBBLE_ENABLED_BY_DEFAULT))
    position = str(payload.get("position") or config.BUBBLE_DEFAULT_POSITION)
    retry_failed = bool(payload.get("retry_failed", False))
    # 整理过分镜之后通常需要整章重绘：force=true 时不跳过已完成格
    force = bool(payload.get("force", False))
    only_ids = payload.get("shot_ids") or None
    # 画幅：条漫模式支持"自动（按镜头分档）"或显式的 宽×高 / 高度；
    # 旧方图模式仍按 512/768/1024/1344 的正方形预设解析。
    size_text = str(payload.get("size") or "").strip()
    size: tuple[int, int] | None = None
    if getattr(config, "WEBTOON_MODE", False):
        m = re.search(r"(\d{3,4})\s*[×xX*]\s*(\d{3,4})", size_text)
        if m:
            size = (int(m.group(1)), int(m.group(2)))
        else:
            h = re.search(r"(\d{3,4})", size_text)      # 只给高度时沿用条漫统一宽度
            if h:
                size = (config.WEBTOON_PANEL_WIDTH, int(h.group(1)))
    else:
        for edge in (512, 768, 1024, 1344):
            if str(edge) in size_text:
                size = (edge, edge)
                break

    # 老工程没有 handraw 风格时，出图前补一次本地推荐（不调模型，很快）
    if not project.style_id and project.style_auto and bool(getattr(config, "HANDRAW_ENABLED", True)):
        try:
            pipe.recommend_style(project, use_llm=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("出图前风格推荐失败：%s", exc)
    # 分格页模式下画幅由版式决定，忽略界面上的统一画幅
    if project.pages and (project.render_mode or config.render_mode()) != "webtoon":
        size = None

    batch = imggen.ShotBatchGenerator(pipe)
    STATE.batch = batch
    auto_repair = payload.get("auto_repair")
    auto_repair = None if auto_repair is None else bool(auto_repair)
    auto_pose = payload.get("auto_pose")
    auto_pose = None if auto_pose is None else bool(auto_pose)
    try:
        job_id = new_job("generate", "分镜出图")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    attach_job(pipe, job_id, 0.02, 0.96)
    # 把任务进度真正接到出图器上，界面的进度条与日志才有内容
    batch.progress = lambda m, v: update_job(job_id, m, 0.02 + max(0.0, min(1.0, v)) * 0.94)
    batch.log = log.info

    def work() -> dict[str, Any]:
        batch.run(project, shot_ids=only_ids, bubbles_enabled=bubbles,
                  bubble_position=position, size=size,
                  retry_failed_only=retry_failed, skip_done=not force,
                  auto_repair=auto_repair, auto_pose=auto_pose)
        project.save()
        return serialize_project(project)

    run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/generate/pause")
def api_generate_pause() -> dict[str, Any]:
    if STATE.batch is None:
        raise HTTPException(status_code=400, detail="当前没有生成任务")
    STATE.batch.pause()
    return {"message": "已暂停生成"}


@app.post("/api/generate/resume")
def api_generate_resume() -> dict[str, Any]:
    if STATE.batch is None:
        raise HTTPException(status_code=400, detail="当前没有生成任务")
    STATE.batch.resume()
    return {"message": "已继续生成"}


@app.post("/api/generate/cancel")
def api_generate_cancel() -> dict[str, Any]:
    if STATE.batch is None:
        raise HTTPException(status_code=400, detail="当前没有生成任务")
    STATE.batch.cancel()
    return {"message": "已取消生成"}


@app.post("/api/generate/single")
def api_generate_single(payload: dict[str, Any]) -> dict[str, Any]:
    """单独重新生成某一格。"""
    project = STATE.get_project()
    sid = str(payload.get("id", "") or "")
    shot = project.shot_by_id(sid)
    if shot is None:
        raise HTTPException(status_code=404, detail=f"未找到分镜：{sid}")
    pipe = STATE.pipeline()
    bubbles = bool(payload.get("bubbles", config.BUBBLE_ENABLED_BY_DEFAULT))
    position = str(payload.get("position") or config.BUBBLE_DEFAULT_POSITION)
    try:
        job_id = new_job("single_shot", f"重新生成第 {shot.index} 格")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    def work() -> dict[str, Any]:
        update_job(job_id, f"正在生成第 {shot.index} 格（含自检与自动返修）…", 0.12)
        batch = imggen.ShotBatchGenerator(pipe)
        batch.progress = lambda m, v: update_job(job_id, m, 0.12 + max(0.0, min(1.0, v)) * 0.82)
        batch.log = log.info
        auto_repair = payload.get("auto_repair")
        auto_repair = None if auto_repair is None else bool(auto_repair)
        auto_pose = payload.get("auto_pose")
        auto_pose = None if auto_pose is None else bool(auto_pose)
        batch.run(project, shot_ids=[shot.id], bubbles_enabled=bubbles,
                  bubble_position=position, skip_done=False,
                  auto_repair=auto_repair, auto_pose=auto_pose)
        if shot.status == "失败":
            raise RuntimeError(shot.error or f"第 {shot.index} 格生成失败")
        # 这一格所属的页要跟着重拼（页图比成品图旧，compose 会自己判断）
        page = project.page_for_shot(shot.id)
        if page is not None and bool(getattr(config, "PAGE_COMPOSE_ON_GENERATE", True)):
            try:
                pagelayout.compose_page(project, page)
            except Exception as exc:  # noqa: BLE001
                log.warning("第 %s 页重拼失败：%s", page.index, exc)
        project.save()
        update_job(job_id, f"第 {shot.index} 格生成完成（自检 {shot.quality_score} 分）", 1.0)
        return {"shot": serialize_shot(shot, project)}

    run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/shot/skip")
def api_shot_skip(payload: dict[str, Any]) -> dict[str, Any]:
    project = STATE.get_project()
    shot = project.shot_by_id(str(payload.get("id", "") or ""))
    if shot is None:
        raise HTTPException(status_code=404, detail="未找到该分镜")
    shot.skip = bool(payload.get("skip", True))
    if shot.skip:
        shot.status = "跳过"
    elif shot.status == "跳过":
        # 取消跳过时恢复跳之前的可用状态：有成品图就回到已完成，不再强制重画
        has_image = bool(shot.image_path) and Path(shot.image_path).exists()
        shot.status = "已完成" if has_image else "待生成"
        if has_image:
            shot.error = ""
    project.save()
    return {"message": f"第 {shot.index} 格已{'标记跳过' if shot.skip else '取消跳过'}",
            "shot": serialize_shot(shot, project), "project": serialize_project(project)}


@app.post("/api/shot/panel-size")
def api_shot_panel_size(payload: dict[str, Any]) -> dict[str, Any]:
    """
    调整单格的条漫画幅（只改高度，宽度沿用条漫统一宽度）。

    height 为空或 0 表示恢复"按镜头类型自动分档"。
    已有画面不会自动重绘，新的画幅在该格下次生成时生效。
    """
    project = STATE.get_project()
    shot = project.shot_by_id(str(payload.get("id", "") or ""))
    if shot is None:
        raise HTTPException(status_code=404, detail="未找到该分镜")
    try:
        height = int(payload.get("height") or 0)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="画幅高度必须是数字") from None
    if height <= 0:
        shot.panel_w = shot.panel_h = 0
        msg = f"第 {shot.index} 格已恢复为按镜头自动分档"
    else:
        w, h = config.normalize_image_size((config.WEBTOON_PANEL_WIDTH, height))
        if (w, h) != (config.WEBTOON_PANEL_WIDTH, height):
            msg_note = f"（已对齐为 32 的倍数：{w}×{h}）"
        else:
            msg_note = ""
        shot.panel_w, shot.panel_h = w, h
        msg = f"第 {shot.index} 格画幅已设为 {w}×{h}{msg_note}，下次生成生效"
    project.save()
    return {"message": msg, "shot": serialize_shot(shot, project),
            "project": serialize_project(project)}


# =============================================================================
# 气泡编辑
# =============================================================================

@app.get("/api/bubble/layout")
def api_bubble_layout(shot_id: str = Query(...)) -> dict[str, Any]:
    project = STATE.get_project()
    shot = project.shot_by_id(shot_id)
    if shot is None:
        raise HTTPException(status_code=404, detail="未找到该分镜")
    if not shot.bubble_layout and shot.raw_image_path and \
            Path(shot.raw_image_path).exists():
        base = Image.open(shot.raw_image_path)
        layouts = bubble_mod.build_layout(
            shot, base.width, base.height,
            top_reserved=bubble_mod.narration_bottom_ratio(shot, base.width, base.height))
        shot.bubble_layout = [b.to_dict() for b in layouts]
        project.save()
    return {"shot": serialize_shot(shot, project), "layout": shot.bubble_layout}


@app.post("/api/bubble/preview")
def api_bubble_preview(payload: dict[str, Any]) -> dict[str, Any]:
    """按给定布局渲染预览图，返回可访问的临时图片路径。"""
    project = STATE.get_project()
    shot = project.shot_by_id(str(payload.get("id", "") or ""))
    if shot is None:
        raise HTTPException(status_code=404, detail="未找到该分镜")
    layout = payload.get("layout")
    if not isinstance(layout, list):
        layout = shot.bubble_layout
    source = shot.raw_image_path or shot.image_path
    if not source or not Path(source).exists():
        raise HTTPException(status_code=400, detail="该分镜还没有画面，请先生成图像")
    try:
        img = bubble_mod.preview_from_layout(source, shot, layout)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"预览渲染失败：{exc}") from exc
    out_dir = Path(config.SHOT_DIR) / project.id
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"预览_{shot.index:03d}.png"
    img.save(target, format="PNG")
    return {"preview_path": str(target)}


@app.post("/api/bubble/save")
def api_bubble_save(payload: dict[str, Any]) -> dict[str, Any]:
    """保存气泡布局并重绘成品图。"""
    project = STATE.get_project()
    shot = project.shot_by_id(str(payload.get("id", "") or ""))
    if shot is None:
        raise HTTPException(status_code=404, detail="未找到该分镜")
    layout = payload.get("layout")
    if isinstance(layout, list):
        shot.bubble_layout = layout
    try:
        imggen.regenerate_bubbles(project, shot, layouts=shot.bubble_layout,
                                  position=str(payload.get("position") or "") or None)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"重绘气泡失败：{exc}") from exc
    project.save()
    return {"message": f"第 {shot.index} 格的气泡已保存",
            "shot": serialize_shot(shot, project)}


@app.post("/api/bubble/auto")
def api_bubble_auto(payload: dict[str, Any]) -> dict[str, Any]:
    """按对话自动排布气泡。"""
    project = STATE.get_project()
    shot = project.shot_by_id(str(payload.get("id", "") or ""))
    if shot is None:
        raise HTTPException(status_code=404, detail="未找到该分镜")
    source = shot.raw_image_path
    if not source or not Path(source).exists():
        raise HTTPException(status_code=400, detail="该分镜还没有画面，请先生成图像")
    style = str(payload.get("style") or "")
    position = str(payload.get("position") or config.BUBBLE_DEFAULT_POSITION)
    base = Image.open(source)
    # 先按"模型画的气泡"定位并回填；一个都没找到才退回程序排布
    plan = list(getattr(shot, "bubble_plan", []) or [])
    matched = bubble_mod.detect_blank_bubbles(base, plan) if plan else []
    if any(matched):
        layouts = bubble_mod.layouts_from_plan(shot, plan, matched, base.width, base.height)
    else:
        layouts = bubble_mod.build_layout(
            shot, base.width, base.height, position,
            top_reserved=bubble_mod.narration_bottom_ratio(shot, base.width, base.height))
    objs = []
    for b in layouts:
        if style and style != "自动":
            b.style = style
        objs.append(b)
    shot.bubble_layout = [b.to_dict() for b in objs]
    img = bubble_mod.render_bubbles(base, objs, shot=shot)
    out_dir = Path(config.SHOT_DIR) / project.id
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"预览_{shot.index:03d}.png"
    img.save(target, format="PNG")
    project.save()
    return {"message": f"已排布 {len(objs)} 个气泡，样式：{style or '自动'}",
            "layout": shot.bubble_layout, "preview_path": str(target),
            "shot": serialize_shot(shot, project)}


# =============================================================================
# 导出与打包
# =============================================================================

@app.post("/api/export")
def api_export(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    project = STATE.get_project()
    if not project.shots:
        raise HTTPException(status_code=400, detail="还没有分镜，无法导出")
    data = payload or {}
    folder = str(data.get("folder_name") or "") or None
    reader = bool(data.get("reader", config.GENERATE_READER_FILE))
    make_zip = bool(data.get("zip", True))
    try:
        job_id = new_job("export", "导出打包")
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    def work() -> dict[str, Any]:
        def progress(msg: str, value: float) -> None:
            update_job(job_id, msg, 0.02 + value * 0.95)

        result = exporter.export_project(project, folder_name=folder,
                                         progress_cb=progress,
                                         generate_reader=reader, make_zip=make_zip)
        return result

    run_job(job_id, work)
    return {"job_id": job_id}


@app.post("/api/export/strip")
def api_export_strip(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    只重新拼接条漫长图（不重新导出其它产物）。

    改动某几格、或换了气泡布局之后，用它可以快速重出最终成品长条。
    """
    project = STATE.get_project()
    data = payload or {}
    # 有分格页时先合成页面，再按"页 → 未入页单格"的顺序拼条漫
    if project.pages:
        try:
            pagelayout.compose_all_pages(project)
        except Exception as exc:  # noqa: BLE001
            log.warning("拼接前合成页面失败：%s", exc)
    sources: list[str] = []
    if project.pages:
        sources = pagelayout.strip_tiles(project)
    if not sources:
        for s in project.shots:
            if s.skip:
                continue
            p = s.image_path or s.raw_image_path
            if p and Path(p).exists():
                sources.append(p)
    if not sources:
        raise HTTPException(status_code=400, detail="还没有可拼接的分镜成品图")

    out_dir = Path(project.output_dir) if project.output_dir else \
        Path(config.OUTPUT_ROOT) / (project.output_folder_name
                                    or config.default_output_name())
    strip_dir = out_dir / config.OUTPUT_SUBDIR_STRIP
    max_h = int(data.get("max_height") or config.WEBTOON_STRIP_MAX_HEIGHT)
    cover_backdrop = None
    for s in project.shots:
        if s.raw_image_path and Path(s.raw_image_path).exists() and not (s.bubble_plan or []):
            cover_backdrop = s.raw_image_path
            break
    try:
        ep = episode_mod.build_episode_strip(sources, project, strip_dir,
                                            max_height=max_h,
                                            cover_backdrop=cover_backdrop)
        made = ep.get("files") or []
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"拼接失败：{exc}") from exc
    project.save()
    total = sum(1 for _ in made)
    return {"message": f"已重新拼接 {total} 个条漫长图（共 {len(sources)} 格）",
            "files": made, "dir": str(strip_dir)}


@app.get("/api/export/list")
def api_export_list() -> dict[str, Any]:
    """列出已导出的结果文件夹。"""
    root = Path(config.OUTPUT_ROOT)
    items = []
    if root.exists():
        for d in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if d.is_dir() and not d.name.startswith("_"):
                files = [f for f in d.rglob("*") if f.is_file()]
                items.append({
                    "name": d.name, "path": str(d),
                    "files": len(files),
                    "modified": time.strftime("%Y-%m-%d %H:%M",
                                              time.localtime(d.stat().st_mtime)),
                })
    zips = []
    if root.exists():
        zips = [{"name": z.name, "path": str(z),
                 "size": z.stat().st_size}
                for z in sorted(root.glob("*.zip"),
                                key=lambda p: p.stat().st_mtime, reverse=True)]
    return {"folders": items[:30], "zips": zips[:30]}


@app.post("/api/storyboard/doc")
def api_storyboard_doc() -> dict[str, Any]:
    """生成分镜描述文档并返回下载路径。"""
    project = STATE.get_project()
    if not project.shots:
        raise HTTPException(status_code=400, detail="还没有分镜")
    docs_dir = Path(config.OUTPUT_ROOT) / "_文档"
    docs_dir.mkdir(parents=True, exist_ok=True)
    target = docs_dir / f"{project.title or '未命名作品'}_分镜脚本.md"
    target.write_text(exporter.build_storyboard_markdown(project), encoding="utf-8")
    return {"message": f"已生成文档：{target.name}", "file": str(target)}


# =============================================================================
# 静态前端
# =============================================================================

WEB_DIR = Path(config.WEB_DIR)
ASSETS_DIR = Path(config.ASSETS_DIR)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    page = WEB_DIR / "index.html"
    if not page.exists():
        return HTMLResponse("<h1>前端文件缺失</h1><p>找不到 web/index.html</p>",
                            status_code=500)
    return HTMLResponse(page.read_text(encoding="utf-8"))


@app.get("/favicon.ico")
def favicon() -> Response:
    icon = ASSETS_DIR / "favicon.png"
    if icon.exists():
        return FileResponse(icon, media_type="image/png")
    return Response(status_code=204)


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")


@app.exception_handler(HTTPException)
def http_error_handler(request, exc: HTTPException) -> JSONResponse:  # type: ignore[no-untyped-def]
    return JSONResponse(status_code=exc.status_code,
                        content={"ok": False, "detail": exc.detail})


@app.exception_handler(Exception)
def server_error_handler(request, exc: Exception) -> JSONResponse:  # type: ignore[no-untyped-def]
    log.error("未捕获异常：%s", exc)
    log.error(traceback.format_exc(limit=4))
    return JSONResponse(status_code=500,
                        content={"ok": False, "detail": f"服务器内部错误：{exc}"})


# =============================================================================
# 入口
# =============================================================================

def main() -> int:
    import uvicorn

    log.info("=" * 72)
    log.info("%s 启动中…", config.APP_TITLE)
    for key, value in config.config_summary().items():
        log.info("  %s：%s", key, value)
    log.info("  界面地址：http://%s:%d", config.WEB_HOST, config.WEB_PORT)
    log.info("=" * 72)
    if not config.api_key():
        log.warning("未读取到 API 密钥，真实模型调用会失败；"
                    "可在界面右上角「设置」中填写。")

    if config.WEB_OPEN_BROWSER:
        threading.Timer(1.5, lambda: webbrowser.open(
            f"http://{config.WEB_HOST}:{config.WEB_PORT}/")).start()

    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT,
                log_level="info", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())











