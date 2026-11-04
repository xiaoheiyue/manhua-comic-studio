# -*- coding: utf-8 -*-
"""ComfyUI 工作流的读取、格式转换与参数注入。

ComfyUI 有两套工作流格式，别混：

  · 界面格式（UI 格式）
      ComfyUI 里 Ctrl+S 存下来的那份，形如 {"nodes": [...], "links": [...],
      "definitions": {"subgraphs": [...]}}。前端靠它渲染画布，**不能**直接提交给
      /prompt 接口。新版 ComfyUI 的官方模板普遍用「子图（subgraph）」组织，
      节点 id 长这样：57:3（57 = 子图实例，3 = 子图内部节点）。

  · API 格式
      POST /prompt 需要的节点图，形如
      {"57:3": {"class_type": "KSampler", "inputs": {...}}}。

本模块干三件事：

  1. 把界面格式拍平成 API 格式（递归展开子图、处理 Reroute / PrimitiveNode /
     Bypass），拍平后的节点 id 与 ComfyUI「导出（API）」的结果一致；
  2. 在节点图里定位「正向提示词 / 反向提示词 / 画幅 / 采样器 / 保存」五个位置，
     不靠写死节点 id，换成你自己搭的工作流也能跑；
  3. 按本次请求把提示词、画幅、种子、文件名前缀写进节点图，返回一份可直接
     提交的副本（原图不动，线程安全）。

只依赖标准库，方便离线单测（selftest.py 会拿真实工作流跑一遍转换）。
"""

from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote

import config

from .logger import get_logger

log = get_logger(__name__)


class WorkflowError(RuntimeError):
    """工作流无法读取 / 转换 / 定位节点。"""


# =============================================================================
# 常量
# =============================================================================

# 只用于注释、不参与执行的节点类型
_NOTE_TYPES = {"Note", "MarkdownNote", "Note Plus (mtb)", "Label (rgthree)"}

# 旁路（只做透传，不产生自己逻辑）的节点
_PASSTHROUGH_TYPES = {"Reroute", "Reroute (rgthree)", "PrimitiveNode"}

# 前端专用、API 格式里不应该出现的 widget
_IGNORED_WIDGETS = {
    "control_after_generate", "control_before_generate",
    "control_filter_list", "$$canvas-image", "upload",
}

# 「生成之后随机」这类前端控件的取值，旧版前端把它混在 widgets_values 里
_CONTROL_VALUES = {"fixed", "increment", "decrement", "randomize"}

# 保存节点类型（用于取回成图）
_SAVE_TYPES = {"SaveImage", "Image Save", "SaveImageWebsocket",
               "SaveImageWithMetadata", "Save Animated WEBP"}

# 会把 conditioning 抹成零的节点：其上游的文本对采样没有任何影响
_ZERO_OUT_TYPES = {"ConditioningZeroOut", "ConditioningZeroOutSDXL"}

# 有图像输入的节点类型（出现即认为工作流支持参考图）
_IMAGE_LOAD_TYPES = {"LoadImage", "LoadImageMask", "LoadImageOutput",
                     "VHS_LoadImagePath", "Image Load"}


# =============================================================================
# 格式判定与拍平
# =============================================================================

def is_api_graph(obj: Any) -> bool:
    """判断是不是 /prompt 需要的 API 格式节点图。"""
    if not isinstance(obj, dict) or not obj:
        return False
    for value in obj.values():
        if not isinstance(value, dict):
            return False
        if "class_type" not in value or "inputs" not in value:
            return False
    return True


def _norm_links(raw: Any) -> dict[int, dict[str, int]]:
    """把界面格式的 links 统一成 {id: {origin_id, origin_slot, target_id, target_slot}}。"""
    out: dict[int, dict[str, int]] = {}
    if isinstance(raw, dict):                       # 少数前端存成 dict
        raw = list(raw.values())
    for item in raw or []:
        if isinstance(item, dict):
            try:
                out[int(item["id"])] = {
                    "origin_id": int(item["origin_id"]),
                    "origin_slot": int(item.get("origin_slot", 0)),
                    "target_id": int(item["target_id"]),
                    "target_slot": int(item.get("target_slot", 0)),
                }
            except (KeyError, TypeError, ValueError):
                continue
        elif isinstance(item, (list, tuple)) and len(item) >= 5:
            # 顶层 links 是数组：[id, origin_id, origin_slot, target_id, target_slot, type]
            try:
                out[int(item[0])] = {
                    "origin_id": int(item[1]),
                    "origin_slot": int(item[2]),
                    "target_id": int(item[3]),
                    "target_slot": int(item[4]),
                }
            except (TypeError, ValueError):
                continue
    return out


def _widget_values(node: dict) -> dict[str, Any]:
    """取出一个节点的 widget 取值（新版前端有 widgets_values_named 就直接用）。"""
    named = node.get("widgets_values_named")
    if isinstance(named, dict):
        return {k: v for k, v in named.items() if k not in _IGNORED_WIDGETS}

    raw = node.get("widgets_values")
    if not isinstance(raw, (list, tuple)):
        return {}
    names = [str((inp.get("widget") or {}).get("name") or inp.get("name"))
             for inp in (node.get("inputs") or [])
             if isinstance(inp.get("widget"), dict)]
    if not names:
        return {}

    values = list(raw)
    # 旧版前端把「生成后随机」控件混在 seed 后面，API 格式里没有它
    if (len(values) == len(names) + 1 and names and "seed" in names[0]
            and str(values[1]) in _CONTROL_VALUES):
        values.pop(1)
    return {name: values[i] for i, name in enumerate(names) if i < len(values)}


