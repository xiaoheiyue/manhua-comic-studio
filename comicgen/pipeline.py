# -*- coding: utf-8 -*-
"""五阶段业务流水线。

阶段一：文本输入（由界面负责，本模块只做校验与清洗）
阶段二：小说内容分析（角色 / 场景 / 情节 / 风格）
阶段三：角色人设 + 主视觉图 + 三视图
阶段四：分镜拆解
阶段五：分镜图像批量生成（见 imggen.py，本模块负责单张与重试）
"""

from __future__ import annotations

import difflib
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from itertools import combinations
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PIL import Image, ImageOps, ImageChops, ImageFilter, ImageOps, ImageStat

import config
from . import bubble as bubble_mod
from . import charrefs
from . import director as director_mod
from . import pagelayout
from . import poseplan
from . import prompt_overrides
from . import prompt_trace
from . import prompts
from . import rules as rules_mod
from . import storyboard as storyboard_mod
from . import stylelib
from . import workflow_studio
from .imageclient import GenResult, ImageClient, ImageGenError
from .qwencomic import QwenComicClient
from .logger import get_logger
from .models import Character, Dialogue, Project, Scene, Shot
from .textclient import LLMError, TextClient
from .textutil import (chunk_with_offsets, normalize_novel, parse_json_block,
                       truncate, unescape_text)

log = get_logger(__name__)

ProgressCb = Callable[[str, float], None]      # (说明, 0~1 进度)
LogCb = Callable[[str], None]                  # 追加一行日志
StopCb = Callable[[], bool]                    # 返回 True 表示用户请求停止


def _noop_progress(msg: str, value: float) -> None:  # pragma: no cover
    log.info("%s (%.0f%%)", msg, value * 100)


def _noop_log(msg: str) -> None:  # pragma: no cover
    log.info(msg)


# =============================================================================
# 提示词关键词兜底提取
# =============================================================================

_KEYWORD_HINTS = (
    "发", "色", "眼", "眉", "脸", "肤", "身", "高", "瘦", "胖", "衣", "衫",
    "袍", "裙", "裤", "鞋", "帽", "镜", "疤", "饰", "戒", "剑", "杖", "纹",
    "气", "神", "风", "袖", "领", "带", "巾", "面",
)


def extract_keywords(text: str, limit: int | None = None) -> list[str]:
    """从人设描述里抽取可复用的外貌关键词（LLM 未给 keywords 时的兜底）。"""
    limit = limit or config.CHARACTER_KEYWORD_COUNT
    text = text or ""
    # 去掉小标题
    body = re.sub(r"【[^】]*】", " ", text)
    pieces = re.split(r"[，。；、,.;\n]+", body)
    out: list[str] = []
    for piece in pieces:
        p = piece.strip()
        if not (3 <= len(p) <= 16):
            continue
        if any(h in p for h in _KEYWORD_HINTS):
            if p not in out:
                out.append(p)
        if len(out) >= limit:
            break
    if not out:
        # 再退一步：直接取前几个短句
        out = [p.strip() for p in pieces if 3 <= len(p.strip()) <= 16][:limit]
    return out


# =============================================================================
# 三视图切分
# =============================================================================

