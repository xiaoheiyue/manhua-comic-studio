"""Qwen Image 2.1 Edit：本地图生图，保留参考图角色/服装。"""

from __future__ import annotations

import hashlib
import io
import time
import uuid
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageOps

import config


class QwenEditClient:
    def __init__(self, host: str | None = None) -> None:
        self.host = (host or config.COMFY_API_HOST).rstrip("/")
        self.session = requests.Session()
        self._freed = False

    @staticmethod
    def _as_target(size: Any) -> tuple[int, int] | None:
        """把 ``size`` 归一成 (w,h)；int / None 表示不需要目标画幅。"""
        if isinstance(size, (tuple, list)) and len(size) == 2:
            try:
                w, h = int(size[0]), int(size[1])
                if w > 0 and h > 0:
                    return (w, h)
            except (TypeError, ValueError):
                return None
        return None

    def _pad_ref(self, path: str, target: tuple[int, int]) -> str:
        """把参考图 pad 到目标画幅比例（白底、不裁人物）。

        Qwen TextEncode 的 latent 跟随第 1 张参考图的比例；不 pad 的话，
        请求 1088×1280 也可能出 832×1248，页面合成时再裁就会裁头/裁脚。
        """
        if not bool(getattr(config, "QWEN_REFERENCE_PAD_TO_PANEL", True)):
            return path
        src = Path(path)
        if not src.is_file():
            return path
        tw, th = target
        out_dir = Path(config.BASE_DIR) / "outputs" / "_refpad"
        out_dir.mkdir(parents=True, exist_ok=True)
        # 不同角色都有 主视图.png / 面部特写.png；缓存名必须带路径 hash，
        # 否则第二个角色会命中第一个角色的 pad 图，造成串脸。
        key = hashlib.sha1(str(src.resolve()).encode("utf-8")).hexdigest()[:10]
        out = out_dir / f"{src.stem}_{key}_{tw}x{th}_fit.png"
        try:
            if out.is_file() and out.stat().st_mtime >= src.stat().st_mtime:
                return str(out)
            img = Image.open(src).convert("RGB")
            # 不放大参考图：小图（如 512 面部特写）原样贴到白底画布上，
            # 避免先放大再让模型看模糊边缘；大图等比缩到画布内。
            img.thumbnail((tw, th), Image.LANCZOS)
            canvas = Image.new("RGB", (tw, th), (255, 255, 255))
            canvas.paste(img, ((tw - img.width) // 2, max(0, (th - img.height) // 2)))
            canvas.save(out, format="PNG")
            return str(out)
        except Exception:
            return path

    def _pad_pose(self, path: str, target: tuple[int, int]) -> str:
        """把姿态骨架等比放进目标画幅（黑底、不裁头脚）。

        分镜格可能是 1280×1024 横格，而骨架库多数是竖图。旧实现用
        ``ImageScale(crop="center")`` 直接裁成横格，会把头/脚裁掉；
        ControlNet 按裁剩的躯干生成，结果就是“无头长衫 / 头陷进胸口”。
        这里先等比缩放再补黑边，保证头、肩、髋、脚都在画幅内。
        """
        src = Path(path)
        if not src.is_file():
            return path
        tw, th = target
        out_dir = Path(config.BASE_DIR) / "outputs" / "_posepad"
        out_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(
            f"{src.resolve()}|{src.stat().st_mtime_ns}|{tw}x{th}".encode("utf-8")
        ).hexdigest()[:12]
        out = out_dir / f"{src.stem}_{key}_{tw}x{th}_pose.png"
        try:
            if out.is_file():
                return str(out)
            img = Image.open(src).convert("RGB")
            img.thumbnail((tw, th), Image.LANCZOS)
            # 黑底与骨架背景一致，不会在 ControlNet 里多出一条白边
            canvas = Image.new("RGB", (tw, th), (0, 0, 0))
            canvas.paste(img, ((tw - img.width) // 2, (th - img.height) // 2))
            canvas.save(out, format="PNG")
            return str(out)
        except Exception:
            return path

    def _upload(self, path: str) -> str:
        # 关键：每次上传都用内容 hash + 随机名，不能复用同名文件。
        # 否则 ComfyUI 会按文件名命中上一次的 LoadImage 缓存，
        # 用户换了参考图，出来的还是上一张图。
        data = Path(path).read_bytes()
        tag = hashlib.sha1(data).hexdigest()[:10]
        name = f"qwen_{tag}_{uuid.uuid4().hex[:8]}_{Path(path).stem[:24]}.png"
        with open(path, "rb") as f:
            r = self.session.post(
                f"{self.host}/upload/image",
                files={"image": (name, f, "image/png")},
                data={"overwrite": "false"}, timeout=120)
        r.raise_for_status()
        return r.json()["name"]

    def generate(self, prompt: str, reference: str | list[str], *,
                 negative: str = "", size: int | tuple[int, int] = 1024, seed: int = 0,
                 steps: int = 8, denoise: float = 1.0, timeout: float = 300) -> Image.Image:
        if not reference:
            raise ValueError("Qwen Edit 需要参考图")
        target = self._as_target(size)
        resolution = max(target) if target else int(size)
        # 先卸载 Z-Image，给 Qwen 的 unet + 8B 文本编码器腾出内存；
        # 8GB 机器上两套模型不能同时驻留。
        if not self._freed:
            try:
                self.session.post(f"{self.host}/free",
                                  json={"unload_models": True, "free_memory": True},
                                  timeout=30)
            except Exception:
                pass
            self._freed = True
        refs = [reference] if isinstance(reference, str) else list(reference or [])
        if target:
            refs = [self._pad_ref(p, target) for p in refs]
        names = [self._upload(x) for x in refs if x and Path(x).exists()]
        seed = int(seed or 888001)
        # 每个参考图一个 LoadImage 节点；TextEncodeQwenImage21.images 是
        # COMFY_AUTOGROW_V3，必须用扁平键 images.image_N（嵌套 dict 会被静默丢弃）。
        load_nodes = {str(20 + i): {"class_type": "LoadImage",
                                    "inputs": {"image": name}}
                      for i, name in enumerate(names)}
        if not load_nodes:
            load_nodes["20"] = {"class_type": "LoadImage", "inputs": {"image": ""}}
        enc_inputs: dict[str, Any] = {
            "clip": ["2", 0], "prompt": prompt,
            "negative_prompt": negative or "低质量, 模糊, 文字, 水印",
            "resolution": resolution,
            "vae": ["3", 0],
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
            **({"21": {"class_type": "VAEEncode", "inputs": {
                "pixels": ["20", 0], "vae": ["3", 0]}}} if float(denoise) < 0.999 and names else {}),
            "6": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": int(steps), "cfg": 1.0,
                "sampler_name": "euler", "scheduler": "simple", "denoise": float(denoise),
                "model": ["1", 0], "positive": ["5", 0],
                "negative": ["5", 1], "latent_image": ["5", 2]}},
            "7": {"class_type": "VAEDecode", "inputs": {
                "samples": ["6", 0], "vae": ["3", 0]}},
            "8": {"class_type": "SaveImage", "inputs": {
                "filename_prefix": "qwen_edit_studio", "images": ["7", 0]}},
        }
        if float(denoise) < 0.999 and names:
            graph["6"]["inputs"]["latent_image"] = ["21", 0]
        r = self.session.post(f"{self.host}/prompt",
                              json={"prompt": graph,
                                    "client_id": uuid.uuid4().hex}, timeout=120)
        r.raise_for_status()
        pid = r.json()["prompt_id"]
        started = time.time()
        entry = None
        while time.time() - started < timeout:
            h = self.session.get(f"{self.host}/history/{pid}", timeout=30).json()
            if h:
                entry = h.get(pid) or next(iter(h.values()), None)
                break
            time.sleep(1.5)
        if not entry:
            raise TimeoutError("Qwen Edit 超时")
        outs = (entry.get("outputs") or {})
        for node in outs.values():
            for img in (node.get("images") or []):
                q = self.session.get(f"{self.host}/view", params={
                    "filename": img["filename"],
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output")}, timeout=120)
                q.raise_for_status()
                image = Image.open(io.BytesIO(q.content)).convert("RGB")
                if target:
                    image = ImageOps.fit(image, target, method=Image.LANCZOS,
                                         centering=(0.5, 0.45))
                return image
        raise RuntimeError("Qwen Edit 没有输出图片")

    def _run(self, graph: dict[str, Any], timeout: float = 300) -> Image.Image:
        r = self.session.post(f"{self.host}/prompt",
                              json={"prompt": graph,
                                    "client_id": uuid.uuid4().hex}, timeout=120)
        r.raise_for_status()
        pid = r.json()["prompt_id"]
        started = time.time()
        entry = None
        while time.time() - started < timeout:
            h = self.session.get(f"{self.host}/history/{pid}", timeout=30).json()
            if h:
                entry = h.get(pid) or next(iter(h.values()), None)
                break
            time.sleep(1.5)
        if not entry:
            raise TimeoutError("Qwen Edit 超时")
        for node in (entry.get("outputs") or {}).values():
            for img in (node.get("images") or []):
                q = self.session.get(f"{self.host}/view", params={
                    "filename": img["filename"],
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output")}, timeout=120)
                q.raise_for_status()
                return Image.open(io.BytesIO(q.content)).convert("RGB")
        raise RuntimeError("Qwen Edit 没有输出图片")

    def generate_t2i(self, prompt: str, *, negative: str = "", size: tuple[int, int] = (1024, 1024),
                     seed: int = 0, steps: int = 8, timeout: float = 300) -> Image.Image:
        if not self._freed:
            try:
                self.session.post(f"{self.host}/free",
                                  json={"unload_models": True, "free_memory": True}, timeout=30)
            except Exception:
                pass
            self._freed = True
        seed = int(seed or 888001)
        graph = {
            "1": {"class_type": "UNETLoader", "inputs": {
                "unet_name": "qwen_image_2.1_turbo_int8_convrot.safetensors",
                "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {
                "clip_name": "qwen3vl_8b_w4a8.safetensors",
                "type": "qwen_image", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {
                "vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
            "4": {"class_type": "TextEncodeQwenImage21", "inputs": {
                "clip": ["2", 0], "prompt": prompt,
                "negative_prompt": negative or "低质量, 模糊, 文字, 水印",
                "resolution": 1024, "images": {}, "vae": ["3", 0]}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {
                "width": int(size[0]), "height": int(size[1]), "batch_size": 1}},
            "6": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": int(steps), "cfg": 1.0,
                "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                "model": ["1", 0], "positive": ["4", 0],
                "negative": ["4", 1], "latent_image": ["5", 0]}},
            "7": {"class_type": "VAEDecode", "inputs": {
                "samples": ["6", 0], "vae": ["3", 0]}},
            "8": {"class_type": "SaveImage", "inputs": {
                "filename_prefix": "qwen_t2i_studio", "images": ["7", 0]}},
        }
        return self._run(graph, timeout)

    def generate_control(self, prompt: str, reference: str | list[str] | None,
                         pose_image: str, *, negative: str = "",
                         size: tuple[int, int] = (1024, 1024), seed: int = 0,
                         steps: int = 8, strength: float = 0.8,
                         timeout: float = 300) -> Image.Image:
        """Qwen Image Edit + Qwen ControlNet Union（姿态骨架 + 多参考图）。"""
        if not self._freed:
            try:
                self.session.post(f"{self.host}/free",
                                  json={"unload_models": True, "free_memory": True}, timeout=30)
            except Exception:
                pass
            self._freed = True
        seed = int(seed or 888001)
        target = (int(size[0]), int(size[1]))
        pose_name = self._upload(self._pad_pose(pose_image, target))
        if isinstance(reference, str):
            ref_paths = [reference]
        else:
            ref_paths = list(reference or [])
        ref_paths = [self._pad_ref(x, target) for x in ref_paths if x and Path(x).exists()]
        ref_names = [self._upload(x) for x in ref_paths]
        ref_nodes = {str(14 + i): {"class_type": "LoadImage",
                                   "inputs": {"image": name}}
                     for i, name in enumerate(ref_names)}
        enc_inputs: dict[str, Any] = {
            "clip": ["2", 0], "prompt": prompt,
            "negative_prompt": negative or "低质量, 模糊, 文字, 水印",
            "resolution": max(target), "vae": ["3", 0],
        }
        for i in range(len(ref_names)):
            enc_inputs[f"images.image_{i+1}"] = [str(14 + i), 0]
        graph: dict[str, Any] = {
            "1": {"class_type": "UNETLoader", "inputs": {
                "unet_name": "qwen_image_2.1_turbo_int8_convrot.safetensors",
                "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {
                "clip_name": "qwen3vl_8b_w4a8.safetensors",
                "type": "qwen_image", "device": "default"}},
            "3": {"class_type": "VAELoader", "inputs": {
                "vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
            "9": {"class_type": "LoadImage", "inputs": {"image": pose_name}},
            "10": {"class_type": "ImageScale", "inputs": {
                "upscale_method": "lanczos", "width": int(size[0]),
                "height": int(size[1]), "crop": "disabled", "image": ["9", 0]}},
            "11": {"class_type": "ModelPatchLoader", "inputs": {
                "name": "qwen_image_2.1_fun_controlnet_union_int8_convrot.safetensors"}},
            "12": {"class_type": "QwenImageDiffsynthControlnet", "inputs": {
                "model": ["1", 0], "model_patch": ["11", 0], "vae": ["3", 0],
                "image": ["10", 0], "strength": float(strength)}},
            "5": {"class_type": "TextEncodeQwenImage21", "inputs": enc_inputs},
            "6": {"class_type": "KSampler", "inputs": {
                "seed": seed, "steps": int(steps), "cfg": 1.0,
                "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                "model": ["12", 0], "positive": ["5", 0],
                "negative": ["5", 1], "latent_image": ["5", 2]}},
            "7": {"class_type": "VAEDecode", "inputs": {
                "samples": ["6", 0], "vae": ["3", 0]}},
            "8": {"class_type": "SaveImage", "inputs": {
                "filename_prefix": "qwen_control_studio", "images": ["7", 0]}},
            **ref_nodes,
        }
        if not ref_names:
            graph["13"] = {"class_type": "EmptyLatentImage", "inputs": {
                "width": int(size[0]), "height": int(size[1]), "batch_size": 1}}
            graph["6"]["inputs"]["latent_image"] = ["13", 0]
        image = self._run(graph, timeout)
        return ImageOps.fit(image, target, method=Image.LANCZOS,
                            centering=(0.5, 0.45))

    def available(self) -> bool:
        try:
            r = self.session.get(f"{self.host}/system_stats", timeout=5)
            return r.ok
        except Exception:
            return False