def _node_title(node: dict) -> str:
    title = str(node.get("title") or "").strip()
    if title:
        return title
    meta = node.get("properties") or {}
    return str(meta.get("Node name for S&R") or "")


def flatten_ui_workflow(ui: dict) -> dict[str, dict]:
    """
    把界面格式（含子图）拍平成 API 格式。

    返回 {"<节点id>": {"class_type": ..., "inputs": {...}, "_meta": {...}}}，
    节点 id 与 ComfyUI「导出（API）」完全一致（子图内部节点是 "实例id:内部id"）。
    """
    subgraphs = {}
    for sg in ((ui.get("definitions") or {}).get("subgraphs") or []):
        if isinstance(sg, dict) and sg.get("id"):
            subgraphs[str(sg["id"])] = sg

    out: dict[str, dict] = {}
    aliases: dict[tuple[str, int], Any] = {}       # (节点键, 输出槽) -> 真实来源

    def emit(nodes: list[dict], links: Any, prefix: str,
             bindings: dict[tuple[int, int], Any], depth: int = 0) -> None:
        """展开一个作用域（顶层或某个子图内部）。"""
        if depth > 8:
            raise WorkflowError("子图嵌套层数超过 8 层，疑似循环引用")
        link_map = _norm_links(links)
        by_id = {int(n["id"]): n for n in nodes if isinstance(n, dict) and "id" in n}

        for node in nodes:
            if not isinstance(node, dict) or "id" not in node:
                continue
            node_id = int(node["id"])
            ntype = str(node.get("type") or "")
            key = f"{prefix}{node_id}"
            mode = int(node.get("mode") or 0)

            if ntype in _NOTE_TYPES:
                continue
            if mode == 4:                              # 静音
                log.debug("工作流节点 %s（%s）被静音，跳过", key, ntype)
                continue

            # ---- 子图实例：递归展开 ----
            if ntype in subgraphs:
                sg = subgraphs[ntype]
                sg_links = _norm_links(sg.get("links") or [])
                inner_prefix = f"{key}:"
                inner_bind = _subgraph_bindings(sg, node, link_map, sg_links,
                                                prefix, bindings)
                emit(list(sg.get("nodes") or []), sg.get("links") or [],
                     inner_prefix, inner_bind, depth + 1)
                out_node_id = int((sg.get("outputNode") or {}).get("id", -20))
                for slot, outp in enumerate(sg.get("outputs") or []):
                    for lid in (outp.get("linkIds") or []):
                        link = sg_links.get(int(lid))
                        if not link or link["target_id"] != out_node_id:
                            continue
                        aliases[(key, slot)] = [f"{inner_prefix}{link['origin_id']}",
                                                link["origin_slot"]]
                        break
                continue

            # ---- 透传节点：记个别名，不产生真实节点 ----
            if ntype in _PASSTHROUGH_TYPES:
                resolved = _resolve_node_input(node, bindings, link_map, prefix)
                origin = resolved.get(0)
                if ntype == "PrimitiveNode":
                    values = list(node.get("widgets_values") or [])
                    origin = values[0] if values else None
                if origin is not None:
                    aliases[(key, 0)] = origin
                continue

            # ---- 普通节点 ----
            inputs: dict[str, Any] = _widget_values(node)
            for slot, inp in enumerate(node.get("inputs") or []):
                if not isinstance(inp, dict):
                    continue
                name = str(inp.get("name") or f"input{slot}")
                if (node_id, slot) in bindings:
                    inputs[name] = bindings[(node_id, slot)]
                    continue
                lid = inp.get("link")
                if lid is None:
                    continue
                link = link_map.get(int(lid))
                if link is None:
                    continue
                src = by_id.get(link["origin_id"])
                if src is not None and str(src.get("type")) == "PrimitiveNode":
                    # 控件由 Primitive 节点提供：直接取字面值
                    vals = list(src.get("widgets_values") or [])
                    if vals:
                        inputs[name] = vals[0]
                    continue
                inputs[name] = [f"{prefix}{link['origin_id']}", link["origin_slot"]]

            if mode == 2:                              # 旁路：只做透传，不执行
                out_types = [o.get("type") for o in (node.get("outputs") or [])
                             if isinstance(o, dict)]
                for slot, otype in enumerate(out_types):
                    match = next((n for n in (node.get("inputs") or [])
                                  if isinstance(n, dict) and n.get("type") == otype
                                  and str(n.get("name")) in inputs), None)
                    if match is None:
                        continue
                    aliases[(key, slot)] = inputs.get(str(match.get("name")))
                log.debug("工作流节点 %s（%s）处于旁路状态，已按透传处理", key, ntype)
                continue

            out[key] = {
                "class_type": ntype,
                "inputs": inputs,
                "_meta": {"title": _node_title(node) or ntype},
            }

    def _subgraph_bindings(sg: dict, instance: dict,
                           outer_links: dict[int, dict], inner_links: dict[int, dict],
                           prefix: str,
                           outer_bindings: dict[tuple[int, int], Any]) -> dict[tuple[int, int], Any]:
        """把「子图实例的入口」映射成「子图内部某个节点输入」的取值。"""
        bind: dict[tuple[int, int], Any] = {}
        inst_inputs = [i for i in (instance.get("inputs") or []) if isinstance(i, dict)]
        inst_named = instance.get("widgets_values_named") \
            if isinstance(instance.get("widgets_values_named"), dict) else {}
        inst_values = list(instance.get("widgets_values") or [])
        inst_id = int(instance["id"])

        for idx, sg_in in enumerate(sg.get("inputs") or []):
            name = str(sg_in.get("name") or "")
            inst_idx = next((i for i, item in enumerate(inst_inputs)
                             if item.get("name") == name),
                            idx if idx < len(inst_inputs) else -1)
            inst_input = inst_inputs[inst_idx] if inst_idx >= 0 else None

            value: Any = None
            # 外层作用域已经把这一格的值算好了（如「子图里再套子图」）
            if inst_idx >= 0 and (inst_id, inst_idx) in outer_bindings:
                value = outer_bindings[(inst_id, inst_idx)]
            else:
                lid = inst_input.get("link") if inst_input else None
                if lid is not None and int(lid) in outer_links:
                    link = outer_links[int(lid)]
                    if link["origin_id"] < 0:
                        log.debug("子图 %s 的入口 %s 来自外层子图入口，已跳过", instance["id"], name)
                        continue
                    value = [f"{prefix}{link['origin_id']}", link["origin_slot"]]
                elif name in inst_named:
                    value = inst_named[name]
                elif idx < len(inst_values):
                    value = inst_values[idx]
                else:
                    continue

            for inner_lid in (sg_in.get("linkIds") or []):
                inner = inner_links.get(int(inner_lid))
                if inner:
                    bind[(inner["target_id"], inner["target_slot"])] = value
        return bind

    def _resolve_node_input(node: dict, bindings: dict[tuple[int, int], Any],
                            link_map: dict[int, dict], prefix: str) -> dict:
        """透传节点取它第一个已连线的输入来源。"""
        node_id = int(node["id"])
        for slot, inp in enumerate(node.get("inputs") or []):
            if not isinstance(inp, dict):
                continue
            if (node_id, slot) in bindings:
                return {0: bindings[(node_id, slot)]}
            lid = inp.get("link")
            if lid is None:
                continue
            link = link_map.get(int(lid))
            if link:
                return {0: [f"{prefix}{link['origin_id']}", link["origin_slot"]]}
        return {}

    emit(list(ui.get("nodes") or []), ui.get("links") or [], "", {})

    # ---- 收尾：把别名（子图输出 / Reroute / 旁路）替换成真实来源 ----
    seen: set[tuple[str, int]] = set()

    def resolve(ref: Any, depth: int = 0) -> Any:
        while (isinstance(ref, list) and len(ref) == 2 and isinstance(ref[0], str)
               and isinstance(ref[1], int)):
            key = (ref[0], ref[1])
            if key not in aliases:
                return ref
            if key in seen or depth > 20:
                raise WorkflowError(f"工作流连线出现环：{ref[0]}[{ref[1]}]")
            seen.add(key)
            ref = aliases[key]
            depth += 1
        return ref

    for key, node in out.items():
        for name, value in list(node["inputs"].items()):
            resolved = resolve(value)
            if isinstance(resolved, list) and len(resolved) == 2 \
                    and isinstance(resolved[0], str):
                if resolved[0] not in out:
                    title = out[key]["_meta"].get("title") or key
                    raise WorkflowError(
                        f"节点「{title}」的输入 {name} 指向不存在的节点 "
                        f"{resolved[0]}（可能该节点被静音/旁路，或工作流不完整）")
            node["inputs"][name] = resolved
        node["inputs"] = {k: v for k, v in node["inputs"].items()
                          if k not in _IGNORED_WIDGETS}
    return out