def _column_activity(img: Image.Image) -> list[float]:
    """计算每一列的"内容活跃度"（用于找角色之间的空白分割线）。"""
    gray = img.convert("L")
    w, h = gray.size
    small = gray.resize((w, max(32, h // 8)), Image.BILINEAR)
    px = small.load()
    sw, sh = small.size
    activity: list[float] = []
    for x in range(sw):
        total = 0
        for y in range(sh):
            total += px[x, y]
        # 与整幅均值比较，统计与背景差异大的列
        activity.append(float(total) / sh)
    # 归一化
    lo, hi = min(activity), max(activity)
    span = (hi - lo) or 1.0
    return [(v - lo) / span for v in activity]


def _best_split(activity: list[float], target_ratio: float,
                window_ratio: float = 0.08) -> int:
    """在目标比例附近找活跃度最低的列作为分割线。"""
    n = len(activity)
    target = int(n * target_ratio)
    win = max(4, int(n * window_ratio))
    lo = max(1, target - win)
    hi = min(n - 2, target + win)
    if hi <= lo:
        return target
    best_idx, best_val = target, float("inf")
    for x in range(lo, hi + 1):
        val = activity[x]
        if val < best_val:
            best_val, best_idx = val, x
    return best_idx


def split_triple_view(path: str | Path,
                      out_dir: str | Path,
                      basename: str = "view") -> list[str]:
    """
    把一张三视图切成正面/侧面/背面三张单视图。

    返回三张图片的路径（front, side, back）。找不到合适分割线时按比例均分。
    """
    src = Image.open(path).convert("RGB")
    w, h = src.size
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        activity = _column_activity(src)
        ratios = config.TRIPLE_VIEW_SPLIT_RATIOS
        cut1 = _best_split(activity, ratios[0])
        cut2 = _best_split(activity, ratios[0] + ratios[1])
        if cut2 - cut1 < w * 0.12:
            cut1, cut2 = int(w / 3), int(w * 2 / 3)
    except Exception as exc:  # noqa: BLE001
        log.warning("三视图自动切分失败，改用均分：%s", exc)
        cut1, cut2 = int(w / 3), int(w * 2 / 3)

    boxes = [(0, 0, cut1, h), (cut1, 0, cut2, h), (cut2, 0, w, h)]
    names = ["正面", "侧面", "背面"]
    paths: list[str] = []
    for box, name in zip(boxes, names):
        crop = src.crop(box)
        # 裁掉左右多余留白
        crop = _trim_whitespace(crop)
        target = out_dir / f"{basename}_{name}.png"
        crop.save(target, format="PNG")
        paths.append(str(target))
    log.info("三视图已切分为：%s", "、".join(p.name for p in map(Path, paths)))
    return paths


def _compose_triple_views(view_paths: Sequence[str], out_path: str | Path,
                          size: tuple[int, int] | None = None) -> str:
    """把逐张生成的正面/侧面/背面横向拼成一张三视图（不依赖分割线）。"""
    width, height = size or config.CHARACTER_TRIPLE_SIZE
    canvas = Image.new("RGB", (width, height), (236, 236, 236))
    per = max(1, width // 3)
    for i, path in enumerate(list(view_paths)[:3]):
        try:
            im = Image.open(path).convert("RGB")
            im = ImageOps.fit(im, (per, height), method=Image.LANCZOS,
                              centering=(0.5, 0.5))
        except Exception:  # noqa: BLE001
            continue
        canvas.paste(im, (i * per, 0))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, format="PNG")
    return str(out)


def _main_from_front(front_path: str | Path, out_path: str | Path,
                     size: tuple[int, int] | None = None) -> str:
    """把三视图的正面单视图放到 1024² 画布上，作为主视觉（保证服装一致）。"""
    width, height = size or config.CHARACTER_IMAGE_SIZE
    try:
        im = Image.open(front_path).convert("RGB")
        # 用同一张图放大模糊做背景，再居中放人；比纯色竖条自然
        bg = im.resize((width, height), Image.LANCZOS).filter(
            ImageFilter.GaussianBlur(max(18, width // 24)))
        canvas = bg.convert("RGB")
        scale = min(width / im.width, height / im.height)
        nw, nh = max(1, int(im.width * scale)), max(1, int(im.height * scale))
        fg = im.resize((nw, nh), Image.LANCZOS)
        canvas.paste(fg, ((width - nw) // 2, (height - nh) // 2))
    except Exception:  # noqa: BLE001
        return str(out_path)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, format="PNG")
    return str(out)


def _view_prompt(base: str, label: str) -> str:
    """单张正面/侧面/背面的提示词（比"一张图画三个人"更稳）。"""
    if label == "背面":
        return (base + "单人背影，背对镜头，画面中只有一个人，只有他的后背，"
                "看不见脸，不能出现正面人物；同一件外层服装的背面结构，"
                "短款下摆、袖口与肩线清楚；全身像，同一角色、同一套服装，"
                "背景简洁中性纯色。")
    if label == "侧面":
        return (base + "侧面视图，side view，90度侧身，全身像；"
                "同一件外层服装的侧面轮廓清楚，同一角色、同一套服装，"
                "背景简洁中性纯色，画面里只有这个角色。")
    return (base + "正面视图，front view，正对镜头，全身像；"
            "同一件外层服装的正面结构清楚，同一角色、同一套服装，"
            "背景简洁中性纯色，画面里只有这个角色。")


def _crop_pose_front(sheet_path: str | Path,
                     out_path: str | Path,
                     size: tuple[int, int] | None = None) -> str:
    """从姿态总图裁出正面全身，做成主视图。

    不再用固定像素比例：先按白底做连通域，取左侧第一个“足够高”的人物，
    这样模型把正面放在左/中/右都能裁到完整全身，不会只裁到腰。
    姿态总图和主视图因此一定是同一张脸、同一套衣服。
    """
    width, height = size or config.CHARACTER_IMAGE_SIZE
    im = Image.open(sheet_path).convert("RGB")
    w, h = im.size
    crop = None
    try:
        import numpy as np
        from scipy import ndimage

        arr = np.asarray(im).astype(np.int16)
        # 背景色从四角估计，兼容纯白/米白/浅灰底
        corners = np.concatenate([
            arr[:8, :8].reshape(-1, 3), arr[:8, -8:].reshape(-1, 3),
            arr[-8:, :8].reshape(-1, 3), arr[-8:, -8:].reshape(-1, 3)])
        bg = np.median(corners, axis=0)
        mask = (np.abs(arr - bg).max(axis=2) > 16)
        mask = ndimage.binary_dilation(mask, iterations=3)
        labels, count = ndimage.label(mask)
        best = None
        for idx in range(1, count + 1):
            ys, xs = np.where(labels == idx)
            if len(xs) < 0.01 * w * h:
                continue
            x0, x1 = int(xs.min()), int(xs.max())
            y0, y1 = int(ys.min()), int(ys.max())
            cw, ch = x1 - x0, y1 - y0
            if ch < h * 0.28 or cw < w * 0.04:
                continue
            # 正面通常是最左侧的高个人物；同高度时取面积大的
            key = (y0 > h * 0.45, x0, -ch)
            if best is None or key < best[0]:
                best = (key, (x0, y0, x1, y1), idx)
        if best is not None:
            x0, y0, x1, y1 = best[1]
            pad_x = max(12, int((x1 - x0) * 0.06))
            pad_y = max(12, int((y1 - y0) * 0.03))
            cx0, cy0 = max(0, x0 - pad_x), max(0, y0 - pad_y)
            cx1, cy1 = min(w, x1 + pad_x), min(h, y1 + pad_y)
            # 只保留选中的正面人物，其它小人/阴影/背景统一刷白
            keep = ndimage.binary_dilation(labels == best[2], iterations=2)
            arr_out = np.full((cy1 - cy0, cx1 - cx0, 3), 255, dtype=np.uint8)
            sub = arr[cy0:cy1, cx0:cx1].astype(np.uint8)
            km = keep[cy0:cy1, cx0:cx1]
            arr_out[km] = sub[km]
            crop = Image.fromarray(arr_out)
    except Exception:  # noqa: BLE001
        crop = None
    if crop is None or crop.width < 60 or crop.height < 160:
        crop = im.crop((0, 0, int(w * 0.52), int(h * 0.90)))
    crop = _trim_whitespace(crop, tolerance=10)
    if crop.width < 40 or crop.height < 80:
        crop = im.crop((0, 0, int(w * 0.55), int(h * 0.92)))
    scale = min(width / crop.width, height / crop.height)
    nw, nh = max(1, int(crop.width * scale)), max(1, int(crop.height * scale))
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    fg = crop.resize((nw, nh), Image.LANCZOS)
    canvas.paste(fg, ((width - nw) // 2, (height - nh) // 2))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, format="PNG")
    return str(out)


def _trim_whitespace(img: Image.Image, tolerance: int = 12) -> Image.Image:
    """去掉图像四周与背景色接近的留白。"""
    try:
        gray = img.convert("L")
        w, h = gray.size
        bg = Image.new("L", gray.size, gray.getpixel((1, 1)))
        diff = ImageChops.difference(gray, bg)
        mask = diff.point(lambda v: 255 if v > tolerance else 0)
        bbox = mask.getbbox()
        if not bbox:
            return img
        x0, y0, x1, y1 = bbox
        pad = 6
        x0 = max(0, x0 - pad)
        y0 = max(0, y0 - pad)
        x1 = min(w, x1 + pad)
        y1 = min(h, y1 + pad)
        if (x1 - x0) < w * 0.15 or (y1 - y0) < h * 0.3:
            return img
        return img.crop((x0, y0, x1, y1))
    except Exception:  # noqa: BLE001
        return img


# =============================================================================
# 流水线
# =============================================================================

class Pipeline:
    """把五个阶段串起来的业务入口。"""

    def __init__(self,
                 text_client: TextClient | None = None,
                 image_client: ImageClient | None = None,
                 progress_cb: ProgressCb | None = None,
                 log_cb: LogCb | None = None,
                 stop_cb: StopCb | None = None):
        self.text = text_client if text_client is not None else TextClient()
        if image_client is not None:
            self.image = image_client
        elif str(getattr(config, "IMAGE_FAMILY", "zimage")).lower() == "qwen":
            self.image = QwenComicClient()
        else:
            self.image = ImageClient()
        self.progress = progress_cb or _noop_progress
        self.log = log_cb or _noop_log
        self.should_stop = stop_cb or (lambda: False)
        # 服务器在拿到 job_id 后调用 bind_reporter，把真实任务进度接进来
        self._reporter: ProgressCb | None = None
        # 最近一次拆解/整理的非致命提示（AI 不可用、回退本地规则等）
        self.last_notice = ""
        # 项目里绑定了自定义工作流时，按目标缓存对应客户端
        self._bound_clients: dict[str, ImageClient] = {}

    def _bound_image_client(self, project: Project,
                            target: str) -> ImageClient | None:
        """读取 project.settings.workflow_bindings[target] 对应的出图客户端。"""
        bindings = (getattr(project, "settings", {}) or {}).get("workflow_bindings")
        if not isinstance(bindings, dict):
            return None
        workflow_id = str(bindings.get(target) or "").strip()
        if not workflow_id:
            return None
        path = workflow_studio.workflow_path(workflow_id)
        if path is None:
            self.log(f"绑定的工作流不存在：{workflow_id}，改用项目默认工作流")
            return None
        key = str(path)
        client = self._bound_clients.get(key)
        if client is None:
            client = ImageClient(workflow=path.stem, workflow_file=str(path))
            self._bound_clients[key] = client
        return client

    def bind_reporter(self, cb: ProgressCb) -> None:
        """绑定任务进度回调（与日志一起由 _emit 触发）。"""
        self._reporter = cb

    @property
    def refs_supported(self) -> bool:
        """
        当前出图后端是否接受参考图。

        ComfyUI 用纯文生图工作流（Z-Image-Turbo）时为 False：参考图送不进去，
        角色一致性只能靠提示词里的外貌设定，因此提示词里也要去掉
        "第 N 张参考图是某某"这类说明，免得模型以为真有图可看。
        """
        return bool(getattr(self.image, "supports_reference_images", True))

    @property
    def storyboard_t2i(self) -> bool:
        """Qwen 分镜是否走纯文生图（与第 6 步 25 格同一条路径）。

        Qwen Edit 会把角色主视图的姿势/背景一起编辑进来，自动姿态 ControlNet
        又会让所有单人镜头变成同一张站姿；实测 25 格 T2I 的构图明显更贴提示词。
        """
        return (isinstance(self.image, QwenComicClient)
                and bool(getattr(config, "QWEN_STORYBOARD_T2I", True)))

    @staticmethod
    def is_user_pose(path: str) -> bool:
        """用户自己上传的单格姿态（data/poses），不是第 6 步自动骨架。"""
        try:
            base = (Path(config.DATA_DIR) / "poses").resolve()
            return Path(path).resolve().is_relative_to(base)
        except Exception:  # noqa: BLE001
            return False

    def _emit(self, msg: str, value: float) -> None:
        clamped = max(0.0, min(1.0, value))
        try:
            self.progress(msg, clamped)
        except Exception:  # noqa: BLE001
            pass
        if self._reporter is not None:
            try:
                self._reporter(msg, clamped)
            except Exception:  # noqa: BLE001
                pass
        self.log(msg)

    def _check_stop(self) -> bool:
        try:
            return bool(self.should_stop())
        except Exception:  # noqa: BLE001
            return False

    @contextmanager
    def _progress_window(self, base: float, span: float):
        """把某个阶段的 0~1 进度映射到整条流水线的一段，避免阶段间跳变。"""
        old_progress, old_reporter = self.progress, self._reporter

        def wrap(fn):
            if fn is None:
                return None
            return lambda m, v: fn(m, base + span * max(0.0, min(1.0, float(v))))

        self.progress = wrap(old_progress) or _noop_progress
        self._reporter = wrap(old_reporter)
        try:
            yield
        finally:
            self.progress, self._reporter = old_progress, old_reporter

    # ==================================================================
    # 阶段二：小说内容分析
    # ==================================================================
    def analyze_novel(self, project: Project) -> Project:
        """阅读整篇小说，识别角色、场景、情节、风格。"""
        text = normalize_novel(project.novel_text)
        if len(text) < config.MIN_NOVEL_LENGTH:
            raise ValueError("请先输入或上传小说文本")
        if len(text) > config.MAX_NOVEL_LENGTH:
            self.log(f"小说过长（{len(text)} 字），仅分析前 {config.MAX_NOVEL_LENGTH} 字")
            text = text[:config.MAX_NOVEL_LENGTH]
        project.novel_text = text

        chunks = [c for _, c in chunk_with_offsets(text)]
        total = len(chunks)
        self._emit(f"开始分析小说，共 {total} 个文本段", 0.02)

        # ---- 1) 分段调用文本模型 ----
        partials: list[dict] = []
        errors: list[str] = []

        def run_one(idx_chunk: tuple[int, str]) -> tuple[int, dict | None, str]:
            idx, chunk = idx_chunk
            try:
                msgs = prompts.novel_overview_prompt(chunk, idx + 1, total)
                msgs = prompt_overrides.apply_messages(
                    project, "analysis.overview", f"chunk:{idx + 1}", msgs)
                prompt_trace.record(project.id, "01_analysis", f"overview_{idx + 1}", msgs,
                                    meta={"chunk": idx + 1, "total": total,
                                          "chars": len(chunk)})
                data = self.text.chat_json(msgs, expect="object", json_mode=True)
                if isinstance(data, dict):
                    return idx, data, ""
                return idx, None, "返回结构不是 JSON 对象"
            except Exception as exc:  # noqa: BLE001
                return idx, None, truncate(str(exc), 200)

        results: dict[int, dict] = {}
        if total == 1:
            idx, data, err = run_one((0, chunks[0]))
            if data:
                results[idx] = data
            else:
                errors.append(err)
            self._emit("小说分析完成", 0.5)
        else:
            done = 0
            with ThreadPoolExecutor(max_workers=max(1, config.CHUNK_CONCURRENCY)) as pool:
                futures = {pool.submit(run_one, item): item[0]
                           for item in enumerate(chunks)}
                for fut in as_completed(futures):
                    idx, data, err = fut.result()
                    if data:
                        results[idx] = data
                    else:
                        errors.append(f"第 {idx + 1} 段分析失败：{err}")
                    done += 1
                    self._emit(f"已分析 {done}/{total} 段", 0.04 + 0.44 * done / total)

        if not results:
            raise LLMError("小说分析全部失败：" + "；".join(errors[:3]) +
                           "。请检查 config.py 中的 TEXT_API_BASE / TEXT_MODEL 配置，"
                           "以及文本模型服务是否已启动。")

        partials = [results[k] for k in sorted(results)]

        # ---- 2) 合并 ----
        if len(partials) == 1:
            merged = partials[0]
        else:
            self._emit("正在合并分段分析结果…", 0.52)
            try:
                msgs = prompts.merge_analysis_prompt(partials, text[:1500])
                msgs = prompt_overrides.apply_messages(
                    project, "analysis.merge", "global", msgs)
                prompt_trace.record(project.id, "01_analysis", "merge", msgs,
                                    meta={"parts": len(partials)})
                merged = self.text.chat_json(
                    msgs, expect="object", json_mode=True)
            except Exception as exc:  # noqa: BLE001
                self.log(f"合并调用失败（{truncate(str(exc), 120)}），改用本地合并")
                merged = self._local_merge(partials)
        if not isinstance(merged, dict):
            merged = self._local_merge(partials)

        # ---- 3) 落到工程对象 ----
        project.summary = str(merged.get("summary", "") or "")
        project.plotline = str(merged.get("plotline", "") or "")
        kp = merged.get("key_points") or []
        if isinstance(kp, str):
            kp = [x.strip() for x in re.split(r"[\n；;]+", kp) if x.strip()]
        project.key_points = [str(x) for x in kp if str(x).strip()]

        style = str(merged.get("style", "") or "").strip()
        project.style = self._normalize_style(style)
        project.style_detail = str(merged.get("style_detail", "") or "")

        project.characters = self._build_characters(merged.get("characters") or [])
        project.scenes = self._build_scenes(merged.get("scenes") or [])

        # 分析完立刻按故事气质选绘制风格（用户手动锁定过就保留）
        try:
            if bool(getattr(config, "HANDRAW_STYLE_AUTO", True)):
                self.recommend_style(project)
        except Exception as exc:  # noqa: BLE001
            self.log(f"风格推荐失败（不影响后续流程）：{truncate(str(exc), 160)}")

        style_note = (f"#{project.style_id} {stylelib.style_meta(project.style_id).get('generation_name', '')}"
                      if project.style_id else project.style)
        self._emit(
            f"分析完成：识别出 {len(project.characters)} 个角色、"
            f"{len(project.scenes)} 个场景，推荐画风「{style_note}」", 0.6)

        if errors:
            for e in errors:
                self.log("警告：" + e)
        return project

    # ------------------------------------------------------------------
    # 第 2 步：小说精修 + 连续故事板
    # ------------------------------------------------------------------
    def refine_novel(self, project: Project) -> dict:
        """把小说压成 8-12 视觉锚点 + 25 格连续故事板（只产出文本）。

        长文本策略：
        - ≤ NOVEL_REFINE_WHOLE_CHARS：整章一次送入，保证 25 格的节奏与人物连续；
        - 超过阈值：按场景边界分段提取"候选锚点/候选格"，再用一次合并调用压成 25 格，
          避免模型在长文里跳读后半段，也避免单次输出被截断。
        """
        text = normalize_novel(project.novel_text)
        if len(text) < config.MIN_NOVEL_LENGTH:
            raise ValueError("请先输入或上传小说文本")
        if len(text) > config.MAX_NOVEL_LENGTH:
            self.log(f"小说过长（{len(text)} 字），精修只取前 {config.MAX_NOVEL_LENGTH} 字")
            text = text[:config.MAX_NOVEL_LENGTH]
        project.novel_text = text
        started = time.time()

        whole_limit = int(getattr(config, "NOVEL_REFINE_WHOLE_CHARS", 14000))
        whole = len(text) <= whole_limit
        partials: list[dict] = []
        warnings: list[str] = []

        if whole:
            self._emit("开始精修（整章一次送入，保证 25 格节奏一致）", 0.04)
            msgs = prompts.novel_refine_prompt(project)
            msgs = prompt_overrides.apply_messages(project, "refine.novel", "whole", msgs)
            prompt_trace.record(project.id, "02_refine", "novel", msgs,
                                meta={"chars": len(text), "mode": "whole"})
            data = self.text.chat_json(msgs, expect="object", json_mode=True)
            if not isinstance(data, dict):
                raise LLMError("精修结果不是 JSON 对象")
            partials.append(data)
            merged = data
        else:
            chunks = [c for _, c in chunk_with_offsets(
                text,
                chunk_size=int(getattr(config, "NOVEL_REFINE_CHUNK_SIZE", 7000)),
                overlap=int(getattr(config, "NOVEL_REFINE_CHUNK_OVERLAP", 400)))]
            total = len(chunks)
            self._emit(f"开始分段精修，共 {total} 段", 0.04)
            prior = ""
            for i, chunk in enumerate(chunks, start=1):
                if self._check_stop():
                    raise LLMError("用户已取消精修")
                msgs = prompts.novel_refine_prompt(
                    project, chunk_text=chunk, chunk_index=i, chunk_total=total,
                    prior_context=prior)
                msgs = prompt_overrides.apply_messages(
                    project, "refine.novel", f"chunk:{i}", msgs)
                prompt_trace.record(project.id, "02_refine", f"chunk_{i}", msgs,
                                    meta={"chars": len(chunk), "chunk": i, "total": total})
                try:
                    data = self.text.chat_json(msgs, expect="object", json_mode=True)
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"第 {i} 段精修失败：{truncate(str(exc), 120)}")
                    self.log(warnings[-1])
                    continue
                if isinstance(data, dict):
                    partials.append(data)
                    digest = data.get("digest") if isinstance(data.get("digest"), dict) else {}
                    events = [str(a.get("event", "")) for a in (data.get("anchors") or [])
                              if isinstance(a, dict)][:4]
                    prior = truncate(
                        "；".join(x for x in [
                            str(data.get("core_task", "") or ""),
                            str(digest.get("turn", "") or ""),
                            "；".join(events),
                        ] if x), 500)
                self._emit(f"已精修 {i}/{total} 段", 0.04 + 0.58 * i / max(1, total))

            if not partials:
                raise LLMError("分段精修全部失败：" + "；".join(warnings[:3]))
            self._emit("正在合并分段精修结果并压成 25 格…", 0.68)
            msgs = prompts.novel_refine_merge_prompt(partials, project, len(partials))
            msgs = prompt_overrides.apply_messages(project, "refine.merge", "global", msgs)
            prompt_trace.record(project.id, "02_refine", "merge", msgs,
                                meta={"parts": len(partials)})
            try:
                merged = self.text.chat_json(msgs, expect="object", json_mode=True)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"合并调用失败，改用本地合并：{truncate(str(exc), 120)}")
                self.log(warnings[-1])
                merged = self._local_refine_merge(partials)
            if not isinstance(merged, dict):
                merged = self._local_refine_merge(partials)

        anchors, cells, digest = self._normalize_refine(merged, project, warnings)
        project.core_task = str(merged.get("core_task") or "")
        project.refine_digest = digest
        project.visual_anchors = anchors
        project.continuous_cells = cells
        project.continuous_storyboard = "\n".join(
            f"{c.get('cell', i)}. {c.get('shot_type', '')}｜{c.get('subject_action', '')}｜"
            f"{c.get('environment', '')}｜{c.get('emotion', '')}｜{c.get('function', '')}"
            for i, c in enumerate(cells, start=1))
        project.refine_status = "已完成" if cells else "失败"
        project.refine_meta = {
            "chars": len(text),
            "mode": "whole" if whole else "chunked",
            "chunks": 1 if whole else len(partials),
            "anchors": len(anchors),
            "cells": len(cells),
            "model": getattr(self.text, "model", ""),
            "elapsed_sec": round(time.time() - started, 1),
            "warnings": warnings[:8],
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if cells:
            # 精修改的是故事本身，后续旧分镜/25格/页面必须失效，避免"新故事 + 旧分镜"。
            project.shots = []
            project.pages = []
            project.grid25_image = ""
            project.grid25_shots = []
            project.grid25_status = "待生成"
            project.page_plan_note = "精修后需要重新拆解分镜"
        project.touch()
        self._emit(f"精修完成：{len(anchors)} 个锚点 / {len(cells)} 格连续故事板", 0.98)
        return {"core_task": project.core_task, "digest": digest,
                "anchors": anchors, "cells": cells,
                "meta": project.refine_meta, "warnings": warnings}

    def _local_refine_merge(self, partials: list[dict]) -> dict:
        """不依赖模型的精修合并兜底：按段顺序拼接，再截到 25 格。"""
        anchors: list[dict] = []
        cells: list[dict] = []
        seen_anchor: set[str] = set()
        seen_cell: set[str] = set()
        core_task = ""
        digest: dict = {}
        for part in partials:
            if not isinstance(part, dict):
                continue
            if not core_task and part.get("core_task"):
                core_task = str(part["core_task"])
            if not digest and isinstance(part.get("digest"), dict):
                digest = dict(part["digest"])
            for a in part.get("anchors") or []:
                if not isinstance(a, dict):
                    continue
                key = str(a.get("event", ""))[:40] or str(a.get("quote", ""))[:40]
                if key and key not in seen_anchor:
                    seen_anchor.add(key)
                    anchors.append(dict(a))
            for c in part.get("cells") or []:
                if not isinstance(c, dict):
                    continue
                key = f"{c.get('subject_action', '')[:30]}|{c.get('quote', '')[:20]}"
                if key.strip("|") and key not in seen_cell:
                    seen_cell.add(key)
                    cells.append(dict(c))
        return {"core_task": core_task, "digest": digest,
                "anchors": anchors[:12], "cells": cells[:25]}

    def _normalize_refine(self, data: dict, project: Project,
                          warnings: list[str]) -> tuple[list[dict], list[dict], dict]:
        """把模型输出收敛成稳定结构；缺字段就补，不合法就丢。"""
        digest_raw = data.get("digest") if isinstance(data.get("digest"), dict) else {}
        digest = {
            "protagonist": str(digest_raw.get("protagonist") or ""),
            "goal": str(digest_raw.get("goal") or ""),
            "obstacle": str(digest_raw.get("obstacle") or ""),
            "turn": str(digest_raw.get("turn") or ""),
            "ending_state": str(digest_raw.get("ending_state") or ""),
            "tone": str(digest_raw.get("tone") or ""),
            "motifs": [str(x) for x in (digest_raw.get("motifs") or []) if str(x).strip()][:6],
        }
        anchors: list[dict] = []
        for i, a in enumerate(data.get("anchors") or [], start=1):
            if not isinstance(a, dict):
                continue
            event = str(a.get("event") or "").strip()
            if not event:
                continue
            anchors.append({
                "id": str(a.get("id") or f"A{i}"),
                "event": truncate(event, 80),
                "function": str(a.get("function") or "推进"),
                "quote": truncate(str(a.get("quote") or ""), 40),
                "characters": [str(x) for x in (a.get("characters") or []) if str(x).strip()],
                "place": str(a.get("place") or ""),
                "time": str(a.get("time") or ""),
                "shot_hint": str(a.get("shot_hint") or ""),
            })
        anchors = anchors[:12]

        cells: list[dict] = []
        for i, c in enumerate(data.get("cells") or [], start=1):
            if not isinstance(c, dict):
                continue
            action = str(c.get("subject_action") or "").strip()
            if not action:
                continue
            cells.append({
                "cell": len(cells) + 1,
                "beat_id": str(c.get("beat_id") or ""),
                "shot_type": str(c.get("shot_type") or "中景"),
                "subject_action": truncate(action, 80),
                "environment": truncate(str(c.get("environment") or ""), 80),
                "light": truncate(str(c.get("light") or ""), 30),
                "emotion": truncate(str(c.get("emotion") or ""), 16),
                "function": str(c.get("function") or "推进"),
                "characters": [str(x) for x in (c.get("characters") or []) if str(x).strip()],
                "props": [str(x) for x in (c.get("props") or []) if str(x).strip()],
                "gaze": str(c.get("gaze") or ""),
                "transition": str(c.get("transition") or "切"),
                "quote": truncate(str(c.get("quote") or ""), 40),
            })
            if len(cells) >= int(getattr(config, "NOVEL_REFINE_CELLS", 25)):
                break

        # 兜底：模型只给了锚点没给格时，按锚点生成最小可用镜头，保证后续步骤不空转。
        if not cells and anchors:
            warnings.append("模型未返回 cells，已按锚点生成占位镜头格")
            for a in anchors:
                cells.append({
                    "cell": len(cells) + 1, "beat_id": a.get("id", ""),
                    "shot_type": a.get("shot_hint") or "中景",
                    "subject_action": a.get("event", ""),
                    "environment": "；".join(x for x in [a.get("place", ""), a.get("time", "")] if x),
                    "light": "", "emotion": "",
                    "function": a.get("function", "推进"),
                    "characters": list(a.get("characters") or []),
                    "props": [], "gaze": "看向画面深处",
                    "transition": "切", "quote": a.get("quote", ""),
                    "_fallback": True,
                })
        if cells and len(cells) < 25:
            warnings.append(f"模型只返回 {len(cells)} 格（目标 25），保留实际结果")
        return anchors, cells, digest

    @staticmethod
    def _normalize_style(style: str) -> str:
        style = (style or "").strip()
        if style in config.STYLE_PRESETS:
            return style
        # 模型常回"…风格 / …画风 / …插画"这种更长的说法：
        # 只要它完整包含某个预设名，就取最长的那个（"古风"这种短词不在此列，
        # 否则会被"古风韩漫"吞掉，又回到韩漫）。
        contained = [p for p in config.STYLE_PRESETS if p in style]
        if contained:
            return max(contained, key=len)
        # 关键词兜底：按"题材 / 媒介 → 画风"映射，不再一律回到韩漫。
        # 先具体后宽泛，避免"古装武侠"被"古"抢先映射到国风。
        keyword_rules: tuple[tuple[tuple[str, ...], str], ...] = (
            (("武侠", "江湖", "功夫", "门派", "侠客"), "港式武侠漫画"),
            (("仙侠", "修真", "修仙", "灵根"), "仙侠国风彩漫"),
            (("机甲", "机器人", "未来科技", "星际"), "日系机甲科幻漫画"),
            (("恐怖", "灵异", "怪谈", "鬼"), "日系恐怖漫画"),
            (("校园", "青春", "高中", "大学", "初恋"), "日系少女漫画"),
            (("悬疑", "犯罪", "凶案", "侦探", "谋杀", "民国"), "美式黑色电影漫画"),
            (("奇幻", "魔法", "异世界", "冒险", "史诗"), "欧美奇幻插画"),
            (("治愈", "温馨", "日常", "生活", "亲情"), "日系水彩轻插画"),
            (("古装", "古代", "古风", "宫廷", "王朝"), "国风水墨"),
            (("水墨",), "国风水墨"),
            (("赛博", "霓虹", "废土"), "赛博朋克霓虹"),
            (("都市", "现代", "职场", "城市"), "电影感写实插画"),
            # 媒介名兜底
            (("水彩",), "儿童绘本水彩"),
            (("油画", "厚涂"), "厚涂油画"),
            (("赛璐璐",), "日系赛璐璐动画"),
            (("版画",), "木刻版画漫画"),
            (("像素",), "像素风漫画"),
            (("黏土", "定格"), "3D 黏土动画"),
            (("丝网",), "复古丝网印刷"),
            (("剪纸", "纸雕"), "剪纸纸雕漫画"),
            (("波普",), "欧美复古波普漫画"),
            (("清线",), "法比清线漫画"),
            (("几何", "极简"), "极简几何插画"),
            (("墨线", "黑白"), "黑白墨线漫画"),
            (("绘本", "童话"), "儿童绘本水彩"),
            # 模型只回了"日漫/美漫/国漫/港漫/欧漫"这类家族名时的兜底
            (("港漫",), "港式武侠漫画"),
            (("美漫",), "美式超级英雄漫画"),
            (("日漫",), "日系少年热血漫画"),
            (("国漫",), "新国风彩漫"),
            (("欧漫", "法漫"), "法比清线漫画"),
            (("韩漫",), "彩色韩漫风格"),
        )
        for words, preset in keyword_rules:
            if any(w in style for w in words) and preset in config.STYLE_PRESETS:
                return preset
        return config.DEFAULT_STYLE

    # ------------------------------------------------------------------
    # 绘制风格：handraw-style 风格库
    # ------------------------------------------------------------------
    def style_text_source(self, project: Project) -> str:
        """把故事信息拼成一段文本，供风格推荐打分/AI 判断。"""
        parts = [project.title, project.summary, project.plotline,
                 project.style_detail, project.style,
                 "；".join(project.key_points or [])]
        for c in project.characters[:10]:
            parts.append(f"{c.name} {c.gender} {c.age} {c.identity} {c.personality}")
        for s in project.scenes[:10]:
            parts.append(f"{s.name} {s.features} {s.atmosphere}")
        return "。".join(p for p in parts if p)

    def recommend_style(self, project: Project, *, force: bool = False,
                        use_llm: bool | None = None) -> dict:
        """
        为工程选一套 handraw-style 绘制风格 + 主题色。

        - 用户手动锁定过（style_auto=False 且有 style_id）时不覆盖，除非 force；
        - 先本地打分收敛候选，再让文本模型在候选里做最终审美判断；
        - 模型不可用/返回不合法时，直接用本地结果，流程不中断。
        """
        if not bool(getattr(config, "HANDRAW_ENABLED", True)) or not stylelib.available():
            return {}
        # 用户手动选过 / 手动清空过（style_auto=False）就尊重用户，除非 force
        if not force and not project.style_auto:
            return stylelib.project_style(project)

        text = self.style_text_source(project)
        shortlist_limit = int(getattr(config, "HANDRAW_STYLE_SHORTLIST", 28) or 28)
        local = stylelib.recommend(
            text,
            style_hint=(config.HANDRAW_DEFAULT_STYLE if not project.style_id else ""),
            color_hint=config.HANDRAW_DEFAULT_COLORS,
            shortlist_limit=shortlist_limit)
        style_id = local.get("style_id") or ""
        colors = list(local.get("color_ids") or [])
        rationale = str(local.get("rationale") or "")

        if use_llm is None:
            use_llm = bool(getattr(config, "HANDRAW_STYLE_REC_LLM", True))
        if use_llm and self.text is not None and style_id:
            try:
                msgs = prompts.style_recommend_prompt(
                    project, local.get("shortlist") or [], stylelib.color_brief_table())
                msgs = prompt_overrides.apply_messages(
                    project, "style.recommend", "global", msgs)
                prompt_trace.record(project.id, "09_style", "recommend", msgs,
                                    meta={"shortlist": len(local.get("shortlist") or [])})
                data = self.text.chat_json(msgs, expect="object", json_mode=True)
                if isinstance(data, dict):
                    ai_id = stylelib.normalize_style_id(data.get("style_id"))
                    allowed = set(local.get("shortlist") or []) | {style_id}
                    if ai_id and ai_id in allowed:
                        style_id = ai_id
                    ai_colors = stylelib.parse_colors(data.get("color_ids"))
                    if ai_colors:
                        colors = ai_colors
                    ai_reason = str(data.get("rationale") or "").strip()
                    if ai_reason:
                        rationale = ai_reason
            except Exception as exc:  # noqa: BLE001
                self.log(f"AI 风格推荐不可用，改用本地匹配：{truncate(str(exc), 120)}")

        project.style_id = style_id
        project.style_color_ids = colors
        project.style_rationale = rationale
        project.style_auto = True
        project.style_tone = stylelib.tone_for(style_id, text)
        project.style_trigger = stylelib.trigger_phrase(style_id, project.style_tone, text)
        try:
            project.style_ref_path = stylelib.style_reference_png(style_id)
        except Exception:  # noqa: BLE001
            project.style_ref_path = ""
        meta = stylelib.style_meta(style_id)
        if meta:
            color_names = "、".join((stylelib.color_meta(c) or {}).get("name_zh", c)
                                   for c in colors)
            self.log(f"绘制风格：#{style_id} {meta.get('generation_name', '')}"
                     f"（参考 {meta.get('reference', '')}）"
                     + (f" · 主题色 {color_names}" if color_names else ""))
        return stylelib.project_style(project)


    def _build_characters(self, raw: Iterable) -> list[Character]:
        out: list[Character] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            c = Character(
                name=name,
                gender=str(item.get("gender", "") or ""),
                age=str(item.get("age", "") or ""),
                identity=str(item.get("identity", "") or ""),
                personality=str(item.get("personality", "") or ""),
                role=str(item.get("role", "") or ""),
                is_extra=bool(item.get("is_extra", False)),
                appearance=str(item.get("appearance", "") or ""),
                aliases=[str(x) for x in (item.get("aliases") or []) if str(x).strip()],
                first_appearance=str(item.get("first_appearance", "") or ""),
            )
            out.append(c)
        # 群众角色排到最后
        out.sort(key=lambda c: (c.is_extra, c.name))
        return out

    def _build_scenes(self, raw: Iterable) -> list[Scene]:
        out: list[Scene] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(Scene(
                name=name,
                features=str(item.get("features", "") or ""),
                atmosphere=str(item.get("atmosphere", "") or ""),
                frequency=str(item.get("frequency", "") or ""),
                objects=[str(x) for x in (item.get("objects") or []) if str(x).strip()],
                light=str(item.get("light", "") or ""),
            ))
        return out

    def _local_merge(self, partials: list[dict]) -> dict:
        """不依赖模型的本地合并兜底。"""
        merged: dict = {"summary": "", "plotline": "", "key_points": [],
                        "style": config.DEFAULT_STYLE, "style_detail": "",
                        "characters": [], "scenes": []}
        summaries: list[str] = []
        plotlines: list[str] = []
        char_map: dict[str, dict] = {}
        scene_map: dict[str, dict] = {}
        style_votes: dict[str, int] = {}

        for part in partials:
            if not isinstance(part, dict):
                continue
            if part.get("summary"):
                summaries.append(str(part["summary"]))
            if part.get("plotline"):
                plotlines.append(str(part["plotline"]))
            merged["key_points"].extend(
                [str(x) for x in (part.get("key_points") or []) if str(x).strip()])
            style = str(part.get("style", "") or "")
            if style:
                style_votes[style] = style_votes.get(style, 0) + 1
            for c in part.get("characters") or []:
                if not isinstance(c, dict):
                    continue
                name = str(c.get("name", "") or "").strip()
                if not name:
                    continue
                old = char_map.get(name)
                if old is None:
                    char_map[name] = dict(c)
                else:
                    for k, v in c.items():
                        if v and not old.get(k):
                            old[k] = v
                    if not c.get("is_extra", True):
                        old["is_extra"] = False
            for s in part.get("scenes") or []:
                if not isinstance(s, dict):
                    continue
                name = str(s.get("name", "") or "").strip()
                if not name:
                    continue
                if name not in scene_map:
                    scene_map[name] = dict(s)

        merged["characters"] = list(char_map.values())
        merged["scenes"] = list(scene_map.values())
        merged["summary"] = truncate(" ".join(summaries), 400, "")
        merged["plotline"] = "\n".join(plotlines)
        merged["key_points"] = list(dict.fromkeys(merged["key_points"]))[:20]
        if style_votes:
            merged["style"] = max(style_votes.items(), key=lambda kv: kv[1])[0]
        merged["style_detail"] = next((str(p.get("style_detail", "")) for p in partials
                                      if p.get("style_detail")), "")
        return merged

    # ==================================================================
    # 阶段三：角色人设与三视图
    # ==================================================================
    # ------------------------------------------------------------------
    # 角色辨识度：撞车检测 / 定向重写 / 唯一点缀（rules/01 H3–H5）
    # ------------------------------------------------------------------
    def character_collisions(self, project: Project,
                             only_ids: Sequence[str] | None = None) -> list[dict]:
        """
        找出"读者分不出谁是谁"的角色对。

        判据（rules/data/identity_rules.json）：
          · 脸型 / 眼型 / 发型 / 服装主色 四个桶里，共享 ≥3 个 → 撞车；
          · 或人设文本相似度 > 0.45；
          · 或两人的"记忆点"（identity_tag）相同。
        """
        cfg = rules_mod.collision_rules()
        buckets = [b for b in (cfg.get("compare_buckets") or
                               ["face", "eyes", "hair", "outfit"])]
        max_shared = int(cfg.get("max_shared_buckets", 2))
        sim_limit = float(cfg.get("design_similarity_limit", 0.45))
        labels = {"face": "脸型", "eyes": "眼型", "hair": "发型", "outfit": "服装"}

        wanted = set(only_ids or [])
        chars = [c for c in project.characters
                 if c.name and (not wanted or c.id in wanted)]
        out: list[dict] = []
        for a, b in combinations(chars, 2):
            ba, bb = a.trait_buckets(), b.trait_buckets()
            shared = [k for k in buckets if (ba.get(k) or set()) & (bb.get(k) or set())]
            # 人设里的小标题（【基本形象】…）会让所有角色都"很像"，比较前先去掉
            da = re.sub(r"【[^】]{1,16}】", "", a.design or a.identity_card())
            db = re.sub(r"【[^】]{1,16}】", "", b.design or b.identity_card())
            sim = difflib.SequenceMatcher(None, da, db).ratio()
            tag_a, tag_b = a.derived_identity_tag(), b.derived_identity_tag()
            tag_hit = bool(tag_a) and tag_a == tag_b
            serious = len(shared) > max_shared
            similar = sim > sim_limit and len(shared) >= 2
            if not (serious or similar or tag_hit):
                continue
            reasons = [labels.get(k, k) for k in shared]
            if tag_hit:
                reasons.append(f"记忆点重复（{tag_a}）")
            # 两个人都已经加过点缀、且记忆点不同 → 视为"已区分"，只提示、不再重写
            resolved = bool(a.design_patchs) and bool(b.design_patchs) \
                and (a.derived_identity_tag() != b.derived_identity_tag())
            out.append({
                "a": a.name, "b": b.name,
                "other": b.name,
                "shared_buckets": shared,
                "shared_labels": reasons,
                "similarity": round(sim, 2),
                "resolved": resolved,
                "level": ("info" if resolved else
                          ("danger" if len(shared) >= 3 or sim > 0.6 else "warn")),
            })
        return out

    def ensure_character_distinctness(self, project: Project, rewrite: bool = True,
                                      only_ids: Sequence[str] | None = None) -> dict:
        """
        角色撞车时：先让文本模型定向重写一次，仍撞车再用"唯一点缀"做加法。

        只改人设（AI 自由发挥的部分），不改小说已写死的外貌与剧情。
        """
        report: dict[str, object] = {"collisions": [], "rewritten": [], "patched": []}
        if not bool(getattr(config, "CHARACTER_DISTINCTNESS_ENABLED", True)):
            return report
        collisions = self.character_collisions(project, only_ids)
        report["collisions"] = collisions
        if not collisions:
            # 没有撞车就不动任何角色：避免"只是整理了分镜，人设却被改了"
            return report

        todo = [c for c in collisions if not c.get("resolved")]
        resolved = [c for c in collisions if c.get("resolved")]
        if resolved:
            self.log(f"角色辨识度：{len(resolved)} 对已用点缀区分（建议重出图后抽检）")
        if not todo:
            return report

        names = "、".join(sorted({c["a"] for c in todo} |
                                 {c["b"] for c in todo}))
        self.log(f"角色辨识度检查：{len(todo)} 对撞车（{names}）")
        for item in todo:
            self.log(f"  · 「{item['a']}」与「{item['b']}」："
                     f"{'、'.join(item['shared_labels']) or '整体相似'}，"
                     f"相似度 {item['similarity']}")

        if rewrite and self.text is not None:
            # 每次只重写"后来出现的那个"，避免把所有角色都洗一遍
            done: set[str] = set()
            for pair in todo[:3]:
                target = project.find_character(pair["b"])
                if target is None or target.name in done:
                    continue
                done.add(target.name)
                try:
                    msgs = prompts.character_distinct_rewrite_prompt(target, project, [pair])
                    msgs = prompt_overrides.apply_messages(
                        project, "character.distinct", f"character:{target.id}", msgs)
                    prompt_trace.record(project.id, "02_characters",
                                        f"distinct_{target.name}", msgs,
                                        meta={"role": target.role,
                                              "collides_with": pair.get("b", "")})
                    data = self.text.chat_json(msgs, expect="object", json_mode=True)
                    if not isinstance(data, dict):
                        continue
                    design = str(data.get("design", "") or "").strip()
                    kws = data.get("keywords") or []
                    if isinstance(kws, str):
                        kws = [x.strip() for x in re.split(r"[，,、;；\n]+", kws) if x.strip()]
                    if design:
                        target.design = design
                    if kws:
                        target.keywords = [str(k).strip() for k in kws if str(k).strip()]
                    hf = data.get("hard_features") or {}
                    if isinstance(hf, dict):
                        target.hard_features = {str(k): str(v).strip()
                                                for k, v in hf.items() if str(v).strip()}
                    tag = str(data.get("identity_tag", "") or "").strip()
                    if tag:
                        target.identity_tag = tag
                    target.identity_tag = target.derived_identity_tag() or target.identity_tag
                    report["rewritten"].append(target.name)  # type: ignore[union-attr]
                    self.log(f"「{target.name}」已按差异约束重写人设："
                             f"{truncate(str(data.get('reason', '')), 60)}")
                except Exception as exc:  # noqa: BLE001
                    self.log(f"「{target.name}」人设重写失败，改用唯一点缀："
                             f"{truncate(str(exc), 80)}")

        remaining = [c for c in self.character_collisions(project, only_ids)
                     if not c.get("resolved")]
        report["collisions"] = remaining
        self._apply_distinct_patch(project, remaining, report)
        return report

    def _apply_distinct_patch(self, project: Project,
                              collisions: list[dict],
                              report: dict | None = None) -> list[str]:
        """给撞车/缺记忆点的角色追加"唯一点缀"（只做加法，可追溯、幂等）。"""
        pool = rules_mod.differentiation_pool()
        chars = [c for c in project.characters if c.name]
        chunks: list[str] = []
        for c in chars:
            chunks.extend(c.keywords or [])
            chunks.append(c.design or "")
        used_text = "；".join(chunks)
        taken = {t for t in (c.derived_identity_tag() for c in chars) if t}

        def overlaps(a: str, b: str) -> bool:
            """判断两个点缀是否"概念重复"（防止两个人各戴一条银链）。"""
            if not a or not b:
                return False
            if a in b or b in a:
                return True
            for n in (5, 4):
                for i in range(max(0, len(a) - n + 1)):
                    if a[i:i + n] in b:
                        return True
            return False

        def pick_unused(options: Sequence[str], current: str) -> str:
            for opt in options:
                if not opt:
                    continue
                if overlaps(opt, used_text) or overlaps(opt, current):
                    continue
                if opt in taken:
                    continue
                return opt
            return ""

        def count_tag(tag: str) -> int:
            return sum(1 for o in chars if o.derived_identity_tag() == tag) if tag else 0

        patched: list[str] = []
        hot = {n for pair in collisions for n in (pair.get("a"), pair.get("b")) if n}
        for c in chars:
            tag = c.derived_identity_tag()
            dup_tag = count_tag(tag) > 1
            if not (c.name in hot or not tag or dup_tag):
                continue
            # 幂等：已经加过两次点缀的角色不再叠加，避免关键词越滚越长
            if len(c.design_patchs or []) >= 2:
                continue
            shared: set[str] = set()
            for pair in collisions:
                if c.name in (pair.get("a"), pair.get("b")):
                    shared.update(pair.get("shared_buckets") or [])
            # 男性角色不要用披肩/开衫/发簪这类女性化点缀（会画出违和造型）
            female_only = ("披肩", "开衫", "木簪", "发绳", "发尾编成", "发夹")

            def options(key: str) -> list[str]:
                items = list(pool.get(key, []))
                if (c.gender or "").strip().startswith("男"):
                    items = [o for o in items if not any(h in o for h in female_only)]
                return items

            accents: list[str] = []
            # 发型/服装二选一，保证同框两人的剪影和色块不一样
            hair_bucket = c.trait_buckets().get("hair", set())
            long_hair = bool(hair_bucket & {"长直发", "长发", "及腰", "及肩", "低马尾",
                                            "高马尾", "马尾", "卷发", "编发", "盘发"})
            if "hair" in shared and long_hair:
                picked = pick_unused(options("hair_accent"), c.design)
                if picked:
                    accents.append(picked)
            if "outfit" in shared or (shared & {"face", "eyes"} and not accents):
                picked = pick_unused(options("outfit_accent"), c.design)
                if picked:
                    accents.append(picked)
            # 脸/眼撞车或记忆点重复：再加一件配饰（已经戴眼镜的人不再加眼镜）
            if shared & {"face", "eyes"} or not tag or dup_tag:
                acc_options = options("accessory")
                if "眼镜" not in (c.design or "") and "眼镜" not in "".join(c.keywords or []):
                    acc_options += options("eyewear")
                picked = pick_unused(acc_options, c.design)
                if picked:
                    accents.append(picked)
            if not accents:
                picked = pick_unused(options("accessory"), c.design)
                if picked:
                    accents.append(picked)
            accents = accents[:2]
            if not accents:
                continue
            c.design = _append_design_note(c.design, "、".join(accents))
            for accent in accents:
                if accent not in (c.keywords or []):
                    c.keywords = [*(c.keywords or []), accent]
            # 记忆点：原来的唯一就保留，缺了/撞了才换成新配饰
            if (not tag or dup_tag):
                candidate = next((a for a in reversed(accents) if a not in taken), "")
                if candidate:
                    c.identity_tag = candidate
                    taken.add(candidate)
            c.design_patchs = [*(c.design_patchs or []), *accents]
            used_text = used_text + "；" + "、".join(accents)
            patched.append(c.name)
            self.log(f"「{c.name}」追加辨识度点缀：{'、'.join(accents)}")
        if report is not None:
            report["patched"] = patched
        return patched

    def generate_character_design(self, project: Project, character: Character,
                                  look_name: str = "默认造型") -> Character:
        """生成（或重新生成）单个角色的人设描述。"""
        character.design_status = "生成中"
        try:
            msgs = prompts.character_design_prompt(character, project)
            msgs = prompt_overrides.apply_messages(
                project, "character.design", f"character:{character.id}", msgs)
            prompt_trace.record(project.id, "02_characters",
                                f"design_{character.name}", msgs,
                                meta={"name": character.name,
                                      "is_extra": character.is_extra})
            data = self.text.chat_json(msgs, expect="object", json_mode=True)
            if not isinstance(data, dict):
                raise LLMError("人设返回结构异常")
            character.design = str(data.get("design", "") or "").strip()
            kws = data.get("keywords") or []
            if isinstance(kws, str):
                kws = [x.strip() for x in re.split(r"[，,、;；\n]+", kws) if x.strip()]
            character.keywords = [str(k) for k in kws if str(k).strip()]
            if not character.keywords:
                character.keywords = extract_keywords(character.design)
            # 机器可读的硬特征与唯一记忆点（rules/01 H3/H4）
            hf = data.get("hard_features") or {}
            if isinstance(hf, dict):
                character.hard_features = {str(k): str(v).strip()
                                           for k, v in hf.items() if str(v).strip()}
            # 模型经常漏填 hard_features；从人设/关键词里补出服装与配色，
            # 否则身份卡里没有"配色"，画图时容易被作品主题色带跑。
            if not character.hard_features.get("outfit"):
                derived_outfit = prompts._character_outfit(character)
                if derived_outfit:
                    character.hard_features["outfit"] = derived_outfit
            if not character.hard_features.get("palette"):
                colors = prompts._character_color_words(character)
                if colors:
                    character.hard_features["palette"] = " + ".join(colors[:3])
            tag = str(data.get("identity_tag", "") or "").strip()
            if tag:
                character.identity_tag = tag
            # 统一清洗成"一个短特征"（模型常写成"…的…女/老板"）
            character.identity_tag = character.derived_identity_tag() or character.identity_tag
            character.design_status = "已完成"
            character.error = ""
            self.log(f"「{character.name}」人设生成完成（{len(character.keywords)} 个关键词）")
            # 单角色重生成时也要检查撞车，但不调模型重写（避免递归）
            if bool(getattr(config, "CHARACTER_DISTINCTNESS_ENABLED", True)):
                self.ensure_character_distinctness(project, rewrite=False,
                                                   only_ids=[character.id])
        except Exception as exc:  # noqa: BLE001
            character.design_status = "失败"
            character.error = truncate(str(exc), 300)
            self.log(f"「{character.name}」人设生成失败：{character.error}")
            raise
        return character

    def style_prompt_text(self, project: Project, *, with_ref: bool = False,
                          traits_limit: int | None = None,
                          include_colors: bool = True) -> str:
        """
        角色图/参考图用的画风块。

        有 handraw 风格时输出"视觉语言 + 主题色"（默认不写风格名，避免被画成标题），
        否则回落到原来的 7 个中文预设。with_ref=True 时附"参考图只取画风/只取色"说明。
        """
        hs = stylelib.project_style(project) if bool(getattr(config, "HANDRAW_ENABLED", True)) else {}
        if hs.get("style_id"):
            ref_mode = "color" if getattr(self.image, "color_matched", False) else "style"
            return stylelib.style_prompt_block(
                hs["style_id"], (hs.get("color_ids") or []) if include_colors else [],
                include_reference_note=with_ref, ref_mode=ref_mode,
                max_chars=traits_limit,
                tone=getattr(project, "style_tone", ""),
                trigger=hs.get("trigger", ""),
                text=f"{project.summary} {project.style_detail}")
        style_key = project.style if project.style in config.STYLE_PROMPT_MAP else config.DEFAULT_STYLE
        return f"整体画风：{config.STYLE_PROMPT_MAP[style_key]}"

    def reference_slot_limit(self) -> int:
        """当前工作流最多能接几张参考图（取不到时用 config 上限）。"""
        try:
            tpl_fn = getattr(self.image, "template", None)
            if callable(tpl_fn):
                n = int(tpl_fn(for_reference=True).reference_slot_count or 0)
                if n > 0:
                    return n
        except Exception:  # noqa: BLE001
            pass
        return max(1, int(getattr(config, "COMFY_MAX_REFERENCE_SLOTS", 3) or 3))

    def style_reference(self, project: Project,
                        with_characters: Sequence[str] | None = None) -> list[str]:
        """
        组装送进工作流的参考图：第 1 张是画风参考，后面是角色参考。

        只在工作流真的支持图像输入时才返回；纯文生图返回空列表。
        """
        if (isinstance(self.image, QwenComicClient)
                or str(getattr(config, "IMAGE_FAMILY", "zimage")).lower() == "qwen"):
            # Qwen 走参考图 Edit；画风参考图不能混进去，否则会把画风图内容一起编辑。
            return []
        if not self.refs_supported or not bool(getattr(config, "HANDRAW_STYLE_REF_ENABLED", True)):
            return []
        refs: list[str] = []
        style_png = ""
        if bool(getattr(config, "HANDRAW_STYLE_REF_ENABLED", True)) and project.style_id:
            style_png = stylelib.style_reference_png(project.style_id)
        if style_png:
            refs.append(style_png)
        for name in with_characters or []:
            c = project.find_character(name)
            if not c:
                continue
            p = c.reference_image()
            if p and p not in refs:
                refs.append(p)
        return refs[:max(1, int(getattr(config, "HANDRAW_MAX_REFERENCE_SLOTS", 3) or 3))]

    def color_transfer_for(self, project: Project) -> float | None:
        """显式主题色优先：有主题色时减弱/关闭 ColorTransfer，否则颜色会被风格图盖掉。"""
        if getattr(project, "style_color_ids", None):
            return float(getattr(config, "COMFY_COLOR_TRANSFER_THEME_STRENGTH", 0.0) or 0.0)
        return None

    def _character_style_base(self, project: Project, character: Character) -> str:
        """角色图共用的身份锁 + 画风块；主视图和姿态总图逐字复用。"""
        style_ref = self.style_reference(project)
        style_text = self.style_prompt_text(
            project, with_ref=bool(style_ref), traits_limit=110,
            include_colors=False)
        lock = prompts.character_feature_lock(character)
        palette = str((getattr(character, "hard_features", {}) or {}).get("palette", "") or "").strip()
        if not palette:
            palette = "、".join(prompts._character_color_words(character)[:3])
        if palette:
            color_note = (f"【角色专属配色】本角色配色：{palette}；"
                          "服装主色与发色必须按这段配色执行，")
        else:
            color_note = ("【角色专属配色】服装主色与发色以身份卡中的服装描述为准，")
        color_note += ("画风选择的主题色只用于背景、环境光与关键物件，"
                       "不改变本角色的服装主色、发色与肤色；"
                       "不要把所有角色统一成同一套主题色。")
        return f"{lock}{color_note}{style_text}"

    def _pose_client(self):
        """姿态总图用独立工作流（Omni 参考 + Pose ControlNet）。"""
        if isinstance(self.image, ImageClient):
            return ImageClient(workflow=config.COMFY_WORKFLOW_CHARACTER_POSE_NAME)
        return self.image

    @staticmethod
    def _make_three_view(pose_sheet: str, out_path: str) -> str:
        """从姿态总图顶行裁出正面/侧面/背面，拼成一张三视图（不含动作）。"""
        from PIL import Image
        src = Image.open(pose_sheet).convert("RGB")
        w, h = src.size
        top = src.crop((0, 0, w, int(h * 0.5)))
        tw = w // 3
        parts = []
        for i in range(3):
            c = top.crop((i * tw, 0, (i + 1) * tw, top.height))
            g = c.convert("L")
            box = g.point(lambda p: 255 if p > 20 else 0).getbbox()
            if box:
                c = c.crop((max(0, box[0] - 10), max(0, box[1] - 10),
                            min(c.width, box[2] + 10), min(c.height, box[3] + 10)))
            parts.append(c)
        sheet = Image.new("RGB", (1536, 768), (255, 255, 255))
        for i, c in enumerate(parts):
            c.thumbnail((500, 740), Image.LANCZOS)
            sheet.paste(c, (i * 512 + (512 - c.width) // 2, (768 - c.height) // 2))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        sheet.save(out_path, format="PNG")
        return out_path

    def generate_character_pose_sheet(self, project: Project, character: Character,
                                      base_prompt: str = "",
                                      look_name: str = "默认造型") -> Character:
        """生成一张多姿态 + 三视图总图（人工查看/姿态库，不作为分镜参考）。"""
        character.pose_status = "生成中"
        out_dir = Path(config.CHARACTER_DIR) / _safe_name(character.name) / \
            _safe_name(look_name or "默认造型")
        out_dir.mkdir(parents=True, exist_ok=True)
        if not base_prompt:
            base_prompt = self._character_style_base(project, character)
        # 用户上传了自定义姿态：只生成这一张单姿态图，不要再套
        # “正面/侧面/背面”总图提示词，否则跑步骨架也会被画成站姿。
        custom_pose = bool(character.pose_source and Path(character.pose_source).exists())
        if custom_pose:
            prompt = (
                f"{base_prompt}；全身白底角色姿态图，画面里只有这一个角色；"
                "严格按骨架做出该动作：身体重心、手臂、腿部角度都跟随骨架；"
                "白色纯色背景，清晰全身。"
            )
            pose_size = (768, 1024)
        else:
            prompt = base_prompt + config.CHARACTER_POSE_INSTRUCTION
            pose_size = config.CHARACTER_POSE_SIZE
        try:
            self.log("正在生成「%s」%s…" % (
                character.name, "自定义姿态图" if custom_pose else "姿态总图"))
            client = self._pose_client()
            # ControlNet 与 ReferenceLatent 在当前 ComfyUI 里 batch 不兼容；
            # 自定义姿态只走骨架，不用角色参考图。
            res: GenResult = client.generate(
                prompt, reference_paths=[],
                negative_prompt=prompts.negative_prompt(
                    project, kind="character_pose", character=character),
                size=pose_size,
                seed=character.seed or None,
                pose_image=character.pose_source or str(config.CHARACTER_POSE_DEFAULT_SKELETON),
                color_transfer_strength=self.color_transfer_for(project))
            path = out_dir / f"{config.CHARACTER_POSE_FILENAME}.png"
            res.image.save(path, format="PNG")
            character.pose_sheet = str(path)
            try:
                prompt_trace.record(
                    project.id, "03_views",
                    f"pose_{character.name}_{'custom' if custom_pose else 'sheet'}",
                    [{"role": "user", "content": prompt}],
                    meta={"image": str(path), "pose_source": character.pose_source,
                          "custom": custom_pose})
            except Exception:  # noqa: BLE001
                pass
            character.pose_status = "已完成"
            self.log(f"「{character.name}」姿态图完成（{pose_size[0]}×{pose_size[1]}，"
                     f"{'自定义骨架' if custom_pose else '内置总图'}）")
        except Exception as exc:  # noqa: BLE001
            character.pose_status = "失败"
            character.error = truncate(str(exc), 300)
            self.log(f"「{character.name}」姿态总图失败：{character.error}")
            raise
        return character

    def generate_character_top_view(self, project: Project, character: Character,
                                    base_prompt: str = "",
                                    look_name: str = "默认造型") -> Character:
        """额外生成一张 45° 俯视图，补足正面/侧面/背面看不到的头顶与肩线。"""
        character.top_status = "生成中"
        out_dir = Path(config.CHARACTER_DIR) / _safe_name(character.name) / \
            _safe_name(look_name or "默认造型")
        out_dir.mkdir(parents=True, exist_ok=True)
        if not base_prompt:
            base_prompt = self._character_style_base(project, character)
        prompt = base_prompt + config.CHARACTER_TOP_INSTRUCTION
        try:
            client = self._pose_client() if Path(config.CHARACTER_TOP_SKELETON).exists() else self.image
            kwargs = dict(
                prompt=prompt, reference_paths=[],
                negative_prompt=prompts.negative_prompt(
                    project, kind="character_top", character=character),
                size=config.CHARACTER_TOP_SIZE,
                seed=character.seed or None,
                color_transfer_strength=self.color_transfer_for(project))
            if client is not self.image:
                kwargs["pose_image"] = str(config.CHARACTER_TOP_SKELETON)
            res: GenResult = client.generate(**kwargs)
            path = out_dir / f"{config.CHARACTER_TOP_FILENAME}.png"
            res.image.save(path, format="PNG")
            character.top_view = str(path)
            character.top_status = "已完成"
            self.log(f"「{character.name}」俯视图完成")
        except Exception as exc:  # noqa: BLE001
            character.top_status = "失败"
            self.log(f"「{character.name}」俯视图失败（主视图/姿态图不受影响）："
                     f"{truncate(str(exc), 160)}")
        return character

    def generate_character_main_visual(self, project: Project, character: Character,
                                       base_prompt: str = "",
                                       look_name: str = "默认造型") -> Character:
        """单独生成主视图（单人全身正面），不再从多姿态总图里裁。

        姿态总图里有正/侧/背 + 动作 + 表情头像，人物之间容易重叠；
        从里面裁会把第二个人、甚至一张脸一起裁进主视图（腿上的脸就是这么来的）。
        Qwen 现在有能力按身份卡直接画单人主视图，这里直接生成并加负向词锁单人。
        """
        out_dir = Path(config.CHARACTER_DIR) / _safe_name(character.name) / \
            _safe_name(look_name or "默认造型")
        out_dir.mkdir(parents=True, exist_ok=True)
        if not base_prompt:
            base_prompt = self._character_style_base(project, character)
        prompt = (base_prompt + config.MAIN_VISUAL_INSTRUCTION
                  + "；画面里只有这一个角色、一个头、一个身体，单人全身像；"
                    "这是一张角色主视图，不是多视图拼图，不要画第二个人、"
                    "不要画表情头像、不要画侧面/背面，不要分格。")
        neg = (prompts.negative_prompt(project, kind="character_main",
                                       character=character)
               + "，多个人物, 第二个人, 重复人脸, 双头, 两个身体, 人物重叠, "
                 "多视图, 三视图拼图, 拼图, 表情头像, 分镜, 画中画")
        try:
            self.log(f"正在生成「{character.name}」主视图（单人正面）…")
            image_client = self._bound_image_client(project, "character") or self.image
            res: GenResult = image_client.generate(
                prompt, reference_paths=self.style_reference(project),
                negative_prompt=neg,
                size=config.CHARACTER_IMAGE_SIZE,
                seed=character.seed or None,
                color_transfer_strength=self.color_transfer_for(project))
            path = out_dir / f"{config.CHARACTER_MAIN_FILENAME}.png"
            res.image.save(path, format="PNG")
            character.main_visual = str(path)
            character.image_status = "已完成"
            try:
                prompt_trace.record(
                    project.id, "03_views", f"main_{character.name}",
                    [{"role": "user", "content": prompt}],
                    meta={"image": str(path),
                          "negative": neg, "size": list(config.CHARACTER_IMAGE_SIZE)})
            except Exception:  # noqa: BLE001
                pass
            self.log(f"「{character.name}」主视图完成（单人正面）")
        except Exception as exc:  # noqa: BLE001
            character.image_status = "失败"
            character.error = truncate(str(exc), 300)
            self.log(f"「{character.name}」主视图失败：{character.error}")
            raise
        return character

    def generate_character_three_view(self, project: Project, character: Character,
                                      base_prompt: str = "",
                                      look_name: str = "默认造型") -> Character:
        """以主视图为正面，生成同一角色的侧面/背面，拼成三视图。

        旧版三视图从多姿态总图顶行裁，脸和主视图不是同一次生成，所以
        “主视图有灵气、三视图像另一个人”。这里正面直接使用主视图，
        侧面/背面用主视图当参考图生成，再统一拼版。
        """
        if not isinstance(self.image, QwenComicClient):
            raise RuntimeError("三视图拼版需要 Qwen 参考图能力")
        from .qwenedit import QwenEditClient
        main = Path(character.main_visual or "")
        if not main.exists():
            self.generate_character_main_visual(project, character, base_prompt, look_name)
            main = Path(character.main_visual or "")
        if not main.exists():
            raise RuntimeError("主视图不存在，无法生成三视图")
        out_dir = Path(config.CHARACTER_DIR) / _safe_name(character.name) / \
            _safe_name(look_name or "默认造型")
        out_dir.mkdir(parents=True, exist_ok=True)
        base = base_prompt or self._character_style_base(project, character)
        q = QwenEditClient()
        neg = (prompts.negative_prompt(project, kind="character_main",
                                       character=character)
               + "，场景, 文字, 水印, 多个人物, 重复人脸, 双头, 幼态, 儿童, "
                 "Q版, 大头, 短腿, 分格, 画中画")
        views: dict[str, str] = {}
        for label, instruction in (
            ("侧面", "90度侧面全身像，人物朝画面右侧，只能看到一侧脸和侧面服装轮廓；"),
            ("背面", "背面全身像，背对镜头，只能看到后脑、背部与服装背面；"),
        ):
            prompt = (
                base
                + "；成年角色，正常成人比例，约7头身，不是Q版、不是幼态；白色纯色背景；"
                + instruction
                + "画面里只有这一个角色、一个全身像；严格保持主视图的脸型、五官、发型、"
                  "发色、服装款式、服装主色与配色；不要正面、不要文字、不要场景。"
            )
            self.log(f"正在生成「{character.name}」三视图·{label}…")
            img = q.generate(prompt, str(main), negative=neg, size=1024,
                             seed=character.seed or 0, steps=8, denoise=1.0)
            path = out_dir / f"三视图_{label}.png"
            img.save(path, format="PNG")
            views[label] = str(path)
        # 正面用主视图原图，保证和三视图完全是同一张脸/同一张主视觉
        panels = [main, Path(views["侧面"]), Path(views["背面"])]
        sheet = Image.new("RGB", (1536, 768), (255, 255, 255))
        for i, p in enumerate(panels):
            im = Image.open(p).convert("RGB")
            im = _trim_whitespace(im, tolerance=10)
            im.thumbnail((500, 740), Image.LANCZOS)
            sheet.paste(im, (i * 512 + (512 - im.width) // 2,
                             (768 - im.height) // 2))
        out = out_dir / "三视图.png"
        sheet.save(out, format="PNG")
        character.triple_view = str(out)
        try:
            prompt_trace.record_data(project.id, "03_views",
                                     f"three_{character.name}", {
                                         "image": str(out),
                                         "front": str(main),
                                         "side": views["侧面"],
                                         "back": views["背面"],
                                     })
        except Exception:  # noqa: BLE001
            pass
        self.log(f"「{character.name}」三视图完成（正面=主视图）")
        self.refresh_character_assets(character, look_name)
        return character

    def generate_character_top_view(self, project: Project, character: Character,
                                    look_name: str = "默认造型") -> Character:
        """用"上传的俯视参考 → 自动骨架 → ControlNet"生成真正的俯视图。

        流程与姿态图一致：
        1. 用户上传的俯视参考先转成 OpenPose 骨架（ ``top_skeleton`` ）；
        2. 骨架作为 ControlNet 输入，保证头顶/肩线/发旋位置来自参考图；
        3. 身份（发色、发缝、衣领、配色）由 ``character_feature_lock`` 锁定；
        4. 结果写到 ``俯视图.png``，并回填 ``Character.top_view``。

        如果参考图无法提取骨架（例如只有头顶、没有完整人体），
        自动退回"纯文本正上方俯视"，并在 ``character.error`` 里说明，
        不伪造一张正面图当俯视图。
        """
        character.top_status = "生成中"
        character.error = ""
        out_dir = Path(config.CHARACTER_DIR) / _safe_name(character.name) / \
            _safe_name(look_name or "默认造型")
        out_dir.mkdir(parents=True, exist_ok=True)
        source = Path(character.top_source) if character.top_source else None
        skeleton = Path(character.top_skeleton) if character.top_skeleton else None
        if source and source.is_file() and not (skeleton and skeleton.is_file()):
            try:
                from . import poseutil
                sk = out_dir / "俯视图_骨架.png"
                skeleton = Path(poseutil.photo_to_skeleton(source, sk))
                character.top_skeleton = str(skeleton)
                self.log(f"「{character.name}」俯视参考已提取骨架")
            except Exception as exc:  # noqa: BLE001
                self.log(f"「{character.name}」俯视参考骨架提取失败，改用文本俯视："
                         f"{truncate(str(exc), 120)}")
                skeleton = None

        base = self._character_style_base(project, character)
        prompt = (
            (base + "；" if base else "")
            + "真正的正上方垂直俯视头顶参考图：相机在角色头顶正上方垂直向下，"
              "只画头顶、发旋、发缝、双肩与衣领；白色纯色背景；"
              "严格沿用同一角色的发色、发量、发旋位置、发缝方向、肩宽与衣领款式；"
              "画风必须和主视图/三视图完全一致：同一套线条、同一套水墨/宣纸质感、"
              "同一套配色与渲染方式，不要照片质感、不要3D渲染；"
              "画面里只有这一个人物；看不到正脸、看不到眼睛、看不到全身；"
              "没有文字、没有场景、没有多余人物。"
        )
        neg = (prompts.negative_prompt(project, kind="character_main",
                                       character=character)
               + "，正脸, 人脸, 眼睛, 全身, 侧面, 多余人物, 第二个人, 文字, 水印, "
                 "场景, 桌子, 椅子, 照片, 写实, 摄影, 3D渲染, 真人感, "
                 "低质量, 模糊, 变形, 双头")
        try:
            res = self.image.generate(
                prompt,
                pose_image=str(skeleton) if skeleton and skeleton.is_file() else None,
                negative_prompt=neg,
                size=(1024, 1024),
                seed=int(character.seed or 20261103),
            )
            out = out_dir / "俯视图.png"
            res.image.convert("RGB").save(out, format="PNG")
            character.top_view = str(out)
            character.top_status = "已完成"
            if not (skeleton and skeleton.is_file()):
                character.top_note = "未提取到骨架，已用文本俯视；上传完整人体参考可提高顶视精度"
            else:
                character.top_note = ""
            try:
                prompt_trace.record_data(project.id, "03_views",
                                         f"top_{character.name}", {
                                             "image": str(out),
                                             "source": str(source),
                                             "skeleton": str(skeleton),
                                             "prompt": prompt,
                                         })
            except Exception:  # noqa: BLE001
                pass
            self.log(f"「{character.name}」俯视图完成")
            self.refresh_character_assets(character, look_name)
        except Exception as exc:  # noqa: BLE001
            character.top_status = "失败"
            character.error = truncate(str(exc), 200)
            raise
        return character

    def generate_character_images(self, project: Project, character: Character,
                                  look_name: str = "默认造型") -> Character:
        """生成一张主视图 + 一张多姿态总图；主视图是分镜唯一身份参考。"""
        if not character.design:
            self.generate_character_design(project, character, look_name)

        character.image_status = "生成中"
        character.pose_status = "生成中"
        out_dir = Path(config.CHARACTER_DIR) / _safe_name(character.name) / _safe_name(look_name)
        out_dir.mkdir(parents=True, exist_ok=True)
        base = self._character_style_base(project, character)
        char_seed = (random.randint(1, 2**31 - 1)
                     if bool(getattr(config, "CHARACTER_SAME_SEED", True)) else None)
        if char_seed:
            character.seed = int(char_seed)
        style_ref = self.style_reference(project)

        try:
            # 1) 先生成姿态总图：一张图里同时有正/侧/背与动作，脸和服装在
            #    同一张图内自洽；这是本项目当前的“一次性一致性”主来源。
            self.log(f"正在生成「{character.name}」的姿态总图…")
            self.generate_character_pose_sheet(project, character, base, look_name)

            # 2) 主视图单独生成：多姿态总图里人物会重叠，直接裁会带进
            #    第二个人/第二张脸（典型：腿边多一张脸）。这里改为单人正面直出。
            if self._check_stop():
                character.image_status = "失败"
                character.pose_status = "失败"
                character.error = "用户已取消"
                return character
            self.generate_character_main_visual(project, character, base, look_name)
            main_path = Path(character.main_visual)

            # 2.5) 三视图以主视图为正面，侧面/背面从主视图身份生成后拼版，
            #      不再从多姿态总图裁，避免三视图与主视图不是同一张脸。
            try:
                self.generate_character_three_view(project, character, base, look_name)
            except Exception as exc:  # noqa: BLE001
                self.log(f"三视图生成失败（沿用旧裁切）：{truncate(str(exc), 120)}")
                if character.pose_sheet and Path(character.pose_sheet).exists():
                    try:
                        three_path = out_dir / "三视图.png"
                        self._make_three_view(character.pose_sheet, str(three_path))
                        character.triple_view = str(three_path)
                    except Exception:
                        pass

            # 3) 俯视图：用户上传过真实俯视参考时，先把参考转骨架，
            #    再用骨架 + 身份锁生成真正的俯视图；没有参考就保持待生成，
            #    不自动套用同一张默认图。
            if character.top_source and Path(character.top_source).exists():
                try:
                    self.generate_character_top_view(project, character, look_name)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"俯视图生成失败（不影响主视图/三视图）：{truncate(str(exc), 120)}")
            look = {
                "main_visual": str(main_path),
                "pose_sheet": character.pose_sheet,
                "top_view": character.top_view,
                # 旧字段只写空，避免旧代码把三视图当参考
                "triple_view": "",
            }
            if look_name == "默认造型":
                # triple_view 现在是人工查看的三视图，不再清空
                character.view_paths = {}
                character.extra_looks.pop(look_name, None)
            else:
                character.extra_looks[look_name] = look
                character.active_look = look_name

            # 派生分镜要用的资产：面部特写 + 单视图 + 俯视质量
            self.refresh_character_assets(character, look_name)

            character.image_status = "已完成"
            character.error = ""
            self.log(f"「{character.name}」主视图 + 姿态总图生成完成")
        except Exception as exc:  # noqa: BLE001
            character.image_status = "失败"
            character.error = truncate(str(exc), 300)
            self.log(f"「{character.name}」角色图生成失败：{character.error}")
            raise
        return character

    def refresh_character_assets(self, character: Character,
                                 look_name: str = "默认造型") -> dict:
        """刷新面部特写/单视图/俯视质量（不重新生成角色）。"""
        out_dir = Path(config.CHARACTER_DIR) / _safe_name(character.name) / \
            _safe_name(look_name or "默认造型")
        try:
            info = charrefs.ensure_character_assets(character, out_dir)
            if info.get("face"):
                self.log(f"「{character.name}」面部特写已就绪")
            if info.get("views"):
                self.log(f"「{character.name}」单视图：{'、'.join(info['views'])}")
            if info.get("top"):
                self.log(f"「{character.name}」俯视类型：{info['top']}")
            return info
        except Exception as exc:  # noqa: BLE001
            self.log(f"「{character.name}」参考资产刷新失败：{truncate(str(exc), 160)}")
            return {"name": character.name, "error": str(exc)}

    def regenerate_character_main_visual(self, project: Project,
                                         character: Character) -> Character:
        """重新生成单人主视图，并同步重建三视图/单视图（主视图变了，侧面/背面会过期）。"""
        if not character.seed:
            character.seed = random.randint(1, 2**31 - 1)
        self.generate_character_main_visual(
            project, character, look_name=character.active_look or "默认造型")
        try:
            self.generate_character_three_view(
                project, character, look_name=character.active_look or "默认造型")
        except Exception as exc:  # noqa: BLE001
            self.log(f"主视图已更新，但三视图重建失败（沿用旧单视图）：{truncate(str(exc), 120)}")
        self.refresh_character_assets(character, character.active_look or "默认造型")
        return character

    def regenerate_character_pose_sheet(self, project: Project,
                                        character: Character) -> Character:
        """重新生成姿态总图，并单独重生成主视图与三视图。"""
        self.generate_character_pose_sheet(project, character)
        # 主视图独立生成，和姿态总图解耦
        self.generate_character_main_visual(
            project, character, look_name=character.active_look or "默认造型")
        # 三视图正面=主视图，侧面/背面从主视图生成
        try:
            self.generate_character_three_view(
                project, character, look_name=character.active_look or "默认造型")
        except Exception as exc:  # noqa: BLE001
            self.log(f"三视图生成失败（沿用旧裁切）：{truncate(str(exc), 120)}")
            pose_src = character.effective_pose_sheet()
            if pose_src:
                out_dir = Path(config.CHARACTER_DIR) / _safe_name(character.name) / \
                    _safe_name(character.active_look or "默认造型")
                try:
                    three = out_dir / "三视图.png"
                    self._make_three_view(pose_src, str(three))
                    character.triple_view = str(three)
                except Exception:
                    pass
        character.image_status = "已完成"
        return character

    # 兼容旧调用名：旧代码/API 里的“三视图”统一映射到姿态总图
    def regenerate_character_triple_view(self, project: Project,
                                         character: Character) -> Character:
        return self.regenerate_character_pose_sheet(project, character)

    def generate_all_characters(self, project: Project,
                                only_ids: Sequence[str] | None = None) -> Project:
        """为所有（或指定）角色生成人设、主视图与姿态总图。"""
        targets = [c for c in project.characters
                   if not only_ids or c.id in set(only_ids)]
        total = len(targets)
        if not total:
            self.log("没有需要生成的角色")
            return project

        # 1) 先把人设补齐（只调文本模型），再做一次跨角色差异化检查 ——
        #    否则"先出图后撞车"就要把已经画好的参考图全部作废（rules/01 H4）。
        pending = [c for c in targets if not c.design]
        for i, c in enumerate(pending, start=1):
            if self._check_stop():
                break
            self._emit(f"[{i}/{len(pending)}] 生成角色「{c.name}」的人设…",
                       0.58 + 0.04 * i / max(1, len(pending)))
            try:
                self.generate_character_design(project, c)
            except Exception as exc:  # noqa: BLE001
                self.log(f"「{c.name}」人设生成失败，稍后重试：{truncate(str(exc), 120)}")
        if pending:
            try:
                self.ensure_character_distinctness(project, rewrite=True)
            except Exception as exc:  # noqa: BLE001
                self.log(f"角色辨识度整理失败（不影响生成）：{truncate(str(exc), 120)}")
        else:
            # 人设已经生成过：只提示，不改写，避免和已出的参考图对不上
            try:
                collisions = self.character_collisions(project)
                if collisions:
                    self.log(f"提示：已有 {len(collisions)} 对角色人设相近。"
                             f"如需重做，请删掉旧人设后重新生成（rules/01 H4）")
            except Exception:  # noqa: BLE001
                pass

        # 2) 再生成姿态总图 + 主视图
        for i, c in enumerate(targets, start=1):
            if self._check_stop():
                self.log("用户已取消，停止生成角色图")
                break
            base = 0.6 + 0.35 * (i - 1) / total
            self._emit(f"[{i}/{total}] 生成角色「{c.name}」的主视图与姿态总图…", base)
            last_exc: Exception | None = None
            for attempt in range(2):
                try:
                    self.generate_character_images(project, c)
                    last_exc = None
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if attempt == 0:
                        self.log(f"「{c.name}」第一次生成失败，自动重试一次："
                                 f"{truncate(str(exc), 120)}")
            if last_exc is not None:
                self.log(f"「{c.name}」生成失败，已跳过：{truncate(str(last_exc), 160)}")
        self._emit("角色主视图 / 姿态图阶段完成", 0.96)
        return project

    # ==================================================================
    # 一键全流程：分析 → 人设 → 分镜 → 出图（自检/返修）→ 导出
    # ==================================================================
    def auto_run(self, project: Project, export: bool = True) -> Project:
        """
        从正文自动跑到成品。

        设计成"可恢复"：已有角色/场景/分镜/成品图会自动复用，用户随时可以
        停下来改设定，再点一次继续。出图阶段每一格都会自检，低于阈值自动返修。
        """
        text = (project.novel_text or "").strip()
        if len(text) < config.MIN_NOVEL_LENGTH:
            raise ValueError("请先输入或上传小说文本")

        self._emit("一键全流程：开始", 0.01)

        # ---- 阶段 2：内容分析 ----
        with self._progress_window(0.02, 0.22):
            if not project.characters:
                self._emit("阶段 2/5：分析小说内容…", 0.02)
                self.analyze_novel(project)
            else:
                self._emit("阶段 2/5：复用已有角色/场景", 0.95)
            project.save()
        if self._check_stop():
            self.log("用户已取消，一键全流程停止")
            return project

        if not project.characters:
            raise ValueError("内容分析没有识别出角色，请检查文本或文本模型配置")

        # ---- 阶段 3：人设与参考图 ----
        missing = [c for c in project.characters
                   if not c.is_extra and not c.has_reference()]
        with self._progress_window(0.25, 0.15):
            if missing:
                self._emit(f"阶段 3/5：生成 {len(missing)} 个角色的人设与参考图…", 0.02)
                self.generate_all_characters(project, only_ids=[c.id for c in missing])
            elif not any(c.has_reference() for c in project.characters):
                self._emit("阶段 3/5：生成角色参考图…", 0.02)
                self.generate_all_characters(project)
            else:
                self._emit("阶段 3/5：复用已有角色参考图", 0.95)
            project.save()
        if self._check_stop():
            self.log("用户已取消，一键全流程停止")
            return project

        # ---- 阶段 4：分镜 ----
        with self._progress_window(0.40, 0.14):
            if not project.shots:
                self._emit("阶段 4/5：拆解分镜…", 0.02)
                self.decompose_storyboard(project)
            else:
                self._emit("阶段 4/5：复用已有分镜，执行导演层整理…", 0.30)
                report = self._repair_storyboard(project, attempts=1)
                self.log("导演层：" + director_mod.summary_text(report))
                self._emit("阶段 4/5：分镜整理完成", 0.95)
            # 版面：确保分页与当前分镜一致（换过RENDER_MODE/手动改过分镜都会重排）
            try:
                self.ensure_pages(project)
            except Exception as exc:  # noqa: BLE001
                self.log(f"分页规划失败（不影响出图）：{truncate(str(exc), 160)}")
            project.reindex_shots()
            project.save()
        if self._check_stop():
            self.log("用户已取消，一键全流程停止")
            return project

        if not project.shots:
            raise ValueError("分镜拆解失败：没有生成任何镜头")

        # ---- 阶段 5：出图（自检 + 自动返修） ----
        from .imggen import ShotBatchGenerator

        self._emit("阶段 5/5：逐格出图，并在每格生成后自检/自动返修…", 0.55)
        batch = ShotBatchGenerator(self)
        batch.log = self.log
        batch.progress = lambda m, v: self._emit(m, v)
        with self._progress_window(0.55, 0.39):
            batch.run(project, skip_done=True,
                      auto_repair=bool(getattr(config, "IMAGE_AUTO_REPAIR", True)))
        project.save()

        # ---- 导出 ----
        if export:
            failed = [s for s in project.shots if s.status == "失败" and not s.skip]
            if failed:
                raise ValueError(
                    f"还有 {len(failed)} 格生成失败（第 "
                    + "、".join(str(s.index) for s in failed[:12])
                    + " 格），已保留工程，未导出。请用「只重试失败的格子」再跑一次。")
            done, total = project.progress()
            if done <= 0:
                raise ValueError("没有任何一格生成成功，已停止导出")
            with self._progress_window(0.95, 0.045):
                from . import exporter as exporter_mod
                self._emit("正在导出条漫长图 / PDF / ZIP…", 0.10)
                res = exporter_mod.export_project(
                    project, generate_reader=True, make_zip=True)
                self.log(f"导出完成：{res.get('output_dir', '')}")
                project.save()
        self._emit("一键全流程完成", 1.0)
        return project

    # ==================================================================
    # 阶段四：分镜拆解
    # ==================================================================
    def decompose_storyboard(self, project: Project) -> Project:
        """把小说拆解成一系列分镜条目（整章一次拆完，再跑导演层整理）。"""
        self.last_notice = ""
        text = project.novel_text
        if not text:
            raise ValueError("请先输入或上传小说文本")

        whole_limit = int(getattr(config, "STORYBOARD_WHOLE_CHAPTER_CHARS", 24000))
        whole = len(text) <= whole_limit
        if whole:
            batches = [[text]]
            self._emit("开始拆解分镜（整章一次送入，保证节奏与角色一致）", 0.62)
        else:
            chunks = [c for _, c in chunk_with_offsets(
                text, chunk_size=getattr(config, "STORYBOARD_CHUNK_SIZE", 9000))]
            batch = max(1, config.STORYBOARD_CHUNK_BATCH)
            batches = [chunks[i:i + batch] for i in range(0, len(chunks), batch)]
            self._emit(f"开始拆解分镜，共 {len(chunks)} 个文本段", 0.62)

        all_shots: list[dict] = []
        running_index = 1
        for bi, group in enumerate(batches, start=1):
            if self._check_stop():
                self.log("用户已取消，停止分镜拆解")
                break
            merged_text = "\n\n".join(group)
            label = "整章" if whole else f"第 {bi}/{len(batches)} 批"
            try:
                target = max(6, min(60, int(round(len(merged_text) / 90))))
                # 有精修 25 格时，分镜格数按 25 格骨架展开（每格 1~2 个镜头），
                # 不再按字数把 8000 字推成 60 格——那会把 25 格骨架冲散。
                cells = list(getattr(project, "continuous_cells", []) or [])
                if whole and cells:
                    target = max(18, min(60, int(round(len(cells) * 1.4))))
                msgs = prompts.storyboard_prompt(project, merged_text,
                                                 running_index, label,
                                                 target_shots=target)
                msgs = prompt_overrides.apply_messages(
                    project, "storyboard.decompose", f"batch:{bi}", msgs)
                prompt_trace.record(project.id, "03_storyboard",
                                    f"decompose_{label}", msgs,
                                    meta={"chars": len(merged_text),
                                          "target_shots": target,
                                          "start_index": running_index})
                data = self.text.chat_json(
                    msgs, expect="object", json_mode=True,
                    max_tokens=config.TEXT_MAX_TOKENS)
                shots = []
                if isinstance(data, dict):
                    shots = data.get("shots") or []
                elif isinstance(data, list):
                    shots = data
                if not isinstance(shots, list):
                    shots = []
                for s in shots:
                    if isinstance(s, dict):
                        all_shots.append(s)
                running_index += len(shots)
                self.log(f"{label}拆解出 {len(shots)} 个分镜")
            except Exception as exc:  # noqa: BLE001
                self.log(f"{label}拆解失败（{truncate(str(exc), 140)}），"
                         f"改用本地规则拆解该段")
                self.last_notice = ("AI 分镜拆解失败（文本模型不可用/网络不通），"
                                    "已改用本地规则；结果可能与上次相同")
                all_shots.extend(self._rule_based_shots(project, merged_text))
            self._emit(f"分镜拆解中… {bi}/{len(batches)}",
                       0.62 + 0.14 * bi / len(batches))

        if not all_shots:
            self.log("模型未返回有效分镜，全部改用本地规则拆解")
            self.last_notice = ("AI 分镜拆解没有返回有效结果，"
                                "已改用本地规则；结果可能与上次相同")
            all_shots = self._rule_based_shots(project, text)

        # ---- 只有"分段拆解且总量不大"才做全局合并；整章一次拆完时不浪费调用 ----
        # 超过 80 格时合并调用的输出会非常长，容易触发截断，直接保留分段结果
        # （分段结果本身已按剧情顺序拼接，去重交给导演层）。
        if len(all_shots) > 1 and not whole:
            if len(all_shots) <= 80:
                self._emit("正在整体梳理分镜顺序与去重…", 0.78)
                try:
                    msgs = prompts.storyboard_merge_prompt(all_shots, project)
                    msgs = prompt_overrides.apply_messages(
                        project, "storyboard.merge", "global", msgs)
                    prompt_trace.record(project.id, "03_storyboard", "merge", msgs,
                                        meta={"shots_in": len(all_shots)})
                    data = self.text.chat_json(
                        msgs, expect="object", json_mode=True,
                        max_tokens=config.TEXT_MAX_TOKENS)
                    merged = data.get("shots") if isinstance(data, dict) else data
                    if isinstance(merged, list) and merged:
                        all_shots = [s for s in merged if isinstance(s, dict)]
                except Exception as exc:  # noqa: BLE001
                    self.log(f"分镜整体梳理失败（{truncate(str(exc), 140)}），"
                             f"保留原始拆解结果")
            else:
                self.log(f"分镜共 {len(all_shots)} 格，跳过全局合并（避免输出截断），"
                         f"按分段顺序直接整理")

        project.shots = self._build_shots(project, all_shots)
        project.reindex_shots()

        # ---- 台词归位：必须赶在"按对话拆格"之前 ----
        if config.STORYBOARD_EXTRACT_DIALOGUE:
            storyboard_mod.extract_dialogues(project)

        # ---- 拆细：一格塞太多对话会把画面盖住，按软上限再拆一轮 ----
        limit = getattr(config, "SHOT_DIALOGUE_SOFT_LIMIT", 0) or 0
        if limit > 0:
            crowded = [s for s in project.shots if len(s.dialogues) > limit]
            if crowded:
                self._emit(f"有 {len(crowded)} 格对话过多，正在拆细…", 0.86)
                project = self._split_crowded_shots(project, limit)
            remain = [s for s in project.shots if len(s.dialogues) > limit]
            if remain:
                self.log(f"仍有 {len(remain)} 格超过 {limit} 条对话，执行本地硬拆分")
                project = self._local_split_by_dialogue(project, limit)

        if len(project.shots) < config.MIN_SHOTS:
            self.log(f"分镜数量偏少（{len(project.shots)} 格），已用本地规则补充")
            extra = self._rule_based_shots(project, text)
            existing = {s.description.strip()[:24] for s in project.shots}
            for item in extra:
                if len(project.shots) >= config.MIN_SHOTS:
                    break
                if item.get("description", "")[:24] in existing:
                    continue
                project.shots.extend(self._build_shots(project, [item]))
            project.reindex_shots()

        if len(project.shots) > config.MAX_SHOTS:
            self.log(f"分镜数量过多（{len(project.shots)} 格），截断到 {config.MAX_SHOTS} 格")
            project.shots = project.shots[:config.MAX_SHOTS]
            project.reindex_shots()

        # ---- 导演层：清理描述、补镜头字段、修节奏、分配气泡位 ----
        self._emit("正在做导演层整理（镜头/节奏/气泡位）…", 0.92)
        report = storyboard_mod.normalize(project, extract=False)
        self._log_quality(report)
        d_report = self._repair_storyboard(project, attempts=2)
        self.log("导演层：" + director_mod.summary_text(d_report))

        # 双人同框在 Z-Image 上会补第三个人/互染服装；在分页前就拆成单人格。
        if bool(getattr(config, "AUTO_SPLIT_MULTI_SHOT", True)):
            self._split_multi_character_shots(project)
        project.reindex_shots()
        self._log_quality({"issues": d_report.get("issues") or []})

        # ---- 分镜版式：按内容把格子编成页，逐页选 SB-*/WB-* 版式 ----
        try:
            self.plan_pages(project)
        except Exception as exc:  # noqa: BLE001
            self.log(f"分页规划失败（不影响出图）：{truncate(str(exc), 160)}")

        self._emit(f"分镜拆解完成，共 {len(project.shots)} 格"
                   + (f" / {len(project.pages)} 页" if project.pages else ""), 0.97)
        return project

    def _split_multi_character_shots(self, project: Project) -> int:
        """按单格上限拆多人镜头；默认一格一人，群像模式最多 3 人。

        Qwen 实测 4 人会多画一个，所以群像模式建议上限 3；
        超过上限时仍按“一人一格”拆分，避免一张图糊成一团。
        """
        import copy as _copy
        import uuid as _uuid
        try:
            if bool(getattr(config, "STORYBOARD_ONE_PERSON_PER_PANEL", False)):
                max_cast = 1
            else:
                max_cast = int(rules_mod.cast_policy().get(
                    "max_characters", getattr(config, "MAX_CHARACTERS_PER_SHOT", 4)) or 4)
        except (TypeError, ValueError):
            max_cast = 4
        changed = 0
        i = 0
        while i < len(project.shots):
            shot = project.shots[i]
            names = [n for n in (shot.characters or []) if n]
            if len(names) <= max_cast:
                i += 1
                continue
            extras: list[Any] = []
            for name in names:
                ns = _copy.deepcopy(shot)
                ns.id = _uuid.uuid4().hex[:8]
                ns.characters = [name]
                raw_action = str((shot.character_actions or {}).get(name, "") or "").strip()
                # 单人拆分后，动作里不能再出现其他角色名，否则模型会按名字补人。
                for other in names:
                    if other and other != name:
                        raw_action = raw_action.replace(other, "")
                raw_action = re.sub(r"(和|与|跟)\s*(、|，|,)?\s*(一同|一起|一道)", "独自", raw_action)
                raw_action = raw_action.replace("一同", "独自").replace("一起", "独自").replace("一道", "独自")
                raw_action = re.sub(r"[、，,；;]\s*[、，,；;]+", "，", raw_action).strip("，、；; ")
                if len(raw_action) < 4:
                    raw_action = f"单人反应，情绪：{shot.mood or '平静'}；表情：{shot.expression or '自然'}"
                ns.character_actions = {name: raw_action}
                ns.char_positions = {name: (shot.char_positions or {}).get(name, "")}
                ns.char_sides = {name: (shot.char_sides or {}).get(name, "")}
                ns.description = (f"单人反应镜头：{name}，{raw_action}；"
                                  f"场景：{shot.scene}；画面里只有 {name} 一个人。")
                # 表情/焦点/原文里也不能留下别的角色名，否则模型会照着名字补人
                ns.expression = str(shot.expression or "").strip()
                ns.focus = str(getattr(shot, "focus", "") or "").strip()
                for other in names:
                    if other and other != name:
                        ns.expression = ns.expression.replace(other, "")
                        ns.focus = ns.focus.replace(other, "")
                if (not ns.expression or len(ns.expression) < 3
                        or any(w in ns.expression for w in ("一同", "一起", "酒客", "人群"))):
                    ns.expression = f"{name}的表情：{shot.mood or '平静'}，情绪集中在脸上"
                if not ns.focus or len(ns.focus) < 3:
                    ns.focus = f"{name}的脸与手部动作"
                ns.dialogues = [d for d in (shot.dialogues or [])
                                if not getattr(d, "speaker", "") or d.speaker == name]
                ns.raw_image_path = ""; ns.image_path = ""; ns.prop_image = ""
                ns.pose_image = ""; ns.pose_skeleton = ""; ns.status = "待生成"
                ns.error = ""; ns.attempts = 0
                extras.append(ns)
            project.shots[i:i + 1] = extras
            changed += len(extras) - 1
            self.log(f"第 {shot.index} 格有 {len(names)} 人（上限 {max_cast}），"
                     f"已拆成 {len(extras)} 个单人格")
            i += len(extras)
        if changed:
            self.log(f"超上限多人镜头拆分完成：新增 {changed} 格")
        return changed

    def _repair_storyboard(self, project: Project, attempts: int = 2) -> dict:
        """
        分镜自检 + 自动重拍。

        attempts=1 只整理不重拍（用于用户已经改过的分镜，避免覆盖人工修改）；
        attempts=2 时如果导演层仍报 danger，会让文本模型重拍一次再检查。
        """
        if not bool(getattr(config, "DIRECTOR_AUTO_REPAIR", True)):
            attempts = 1
        report = director_mod.normalize(project)
        for i in range(max(1, attempts)):
            if not self._needs_director_repair(report):
                return report
            if i >= attempts - 1:
                break
            self._emit(f"分镜自检未通过，正在让导演模型重拍（{i + 1}/{attempts - 1}）…", 0.94)
            changed = self.director_rewrite(project, reason="auto")
            if changed <= 0:
                self.log("导演重写没有返回有效分镜，保留现有分镜")
                break
            report = director_mod.normalize(project)
        return report

    @staticmethod
    def _needs_director_repair(report: dict) -> bool:
        """判断是否需要让模型重拍：描述不合格或画外音混入是硬伤。"""
        for it in report.get("issues") or []:
            if it.get("level") == "danger":
                return True
            if it.get("code") in ("char_maybe_offscreen", "duplicate_panel"):
                return True
        return False

    # ------------------------------------------------------------------
    # 分镜版式：把分镜编成页，并逐页选 SB-*/WB-* 版式
    # ------------------------------------------------------------------
    def plan_pages(self, project: Project, *, use_llm: bool | None = None,
                   force: bool = False) -> list:
        """
        分页 + 选版式。

        - webtoon 模式：清空页面，回到"一格一张图"的原条漫；
        - page/hybrid：先让文本模型给方案（可用时），严格校验；
          不合法或不可用就退回本地动态规划。
        """
        mode = (project.render_mode or config.render_mode()).strip().lower()
        if mode not in ("webtoon", "page", "hybrid"):
            mode = "hybrid"
        project.render_mode = mode
        if mode == "webtoon":
            project.clear_pages()
            project.page_plan_note = "原条漫模式：一格一张图，竖屏无缝长条"
            project.touch()
            self.log("分镜形态：原条漫（一格一张图）")
            return []

        if not project.shots:
            project.clear_pages()
            project.page_plan_note = "还没有分镜"
            return []

        pagelayout.annotate_shots(project)

        # 已规划过且分镜没变时直接复用（除非强制重排）
        if not force and project.pages:
            planned = [sid for p in project.pages for sid in p.shot_ids]
            if planned == [s.id for s in project.shots]:
                return project.pages

        if use_llm is None:
            use_llm = bool(getattr(config, "HANDRAW_LAYOUT_LLM", True))
        pages = None
        note = ""
        if use_llm and self.text is not None:
            try:
                layouts_table = stylelib.layout_brief_table()
                shot_lines = pagelayout.shot_brief_lines(project)
                msgs = prompts.page_plan_prompt(project, layouts_table, shot_lines)
                msgs = prompt_overrides.apply_messages(
                    project, "layout.page_plan", "global", msgs)
                prompt_trace.record(project.id, "09_layout", "plan", msgs,
                                    meta={"shots": len(project.shots),
                                          "layouts": len(stylelib.layout_entries())})
                data = self.text.chat_json(
                    msgs, expect="object", json_mode=True,
                    max_tokens=config.TEXT_MAX_TOKENS)
                pages = pagelayout.plan_from_llm(project, data)
                note = "AI 分页 + 本地校验通过" if pages else "AI 分页方案不合法，改用本地动态规划"
                if not pages:
                    self.log("AI 分页方案未通过校验，改用本地动态规划")
            except Exception as exc:  # noqa: BLE001
                self.log(f"AI 分页不可用（{truncate(str(exc), 140)}），改用本地动态规划")
        if not pages:
            pages = pagelayout.plan_local(project, mode=mode)
            note = note or "本地动态规划（按 beat/对白/动作/场景连续性选版式）"

        pagelayout.apply_plans(project, pages, note)
        project.save()
        stats = pagelayout.summary(project)
        self.log(f"分镜版式：{stats['pages']} 页 / {stats['panels']} 格，"
                 f"版式 {', '.join(f'{k}×{v}' for k, v in stats['layouts'].items())}"
                 f"（{note}）")
        return pages

    def ensure_pages(self, project: Project) -> None:
        """分镜图生成前确保页面规划与当前分镜一致。"""
        if not project.shots:
            return
        mode = (project.render_mode or config.render_mode()).strip().lower()
        if mode == "webtoon":
            project.clear_pages()
            return
        planned = [sid for p in project.pages for sid in p.shot_ids]
        if planned != [s.id for s in project.shots]:
            self.plan_pages(project, use_llm=False)


    def director_rewrite(self, project: Project, reason: str = "manual",
                         raise_on_error: bool = False) -> int:
        """
        让导演模型把现有分镜重拍一遍（保留剧情与台词，只重做镜头）。

        返回格数变化。已经出过图的工程默认不动，避免把用户的成品作废。
        """
        if not project.shots:
            return 0
        if not bool(getattr(config, "DIRECTOR_LLM_ENABLED", True)):
            self.log("导演重写已关闭（config.DIRECTOR_LLM_ENABLED=False）")
            return 0
        had_done = any(s.status == "已完成" for s in project.shots)

        payload = []
        for s in project.shots:
            payload.append({
                "index": s.index,
                "shot_type": s.shot_type,
                "camera": s.camera,
                "composition": s.composition,
                "lighting": s.lighting,
                "panel_scale": s.panel_scale,
                "transition": s.transition,
                "characters": list(s.characters),
                "char_sides": dict(s.char_sides),
                "scene": s.scene,
                "time_of_day": s.time_of_day,
                "focus": s.focus,
                "action": s.action,
                "expression": s.expression,
                "description": s.description,
                "mood": s.mood,
                "silent": s.silent,
                "hook": s.hook,
                "dialogues": [d.to_dict() for d in s.dialogues],
                "source_text": truncate(s.source_text, 120),
            })
        try:
            msgs = prompts.director_pass_prompt(project, payload, project.novel_text or "")
            msgs = prompt_overrides.apply_messages(
                project, "director.pass", "global", msgs)
            prompt_trace.record(project.id, "04_director", "pass", msgs,
                                meta={"shots_in": len(payload)})
            data = self.text.chat_json(
                msgs,
                expect="object", json_mode=True,
                max_tokens=config.TEXT_MAX_TOKENS)
        except Exception as exc:  # noqa: BLE001
            msg = f"导演重写失败（{truncate(str(exc), 160)}），保留现有分镜"
            self.log(msg)
            if raise_on_error:
                raise RuntimeError(msg) from exc
            return 0

        merged = data.get("shots") if isinstance(data, dict) else data
        if not isinstance(merged, list) or not merged:
            self.log("导演重写没有返回有效分镜，保留现有分镜")
            return 0
        new_shots = self._build_shots(project, merged)
        if not new_shots:
            return 0
        if len(new_shots) < max(4, int(len(project.shots) * 0.5)):
            self.log(f"导演重写后格数过少（{len(new_shots)}），保留现有分镜")
            return 0
        if any(len((s.description or "").strip()) < 12 for s in new_shots):
            self.log("导演重写后仍有空描述，保留现有分镜")
            return 0

        before = len(project.shots)
        project.shots = new_shots
        project.reindex_shots()
        if had_done:
            # 分镜重写后旧图（含气泡/空白气泡计划）全部失效，重置成待生成，
            # 让阶段 5 按新脚本重跑；图片文件仍保留在 data/shots 下。
            for s in project.shots:
                s.status = "待生成"
                s.image_path = ""
                s.raw_image_path = ""
                s.bubble_layout = []
                s.bubble_plan = []
            self.log("导演重写已作废旧成品图，请在阶段 5 重新出图")
        if config.STORYBOARD_EXTRACT_DIALOGUE:
            storyboard_mod.extract_dialogues(project)
        storyboard_mod.normalize(project, extract=False)
        director_mod.normalize(project)
        self.log(f"导演重写完成：{before} 格 → {len(project.shots)} 格"
                 f"（{reason}）")
        return len(project.shots) - before

    # ------------------------------------------------------------------
    def _log_quality(self, report: dict) -> None:
        """把整理报告与质量校验结果写进日志，让用户看得见。"""
        bits = []
        if report.get("split"):
            bits.append(f"拆出 {report['split']} 格")
        if report.get("scenes"):
            bits.append(f"纠正场景 {report['scenes']} 处")
        if report.get("rhythm"):
            bits.append(f"调整镜头 {report['rhythm']} 格")
        if report.get("moods"):
            bits.append(f"调整情绪 {report['moods']} 格")
        if report.get("narration"):
            bits.append(f"补旁白 {report['narration']} 条")
        if report.get("narration_llm"):
            bits.append(f"模型补旁白 {report['narration_llm']} 条")
        if report.get("director_rewrite"):
            bits.append(f"导演重拍 {report['director_rewrite']} 格")
        if bits:
            self.log("分镜整理：" + "，".join(bits))
        for it in report.get("issues") or []:
            self.log(f"分镜自检[{it['level']}] {it['message']}")
        for it in report.get("director_issues") or []:
            self.log(f"导演自检[{it['level']}] {it['message']}")

    def enhance_storyboard(self, project: Project,
                           use_llm_narration: bool = True) -> dict:
        """
        整理已有分镜（不重新拆解）：抽台词 → 按对话拆格 → 节奏/情绪/场景/旁白 → 校验。

        专门用来修复"原文搬运式"的旧分镜：台词被抄进 description、dialogues 为空、
        镜头情绪场景全部退化成同一个值。整个过程是确定性的，不依赖模型听话。
        """
        self._emit("正在整理分镜（台词/节奏/情绪/场景/旁白）…", 0.05)
        before = len(project.shots)

        # 1) 台词归位：必须先做，后面的"按对话拆格"才有依据
        if config.STORYBOARD_EXTRACT_DIALOGUE:
            storyboard_mod.extract_dialogues(project)

        # 2) 按对话条数拆格：台词抽出来之后这一步才真正生效
        limit = getattr(config, "SHOT_DIALOGUE_SOFT_LIMIT", 0) or 0
        if limit > 0:
            crowded = [s for s in project.shots if len(s.dialogues) > limit]
            if crowded:
                self._emit(f"有 {len(crowded)} 格台词过多，正在拆细…", 0.35)
                project = self._local_split_by_dialogue(project, limit, quiet=True)

        # 3) 动作拆格 + 镜头节奏 + 情绪 + 场景 + 旁白（规则兜底）
        self._emit("正在整理镜头节奏与旁白…", 0.45)
        report = storyboard_mod.normalize(project, extract=False)
        self._log_quality(report)

        # 4) 导演层确定性整理（补字段/修节奏/分气泡位/封面文案）。
        #    注意：这里**不调用模型**。"导演优化"是另一个按钮，专门做重拍；
        #    "整理分镜"必须保持秒级返回，否则用户会以为界面卡死。
        self._emit("正在做导演层整理（机位/画幅/气泡位）…", 0.62)
        d_report = director_mod.normalize(project)
        self.log("导演层：" + director_mod.summary_text(d_report))
        self._log_quality({"issues": d_report.get("issues") or []})

        # 5) 让模型补写旁白（规则兜底之外的质量提升）
        if use_llm_narration:
            self._emit("正在让模型补写旁白…", 0.78)
            try:
                report["narration_llm"] = self.fill_narration_llm(project)
            except Exception as exc:  # noqa: BLE001
                report["llm_error"] = truncate(str(exc), 200)
                self.log(f"模型补旁白失败（{truncate(str(exc), 120)}），"
                         f"保留规则兜底的旁白")

        project.reindex_shots()
        report["before"] = before
        report["after"] = len(project.shots)
        report["issues"] = storyboard_mod.validate(project)
        report["director_issues"] = director_mod.validate(project)
        self._log_quality(report)
        self._emit(f"分镜整理完成：{before} 格 → {len(project.shots)} 格", 0.95)
        return report

    def fill_narration_llm(self, project: Project, max_items: int = 8) -> int:
        """让模型给关键格补旁白（规则兜底之外的质量提升）。返回写入条数。"""
        if not project.shots:
            return 0
        payload = [{
            "index": s.index,
            "scene": s.scene,
            "characters": list(s.characters),
            "description": truncate(s.description, 160),
            "source_text": truncate(s.source_text, 160),
            "speech": [d.text for d in s.dialogues
                       if d.kind == "speech" and (d.text or "").strip()][:3],
            "has_narration": any(d.kind == "narration" and (d.text or "").strip()
                                 for d in s.dialogues),
        } for s in project.shots]
        msgs = prompts.narration_fill_prompt(project, payload,
                                             config.STORYBOARD_NARRATION_MAX_CHARS)
        msgs = prompt_overrides.apply_messages(
            project, "narration.fill", "global", msgs)
        prompt_trace.record(project.id, "05_narration", "fill", msgs,
                            meta={"shots": len(payload)})
        data = self.text.chat_json(msgs, expect="object", json_mode=True)
        items = data.get("narration") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return 0
        by_index = {s.index: s for s in project.shots}
        added = 0
        for it in items[:max_items]:
            if not isinstance(it, dict):
                continue
            text = str(it.get("text") or "").strip()[:config.STORYBOARD_NARRATION_MAX_CHARS]
            if not text:
                continue
            try:
                shot = by_index.get(int(it.get("index") or 0))
            except Exception:  # noqa: BLE001
                shot = None
            if shot is None:
                continue
            existing = [d for d in shot.dialogues if d.kind == "narration"]
            if existing:
                existing[0].text = text
                existing[0].speaker = "旁白"
            else:
                shot.dialogues.insert(0, Dialogue(speaker="旁白", text=text,
                                                  kind="narration", tone="旁白"))
            added += 1
        if added:
            self.log(f"模型补写旁白 {added} 条")
        return added

    def _split_crowded_shots(self, project: Project, limit: int) -> Project:
        """
        把对话过多的分镜拆细。

        这里**不再调用文本模型**。实测模型只回传被拆的那几格，会把其余几十格
        全部丢掉（55 格 → 2 格），是比"格子挤"严重得多的故障。
        本地按对话条数切分是确定性的，且会保留镜头/站位/场景字段，足够用。
        """
        crowded = [s for s in project.shots if len(s.dialogues) > limit]
        if not crowded:
            return project
        self.log(f"有 {len(crowded)} 格对话超过 {limit} 条，按对话条数本地拆细")
        return self._local_split_by_dialogue(project, limit, quiet=True)

    def _local_split_by_dialogue(self, project: Project, limit: int,
                                 quiet: bool = False) -> Project:
        """按对话条数把一格强制拆成多格（不依赖模型）。"""
        out: list[Shot] = []
        for shot in project.shots:
            if len(shot.dialogues) <= limit:
                out.append(shot)
                continue
            groups = [shot.dialogues[i:i + limit]
                      for i in range(0, len(shot.dialogues), limit)]
            # 若最后一组只剩拟声词（不需要气泡），合并回上一组，避免产出无台词的碎格
            if len(groups) > 1:
                tail = groups[-1]
                if tail and all(d.kind == "sfx" for d in tail):
                    groups[-2] = groups[-2] + tail
                    groups.pop()
            desc_parts = self._split_description_by(len(groups), shot.description)
            used_desc: set[str] = set()
            for gi, group in enumerate(groups):
                # 同一格的连续拆分给不同镜头，避免看起来重复
                panel_type = ("近景", "特写", "中景")[gi % 3]
                desc = desc_parts[gi] if gi < len(desc_parts) else shot.description
                # 描述重复说明原句不够分：改用按镜头类型生成的差异化描述，
                # 否则拆出来的几格会画出几乎一样的图（同一动作重复画）
                if desc in used_desc:
                    desc = storyboard_mod.variant_description(panel_type, gi)
                used_desc.add(desc)
                clone = Shot(
                    shot_type=panel_type,
                    characters=list(shot.characters),
                    scene=shot.scene,
                    description=desc,
                    mood=shot.mood,
                    source_text=shot.source_text,
                    camera=(shot.camera if gi == 0 else ""),
                    composition=(shot.composition if gi == 0 else ""),
                    lighting=shot.lighting,
                    focus="",
                    expression=shot.expression,
                    action="",
                    transition=("主体切换" if gi else shot.transition),
                    panel_scale=("S" if panel_type in ("近景", "特写") else "M"),
                    char_sides=dict(shot.char_sides),
                    time_of_day=shot.time_of_day,
                )
                clone.dialogues = list(group)
                out.append(clone)
        project.shots = out
        project.reindex_shots()
        if not quiet:
            self.log(f"本地拆分完成：共 {len(project.shots)} 格")
        return project

    @staticmethod
    def _split_description_by(count: int, description: str) -> list[str]:
        """
        把一段画面描述尽量均匀地切成 count 份（**只按句子边界**）。

        句子不够分时，宁可按句子轮换复用，也绝不从句子中间截断——
        截断会产出"（对话密集：说话"这种半句描述，出图必然失控。
        """
        import re as _re
        text = (description or "").strip()
        if count <= 1 or not text:
            return [text] * max(1, count)
        sentences = [x.strip() for x in _re.split(r"(?<=[。！？…；;])", text)
                     if x.strip()]
        if not sentences:
            return [text] * count
        if len(sentences) >= count:
            per = max(1, len(sentences) // count)
            parts: list[str] = []
            for i in range(count):
                if i == count - 1:
                    parts.append("".join(sentences[i * per:]).strip())
                else:
                    parts.append("".join(sentences[i * per:(i + 1) * per]).strip())
            return [p or text for p in parts]
        # 句子不够：按句轮换，多出来的格子补一个"换角度"提示，保证画面不重复
        hints = ("（说话人近景）", "（听者反应近景）", "（手部或物件特写）",
                 "（同一场景的另一角度）")
        parts = []
        for i in range(count):
            base = sentences[i % len(sentences)]
            if i >= len(sentences):
                base = base + hints[i % len(hints)]
            parts.append(base)
        return parts

    def _build_shots(self, project: Project, raw_list: Iterable) -> list[Shot]:
        """把模型返回的原始分镜字典转成 Shot 对象，并做字段规范化。"""
        out: list[Shot] = []
        valid_names = set(project.character_names())

        for item in raw_list:
            if not isinstance(item, dict):
                continue
            shot = Shot()
            st = str(item.get("shot_type", "") or "").strip()
            shot.shot_type = st if st in config.SHOT_TYPES else self._guess_shot_type(
                str(item.get("description", "")))

            chars = item.get("characters") or []
            if isinstance(chars, str):
                chars = [x.strip() for x in re.split(r"[，,、;；/\s]+", chars) if x.strip()]
            picked: list[str] = []
            for name in chars:
                name = str(name).strip()
                if not name:
                    continue
                if not valid_names or name in valid_names:
                    if name not in picked:
                        picked.append(name)
                else:
                    matched = project.find_character(name)
                    if matched and matched.name not in picked:
                        picked.append(matched.name)
            shot.characters = picked

            scene = str(item.get("scene", "") or "").strip()
            if scene and project.scene_names():
                matched_scene = next((s for s in project.scenes
                                      if s.name == scene or s.name in scene
                                      or scene in s.name), None)
                scene = matched_scene.name if matched_scene else scene
            shot.scene = scene

            shot.description = unescape_text(str(item.get("description", "") or "").strip())
            mood = str(item.get("mood", "") or "").strip()
            shot.mood = mood if mood in config.MOOD_OPTIONS else self._guess_mood(
                shot.description)
            shot.source_text = unescape_text(str(item.get("source_text", "") or "").strip())
            shot.source_quote = unescape_text(
                str(item.get("source_quote", "") or "").strip())[:40]
            shot.light_dir = unescape_text(
                str(item.get("light_dir", "") or "").strip())[:12]

            # ---- 导演层字段 ----
            camera = str(item.get("camera", "") or "").strip()
            shot.camera = camera if camera in config.SHOT_ANGLES else ""
            comp = str(item.get("composition", "") or "").strip()
            shot.composition = comp if comp in config.SHOT_COMPOSITIONS else ""
            light = str(item.get("lighting", "") or "").strip()
            shot.lighting = light if light in config.SHOT_LIGHTINGS else ""
            trans = str(item.get("transition", "") or "").strip()
            shot.transition = trans if trans in config.TRANSITION_TYPES else ""
            scale = str(item.get("panel_scale", "") or "").strip().upper()
            shot.panel_scale = scale if scale in config.PANEL_SCALE_OPTIONS else ""
            shot.time_of_day = unescape_text(str(item.get("time_of_day", "") or "").strip())
            shot.focus = unescape_text(str(item.get("focus", "") or "").strip())
            shot.action = unescape_text(str(item.get("action", "") or "").strip())
            shot.expression = unescape_text(str(item.get("expression", "") or "").strip())
            sides = item.get("char_sides") or {}
            if isinstance(sides, dict):
                shot.char_sides = {
                    str(k): str(v) for k, v in sides.items()
                    if str(k).strip() and str(v).strip()
                }
            positions = item.get("char_positions") or {}
            if isinstance(positions, dict):
                shot.char_positions = {
                    str(k): str(v) for k, v in positions.items()
                    if str(k).strip() and str(v).strip()
                }
            actions = item.get("character_actions") or {}
            if isinstance(actions, dict):
                shot.character_actions = {
                    str(k): str(v) for k, v in actions.items()
                    if str(k).strip() and str(v).strip()
                }
            # 模型经常漏填 character_actions；用这一格 action 回填到每个可见角色，
            # 姿态规划就不会把所有格都当成站桩。
            if not shot.character_actions and shot.action:
                shot.character_actions = {
                    str(name): str(shot.action) for name in shot.characters
                }
            shot.gaze = unescape_text(str(item.get("gaze", "") or "").strip())
            props = item.get("props") or []
            if isinstance(props, str):
                props = [x.strip() for x in re.split(r"[，,、;；/]+", props) if x.strip()]
            shot.props = [str(x).strip() for x in props if str(x).strip()][:6]
            shot.silent = bool(item.get("silent", False))
            shot.hook = bool(item.get("hook", False))

            # ---- 分镜内容分析：beat / 节奏 / 视觉权重 / 字幕栏 ----
            beat = str(item.get("beat", "") or "").strip()
            shot.beat = beat if beat in config.SHOT_BEATS else ""
            pacing = str(item.get("pacing", "") or "").strip()
            shot.pacing = pacing if pacing in config.PACING_OPTIONS else ""
            emph = str(item.get("panel_emphasis", "") or "").strip().lower()
            shot.panel_emphasis = emph if emph in config.EMPHASIS_OPTIONS else ""
            shot.caption = unescape_text(str(item.get("caption", "") or "").strip())[:60]
            hint = str(item.get("layout_hint", "") or "").strip().upper()
            shot.layout_hint = hint if re.fullmatch(r"(SB|WB)-\d{3}", hint) else ""

            dialogues: list[Dialogue] = []
            raw_dialogues = item.get("dialogues") or []
            if isinstance(raw_dialogues, str):
                raw_dialogues = [{"text": x} for x in re.split(r"[\n；;]+", raw_dialogues)
                                 if x.strip()]
            if isinstance(raw_dialogues, dict):
                raw_dialogues = [raw_dialogues]
            for d in raw_dialogues[:config.MAX_BUBBLES_PER_SHOT]:
                if isinstance(d, str):
                    dialogues.append(Dialogue(text=d.strip()))
                    continue
                if not isinstance(d, dict):
                    continue
                text = unescape_text(str(d.get("text", "") or "").strip())
                if not text:
                    continue
                kind = str(d.get("kind", "") or "speech").lower()
                if kind not in ("speech", "narration", "sfx"):
                    kind = "speech"
                speaker = unescape_text(str(d.get("speaker", "") or "").strip())
                if speaker in ("旁白", "narration"):
                    kind = "narration"
                if speaker in ("音效", "sfx", "拟声"):
                    kind = "sfx"
                slot = str(d.get("slot", "") or "").strip()
                tail = str(d.get("tail", "") or "").strip()
                dialogues.append(Dialogue(
                    speaker=speaker, text=text,
                    tone=str(d.get("tone", "") or "").strip(),
                    bubble_style=str(d.get("bubble_style", "") or "").strip(),
                    kind=kind,
                    slot=slot if slot in config.BUBBLE_SLOTS else "",
                    tail=tail,
                    offscreen=bool(d.get("offscreen", False)),
                    size=str(d.get("size", "") or "").strip().upper(),
                ))
            shot.dialogues = dialogues

            # 描述里点名出现、但 characters 漏掉的角色补回来：
            # 否则出图时拿不到该角色的参考图，模型会凭空造一个路人。
            for c in project.characters:
                if c.name and c.name in (shot.description or "") \
                        and c.name not in shot.characters:
                    shot.characters.append(c.name)

            if not shot.description:
                shot.description = shot.source_text or "（该分镜缺乏描述，请补充）"
            out.append(shot)
        return out

    @staticmethod
    def _guess_shot_type(description: str) -> str:
        text = description or ""
        if any(k in text for k in ("特写", "面部", "眼睛", "泪水", "嘴角")):
            return "特写"
        if any(k in text for k in ("近景", "上半身", "胸口")):
            return "近景"
        if any(k in text for k in ("远处", "远眺", "全景", "整个", "街道尽头", "天空")):
            return "远景"
        if any(k in text for k in ("全身", "走进", "站立", "来到", "推开")):
            return "全景"
        return "中景"

    @staticmethod
    def _guess_mood(description: str) -> str:
        text = description or ""
        table = [
            ("紧张", ("紧张", "急促", "心跳", "危机", "追", "逃", "危险")),
            ("悲伤", ("悲伤", "哭", "泪", "痛", "失去", "跪")),
            ("温馨", ("温暖", "温柔", "微笑", "阳光", "轻轻")),
            ("搞笑", ("笑", "滑稽", "尴尬", "吐槽", "搞笑")),
            ("愤怒", ("愤怒", "怒吼", "攥紧", "咬牙", "怒")),
            ("悬疑", ("阴影", "低语", "秘密", "诡异", "黑暗", "凝视")),
            ("激动", ("激动", "兴奋", "喊", "大声")),
            ("压抑", ("压抑", "沉", "阴冷", "沉默", "潮湿")),
            ("浪漫", ("浪漫", "拥抱", "牵手", "对视", "脸红")),
            ("平静", ("平静", "安静", "静静", "缓缓")),
        ]
        for mood, keys in table:
            if any(k in text for k in keys):
                return mood
        return "平静"

    def _rule_based_shots(self, project: Project, text: str) -> list[dict]:
        """不依赖模型的本地规则拆解兜底（每段一画面 + 对话提取）。"""
        text = normalize_novel(text)
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        shots: list[dict] = []
        buffer = ""
        for para in paragraphs:
            buffer = f"{buffer}\n{para}".strip()
            if len(buffer) < 120 and para is not paragraphs[-1]:
                continue
            shots.append(self._make_rule_shot(project, buffer))
            buffer = ""
        if buffer:
            shots.append(self._make_rule_shot(project, buffer))
        return shots

    def _make_rule_shot(self, project: Project, para: str) -> dict:
        dialogues: list[dict] = []
        narrations: list[str] = []
        for line in re.split(r"(?<=[。！？…])", para):
            line = line.strip()
            if not line:
                continue
            m = re.findall(r"[“\"]([^”\"]{1,120})[”\"]", line)
            for quote in m:
                dialogues.append({"speaker": "", "text": quote.strip(),
                                  "tone": "", "kind": "speech"})
            if not m and len(line) > 6:
                narrations.append(line.strip("“”\""))
        desc = para.replace("\n", " ").strip()
        name_hits = [n for n in project.character_names() if n and n in para]
        return {
            "shot_type": self._guess_shot_type(desc),
            "characters": name_hits or project.character_names()[:1],
            "scene": project.scene_names()[0] if project.scene_names() else "",
            "description": truncate(desc, 200, ""),
            "mood": self._guess_mood(desc),
            "source_text": truncate(para, 200, ""),
            "dialogues": dialogues[:config.MAX_BUBBLES_PER_SHOT],
        }

    # ==================================================================
    # 阶段五：分镜出图（单张，批量循环在 imggen.py 中）
    # ==================================================================
    @staticmethod
    def _shot_level(shot: Shot) -> str:
        t = str(getattr(shot, "shot_type", "") or "")
        if t in ("特写", "近景"):
            return "close"
        if t in ("全景", "远景"):
            return "wide"
        return "medium"

    @staticmethod
    def _shot_view(shot: Shot) -> str:
        """这一格需要的视角：front/side/back/top。"""
        hint = str(getattr(shot, "view_hint", "") or "").strip().lower()
        alias = {
            "front": "front", "正面": "front",
            "side": "side", "侧面": "side", "侧身": "side", "侧脸": "side",
            "back": "back", "背面": "back", "背影": "back", "背身": "back",
            "top": "top", "俯视": "top", "顶视": "top", "鸟瞰": "top",
        }
        if hint in alias:
            return alias[hint]
        text = f"{getattr(shot, 'camera', '')} {getattr(shot, 'shot_type', '')} " \
               f"{getattr(shot, 'description', '')}"
        if any(k in text for k in ("俯视", "顶视", "鸟瞰", "bird")):
            return "top"
        if any(k in text for k in ("背影", "背对", "背面", "背身")):
            return "back"
        if any(k in text for k in ("侧脸", "侧面", "侧身")):
            return "side"
        return "front"

    @staticmethod
    def _ordered_names(shot: Shot) -> list[str]:
        names = [n for n in (shot.characters or []) if n]
        sides = getattr(shot, "char_sides", {}) or {}

        def key(name: str) -> int:
            side = str(sides.get(name, ""))
            if "左" in side:
                return 0
            if "右" in side:
                return 2
            return 1

        return sorted(names, key=key)  # 稳定排序，未知侧别保持原顺序

    def _character_ref_path(self, character: Character, role: str) -> str:
        if role == "face":
            return character.face_reference()
        if role in ("side", "back", "top"):
            return character.view(role)
        return character.reference_image() or ""

    def plan_shot_refs(self, project: Project, shot: Shot) -> list[dict]:
        """按景别/视角/人数生成参考图计划（Qwen 分镜专用）。

        返回 ``[{path, character, role, view, order}]``；顺序即提示词“第 N 张”。
        """
        if not (isinstance(self.image, QwenComicClient)
                and bool(getattr(config, "QWEN_STORYBOARD_USE_CHAR_REFS", True))):
            return []
        if not self.refs_supported:
            return []
        names = self._ordered_names(shot)
        if not names:
            return []
        level = self._shot_level(shot)
        view = self._shot_view(shot)
        face_enabled = bool(getattr(config, "QWEN_STORYBOARD_FACE_REF", True))
        plan: list[tuple[str, str]] = []

        # 全景/远景默认不送角色参考：实测“远景+主视图+背面”会把背影复制成两个人，
        # 而且脸只占很小，参考收益低。需要时用 QWEN_STORYBOARD_WIDE_USE_REF 单独开。
        if level == "wide" and not bool(getattr(config, "QWEN_STORYBOARD_WIDE_USE_REF", False)):
            return []

        if len(names) == 1:
            c = project.find_character(names[0])
            if not c:
                return []
            if view == "top":
                top = c.view("top")
                if top and c.view_quality_of("top") == "top_down":
                    plan = [("main", "identity"), ("top", "view")]
                else:
                    plan = [("main", "identity")]
            elif view in ("side", "back"):
                vp = c.view(view)
                plan = [("main", "identity"), (view, "view")] if vp else [("main", "identity")]
            elif level == "close" and face_enabled:
                face = c.face_reference()
                main = c.reference_image() or ""
                if face and face != main:
                    plan = [("face", "identity"), ("main", "support")]
                else:
                    plan = [("face", "identity")] if face else []
            elif level == "wide":
                plan = [("main", "identity")] if bool(
                    getattr(config, "QWEN_STORYBOARD_WIDE_USE_REF", False)) else []
            else:
                plan = [("main", "identity")]
        elif len(names) == 2:
            if level == "wide":
                plan = []
            else:
                for name in names:
                    c = project.find_character(name)
                    if not c:
                        continue
                    face = c.face_reference() if face_enabled else ""
                    if face and c.face_ref and Path(c.face_ref).is_file():
                        plan.append(("face", "identity"))
                    else:
                        plan.append(("main", "identity"))
        else:
            plan = []

        out: list[dict] = []
        seen: set[str] = set()
        for idx, (role, _kind) in enumerate(plan):
            if len(names) == 1:
                c = project.find_character(names[0])
            else:
                if idx >= len(names):
                    break
                c = project.find_character(names[idx])
            if c is None:
                continue
            path = self._character_ref_path(c, role)
            if not path or not Path(path).is_file() or path in seen:
                continue
            seen.add(path)
            out.append({"path": path, "character": c.name, "role": role,
                        "view": view if role in ("side", "back", "top") else "front",
                        "order": len(out) + 1})
        plan_limit = min(
            int(getattr(config, "QWEN_STORYBOARD_REF_MAX", 3) or 3),
            int(self.reference_slot_limit() or 3),
        )
        return out[:max(0, plan_limit)]

    def build_shot_reference_plan(self, project: Project,
                                  shot: Shot) -> tuple[list[str], list[str]]:
        """收集该分镜的参考图，并返回与图片一一对应的角色名。"""
        plan = self.plan_shot_refs(project, shot)
        if plan:
            return [p["path"] for p in plan], [p["character"] for p in plan]
        if (isinstance(self.image, QwenComicClient)
                and bool(getattr(config, "QWEN_STORYBOARD_USE_CHAR_REFS", True))):
            return [], []
        refs: list[str] = []
        names: list[str] = []
        for name in shot.characters:
            character = project.find_character(name)
            if not character:
                continue
            look = shot.look_override.get(character.name)
            old_look = character.active_look
            if look:
                character.active_look = look
            path = character.reference_image()
            character.active_look = old_look
            if path and path not in refs:
                refs.append(path)
                names.append(character.name)
            if len(refs) >= config.MAX_REFERENCE_IMAGES:
                break
        return refs, names

    def build_shot_references(self, project: Project, shot: Shot) -> list[str]:
        """收集该分镜实际要送进工作流的参考图。"""
        if not self.refs_supported:
            return []
        if self.storyboard_t2i:
            return [p["path"] for p in self.plan_shot_refs(project, shot)]
        refs = self.style_reference(project)
        # style_color 只接 1 张"画风参考"，角色图不能混进去（否则会拿角色图做色彩基准）
        if bool(getattr(self.image, "color_matched", False)):
            return refs[:1]
        char_refs, _names = self.build_shot_reference_plan(project, shot)
        for p in char_refs:
            if p not in refs:
                refs.append(p)
        limit = self.reference_slot_limit()
        return refs[:limit]

    def character_reference_bundle(self, project: Project, shot: Shot,
                                   max_refs: int | None = None) -> list[str]:
        """兼容旧调用：Qwen 返回路由计划；其他后端保留“单人主视图”旧行为。"""
        paths = [p["path"] for p in self.plan_shot_refs(project, shot)]
        if paths:
            return paths[:int(max_refs)] if max_refs else paths
        if (isinstance(self.image, QwenComicClient)
                and bool(getattr(config, "QWEN_STORYBOARD_USE_CHAR_REFS", True))):
            return []
        names = [n for n in (shot.characters or []) if n]
        if len(names) != 1 or str(getattr(shot, "shot_type", "") or "") in ("远景", "全景"):
            return []
        c = project.find_character(names[0])
        p = c.reference_image() if c else ""
        out = [p] if p and Path(p).is_file() else []
        return out[:int(max_refs)] if max_refs else out

    @staticmethod
    def _reference_owner(project: Project, path: str) -> str:
        """参考图 → 角色名：提示词里的“第 N 张参考图是某某”必须与图对得上。"""
        if not path:
            return ""
        for c in getattr(project, "characters", []) or []:
            try:
                own = {c.reference_image(), c.triple_view, c.top_view}
            except Exception:  # noqa: BLE001
                own = {c.main_visual, c.triple_view, c.top_view}
            if path in own:
                return c.name
        return ""

    def panel_size_for_shot(self, project: Project, shot: Shot) -> tuple[int, int]:
        """这一格实际出图用的画幅：分格页按格子的比例算，原条漫按镜头分档。"""
        page = project.page_for_shot(shot.id)
        if page is not None and page.mode != "webtoon" \
                and 0 <= shot.panel_index < len(page.cells):
            return pagelayout.panel_generation_size(page.cells[shot.panel_index], page)
        return shot.panel_size()

    def build_shot_prompt(self, project: Project, shot: Shot,
                          include_bubbles: bool = True,
                          draw_bubbles: bool | None = None) -> str:
        """组装分镜的图像生成提示词。

        这里只负责"取数据 + 拼接参考图说明"，**不再往正向提示词里塞
        '不要画第二个人 / 没有路人 / 禁止融合'**：
        正向否定会把该名词喂给模型（rules/03 D1），而且多次调用会重复叠加。
        人数锁、单人去人群、双人差异对照统一由 ``castguard`` 生成，
        且只作用于提示词副本，不改工程里的分镜数据。
        """
        cast: list[dict] = []
        for name in shot.characters:
            c = project.find_character(name)
            if not c:
                continue
            cast.append({
                "name": c.name,
                # 身份锁：脸/发/眼/服装/记忆点，跨格逐字复用
                "card": prompts.character_feature_lock(c) or c.identity_card(),
                "outfit": prompts._character_outfit(c),
                "side": (shot.char_sides or {}).get(c.name, ""),
                "position": (shot.char_positions or {}).get(c.name, ""),
                "char_action": (shot.character_actions or {}).get(c.name, ""),
                "age": prompts._normalize_age(c),
                "build": str((prompts._character_hard_features(c) or {}).get("build", "") or ""),
            })
        style_refs = self.style_reference(project)
        color_match = bool(getattr(self.image, "color_matched", False))
        plan: list[dict] = []
        ref_roles: list[dict] = []
        if self.storyboard_t2i:
            # Qwen 分镜：按景别/视角/人数路由，只送 1~2 张有明确职责的参考图。
            plan = self.plan_shot_refs(project, shot)
            char_refs = [p["path"] for p in plan]
            char_names = [p["character"] for p in plan]
        else:
            char_refs, char_names = self.build_shot_reference_plan(project, shot)
        allowed = self.reference_slot_limit()
        ref_names: list[str] = []
        if self.refs_supported:
            # 色彩迁移的"只取色"说明已经在风格块里，不再重复一遍参考图编号
            if style_refs and not color_match:
                ref_names.append("__style__")
                ref_roles.append({"name": "__style__", "role": "style"})
            if not color_match:
                if plan:
                    for p in plan:
                        ref_names.append(p["character"])
                        ref_roles.append({"name": p["character"], "role": p["role"]})
                else:
                    ref_names.extend(char_names)
                    ref_roles.extend({"name": n, "role": "main"} for n in char_names)
            ref_names = ref_names[:allowed]
            ref_roles = ref_roles[:allowed]
        shot.ref_plan = list(plan)
        triggers = [str(c.get("lora_trigger") or "").strip() for c in cast]
        triggers = [x for x in triggers if x] if getattr(config, "IMAGE_LORA_ENABLED", True) else []
        trigger = "、".join(dict.fromkeys(triggers))
        ref_mode = "color" if color_match else "style"
        # 触发词交给提示词构建器放在画风锚点里：超预算时它会按 drop_order
        # 丢场景/构图，而不是把尾巴（气泡指令）切掉。
        prompt = prompts.shot_image_prompt(project, shot, cast=cast,
                                           include_bubbles=include_bubbles,
                                           ref_names=ref_names,
                                           ref_roles=ref_roles,
                                           style_ref=bool(style_refs),
                                           style_ref_mode=ref_mode,
                                           draw_bubbles=draw_bubbles,
                                           trigger=trigger)
        prompt = prompt_overrides.text(
            project, "image.shot_prompt", f"shot:{shot.id}", prompt)
        note = (getattr(shot, "image_note", "") or "").strip()
        if note:
            prompt += "\n\n【本轮返修要求，优先级最高】" + note
        # 无人物镜头：用正面描述把画面锁在"道具 + 承托面"上。
        # 不写"不要出现人物"——那等于把"人物"喂给模型。
        if not (shot.characters or []):
            prompt += ("；本格是无人物局部特写：画面主体是道具与承托它的桌面/台面，"
                       "画面里只有这个物件与它所在的空间。")
        # handraw 风格块已经完整定义了画风；再叠一条"项目画风锁定"只会
        # 挤占预算，并带进"不要每格换风格"这类正向否定。
        if not (bool(getattr(config, "HANDRAW_ENABLED", True)) and getattr(project, "style_id", "")):
            prompt = prompts.comic_style_lock(project) + "；" + prompt
        return prompt

    def generate_one_shot(self, project: Project, shot: Shot,
                          include_bubbles: bool = True,
                          size: tuple[int, int] | None = None,
                          auto_pose: bool | None = None) -> Shot:
        """
        生成单张分镜（不含气泡叠加，气泡由 bubble.py 处理）。
        结果写入 shot.raw_image_path。
        """
        panel = size or self.panel_size_for_shot(project, shot)
        # 出图前先算好气泡落点：
        #   · IMAGE_DRAW_BUBBLES=True 且工作流不做色彩迁移 → 提示词要求模型按坐标画空白气泡；
        #   · 色彩迁移工作流会把气泡一起染色（检测阈值失效）→ 气泡全部交给程序画；
        #   · 默认 False → 提示词只要求留出干净背景，气泡由程序画。
        color_match = bool(getattr(self.image, "color_matched", False) and project.style_id)
        # Qwen T2I 与 25 格一致：不把“画空白气泡”写进正向提示词。
        # 模型自绘气泡一旦定位失败，就会留下没有字的黑气泡；统一由程序画。
        model_bubbles = bool(include_bubbles and shot.has_dialogue
                             and getattr(config, "IMAGE_DRAW_BUBBLES", False)
                             and not color_match
                             and not self.storyboard_t2i)
        shot.bubble_programmatic = bool(include_bubbles and shot.has_dialogue
                                        and not model_bubbles)
        if include_bubbles and shot.has_dialogue:
            # 即使由程序画气泡，也要先算好落点：提示词会要求把这一带留成干净背景，
            # 画面焦点才不会被事后贴上的文字压住。
            shot.bubble_plan = bubble_mod.plan_layout(shot, panel[0], panel[1])
        else:
            shot.bubble_plan = []

        # 纯道具镜头：Z-Image 文生图会强行补完整人物，所以没有道具参考图就拒绝；
        # Qwen T2I 能画物件特写（25 格已验证），没有参考图也允许直接生成。
        if not (shot.characters or []):
            has_prop = bool(shot.prop_image and Path(shot.prop_image).exists())
            if has_prop:
                out_dir = Path(config.SHOT_DIR) / project.id
                out_dir.mkdir(parents=True, exist_ok=True)
                raw_path = out_dir / f"{config.SHOT_FILENAME_TEMPLATE.format(index=shot.index)}_raw.png"
                try:
                    im = ImageOps.fit(Image.open(shot.prop_image).convert("RGB"),
                                      panel, method=Image.LANCZOS, centering=(0.5, 0.5))
                except Exception as exc:  # noqa: BLE001
                    raise ValueError(f"道具参考图无法读取：{exc}") from exc
                im.save(raw_path, format="PNG")
                shot.raw_image_path = str(raw_path)
                self.log(f"分镜 {shot.index} 纯道具格，使用上传的道具图，不生成人物")
                return shot
            if not isinstance(self.image, QwenComicClient):
                raise ValueError(
                    f"第 {shot.index} 格是纯道具镜头，需要先上传道具参考图；"
                    "当前 Z-Image 文生图会补人物，已禁止。")
            self.log(f"分镜 {shot.index} 纯道具格，Qwen T2I 直接生成物件特写")

        prompt = self.build_shot_prompt(project, shot, include_bubbles,
                                        draw_bubbles=model_bubbles)
        wf_label = ("Qwen-Image-2.1 Turbo（T2I）" if self.storyboard_t2i
                    else ("Qwen-Image-2.1 Turbo" if isinstance(self.image, QwenComicClient)
                          else f"ComfyUI 工作流 {config.COMFY_WORKFLOW_NAME}"))
        prompt_trace.record(
            project.id, "06_image", f"shot_{shot.index:03d}",
            [{"role": "system", "content": f"图像生成（{wf_label}）"},
             {"role": "user", "content": prompt}],
            meta={"shot": shot.index, "shot_type": shot.shot_type,
                  "characters": "、".join(shot.characters or []) or "（无）",
                  "size": f"{panel[0]}x{panel[1]}",
                  "bubbles": len(shot.bubble_plan or []),
                  "attempt": shot.attempts + 1})
        refs = self.build_shot_references(project, shot)
        # 记录用参考图：即使当前工作流只把画风图送进模型，角色参考图仍记录在案，
        # 方便自检/回溯知道"这个角色有过参考图"。
        _char_refs, _ = self.build_shot_reference_plan(project, shot)
        shot.reference_paths = list(dict.fromkeys(list(refs) + list(_char_refs)))
        shot.attempts += 1
        # 画幅：显式传入优先，否则按镜头类型取条漫分档画幅（旧模式即 IMAGE_SIZE）
        if getattr(config, "WEBTOON_MODE", False):
            self.log(f"分镜 {shot.index} 条漫画幅 {panel[0]}×{panel[1]}"
                     f"（{shot.shot_type}）")
        # 本格姿态：用户手传优先；否则按动作/机位自动选骨架。
        # Qwen T2I 模式只保留用户上传的姿态（data/poses），第 6 步自动骨架不参与，
        # 否则单人镜头会被 ControlNet 固定成同一张站姿（25 格没有挂骨架，构图更贴文本）。
        pose_path = shot.pose_image if shot.pose_image and Path(shot.pose_image).exists() else ""
        if not pose_path:
            _sk = str(getattr(shot, "pose_skeleton", "") or "")
            if _sk and Path(_sk).exists():
                pose_path = _sk
        if self.storyboard_t2i and pose_path and not self.is_user_pose(pose_path):
            # Qwen T2I 与 25 格保持同一条路径：自动姿态骨架一律不参与。
            # 旧逻辑只在“特写/近景”或“没开角色参考图”时跳过，导致全景镜头
            # 仍挂 pose_library 的自动骨架；实测那套骨架没有肩→髋躯干段，
            # 且竖骨架被按横格中心裁切，会生成“无头长衫 / 头陷进胸口 /
            # 手臂过长”。25 格不挂骨架时同一提示词、同一 seed 是正常的，
            # 所以这里以“角色外观参考 + 文字动作描述”为准。
            # 用户在第 7 步自己上传的姿态（data/poses）仍然保留，不受影响。
            self.log(f"分镜 {shot.index} Qwen T2I：忽略自动姿态骨架（与 25 格同路径），"
                     f"只保留用户上传姿态")
            pose_path = ""
        elif (pose_path and not self.is_user_pose(pose_path)
              and str(getattr(config, "AUTO_POSE_SHOTS", "off") or "off").lower() == "off"):
            # 非 Qwen 后端（Z-Image 等）也遵守 AUTO_POSE_SHOTS=off：
            # 旧工程里残留的自动骨架不参与，避免继续带坏比例。
            self.log(f"分镜 {shot.index} 自动姿态已关闭：忽略自动骨架，只保留用户上传姿态")
            pose_path = ""
        # 单人全景/远景强制单骨架（仅 Z-Image 且自动姿态未关闭时）：
        # 用一张骨架物理上限制只画一个人。AUTO_POSE_SHOTS=off 时不走这里，
        # 否则关闭开关也拦不住内置坏骨架。
        if (not self.storyboard_t2i and not pose_path
                and str(getattr(config, "AUTO_POSE_SHOTS", "off") or "off").lower() != "off"
                and len([n for n in (shot.characters or []) if n]) == 1
                and str(getattr(shot, "shot_type", "")) in ("全景", "远景")):
            try:
                _p, _reason, _dyn = poseplan.choose_pose(shot)
                if _p and Path(_p).exists():
                    pose_path = _p
                    self.log(f"分镜 {shot.index} 单人强制姿态：{_reason}（{Path(_p).name}）")
            except Exception:
                pass
        if not self.storyboard_t2i and not pose_path and (auto_pose is not False):
            mode = None
            if poseplan.should_auto_pose(shot, mode):
                pose_path, reason, _dynamic = poseplan.choose_pose(shot)
                if pose_path:
                    self.log(f"分镜 {shot.index} 自动姿态：{reason}（{Path(pose_path).name}）")
        # 单角色镜头优先用角色固定 seed，减少跨格脸型漂移；
        # 多角色/无角色仍用本格 seed。
        seed_for = shot.seed
        try:
            _names = [n for n in (shot.characters or []) if n]
            if len(_names) == 1:
                _c = project.find_character(_names[0])
                if _c is not None and getattr(_c, "seed", 0):
                    seed_for = int(_c.seed)
        except Exception:  # noqa: BLE001
            pass
        _single = len([n for n in (shot.characters or []) if n]) == 1
        # T2I 不做人脸裁切（裁切会切掉场景/道具）；Edit 模式才需要防双头。
        def _extra_kw(client) -> dict:
            return ({"single_subject": _single}
                    if isinstance(client, QwenComicClient) and not self.storyboard_t2i
                    else {})
        if pose_path:
            # 姿态工作流：Qwen 现在支持 ControlNet + 多参考图，姿态锁动作、
            # 参考图锁身份；Z-Image 仍只送骨架（参考 latent 会冲突）。
            pose_client = (self._bound_image_client(project, "pose")
                           or self._pose_client())
            pose_refs = refs if isinstance(pose_client, QwenComicClient) else []
        else:
            image_client = self._bound_image_client(project, "shot") or self.image

        def _generate_once(seed_value: int) -> GenResult:
            if pose_path:
                return pose_client.generate(
                    prompt, reference_paths=pose_refs,
                    size=panel, seed=seed_value,
                    pose_image=pose_path,
                    color_transfer_strength=self.color_transfer_for(project),
                    negative_prompt=prompts.negative_prompt(project, shot, kind="shot"),
                    **_extra_kw(pose_client))
            return image_client.generate(
                prompt, reference_paths=refs if self.refs_supported else [],
                size=panel,
                seed=seed_value,
                color_transfer_strength=self.color_transfer_for(project),
                negative_prompt=prompts.negative_prompt(project, shot, kind="shot"),
                **_extra_kw(image_client))

        res: GenResult = _generate_once(seed_for)
        expected_faces = len([n for n in (shot.characters or []) if n])
        if (isinstance(self.image, QwenComicClient) and expected_faces >= 4):
            self.log(f"分镜 {shot.index} 是 {expected_faces} 人群像：Qwen 实测 4 人易多画，"
                     f"建议拆成 3+1 或 2+2")
        if (isinstance(self.image, QwenComicClient) and expected_faces
                and bool(getattr(config, "IMAGE_DUPLICATE_CHECK", True))):
            detected = len(charrefs.detect_faces_in_image(res.image))
            retries = max(0, int(getattr(config, "IMAGE_DUPLICATE_RETRY", 1) or 0))
            if detected > expected_faces and retries:
                self.log(f"分镜 {shot.index} 检测到 {detected} 张脸，预期 {expected_faces} 人，换 seed 重跑")
                best, best_faces = res, detected
                for attempt in range(retries):
                    res2 = _generate_once(int(res.seed or seed_for) + attempt + 1)
                    f2 = len(charrefs.detect_faces_in_image(res2.image))
                    if f2 < best_faces:
                        best, best_faces = res2, f2
                    if f2 <= expected_faces:
                        break
                res = best
                self.log(f"分镜 {shot.index} 多脸返修：{detected} → {best_faces} 张脸")
        if res.seed is not None:
            shot.seed = res.seed
        out_dir = Path(config.SHOT_DIR) / project.id
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_path = out_dir / f"{config.SHOT_FILENAME_TEMPLATE.format(index=shot.index)}_raw.png"
        res.image.save(raw_path, format="PNG")
        shot.raw_image_path = str(raw_path)
        if res.degraded_note:
            self.log(f"分镜 {shot.index} 因显存不足降级生成：{res.degraded_note}")
        return shot


def _append_design_note(design: str, note: str) -> str:
    """把"辨识度补丁"追加到人设的【标志性特征】段（没有该段就新建）。"""
    design = (design or "").rstrip()
    note = (note or "").strip("；;。 ")
    if not note:
        return design
    marker = "【标志性特征】"
    idx = design.find(marker)
    if idx < 0:
        return f"{design}\n{marker}{note}" if design else f"{marker}{note}"
    nxt = design.find("【", idx + len(marker))
    end = nxt if nxt >= 0 else len(design)
    section = design[idx:end].rstrip()
    if not section.endswith(("。", "；", "、", "，", "\n")):
        section += "；"
    return f"{design[:idx]}{section}{note}\n{design[end:]}"


def _safe_name(name: str, fallback: str = "unnamed") -> str:
    """把角色名/造型名转成安全的目录名。"""
    import re as _re
    name = (name or "").strip() or fallback
    name = _re.sub(r'[\\/:*?"<>|\s]+', "_", name)
    return name[:60] or fallback












