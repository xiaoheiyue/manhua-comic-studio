# -*- coding: utf-8 -*-
"""图像生成客户端 —— 本地 ComfyUI（默认 http://127.0.0.1:8188）。

和原来的「OpenAI 兼容图像接口」最大的区别：ComfyUI 不是"发一段提示词、回一张图"，
而是"提交一整张节点图、排队执行、按文件名取图"。所以这里的流程是：

    1. 取工作流（config.COMFY_WORKFLOW_NAME，默认 image_z_image_turbo_int8）
       并拍平成 /prompt 需要的 API 格式（见 comfyworkflow.py）；
    2. 把本次的提示词 / 画幅 / 种子 / 文件名前缀写进节点图；
    3. POST /prompt 排队，拿 prompt_id；
    4. 轮询 GET /history/{prompt_id} 等执行结束；
    5. GET /view 把成图字节取回来，交给上层保存。

服务端不需要 API 密钥（除非你套了反向代理），也不再需要"加载图像模型"——
ComfyUI 在执行工作流时按需加载 UNET/CLIP/VAE。界面上的「检查工作流」按钮
对应 load_image_model()：它会核对节点类型与模型文件是否齐全。

另外保留了：
  * 显存不足自动降级重试（只压画幅，不改提示词）
  * 结果缓存（相同提示词/画幅/种子/工作流直接复用）
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import requests
from PIL import Image

import config
from . import comfyworkflow as comfy
from .logger import get_logger
from .textutil import truncate

log = get_logger(__name__)


class ImageGenError(RuntimeError):
    """图像生成失败。"""


class OutOfMemoryError(ImageGenError):
    """显存不足。"""


class AuthError(ImageGenError):
    """鉴权失败（ComfyUI 套了反代时的 HTTP 401/403）。"""


class ModelNotLoadedError(ImageGenError):
    """工作流引用的模型文件不存在 / 节点类型不可用。"""


@dataclass
class GenResult:
    """一次生成的结果。"""

    image: Image.Image
    seed: int | None = None
    size: tuple[int, int] = config.IMAGE_SIZE
    reference_paths: list[str] = field(default_factory=list)
    workflow: str = ""                 # 用的哪个 ComfyUI 工作流
    prompt_id: str = ""                # ComfyUI 的任务 id（排查问题时有用）
    protocol: str = "comfyui"          # 兼容旧字段
    degraded_note: str = ""            # 显存不足时的降级说明
    raw: dict[str, Any] = field(default_factory=dict)
    from_cache: bool = False


# =============================================================================
# 图片编解码工具
# =============================================================================

def encode_image_b64(img: Image.Image, fmt: str = "PNG") -> str:
    """PIL 图像 -> base64 字符串（不带 data URI 前缀）。"""
    buf = io.BytesIO()
    save_fmt = "PNG" if fmt.upper() == "PNG" else "JPEG"
    if save_fmt == "JPEG" and img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    img.save(buf, format=save_fmt, **({"quality": config.OUTPUT_JPEG_QUALITY}
                                      if save_fmt == "JPEG" else {}))
    return base64.b64encode(buf.getvalue()).decode("ascii")


def prepare_reference(img: Image.Image, max_edge: int | None = None) -> Image.Image:
    """参考图预处理：统一 RGB、限制最长边（留给将来接图生图工作流用）。"""
    limit = int(max_edge or config.REFERENCE_MAX_EDGE)
    if img.mode != "RGB":
        img = img.convert("RGB")
    if limit > 0 and max(img.size) > limit:
        ratio = limit / max(img.size)
        img = img.resize((max(1, int(img.width * ratio)),
                          max(1, int(img.height * ratio))), Image.LANCZOS)
    return img


def file_to_b64(path: str | Path, max_edge: int | None = None) -> str:
    """图片文件 -> base64（可先等比缩放）。"""
    img = prepare_reference(Image.open(path), max_edge)
    return encode_image_b64(img)


def file_to_data_uri(path: str | Path, max_edge: int | None = None) -> str:
    """图片文件 -> data URI。"""
    return "data:image/png;base64," + file_to_b64(path, max_edge)


def decode_any_image(payload: str) -> Image.Image:
    """把 base64 / data URI / 本地路径 / http 地址统一解成 PIL 图像。"""
    payload = (payload or "").strip()
    if not payload:
        raise ImageGenError("空图片数据")

    if payload.startswith("data:image"):
        payload = payload.split(",", 1)[-1]

    if len(payload) < 500 and Path(payload).exists():
        return Image.open(payload).convert("RGB")

    if payload.startswith("http://") or payload.startswith("https://"):
        r = requests.get(payload, timeout=180)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGB")

    try:
        raw = base64.b64decode(payload, validate=False)
        return Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise ImageGenError(f"无法解析图片数据：{truncate(str(exc), 120)}") from exc


# =============================================================================
# 客户端
# =============================================================================

class ImageClient:
    """ComfyUI 图像生成客户端。"""

    def __init__(self,
                 host: str | None = None,
                 workflow: str | None = None,
                 api_key: str | None = None,
                 timeout: int | None = None,
                 model: str | None = None,
                 workflow_file: str | None = None):
        host = (host or config.COMFY_API_HOST).rstrip("/")
        self.host = host
        self._explicit_workflow = bool((workflow or "").strip()) or bool(workflow_file)
        self.workflow_name = (workflow or config.COMFY_WORKFLOW_NAME).strip()
        self.workflow_file = str(workflow_file or "").strip()
        self._explicit_key = api_key
        self.timeout = timeout or config.IMAGE_TIMEOUT
        self.model = model                      # 兼容旧签名，ComfyUI 下不使用
        self.client_id = uuid.uuid4().hex
        self.session = requests.Session()
        self._ref_notice_done = False
        # 参考图上传缓存：本地路径 -> (mtime, ComfyUI 里的文件名)
        self._uploaded: dict[str, tuple[float, str]] = {}

    # ------------------------------------------------------------------
    @property
    def key(self) -> str:
        return self._explicit_key if self._explicit_key is not None else config.api_key()

    def _headers(self, json_body: bool = True) -> dict[str, str]:
        h: dict[str, str] = {}
        if json_body:
            h["Content-Type"] = "application/json"
        key = self.key
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    def _url(self, path: str) -> str:
        return f"{self.host}{path}"

    # ==================================================================
    # 工作流
    # ==================================================================
    def template(self, refresh: bool = False,
                 for_reference: bool | None = None) -> comfy.WorkflowTemplate:
        """
        取当前工作流（带缓存）。

        for_reference=True 时（本次要送参考图），auto 预设会切到
        style_color 工作流（生成后按画风参考图做 ColorTransfer，只迁色）。
        """
        if refresh:
            comfy.clear_cache()
        name = self.workflow_name
        if not self._explicit_workflow:
            name = config.active_workflow_name(
                has_reference=bool(for_reference))
        return comfy.load_template(
            host=self.host,
            name=name,
            session=self.session,
            explicit_file=self.workflow_file or config.COMFY_WORKFLOW_FILE or None,
            local_dir=config.COMFY_WORKFLOW_DIR,
            cache_seconds=config.COMFY_WORKFLOW_CACHE_SECONDS,
            allow_inline=config.COMFY_INLINE_FALLBACK,
            overrides=getattr(config, "COMFY_NODE_OVERRIDES", None),
        )

    @property
    def supports_reference_images(self) -> bool:
        """当前（含 auto 预设下的画风参考）工作流是否有图像输入。"""
        try:
            if not self._explicit_workflow:
                return self.template(for_reference=True).supports_reference_images
            return self.template().supports_reference_images
        except Exception as exc:  # noqa: BLE001
            log.debug("无法判断工作流是否支持参考图：%s", exc)
            return False

    @property
    def color_matched(self) -> bool:
        """当前风格工作流是否会在生成后做色彩迁移（决定气泡是否交给程序画）。"""
        try:
            return bool(self.template(for_reference=True).color_matched)
        except Exception as exc:  # noqa: BLE001
            log.debug("无法判断工作流是否做色彩迁移：%s", exc)
            return False

    # ==================================================================
    # 主入口
    # ==================================================================
    def generate(self,
                 prompt: str,
                 reference_paths: Sequence[str] | None = None,
                 size: tuple[int, int] | None = None,
                 steps: int | None = None,
                 guidance: float | None = None,
                 seed: int | None = None,
                 negative_prompt: str | None = None,
                 max_reference_images: int | None = None,
                 reference_max_edge: int | None = None,
                 lora_name: str | None = None,
                 lora_strength: float | None = None,
                 color_transfer_strength: float | None = None,
                 pose_image: str | Path | None = None,
                 use_cache: bool | None = None) -> GenResult:
        """生成一张图。参考图数量超出上限时按顺序截断。"""
        size = config.normalize_image_size(size or config.IMAGE_SIZE)

        seed_in = config.IMAGE_SEED if seed is None else seed
        actual_seed = seed_in if (seed_in is not None and seed_in >= 0) \
            else random.randint(0, 2 ** 31 - 1)
        neg = config.IMAGE_NEGATIVE_PROMPT if negative_prompt is None \
            else negative_prompt
        if not bool(getattr(config, "COMFY_NEGATIVE_ENABLED", True)):
            neg = ""

        max_refs = max_reference_images or config.MAX_REFERENCE_IMAGES
        refs = [str(p) for p in (reference_paths or [])
                if p and Path(p).exists()][:max_refs]

        # 有参考图 → auto 预设会选 style_color 工作流；没有则用基础工作流
        template = self.template(for_reference=bool(refs))
        if template.color_matched and not refs:
            raise ImageGenError(
                "当前工作流是色彩迁移（style_color），需要一张画风参考图；"
                "请先选择 handraw 绘制风格，或把 COMFY_WORKFLOW_PRESET 改成 auto/base。")
        if refs and not template.supports_reference_images:
            if not self._ref_notice_done:
                log.info("工作流「%s」没有图像输入，参考图不会送进模型："
                         "角色一致性由提示词里的外貌设定维持", template.name)
                self._ref_notice_done = True
            refs = []
            template = self.template(for_reference=False)
        # 工作流只接了几路图像输入，就只送几张（style_color 只接 1 张画风参考）
        if refs and template.supports_reference_images:
            slots = int(template.reference_slot_count or 0)
            if slots > 0:
                refs = refs[:slots]

        text = self._prepare_prompt(prompt, template)
        lora = str(lora_name or "").strip() if getattr(config, "IMAGE_LORA_ENABLED", True) else ""
        lora_w = float(lora_strength if lora_strength is not None
                       else getattr(config, "IMAGE_LORA_STRENGTH", 0.8))
        if lora and not template.models.get("lora_name"):
            log.info("启用角色 LoRA：%s（强度 %.2f）", lora, lora_w)

        cache_on = config.CACHE_ENABLED if use_cache is None else use_cache
        color_strength = (float(color_transfer_strength)
                          if color_transfer_strength is not None
                          else float(getattr(config, "COMFY_COLOR_TRANSFER_STRENGTH", 0.55)))
        pose_path = str(pose_image or "").strip()
        cache_key = self._cache_key(text, refs, size, actual_seed, template,
                                    lora=lora, lora_strength=lora_w,
                                    color_strength=color_strength,
                                    negative=neg + f"|pose={pose_path}") \
            if cache_on else None
        if cache_key:
            hit = self._cache_get(cache_key)
            if hit is not None:
                log.info("命中缓存，直接复用上次生成结果")
                return GenResult(image=hit, seed=actual_seed, size=size,
                                 reference_paths=refs, workflow=template.name,
                                 from_cache=True)

        plan: list[dict[str, Any]] = [{"size": size, "label": ""}]
        if tuple(size) == tuple(config.IMAGE_SIZE):
            plan.extend({"size": tuple(p["size"]), "label": p.get("label", "")}
                        for p in config.OOM_RETRY_PLAN if p.get("size"))
        elif getattr(config, "WEBTOON_MODE", False):
            # 条漫画幅不等于 IMAGE_SIZE，且拼条要求所有格子同宽，
            # 所以降级时只压低高度，宽度保持不变。
            plan.extend(self._webtoon_degrade_plan(size))

        last_err: Exception | None = None
        step_arg = steps if steps is not None else config.IMAGE_STEPS
        cfg_arg = guidance if guidance is not None else float(
            getattr(config, "COMFY_CFG", 1.4) or 1.4)
        for i, step_cfg in enumerate(plan):
            try:
                result = self._generate_once(
                    template=template, prompt=text, negative=neg,
                    size=tuple(step_cfg["size"]), seed=actual_seed, refs=refs,
                    steps=step_arg, cfg=cfg_arg,
                    lora_name=lora, lora_strength=lora_w,
                    color_transfer_strength=color_strength,
                    pose_image=pose_path or None)
                if i > 0:
                    result.degraded_note = step_cfg.get("label") or "已降级"
                    log.warning("显存不足，已自动降级重试并成功：%s",
                                result.degraded_note)
                if cache_key:
                    self._cache_put(cache_key, result.image)
                return result
            except OutOfMemoryError as exc:
                last_err = exc
                if i + 1 < len(plan):
                    log.warning("显存不足，准备降级：%s", plan[i + 1].get("label"))
                    continue
                raise ImageGenError(
                    "显存不足：已尝试全部降级方案仍失败。请进一步降低画幅"
                    "（条漫模式调小 config.py 的 WEBTOON_PANEL_SIZE_MAP 高度，"
                    "非条漫模式调小 IMAGE_SIZE），或换更小的量化模型。"
                ) from exc
        raise ImageGenError(f"图像生成失败：{truncate(str(last_err), 300)}")

    # ------------------------------------------------------------------
    @staticmethod
    def _webtoon_degrade_plan(size: tuple[int, int]) -> list[dict[str, Any]]:
        """条漫模式的显存降级方案：只压高度，绝不改宽度（否则拼条会错位）。"""
        w, h = int(size[0]), int(size[1])
        m = max(1, int(config.IMAGE_DIMENSION_MULTIPLE))
        plan: list[dict[str, Any]] = []
        for factor, label in ((0.80, "高度降到 80%"),
                              (0.66, "高度降到 66%"),
                              (0.55, "高度降到 55%")):
            nh = max(m, int(h * factor / m) * m)
            if nh >= h:
                nh = max(m, h - m)
            if nh < m:
                continue
            plan.append({"size": config.normalize_image_size((w, nh)),
                         "label": label})
        return plan

    @staticmethod
    def _prepare_prompt(prompt: str, template: comfy.WorkflowTemplate) -> str:
        """纯文生图工作流下，去掉提示词里"第 N 张参考图是某某"的说明。"""
        text = (prompt or "").strip()
        if not text or template.supports_reference_images \
                or not getattr(config, "COMFY_STRIP_REFERENCE_HINTS", True):
            return text
        import re
        text = re.sub(r"参考图使用说明：[^。]*。", "", text)
        return text.strip()

    # ------------------------------------------------------------------
    def _generate_once(self, template: comfy.WorkflowTemplate, prompt: str,
                       negative: str, size: tuple[int, int], seed: int,
                       refs: list[str], steps: int | None = None,
                       cfg: float | None = None,
                       lora_name: str = "", lora_strength: float = 0.8,
                       color_transfer_strength: float | None = None,
                       pose_image: str | None = None) -> GenResult:
        # 参考图必须先上传到 ComfyUI 的 input 目录，工作流里的 LoadImage 才有图可读
        ref_names: list[str] = []
        if refs:
            square_refs = bool(template.requires_square_references)
            for path in refs:
                try:
                    ref_names.append(self._upload_reference(path, square=square_refs))
                except Exception as exc:  # noqa: BLE001
                    raise ImageGenError(
                        f"参考图上传失败（{Path(path).name}）：{truncate(str(exc), 180)}") from exc
        pose_name = None
        if pose_image and Path(pose_image).exists():
            try:
                pose_name = self._upload_reference(pose_image, square=False)
            except Exception as exc:  # noqa: BLE001
                raise ImageGenError(
                    f"姿态图上传失败（{Path(pose_image).name}）：{truncate(str(exc), 180)}") from exc
        graph = template.build(
            prompt,
            negative_prompt=negative,
            size=size,
            seed=seed,
            steps=steps,
            cfg=cfg if cfg is not None else float(
                getattr(config, "COMFY_CFG", 1.4) or 1.4),
            filename_prefix=self._filename_prefix(),
            lora_name=lora_name or None,
            lora_strength=lora_strength,
            reference_names=ref_names,
            pose_name=pose_name,
            color_transfer_strength=float(
                color_transfer_strength
                if color_transfer_strength is not None
                else getattr(config, "COMFY_COLOR_TRANSFER_STRENGTH", 0.55)),
            allow_sampler_override=bool(
                getattr(config, "COMFY_APPLY_SAMPLER_SETTINGS", False)),
        )
        prompt_id = self._submit(graph)
        log.info("本次使用工作流：%s（%d 个节点，参考图 %d 张）",
                 template.name, len(graph), len(refs))
        entry = self._wait(prompt_id)
        images = self._collect_images(entry, template)
        image = self._download(images[0])

        seed_out = None
        try:
            ins = graph[str(template.nodes["sampler"])]["inputs"]
            seed_out = ins.get("seed", ins.get("noise_seed"))
            seed_out = int(seed_out) if seed_out is not None else seed
        except Exception:  # noqa: BLE001
            seed_out = seed
        return GenResult(image=image, seed=seed_out, size=(image.width, image.height),
                         reference_paths=refs, workflow=template.name,
                         prompt_id=prompt_id,
                         raw={"prompt_id": prompt_id, "images": images})

    def _filename_prefix(self) -> str:
        prefix = (getattr(config, "COMFY_FILENAME_PREFIX", "manhua") or "manhua").strip()
        return prefix.strip("/")

    # ==================================================================
    # /prompt 提交
    # ==================================================================
    # ------------------------------------------------------------------
    def _prepare_upload_image(self, path: str | Path,
                              square: bool = False) -> Path:
        """
        把参考图处理成可上传的 PNG。

        square=True：统一成固定正方形（Z-Image Omni 多图条件要求同尺寸）；
        square=False：只等比缩到最长边，不填灰边——ColorTransfer 会统计整张参考图的
        色彩分布，填灰边会把灰色算进去，导致成图发灰/出现灰框。
        """
        src = Path(path)
        size = int(getattr(config, "COMFY_REFERENCE_SIZE", 768) or 768)
        cache_dir = Path(config.DATA_DIR) / "cache" / "ref_upload"
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = f"{src.resolve()}:{src.stat().st_mtime:.3f}:{size}:{int(square)}"
        out = cache_dir / f"{uuid.uuid5(uuid.NAMESPACE_URL, key).hex[:16]}.png"
        if out.exists():
            return out
        try:
            from PIL import ImageOps
            with Image.open(src) as im:
                img = im.convert("RGB")
                if square:
                    pad = tuple(getattr(config, "COMFY_REFERENCE_PAD", (128, 128, 128))[:3])
                    img = ImageOps.pad(img, (size, size), method=Image.LANCZOS,
                                       color=pad, centering=(0.5, 0.5))
                else:
                    img.thumbnail((size, size), Image.LANCZOS)
                img.save(out, format="PNG")
            return out
        except Exception as exc:  # noqa: BLE001
            log.warning("参考图预处理失败（改用原图上传）：%s", exc)
            return src

    def _upload_reference(self, path: str | Path, square: bool = False) -> str:
        """
        把参考图上传到 ComfyUI 的 input 目录，返回 LoadImage 可用的文件名。

        同一文件（mtime 未变）只上传一次。返回形如 "manhua_refs/xxx.png"。
        """
        src = self._prepare_upload_image(path, square=square)
        key = str(Path(path).resolve())
        mtime = Path(path).stat().st_mtime
        cached = self._uploaded.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
        suffix = src.suffix.lower() or ".png"
        if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
            suffix = ".png"
        safe = re.sub(r"[^0-9a-zA-Z_-]+", "_", Path(path).stem)[:40] or "ref"
        name = f"{safe}_{uuid.uuid5(uuid.NAMESPACE_URL, key).hex[:10]}{suffix}"
        mime = {".png": "image/png", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(suffix, "image/png")
        subfolder = str(getattr(config, "COMFY_UPLOAD_SUBFOLDER", "") or "").strip()
        form = {"overwrite": "true", "type": "input"}
        if subfolder:
            form["subfolder"] = subfolder
        try:
            with src.open("rb") as fh:
                resp = self.session.post(
                    self._url("/upload/image"),
                    files={"image": (name, fh.read(), mime)},
                    data=form,
                    headers=self._headers(json_body=False),
                    timeout=60,
                )
        except requests.RequestException as exc:
            raise ImageGenError(f"无法连接 ComfyUI 上传参考图：{exc}") from exc
        if resp.status_code >= 400:
            raise ImageGenError(
                f"ComfyUI 拒绝上传参考图（HTTP {resp.status_code}）："
                f"{truncate(resp.text, 160)}")
        try:
            info = resp.json()
        except ValueError:
            info = {}
        out_name = str(info.get("name") or name)
        out_sub = str(info.get("subfolder") or subfolder).strip("/")
        full = f"{out_sub}/{out_name}" if out_sub else out_name
        self._uploaded[key] = (mtime, full)
        log.info("参考图已上传：%s → %s", src.name, full)
        return full

    def _submit(self, graph: dict[str, Any]) -> str:
        payload = {"prompt": graph, "client_id": self.client_id}
        resp = self.session.post(self._url("/prompt"), headers=self._headers(True),
                                 json=payload, timeout=60)
        if resp.status_code in (401, 403):
            raise AuthError(
                f"ComfyUI 拒绝访问（HTTP {resp.status_code}）：{truncate(resp.text, 200)}\n"
                "  如果给 ComfyUI 套了鉴权，请在 config.py 的 COMFY_API_KEY 里填密钥。")
        if resp.status_code >= 400:
            raise ModelNotLoadedError(self._format_prompt_error(resp))
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ImageGenError(
                f"ComfyUI 返回无法解析的内容：{truncate(resp.text, 200)}") from exc

        if data.get("node_errors"):
            raise ModelNotLoadedError(self._format_node_errors(data["node_errors"]))
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            if data.get("error"):
                err = data["error"]
                msg = err.get("message") if isinstance(err, dict) else str(err)
                raise ImageGenError(f"ComfyUI 拒绝了工作流：{truncate(str(msg), 300)}")
            raise ImageGenError(
                f"ComfyUI 没有返回 prompt_id：{truncate(json.dumps(data, ensure_ascii=False), 240)}")
        log.info("已提交 ComfyUI 任务 %s", prompt_id)
        return str(prompt_id)

    @staticmethod
    def _format_prompt_error(resp: requests.Response) -> str:
        """把 /prompt 的 400 报文翻成人话（缺模型时最常见的报错就出自这里）。"""
        text = truncate(resp.text, 800)
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            return f"ComfyUI 拒绝执行工作流（HTTP {resp.status_code}）：{text}"
        if isinstance(data.get("node_errors"), dict) and data["node_errors"]:
            return ImageClient._format_node_errors(data["node_errors"])
        err = data.get("error") or {}
        if isinstance(err, dict):
            msg = err.get("message") or err.get("type") or ""
            detail = err.get("details") or ""
            return (f"ComfyUI 拒绝执行工作流（HTTP {resp.status_code}）："
                    f"{truncate(str(msg), 200)} {truncate(str(detail), 300)}")
        return f"ComfyUI 拒绝执行工作流（HTTP {resp.status_code}）：{text}"

    @staticmethod
    def _format_node_errors(node_errors: dict[str, Any]) -> str:
        lines: list[str] = []
        for node_id, info in (node_errors or {}).items():
            ctype = (info or {}).get("class_type", "")
            errors = (info or {}).get("errors") or []
            for err in errors:
                msg = err.get("message", "")
                detail = err.get("details", "")
                lines.append(f"节点 {node_id}（{ctype}）：{msg} {detail}".strip())
        joined = "；".join(lines[:6]) or "未知节点错误"
        hint = ""
        low = joined.lower()
        if "not in list" in low or "value not in list" in low:
            hint = "\n  提示：工作流里写的模型文件在 ComfyUI 的 models 目录下找不到，" \
                   "请先下载对应模型，或把工作流里的模型名改成你本地已有的。"
        return f"ComfyUI 工作流校验失败：{joined}{hint}"

    # ==================================================================
    # /history 轮询
    # ==================================================================
    def _wait(self, prompt_id: str) -> dict[str, Any]:
        """轮询 /history 直到执行结束；超时会中断 ComfyUI 的任务。"""
        deadline = time.time() + self.timeout
        next_queue_log = time.time() + config.COMFY_QUEUE_LOG_INTERVAL
        started = time.time()

        while time.time() < deadline:
            entry = self._history(prompt_id)
            if entry:
                return self._check_entry(entry, prompt_id)

            now = time.time()
            if now >= next_queue_log:
                next_queue_log = now + config.COMFY_QUEUE_LOG_INTERVAL
                self._log_queue_position(prompt_id, now - started)
            time.sleep(config.COMFY_POLL_INTERVAL)

        if getattr(config, "COMFY_INTERRUPT_ON_TIMEOUT", True):
            self.interrupt()
            note = "已尝试中断 ComfyUI 上的任务。"
        else:
            note = ""
        raise ImageGenError(
            f"等待 ComfyUI 出图超时（{self.timeout}s）。{note}"
            "常见原因：显存不足导致卡死、模型文件过大首次加载很慢、"
            "或工作流里有节点弹出了需要人工确认的输入。")

    def _history(self, prompt_id: str) -> dict[str, Any] | None:
        try:
            resp = self.session.get(self._url(f"/history/{prompt_id}"),
                                    headers=self._headers(False),
                                    timeout=min(30, self.timeout))
        except requests.RequestException as exc:
            log.debug("读取 /history 失败：%s", exc)
            return None
        if resp.status_code >= 400:
            return None
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            return None
        if isinstance(data, dict):
            entry = data.get(prompt_id)
            if isinstance(entry, dict):
                return entry
        return None

    def _check_entry(self, entry: dict[str, Any], prompt_id: str) -> dict[str, Any]:
        status = entry.get("status") or {}
        status_str = str(status.get("status_str") or "").lower()
        messages = status.get("messages") or []

        if status_str == "error":
            detail = self._error_message(messages)
            if self._is_oom(detail):
                raise OutOfMemoryError(f"ComfyUI 执行时显存不足：{truncate(detail, 300)}")
            raise ImageGenError(f"ComfyUI 执行失败：{truncate(detail, 500)}")
        for item in (messages if isinstance(messages, list) else []):
            if not (isinstance(item, (list, tuple)) and len(item) == 2):
                continue
            name, payload = item
            if name == "execution_interrupted":
                raise ImageGenError(f"ComfyUI 任务 {prompt_id} 被中断（可能有人在界面上点了取消）")
            if name == "execution_error":
                detail = str((payload or {}).get("exception_message") or payload) \
                    if isinstance(payload, dict) else str(payload)
                if self._is_oom(detail):
                    raise OutOfMemoryError(f"ComfyUI 执行时显存不足：{truncate(detail, 300)}")
                raise ImageGenError(f"ComfyUI 执行失败：{truncate(detail, 500)}")
        return entry

    @staticmethod
    def _error_message(messages: Any) -> str:
        if isinstance(messages, list):
            for item in messages:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    name, payload = item
                    if name in ("execution_error", "execution_interrupted"):
                        if isinstance(payload, dict):
                            node = payload.get("node_type") or payload.get("node_id") or ""
                            msg = payload.get("exception_message") or payload
                            return f"{node} {msg}".strip()
                        return str(payload)
        return str(messages)

    def _log_queue_position(self, prompt_id: str, elapsed: float) -> None:
        try:
            resp = self.session.get(self._url("/queue"), headers=self._headers(False),
                                    timeout=10)
            data = resp.json()
        except Exception:  # noqa: BLE001
            log.info("等待 ComfyUI 出图…（已等 %.0f 秒）", elapsed)
            return
        running = [item[1] for item in (data.get("queue_running") or [])
                   if isinstance(item, (list, tuple)) and len(item) > 1]
        pending = [item[1] for item in (data.get("queue_pending") or [])
                   if isinstance(item, (list, tuple)) and len(item) > 1]
        if prompt_id in running:
            log.info("ComfyUI 正在执行任务（已等 %.0f 秒）", elapsed)
        else:
            ahead = pending.index(prompt_id) if prompt_id in pending else len(pending)
            log.info("任务在排队，前面还有 %d 个（已等 %.0f 秒）", ahead, elapsed)

    def _collect_images(self, entry: dict[str, Any],
                        template: comfy.WorkflowTemplate) -> list[dict[str, Any]]:
        outputs = entry.get("outputs") or {}
        save_id = str(template.nodes.get("save") or "")
        node_out = outputs.get(save_id) or {}
        images = node_out.get("images") if isinstance(node_out, dict) else None
        if not images:
            for node_id, node in outputs.items():
                if isinstance(node, dict) and node.get("images"):
                    images = node["images"]
                    if node_id != save_id:
                        log.debug("保存节点 %s 没有输出，退回节点 %s 的图片", save_id, node_id)
                    break
        if not images:
            raise ImageGenError(
                "ComfyUI 执行成功但没有返回图片：请确认工作流里有 SaveImage 节点，"
                "且它连在 VAE 解码之后")
        return [img for img in images if isinstance(img, dict) and img.get("filename")]

    def _download(self, image_info: dict[str, Any]) -> Image.Image:
        params = {
            "filename": image_info.get("filename", ""),
            "subfolder": image_info.get("subfolder", ""),
            "type": image_info.get("type", "output"),
        }
        try:
            resp = self.session.get(self._url("/view"), params=params,
                                    headers=self._headers(False),
                                    timeout=max(60, min(300, self.timeout)))
        except requests.RequestException as exc:
            raise ImageGenError(
                f"从 ComfyUI 取图失败：{truncate(str(exc), 200)}") from exc
        if resp.status_code >= 400:
            raise ImageGenError(
                f"从 ComfyUI 取图失败（HTTP {resp.status_code}）：{truncate(resp.text, 200)}")
        try:
            return Image.open(io.BytesIO(resp.content)).convert("RGB")
        except Exception as exc:  # noqa: BLE001
            raise ImageGenError(
                f"ComfyUI 返回的不是有效图片：{truncate(str(exc), 200)}") from exc

    # ==================================================================
    # 显存不足判定
    # ==================================================================
    @staticmethod
    def _is_oom(text: str) -> bool:
        low = (text or "").lower()
        return any(k.lower() in low for k in config.OOM_KEYWORDS)

    # ==================================================================
    # 缓存
    # ==================================================================
    def _cache_key(self, prompt: str, refs: list[str], size: tuple[int, int],
                   seed: int, template: comfy.WorkflowTemplate,
                   lora: str = "", lora_strength: float = 0.0,
                   color_strength: float | None = None,
                   negative: str = "") -> str:
        h = hashlib.sha256()
        h.update(prompt.encode("utf-8"))
        h.update(f"|neg={negative}".encode("utf-8"))
        ins = template.sampler_inputs
        h.update(f"|{size}|{seed}|{template.name}|{template.source}"
                 f"|{ins.get('steps')}|{ins.get('cfg')}"
                 f"|{ins.get('sampler_name')}|{ins.get('scheduler')}"
                 f"|lora={lora}|{lora_strength}"
                 f"|color={color_strength if color_strength is not None else getattr(config, 'COMFY_COLOR_TRANSFER_STRENGTH', 0)}"
                 .encode("utf-8"))
        for name, value in sorted(template.models.items()):
            h.update(f"|{name}={value}".encode("utf-8"))
        for p in refs:
            try:
                h.update(Path(p).read_bytes())
            except OSError:
                h.update(str(p).encode("utf-8"))
        return h.hexdigest()[:32]

    def _cache_path(self, key: str) -> Path:
        return Path(config.CACHE_DIR) / f"{key}.png"

    def _cache_get(self, key: str) -> Image.Image | None:
        p = self._cache_path(key)
        if not p.exists():
            return None
        try:
            return Image.open(p).convert("RGB")
        except Exception:  # noqa: BLE001
            return None

    def _cache_put(self, key: str, img: Image.Image) -> None:
        try:
            self._cache_path(key).parent.mkdir(parents=True, exist_ok=True)
            img.save(self._cache_path(key), format="PNG")
        except Exception as exc:  # noqa: BLE001
            log.debug("写缓存失败：%s", exc)

    # ==================================================================
    # 服务状态 / 就绪检查
    # ==================================================================
    def health(self) -> tuple[bool, str]:
        """检查 ComfyUI 是否在线，并报告设备与工作流情况。"""
        info: dict[str, Any] = {}
        net_err = ""
        for path in ("/system_stats", "/queue"):
            try:
                r = self.session.get(self._url(path), headers=self._headers(False),
                                     timeout=config.STARTUP_PING_TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                net_err = truncate(str(exc), 160)
                continue
            if r.status_code in (401, 403):
                return False, (f"ComfyUI 拒绝访问（HTTP {r.status_code}）："
                               f"{config.api_key_status()}")
            if r.status_code >= 400:
                continue
            try:
                info = r.json() or {}
            except Exception:  # noqa: BLE001
                info = {}
            net_err = ""
            break

        if not info and net_err:
            return False, (f"无法连接 ComfyUI {self.host}：{net_err}\n"
                           "  请先启动 ComfyUI（或确认端口不是 8188）")
        if not info:
            return False, f"无法连接 ComfyUI {self.host}"

        parts: list[str] = ["在线"]
        version = ((info.get("system") or {}).get("comfyui_version")
                   if isinstance(info.get("system"), dict) else None)
        if version:
            parts.append(f"ComfyUI {version}")
        device = self._device_summary(info)
        if device:
            parts.append(device)

        try:
            tpl = self.template()
        except Exception as exc:  # noqa: BLE001
            return True, ("；".join(parts) + f"，但工作流 {self.workflow_name} 不可用："
                          + truncate(str(exc), 200))
        ins = tpl.sampler_inputs
        parts.append(f"工作流「{tpl.name}」就绪（{len(tpl.graph)} 节点，"
                     f"{ins.get('steps')} 步，cfg={ins.get('cfg')}，来源 {tpl.source}）")
        if not tpl.supports_reference_images:
            parts.append("纯文生图（不接收参考图）")
        return True, "；".join(parts)

    @staticmethod
    def _device_summary(info: dict[str, Any]) -> str:
        devices = info.get("devices") or []
        if not isinstance(devices, list) or not devices:
            return ""
        dev = devices[0] if isinstance(devices[0], dict) else {}
        name = str(dev.get("name") or "").replace("cuda:0", "").strip(" :")
        try:
            total = float(dev.get("vram_total") or 0) / (1024 ** 3)
            free = float(dev.get("vram_free") or 0) / (1024 ** 3)
        except (TypeError, ValueError):
            total = free = 0.0
        if total:
            return f"{name}（显存 {free:.1f}/{total:.1f}GB 空闲）"
        return name

    def wait_until_ready(self, attempts: int | None = None,
                         interval: float | None = None,
                         on_tick=None) -> tuple[bool, str]:
        """启动时等待 ComfyUI 就绪（界面显示"正在等待服务就绪"）。"""
        attempts = attempts or config.STARTUP_PING_ATTEMPTS
        interval = config.STARTUP_PING_INTERVAL if interval is None else interval
        msg = ""
        for i in range(1, attempts + 1):
            if on_tick:
                try:
                    on_tick(i, attempts)
                except Exception:  # noqa: BLE001
                    pass
            ok, msg = self.health()
            if ok:
                return True, msg
            if i < attempts:
                time.sleep(interval)
        return False, msg

    def image_model_status(self) -> dict[str, Any]:
        """读取服务与工作流详情（给界面展示用）。"""
        out: dict[str, Any] = {
            "loaded": False, "workflow": self.workflow_name, "host": self.host,
            "engine": "ComfyUI",
        }
        try:
            r = self.session.get(self._url("/system_stats"),
                                 headers=self._headers(False),
                                 timeout=config.STARTUP_PING_TIMEOUT)
            if r.status_code < 400:
                info = r.json() or {}
                system = info.get("system") or {}
                out["version"] = system.get("comfyui_version")
                out["device"] = self._device_summary(info)
        except Exception as exc:  # noqa: BLE001
            out["error"] = truncate(str(exc), 200)

        try:
            tpl = self.template(for_reference=True)
            ins = tpl.sampler_inputs
            out.update({
                "loaded": True,
                "workflow": tpl.name,
                "source": tpl.source,
                "nodes": len(tpl.graph),
                "steps": ins.get("steps"),
                "cfg": ins.get("cfg"),
                "sampler_name": ins.get("sampler_name"),
                "scheduler": ins.get("scheduler"),
                "models": tpl.models,
                "supports_reference_images": tpl.supports_reference_images,
                "reference_slots": tpl.reference_slot_count,
                "color_matched": tpl.color_matched,
                "supports_negative_prompt": bool(tpl.nodes.get("negative")),
                "negative_enabled": bool(getattr(config, "COMFY_NEGATIVE_ENABLED", True)),
                "negative_effective": bool(getattr(config, "COMFY_NEGATIVE_ENABLED", True))
                                      and float(ins.get("cfg") or 0) > 1.0,
                "node_ids": {k: v for k, v in tpl.nodes.items() if v},
            })
        except Exception as exc:  # noqa: BLE001
            out["error"] = truncate(str(exc), 300)
        return out

    def load_image_model(self, model_path: str | None = None,
                         on_progress=None) -> tuple[bool, str]:
        """
        ComfyUI 不需要手动加载模型（执行工作流时按需加载）。

        这里做的是「就绪检查」：清掉缓存、重新读取工作流，并核对节点类型
        与模型文件在 ComfyUI 里是否都存在，把问题提前暴露出来。
        model_path 若传入的是工作流名，可以临时切换（不写回 config）。
        """
        def tick(msg: str, value: float) -> None:
            if on_progress:
                try:
                    on_progress({"message": msg, "percent": value * 100})
                except Exception:  # noqa: BLE001
                    pass

        if model_path and str(model_path).strip() and \
                str(model_path).strip() != self.workflow_name:
            name = str(model_path).strip()
            if name.lower().endswith(".json"):
                name = name[:-5]
            if "/" not in name and "\\" not in name:
                log.info("就绪检查使用工作流：%s", name)
                self.workflow_name = name

        tick("正在检查 ComfyUI 服务…", 0.1)
        ok, msg = self.health()
        if not ok:
            return False, msg

        tick("正在读取工作流…", 0.35)
        try:
            tpl = self.template(refresh=True, for_reference=True)
        except Exception as exc:  # noqa: BLE001
            return False, truncate(str(exc), 400)

        tick("正在核对节点与模型文件…", 0.6)
        missing: list[str] = []
        model_widgets = ("unet_name", "ckpt_name", "clip_name", "vae_name", "lora_name")
        by_class: dict[str, list[dict[str, Any]]] = {}
        for node in tpl.graph.values():
            by_class.setdefault(str(node.get("class_type")), []).append(node)

        for class_type in sorted(by_class):
            try:
                r = self.session.get(self._url(f"/object_info/{class_type}"),
                                     headers=self._headers(False),
                                     timeout=10)
            except Exception as exc:  # noqa: BLE001
                missing.append(f"{class_type}（查询失败：{truncate(str(exc), 80)}）")
                continue
            if r.status_code >= 400:
                missing.append(f"{class_type}（ComfyUI 里没有这个节点类型，"
                               "可能是缺少自定义节点）")
                continue
            try:
                info = (r.json() or {}).get(class_type) or {}
            except Exception:  # noqa: BLE001
                continue
            required = (info.get("input") or {}).get("required") or {}
            for name in model_widgets:
                spec = required.get(name)
                choices = spec[0] if isinstance(spec, list) and spec else None
                if not isinstance(choices, list) or not choices:
                    continue
                for node in by_class[class_type]:
                    used = node["inputs"].get(name)
                    if isinstance(used, str) and used and used not in choices:
                        missing.append(f"{name}={used}")

        tick("就绪检查完成", 1.0)
        if missing:
            detail = "；".join(sorted(set(missing))[:8])
            return False, (f"服务在线，但工作流「{tpl.name}」缺东西：{detail}\n"
                           "  请把模型放到 ComfyUI 的 models 对应目录后重试。")
        return True, (f"服务在线，工作流「{tpl.name}」就绪（{len(tpl.graph)} 个节点，"
                      f"来源 {tpl.source}）")

    def list_models(self) -> list[str]:
        """列出 ComfyUI 里可用的工作流名称（给界面下拉用）。"""
        return comfy.list_workflows(self.session, self.host,
                                    timeout=config.STARTUP_PING_TIMEOUT)

    def interrupt(self) -> None:
        """中断 ComfyUI 当前正在执行的任务。"""
        try:
            self.session.post(self._url("/interrupt"), headers=self._headers(False),
                              timeout=10)
        except Exception as exc:  # noqa: BLE001
            log.debug("中断 ComfyUI 任务失败：%s", exc)