def to_api_graph(obj: Any) -> dict[str, dict]:
    """把任意来源的工作流 JSON 转成 API 格式节点图。"""
    if isinstance(obj, dict):
        for wrap in ("prompt", "workflow", "graph"):
            if wrap in obj and is_api_graph(obj[wrap]):
                return copy.deepcopy(obj[wrap])
    if is_api_graph(obj):
        return copy.deepcopy(obj)
    if isinstance(obj, dict) and isinstance(obj.get("nodes"), list):
        graph = flatten_ui_workflow(obj)
        if not graph:
            raise WorkflowError("工作流里没有可执行的节点（是否全部被静音？）")
        return graph
    raise WorkflowError("无法识别的工作流格式：既不是 API 格式，也不是界面格式")


# =============================================================================
# 节点定位
# =============================================================================

def _link_target(graph: dict[str, dict], value: Any) -> tuple[str, int] | None:
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) \
            and isinstance(value[1], int) and value[0] in graph:
        return value[0], value[1]
    return None


def _find_sampler(graph: dict[str, dict]) -> str:
    """找采样器：优先「同时有 positive/negative/latent_image」的 KSampler。"""
    def score(key: str) -> tuple[int, int]:
        ins = graph[key]["inputs"]
        has = sum(k in ins for k in ("positive", "negative", "latent_image"))
        has_seed = int("seed" in ins or "noise_seed" in ins)
        return (has, has_seed)

    candidates = [k for k, v in graph.items()
                  if score(k)[0] >= 2 and score(k)[1]]
    if not candidates:
        # 高级采样器（SamplerCustomAdvanced）：从 VAEDecode 的 samples 反推
        for key, node in graph.items():
            if str(node.get("class_type")) in ("VAEDecode", "VAEDecodeTiled"):
                src = _link_target(graph, node["inputs"].get("samples"))
                if src and ("seed" in graph[src[0]]["inputs"]
                            or "noise_seed" in graph[src[0]]["inputs"]):
                    candidates.append(src[0])
    if not candidates:
        raise WorkflowError(
            "工作流里找不到采样器节点（需要带 seed/positive/latent_image 输入的节点）")
    return sorted(candidates, key=score)[-1]


def _find_text_node(graph: dict[str, dict], start: tuple[str, int] | None,
                    expect_text: bool = True) -> str | None:
    """从某个 conditioning 连线往上游找文本编码节点，最多走 8 跳。"""
    if start is None:
        return None
    queue: list[tuple[str, int]] = [start]
    visited: set[str] = set()
    for _ in range(64):
        if not queue:
            break
        key, _slot = queue.pop(0)
        if key in visited or key not in graph:
            continue
        visited.add(key)
        node = graph[key]
        ctype = str(node.get("class_type") or "")
        if ctype in _ZERO_OUT_TYPES:
            return None                       # 条件被零化：没有实际的反向文本
        if expect_text:
            for text_key in ("text", "prompt"):
                if isinstance(node["inputs"].get(text_key), str):
                    return key
        for value in node["inputs"].values():
            target = _link_target(graph, value)
            if target:
                queue.append(target)
        if len(visited) > 200:
            break
    return None


def _find_latent(graph: dict[str, dict], sampler: str) -> str:
    """
    从采样器的 latent_image 往上游找画幅节点。

    文生图工作流是带 width/height 的空 Latent 节点；图生图（VAEEncode）节点本身
    没有尺寸，尺寸在它上游的 ImageScale 上，所以找不到时返回第一个节点，
    由 _apply_latent_size 继续往上游找。
    """
    start = _link_target(graph, graph[sampler]["inputs"].get("latent_image"))
    key = start[0] if start else ""
    first = key
    for _ in range(8):
        if not key or key not in graph:
            break
        ins = graph[key]["inputs"]
        if "width" in ins and "height" in ins:
            return key
        nxt = None
        for value in ins.values():
            target = _link_target(graph, value)
            if target:
                nxt = target[0]
                break
        key = nxt or ""
    if first:
        return first
    raise WorkflowError("工作流里找不到画幅节点（带 width/height 输入的节点）")


def _apply_latent_size(graph: dict[str, dict], latent: str,
                       size: tuple[int, int]) -> bool:
    """把画幅写进 latent 节点；若是 VAEEncode（图生图），写到上游的缩放节点。"""
    node = graph.get(str(latent))
    if node is None:
        return False
    if "width" in node["inputs"] and "height" in node["inputs"]:
        node["inputs"]["width"] = int(size[0])
        node["inputs"]["height"] = int(size[1])
        node["inputs"].setdefault("batch_size", 1)
        return True
    queue = [str(latent)]
    seen: set[str] = set()
    for _ in range(12):
        if not queue:
            break
        cur = queue.pop(0)
        if cur in seen or cur not in graph:
            continue
        seen.add(cur)
        ins = graph[cur]["inputs"]
        if "width" in ins and "height" in ins:
            ins["width"] = int(size[0])
            ins["height"] = int(size[1])
            ins.setdefault("batch_size", 1)
            return True
        for value in ins.values():
            target = _link_target(graph, value)
            if target:
                queue.append(target[0])
    return False


def _find_save(graph: dict[str, dict], sampler: str) -> str:
    """找保存节点：优先「下游连到采样器的 SaveImage」。"""
    saves = [k for k, v in graph.items()
             if str(v.get("class_type")) in _SAVE_TYPES
             and "filename_prefix" in v["inputs"]]
    if not saves:
        saves = [k for k, v in graph.items() if str(v.get("class_type")) in _SAVE_TYPES]
    if not saves:
        raise WorkflowError(
            "工作流里找不到 SaveImage 节点：无法取回生成的图片，请在 ComfyUI 里加一个保存节点")

    def reaches_sampler(key: str) -> bool:
        queue = [key]
        seen: set[str] = set()
        for _ in range(80):
            if not queue:
                break
            cur = queue.pop(0)
            if cur in seen or cur not in graph:
                continue
            seen.add(cur)
            if cur == sampler:
                return True
            for value in graph[cur]["inputs"].values():
                target = _link_target(graph, value)
                if target:
                    queue.append(target[0])
        return False

    linked = [k for k in saves if reaches_sampler(k)]
    pool = linked or saves
    return sorted(pool, key=lambda k: (len(str(k)), str(k)))[-1]


def _image_slots(graph: dict[str, dict]) -> list[str]:
    """
    找出工作流里的图像输入节点（LoadImage 一类），按"参考图N"标题排序。

    带"姿势/骨架/pose/keypoint"标题的 LoadImage 不是角色参考图，
    它是 ControlNet 的固定输入，不能计入参考图槽位，也不能被空槽清理掉。
    """
    ids = []
    for k, v in graph.items():
        if str(v.get("class_type")) not in _IMAGE_LOAD_TYPES:
            continue
        title = _node_title(v) or str((v.get("_meta") or {}).get("title") or "")
        if re.search(r"姿势|骨架|pose|keypoint|control", title, re.I):
            continue
        ids.append(str(k))
    if not ids:
        return []

    def sort_key(key: str) -> tuple[int, int, str]:
        title = _node_title(graph[key]) or str(
            (graph[key].get("_meta") or {}).get("title") or "")
        m = re.search(r"(?:参考图|reference|ref)\s*[-_]?\s*(\d+)", title, re.I)
        if m:
            return (0, int(m.group(1)), key)
        try:
            num = int(re.sub(r"\D", "", key) or 0)
        except ValueError:
            num = 0
        return (1, num, key)

    return sorted(ids, key=sort_key)


def _drop_image_slot(graph: dict[str, dict], node_id: str) -> None:
    """把没用到的图像输入节点从下游连线上摘掉并删除（避免空 LoadImage 报错）。"""
    for nid, node in list(graph.items()):
        if nid == node_id:
            continue
        for key, value in list((node.get("inputs") or {}).items()):
            target = _link_target(graph, value)
            if target and target[0] == node_id:
                node["inputs"].pop(key, None)
    graph.pop(node_id, None)


def locate_nodes(graph: dict[str, dict],
                 overrides: dict[str, str] | None = None) -> dict[str, str | None]:
    """定位出提示词/反向/画幅/采样器/保存五个节点 id。"""
    overrides = {k: (v or "").strip() for k, v in (overrides or {}).items()}
    sampler = overrides.get("sampler") or _find_sampler(graph)
    if sampler not in graph:
        raise WorkflowError(f"COMFY_NODE_OVERRIDES 里的采样器节点 {sampler} 不存在")

    latent = overrides.get("latent") or _find_latent(graph, sampler)
    save = overrides.get("save") or _find_save(graph, sampler)

    positive = overrides.get("prompt") or _find_text_node(
        graph, _link_target(graph, graph[sampler]["inputs"].get("positive")))
    if not positive:
        raise WorkflowError(
            "工作流里找不到正向提示词节点（采样器 positive 上游没有带 text 输入的节点）")

    negative = overrides.get("negative")
    if not negative:
        negative = _find_text_node(
            graph, _link_target(graph, graph[sampler]["inputs"].get("negative")))
        if negative == positive:
            negative = None               # 正反向共用同一个节点，没法单独改

    return {"prompt": positive, "negative": negative, "latent": latent,
            "sampler": sampler, "save": save}


# =============================================================================
# 模板
# =============================================================================

def _apply_lora(graph: dict[str, dict], lora_name: str,
                strength: float = 0.8) -> dict[str, dict]:
    """
    在 UNET/Checkpoint 加载器之后插入一个 LoraLoaderModelOnly。

    只改 model 这条链（UNETLoader → LoRA → ModelSamplingAuraFlow → KSampler），
    不动 CLIP/VAE；Z-Image 的角色 LoRA 就是挂在这条链上的。
    """
    name = str(lora_name or "").strip()
    if not name:
        return graph
    loader_id = ""
    for nid, node in graph.items():
        ctype = str(node.get("class_type") or "")
        ins = node.get("inputs") or {}
        if ctype in ("UNETLoader", "CheckpointLoaderSimple",
                     "UNETLoaderGGUF", "UnetLoaderGGUF") \
                and ("unet_name" in ins or "ckpt_name" in ins):
            loader_id = str(nid)
            break
    if not loader_id:
        return graph
    consumers: list[tuple[str, str]] = []
    for nid, node in graph.items():
        for key, val in (node.get("inputs") or {}).items():
            if key == "model" and isinstance(val, (list, tuple)) \
                    and len(val) == 2 and str(val[0]) == loader_id:
                consumers.append((str(nid), key))
    if not consumers:
        return graph
    ids = []
    for nid in graph:
        try:
            ids.append(int(str(nid).split(":")[-1]))
        except ValueError:
            continue
    new_id = str(max(ids + [9000]) + 1)
    graph[new_id] = {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "model": [loader_id, 0],
            "lora_name": name,
            "strength_model": float(strength),
        },
        "_meta": {"title": "角色 LoRA"},
    }
    for nid, key in consumers:
        graph[nid]["inputs"][key] = [new_id, 0]
    return graph


@dataclass
class WorkflowTemplate:
    """一份可直接提交的节点图 + 五个关键节点的位置。"""

    graph: dict[str, dict]
    name: str = ""
    source: str = ""                     # comfyui / file / local / inline
    overrides: dict[str, str] = field(default_factory=dict)
    nodes: dict[str, str | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.nodes:
            self.nodes = locate_nodes(self.graph, self.overrides)

    # ---- 只读描述 ----
    @property
    def sampler_inputs(self) -> dict[str, Any]:
        node = self.graph.get(str(self.nodes.get("sampler"))) or {"inputs": {}}
        return node["inputs"]

    @property
    def supports_reference_images(self) -> bool:
        """工作流是否有图像输入（纯文生图则为 False）。"""
        for node in self.graph.values():
            if str(node.get("class_type")) in _IMAGE_LOAD_TYPES:
                return True
        return False

    @property
    def reference_slot_count(self) -> int:
        """工作流里接了几路图像输入（决定最多能送几张参考图）。"""
        return len(_image_slots(self.graph))

    @property
    def color_matched(self) -> bool:
        """工作流里是否做了 ColorTransfer（只迁色彩，不迁内容）。"""
        return any(str(n.get("class_type")) in ("ColorTransfer", "ImageColorMatch")
                   for n in self.graph.values())

    @property
    def requires_square_references(self) -> bool:
        """是否要求多张参考图同尺寸（Z-Image Omni 条件）。"""
        return any(str(n.get("class_type")) == "TextEncodeZImageOmni"
                   for n in self.graph.values())

    @property
    def models(self) -> dict[str, str]:
        """工作流里用到的模型文件名（只用于展示）。"""
        out: dict[str, str] = {}
        for node in self.graph.values():
            ctype = str(node.get("class_type") or "")
            for name in ("unet_name", "ckpt_name", "clip_name", "vae_name",
                         "lora_name"):
                value = node["inputs"].get(name)
                if isinstance(value, str) and value:
                    out.setdefault(name, value)
        return out

    def describe(self) -> dict[str, Any]:
        ins = self.sampler_inputs
        sampler = self.graph.get(str(self.nodes.get("sampler"))) or {}
        return {
            "name": self.name,
            "source": self.source,
            "nodes": len(self.graph),
            "sampler": str(sampler.get("class_type") or ""),
            "steps": ins.get("steps"),
            "cfg": ins.get("cfg"),
            "sampler_name": ins.get("sampler_name"),
            "scheduler": ins.get("scheduler"),
            "supports_reference_images": self.supports_reference_images,
            "supports_negative_prompt": bool(self.nodes.get("negative")),
            "models": self.models,
            "node_ids": dict(self.nodes),
        }

    # ---- 写入本次参数 ----
    def build(self, prompt: str, *,
              negative_prompt: str | None = None,
              size: tuple[int, int] | None = None,
              seed: int | None = None,
              steps: int | None = None,
              cfg: float | None = None,
              filename_prefix: str | None = None,
              lora_name: str | None = None,
              lora_strength: float = 0.8,
              reference_names: Sequence[str] | None = None,
              pose_name: str | None = None,
              color_transfer_strength: float | None = None,
              allow_sampler_override: bool = False) -> dict[str, dict]:
        """
        按本次请求生成一份新的节点图（不改动 self.graph）。

        reference_names 是已经上传到 ComfyUI input 目录的图片名（按参考图 1..N
        顺序）。工作流里有 LoadImage 节点时会写进 image 输入；多余的空槽位会被
        摘掉，避免 ComfyUI 用默认文件名去加载一张不存在的图。
        allow_sampler_override=False 时只改提示词/画幅/种子/前缀，
        步数与 cfg 保持工作流里的值（蒸馏模型的推荐值）。
        """
        graph = copy.deepcopy(self.graph)

        # 0) 角色 LoRA（可选）：挂在 UNETLoader 与采样器之间
        if lora_name:
            graph = _apply_lora(graph, str(lora_name), float(lora_strength))

        # 0.45) 姿态骨架是固定 ControlNet 输入，和角色参考图分开注入
        if pose_name:
            pose_id = None
            for node_id, node in graph.items():
                if str(node.get("class_type")) not in _IMAGE_LOAD_TYPES:
                    continue
                title = _node_title(node) or str((node.get("_meta") or {}).get("title") or "")
                if re.search(r"姿势|骨架|pose|keypoint|control", title, re.I):
                    node.setdefault("inputs", {})["image"] = str(pose_name)
                    pose_id = node_id
                    break
            # ControlNet 的 ImageScale 要跟本次出图尺寸一致，否则横格会
            # 用 1536² 的正方形骨架去裁，容易出现巨手/断肢。
            if pose_id and size:
                for node in graph.values():
                    if str(node.get("class_type")) != "ImageScale":
                        continue
                    tgt = _link_target(graph, (node.get("inputs") or {}).get("image"))
                    if tgt and str(tgt[0]) == str(pose_id):
                        node.setdefault("inputs", {})["width"] = int(size[0])
                        node.setdefault("inputs", {})["height"] = int(size[1])
                        node["inputs"]["crop"] = "center"
            for node in graph.values():
                if str(node.get("class_type")) in ("QwenImageDiffsynthControlnet", "ZImageFunControlnet"):
                    node.setdefault("inputs", {})["strength"] = float(
                        getattr(config, "CHARACTER_POSE_CONTROL_STRENGTH", 0.85))

        # 0.48) 带角色参考的姿态工作流：ReferenceLatent 会把采样 batch 扩成
        # “参考图数 + 1”，所以骨架和空 latent 都要同步复制到同一 batch。
        if pose_name and reference_names is not None:
            names_n = len([n for n in reference_names if str(n or "").strip()])
            batch = max(1, names_n + 1)
            for node in graph.values():
                if str(node.get("class_type")) == "RepeatImageBatch":
                    node.setdefault("inputs", {})["amount"] = batch
                if str(node.get("class_type")) == "EmptySD3LatentImage":
                    node.setdefault("inputs", {})["batch_size"] = batch

        # 0.5) 参考图（画风 + 角色）：写进 LoadImage 的 image 输入
        if reference_names is not None:
            slots = _image_slots(graph)
            names = [str(n) for n in reference_names if str(n or "").strip()]
            for i, node_id in enumerate(slots):
                if i < len(names):
                    node = graph.get(node_id)
                    if node is not None:
                        node.setdefault("inputs", {})["image"] = names[i]
                else:
                    _drop_image_slot(graph, node_id)

        # 0.6) 色彩迁移强度（ColorTransfer 节点）
        if color_transfer_strength is not None:
            for node in graph.values():
                if str(node.get("class_type")) in ("ColorTransfer", "ImageColorMatch"):
                    node.setdefault("inputs", {})["strength"] = float(
                        color_transfer_strength)

        # 1) 正向提示词
        pos = graph[str(self.nodes["prompt"])]
        if "text" in (pos.get("inputs") or {}):
            pos["inputs"]["text"] = prompt
        elif "prompt" in (pos.get("inputs") or {}):
            pos["inputs"]["prompt"] = prompt
        else:
            pos.setdefault("inputs", {})["text"] = prompt

        # 2) 反向提示词：只有工作流真的接了反向文本、且 cfg>1 时才写。
        #    distil 模型（Z-Image-Turbo）用 cfg=1，反向条件在数学上被忽略，
        #    写了也不会生效，反而误导人。
        neg = self.nodes.get("negative")
        cur_cfg = _as_float(self.sampler_inputs.get("cfg"), 1.0)
        target_cfg = _as_float(cfg, cur_cfg) if allow_sampler_override else cur_cfg
        if neg and negative_prompt and str(neg) in graph and target_cfg > 1.0:
            neg_node = graph[str(neg)]
            if "text" in (neg_node.get("inputs") or {}):
                neg_node["inputs"]["text"] = negative_prompt
            elif "prompt" in (neg_node.get("inputs") or {}):
                neg_node["inputs"]["prompt"] = negative_prompt

        # 3) 画幅
        if size:
            _apply_latent_size(graph, str(self.nodes["latent"]), size)

        # 4) 种子
        if seed is not None and seed >= 0:
            sam = graph[str(self.nodes["sampler"])]
            key = "seed" if "seed" in sam["inputs"] else "noise_seed"
            if key in sam["inputs"] or allow_sampler_override:
                sam["inputs"][key] = int(seed)

        # 5) 步数 / cfg（默认听工作流的）
        if allow_sampler_override:
            sam = graph[str(self.nodes["sampler"])]
            if steps is not None:
                sam["inputs"]["steps"] = int(steps)
            if cfg is not None:
                sam["inputs"]["cfg"] = float(cfg)

        # 6) 保存前缀
        if filename_prefix:
            save = graph[str(self.nodes["save"])]
            save["inputs"]["filename_prefix"] = str(filename_prefix)

        # 顺手清掉前端专用控件，避免 /prompt 报 unknown input
        for node in graph.values():
            for key in _IGNORED_WIDGETS:
                node["inputs"].pop(key, None)
        return graph


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# =============================================================================
# 读取工作流
# =============================================================================

@dataclass
class _CacheEntry:
    at: float
    template: WorkflowTemplate


_CACHE: dict[str, _CacheEntry] = {}


def fetch_from_comfyui(session, host: str, name: str,
                       timeout: float = 10.0) -> dict | None:
    """
    从 ComfyUI 的 userdata 接口按名称取工作流。

    ComfyUI 把界面里的工作流存在 user/default/workflows/<名称>.json，
    对应的 HTTP 路径是 /api/userdata/workflows%2F<名称>.json。
    """
    name = (name or "").strip()
    if not name:
        return None
    if name.lower().endswith(".json"):
        name = name[:-5]
    rel = quote(f"workflows/{name}.json", safe="")
    for path in (f"/api/userdata/{rel}", f"/userdata/{rel}"):
        url = f"{host.rstrip('/')}{path}"
        try:
            resp = session.get(url, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            log.debug("拉取工作流失败 %s：%s", url, exc)
            continue
        if resp.status_code >= 400:
            continue
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            continue
    return None


def list_workflows(session, host: str, timeout: float = 10.0) -> list[str]:
    """列出 ComfyUI 里所有工作流名称（用于界面下拉）。"""
    url = f"{host.rstrip('/')}/api/userdata?dir=workflows&recurse=true&max_depth=10"
    try:
        resp = session.get(url, timeout=timeout)
        if resp.status_code >= 400:
            return []
        items = resp.json()
    except Exception:  # noqa: BLE001
        return []
    names: list[str] = []
    for item in items if isinstance(items, list) else []:
        text = str(item)
        if text.lower().endswith(".json"):
            text = text[:-5]
        if text and text not in names:
            names.append(text)
    return names


def inline_template() -> dict[str, dict]:
    """
    内置兜底模板：Z-Image-Turbo（int8）文生图。

    与 ComfyUI 官方模板 image_z_image_turbo_int8 的节点图等价，
    节点 id 沿用「57:<内部 id>」的拍平写法，方便和导出结果对照。
    """
    return {
        "57:30": {"class_type": "CLIPLoader",
                  "inputs": {"clip_name": "qwen_3_4b_fp8_mixed.safetensors",
                             "type": "lumina2", "device": "default"},
                  "_meta": {"title": "加载CLIP"}},
        "57:29": {"class_type": "VAELoader",
                  "inputs": {"vae_name": "ae.safetensors"},
                  "_meta": {"title": "加载VAE"}},
        "57:28": {"class_type": "UNETLoader",
                  "inputs": {"unet_name": "z_image_turbo_int8_convrot.safetensors",
                             "weight_dtype": "default"},
                  "_meta": {"title": "UNet加载器"}},
        "57:27": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "", "clip": ["57:30", 0]},
                  "_meta": {"title": "CLIP文本编码"}},
        "57:33": {"class_type": "ConditioningZeroOut",
                  "inputs": {"conditioning": ["57:27", 0]},
                  "_meta": {"title": "条件零化"}},
        "57:13": {"class_type": "EmptySD3LatentImage",
                  "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
                  "_meta": {"title": "空Latent图像（SD3）"}},
        "57:11": {"class_type": "ModelSamplingAuraFlow",
                  "inputs": {"shift": 3, "model": ["57:28", 0]},
                  "_meta": {"title": "采样算法（AuraFlow）"}},
        "57:3": {"class_type": "KSampler",
                 "inputs": {"seed": 0, "steps": 8, "cfg": 1,
                            "sampler_name": "res_multistep", "scheduler": "simple",
                            "denoise": 1, "model": ["57:11", 0],
                            "positive": ["57:27", 0], "negative": ["57:33", 0],
                            "latent_image": ["57:13", 0]},
                 "_meta": {"title": "K采样器"}},
        "57:8": {"class_type": "VAEDecode",
                 "inputs": {"samples": ["57:3", 0], "vae": ["57:29", 0]},
                 "_meta": {"title": "VAE解码"}},
        "9": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": "manhua", "images": ["57:8", 0]},
              "_meta": {"title": "保存图像"}},
    }


def load_template(*, host: str, name: str, session,
                  explicit_file: str | Path | None = None,
                  local_dir: str | Path | None = None,
                  cache_seconds: float = 0.0,
                  allow_inline: bool = True,
                  overrides: dict[str, str] | None = None) -> WorkflowTemplate:
    """
    按「显式文件 → ComfyUI 同名工作流 → 本地副本 → 内置模板」的顺序取工作流。

    结果会按 (host, name, explicit_file) 缓存 cache_seconds 秒，
    避免每张图都去拉一次 JSON。
    """
    key = f"{host}|{name}|{explicit_file or ''}|{sorted((overrides or {}).items())}"
    entry = _CACHE.get(key)
    if entry and cache_seconds > 0 and (time.time() - entry.at) < cache_seconds:
        return entry.template

    errors: list[str] = []
    template: WorkflowTemplate | None = None

    def _make(obj: Any, source: str) -> WorkflowTemplate:
        return WorkflowTemplate(graph=to_api_graph(obj), name=name, source=source,
                                overrides=dict(overrides or {}))

    # 1) 显式指定文件
    if explicit_file:
        path = Path(explicit_file)
        if path.is_file():
            try:
                template = _make(json.loads(path.read_text(encoding="utf-8-sig")), "file")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"读取 {path.name} 失败：{exc}")
        else:
            errors.append(f"COMFY_WORKFLOW_FILE 指向的文件不存在：{path}")

    # 2) ComfyUI 里的同名工作流
    if template is None and host:
        obj = fetch_from_comfyui(session, host, name)
        if obj is not None:
            try:
                template = _make(obj, "comfyui")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"ComfyUI 工作流 {name} 转换失败：{exc}")
        else:
            errors.append(f"ComfyUI 里没有名为 {name} 的工作流")

    # 3) 本地副本
    if template is None and local_dir:
        path = Path(local_dir) / f"{name}.json"
        if path.is_file():
            try:
                template = _make(json.loads(path.read_text(encoding="utf-8-sig")), "local")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"读取本地副本 {path.name} 失败：{exc}")

    # 4) 内置模板
    if template is None and allow_inline:
        template = _make(inline_template(), "inline")
        log.warning("未能从 ComfyUI 取到工作流 %s，已使用内置 Z-Image-Turbo 模板兜底（%s）",
                    name, "；".join(errors[:2]) or "无错误信息")

    if template is None:
        raise WorkflowError("读取工作流失败：" + ("；".join(errors) or "未知原因"))

    _CACHE[key] = _CacheEntry(time.time(), template)
    return template


def clear_cache() -> None:
    """清空工作流缓存（界面上点「重新检查」时用）。"""
    _CACHE.clear()
