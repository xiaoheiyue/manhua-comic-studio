# -*- coding: utf-8 -*-
"""界面里的「工作流」工作台。

- 默认工作流来自 ``comfy_workflows/*.json``（不可删除，可编辑/恢复）；
- 用户新增的工作流保存在 ``data/workflows/*.json``（可删除）；
- 每个工作流都有一份 Markdown 说明，默认从节点图自动生成，用户可编辑；
- 节点图以 ComfyUI 的 API 图（``{id: {class_type, inputs, _meta}}``）为准，
  界面展示时附上节点坐标；保存时把连线还原成 API 图的输入引用。

本模块不直接生成图片，只负责“工作流资产”的读写与绑定。生成侧由
``pipeline._bound_image_client()`` 读取项目里的 ``workflow_bindings`` 后使用。
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import config
from . import comfyworkflow as comfy
from .logger import get_logger

log = get_logger(__name__)

DEFAULT_DIR = Path(config.COMFY_WORKFLOW_DIR)
CUSTOM_DIR = Path(config.DATA_DIR) / "workflows"
DOC_DIR = CUSTOM_DIR / "docs"
LAYOUT_DIR = CUSTOM_DIR / "layouts"
BACKUP_DIR = CUSTOM_DIR / "default_backups"
META_FILE = CUSTOM_DIR / "meta.json"

TARGETS = {
    "character": "角色参考图",
    "shot": "分镜出图",
    "pose": "姿态图",
}

# 默认工作流的“适用步骤”推断；界面里也允许用户改绑到角色 / 分镜 / 姿态
DEFAULT_TARGETS: dict[str, list[str]] = {
    "image_z_image_turbo_int8": ["character", "shot"],
    "image_z_image_turbo_int8_style_color": ["shot"],
    "image_z_image_turbo_int8_style_ref": ["shot"],
    "image_z_image_turbo_int8_style_img2img": ["shot"],
    "image_z_image_turbo_int8_character_pose": ["character", "pose"],
    "image_z_image_turbo_int8_character_pose_ref": ["character", "pose"],
    "image_z_image_turbo_int8_story_chain": ["shot"],
    "image_z_image_turbo_int8_prop_control": ["shot"],
    "image_z_image_turbo_int8_studio_emoji": ["shot"],
    "qwen_image_2.1_turbo_t2i_local": ["character", "shot"],
    "qwen_image_2.1_turbo_edit_local": ["character"],
    "qwen_image_2.1_turbo_control_local": ["shot", "pose"],
}

_TITLE_HINTS = {
    "image_z_image_turbo_int8": "Z-Image 基础文生图",
    "image_z_image_turbo_int8_style_color": "Z-Image 画风色彩迁移",
    "image_z_image_turbo_int8_style_ref": "Z-Image 参考图条件（实验）",
    "image_z_image_turbo_int8_style_img2img": "Z-Image 参考图 img2img",
    "image_z_image_turbo_int8_character_pose": "Z-Image 角色姿态（Pose）",
    "image_z_image_turbo_int8_character_pose_ref": "Z-Image 角色姿态参考",
    "image_z_image_turbo_int8_story_chain": "Z-Image 连续故事板",
    "image_z_image_turbo_int8_prop_control": "Z-Image 纯道具控制",
    "image_z_image_turbo_int8_studio_emoji": "Z-Image 创作工坊表情包",
    "image_z_image_turbo_int8_studio_emoji50": "Z-Image 表情包 · 50",
    "image_z_image_turbo_int8_studio_emoji58": "Z-Image 表情包 · 58",
    "image_z_image_turbo_int8_studio_emoji65": "Z-Image 表情包 · 65",
    "image_z_image_turbo_int8_studio_emoji70": "Z-Image 表情包 · 70",
    "qwen_image_2.1_turbo_t2i_local": "Qwen-Image 本地文生图",
    "qwen_image_2.1_turbo_edit_local": "Qwen-Image 本地图生图",
    "qwen_image_2.1_turbo_control_local": "Qwen-Image 姿态控制",
    "_zimage_controlnet_ui": "Z-Image ControlNet（界面格式）",
}

_NODE_INFO_CACHE: tuple[float, dict[str, Any]] = (0.0, {})
_LAYOUT_CACHE: dict[str, Any] = {}


# =============================================================================
# 基础
# =============================================================================

def _slug(text: str) -> str:
    raw = re.sub(r"[^a-zA-Z0-9_\-]+", "_", str(text or "").strip()).strip("_").lower()
    return raw[:48] or "workflow"


def _meta() -> dict[str, Any]:
    try:
        data = json.loads(META_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save_meta(data: dict[str, Any]) -> None:
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    META_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                         encoding="utf-8")


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path(config.BASE_DIR).resolve()).as_posix()
    except Exception:  # noqa: BLE001
        return str(path)


def _default_paths() -> list[Path]:
    if not DEFAULT_DIR.exists():
        return []
    return sorted((p for p in DEFAULT_DIR.glob("*.json")
                   if p.is_file() and not p.name.startswith("_")),
                  key=lambda p: p.name.lower())


def _custom_paths() -> list[Path]:
    if not CUSTOM_DIR.exists():
        return []
    return sorted((p for p in CUSTOM_DIR.glob("wf_*.json") if p.is_file()),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def _find_path(workflow_id: str) -> tuple[Path | None, bool]:
    """返回 (路径, 是否默认工作流)。"""
    wid = str(workflow_id or "")
    if wid.startswith("default:"):
        stem = wid.split(":", 1)[1]
        if not stem or stem != Path(stem).name or stem.startswith("."):
            return None, True
        path = DEFAULT_DIR / f"{stem}.json"
        return (path if path.exists() else None), True
    if wid.startswith("custom:"):
        stem = wid.split(":", 1)[1]
        if not stem or stem != Path(stem).name or stem.startswith("."):
            return None, False
        path = CUSTOM_DIR / f"{stem}.json"
        return (path if path.exists() else None), False
    return None, False


def workflow_path(workflow_id: str) -> Path | None:
    path, _default = _find_path(workflow_id)
    return path


def _workflow_id(path: Path, default: bool) -> str:
    return f"{'default' if default else 'custom'}:{path.stem}"


def _doc_path(workflow_id: str) -> Path:
    return DOC_DIR / f"{workflow_id.replace(':', '__')}.md"


def _layout_path(workflow_id: str) -> Path:
    return LAYOUT_DIR / f"{workflow_id.replace(':', '__')}.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _graph_of(obj: Any) -> dict[str, dict]:
    if comfy.is_api_graph(obj):
        return copy.deepcopy(obj)
    if isinstance(obj, dict) and isinstance(obj.get("nodes"), list):
        return comfy.flatten_ui_workflow(obj)
    raise ValueError("工作流格式无法识别：需要 ComfyUI API 图或界面格式（nodes/links）")


def _load_graph(workflow_id: str) -> tuple[dict[str, dict], dict[str, Any]]:
    path, is_default = _find_path(workflow_id)
    if path is None:
        raise KeyError(f"工作流不存在：{workflow_id}")
    raw = _read_json(path)
    graph = _graph_of(raw)
    return graph, raw if isinstance(raw, dict) else {}


# =============================================================================
# 节点信息 / 布局
# =============================================================================

def _node_info() -> dict[str, Any]:
    global _NODE_INFO_CACHE
    at, cached = _NODE_INFO_CACHE
    if cached and time.time() - at < 300:
        return cached
    try:
        import requests
        url = f"{config.COMFY_API_HOST.rstrip('/')}/object_info"
        resp = requests.get(url, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            _NODE_INFO_CACHE = (time.time(), data)
            return data
    except Exception as exc:  # noqa: BLE001
        log.debug("读取 ComfyUI 节点信息失败：%s", exc)
    return cached or {}


def _simple_node_info(graph: dict[str, dict]) -> dict[str, Any]:
    info = _node_info()
    out: dict[str, Any] = {}
    for node in graph.values():
        ctype = str(node.get("class_type") or "")
        if not ctype:
            continue
        raw = info.get(ctype) or {}
        out[ctype] = {
            "display_name": raw.get("display_name") or ctype,
            "output": raw.get("output") or [],
            "output_name": raw.get("output_name") or [],
            "input_order": raw.get("input_order") or {},
            "description": raw.get("description") or "",
        }
    return out


_LINK_TYPES = {
    "MODEL", "CLIP", "VAE", "IMAGE", "LATENT", "CONDITIONING", "CONTROL_NET",
    "SAMPLER", "NOISE", "GUIDER", "SIGMAS", "MASK", "CLIP_VISION",
    "STYLE_MODEL", "UPSCALE_MODEL", "TAESD", "AUDIO", "VIDEO", "WEBCAM",
}


def _is_link_type(spec_type: Any) -> bool:
    if isinstance(spec_type, list):
        return False
    text = str(spec_type or "")
    return text in _LINK_TYPES or (text.isupper() and text not in
                                   {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"})


def node_catalog(query: str = "", limit: int = 120) -> dict[str, Any]:
    """给「添加节点」用的 ComfyUI 节点库（按关键词过滤）。"""
    info = _node_info()
    q = str(query or "").strip().lower()
    out: list[dict[str, Any]] = []
    for ctype, raw in info.items():
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("display_name") or ctype)
        category = str(raw.get("category") or "")
        if q and q not in f"{ctype} {name} {category}".lower():
            continue
        inputs: list[dict[str, Any]] = []
        for section in ("required", "optional"):
            raw_inputs = (raw.get("input") or raw.get("inputs") or {}).get(section) or {}
            for key, spec in raw_inputs.items():
                if not isinstance(spec, list) or not spec:
                    continue
                stype = spec[0]
                opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                link = _is_link_type(stype)
                default = opts.get("default")
                if default is None and not link:
                    if isinstance(stype, list):
                        default = stype[0] if stype else ""
                    elif stype == "INT":
                        default = int(opts.get("min") or 0)
                    elif stype == "FLOAT":
                        default = float(opts.get("min") or 0.0)
                    elif stype == "BOOLEAN":
                        default = False
                    elif stype == "STRING":
                        default = ""
                inputs.append({
                    "name": str(key),
                    "type": "ENUM" if isinstance(stype, list) else str(stype),
                    "default": default,
                    "link": bool(link),
                    "required": section == "required",
                })
        out.append({
            "class_type": ctype,
            "name": name,
            "category": category,
            "description": raw.get("description") or "",
            "output": raw.get("output") or [],
            "output_name": raw.get("output_name") or [],
            "inputs": inputs,
        })
    out.sort(key=lambda x: (x["category"], x["name"].lower()))
    return {"nodes": out[:max(1, int(limit))], "total": len(out)}


def _layered_positions(graph: dict[str, dict]) -> dict[str, dict[str, float]]:
    """没有界面坐标时，按依赖层级做一份可读的自动布局。"""
    deps: dict[str, set[str]] = {nid: set() for nid in graph}
    for nid, node in graph.items():
        for value in (node.get("inputs") or {}).values():
            target = comfy._link_target(graph, value)  # type: ignore[attr-defined]
            if target and target[0] in graph and target[0] != nid:
                deps[nid].add(target[0])
    layers: dict[str, int] = {}

    def layer(nid: str, seen: set[str]) -> int:
        if nid in layers:
            return layers[nid]
        if nid in seen:
            return 0
        seen.add(nid)
        value = 0
        for dep in deps.get(nid, ()):
            value = max(value, layer(dep, seen) + 1)
        layers[nid] = min(value, 24)
        return layers[nid]

    for nid in graph:
        layer(nid, set())
    buckets: dict[int, list[str]] = {}
    for nid, lv in layers.items():
        buckets.setdefault(lv, []).append(nid)
    pos: dict[str, dict[str, float]] = {}
    for lv, ids in sorted(buckets.items()):
        for i, nid in enumerate(sorted(ids, key=lambda x: str(x))):
            pos[nid] = {"x": 70 + lv * 300, "y": 70 + i * 190}
    return pos


def _ui_positions(raw: dict[str, Any]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    nodes = raw.get("nodes")
    if not isinstance(nodes, list):
        return out
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("id")
        pos = node.get("pos")
        if nid is None or not isinstance(pos, list) or len(pos) < 2:
            continue
        out[str(nid)] = {"x": float(pos[0]), "y": float(pos[1])}
    return out


def _load_layout(workflow_id: str) -> dict[str, dict[str, float]]:
    try:
        data = json.loads(_layout_path(workflow_id).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): {"x": float(v.get("x", 0)), "y": float(v.get("y", 0))}
                    for k, v in data.items() if isinstance(v, dict)}
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_layout(workflow_id: str, positions: dict[str, Any]) -> None:
    clean = {str(k): {"x": float(v.get("x", 0)), "y": float(v.get("y", 0))}
             for k, v in (positions or {}).items() if isinstance(v, dict)}
    _write_json(_layout_path(workflow_id), clean)


def _ensure_positions(workflow_id: str, graph: dict[str, dict],
                      raw: dict[str, Any]) -> dict[str, dict[str, float]]:
    pos = _load_layout(workflow_id)
    if not pos:
        pos = _ui_positions(raw)
    if not pos:
        pos = _layered_positions(graph)
    for nid in graph:
        pos.setdefault(nid, {"x": 80, "y": 80})
    _save_layout(workflow_id, pos)
    return pos


def _graph_payload(workflow_id: str, graph: dict[str, dict],
                   raw: dict[str, Any]) -> dict[str, Any]:
    positions = _ensure_positions(workflow_id, graph, raw)
    links: list[dict[str, Any]] = []
    for nid, node in graph.items():
        for key, value in (node.get("inputs") or {}).items():
            target = comfy._link_target(graph, value)  # type: ignore[attr-defined]
            if target:
                links.append({
                    "from": str(target[0]), "from_slot": int(target[1]),
                    "to": str(nid), "input": key,
                })
    nodes = []
    for nid, node in graph.items():
        nodes.append({
            "id": str(nid),
            "class_type": str(node.get("class_type") or ""),
            "title": str((node.get("_meta") or {}).get("title")
                         or node.get("class_type") or nid),
            "pos": positions.get(str(nid), {"x": 80, "y": 80}),
            "inputs": {k: v for k, v in (node.get("inputs") or {}).items()
                       if not comfy._link_target(graph, v)},  # type: ignore[attr-defined]
            "input_names": list((node.get("inputs") or {}).keys()),
            "_meta": node.get("_meta") or {},
        })
    return {
        "nodes": nodes,
        "links": links,
        "node_info": _simple_node_info(graph),
    }


# =============================================================================
# 文档
# =============================================================================

def _infer_targets(stem: str) -> list[str]:
    for key, value in DEFAULT_TARGETS.items():
        if stem == key:
            return list(value)
    if "pose" in stem:
        return ["character", "pose"]
    if "control" in stem:
        return ["shot", "pose"]
    if "studio" in stem:
        return ["shot"]
    return ["character", "shot"]


def _node_summary(graph: dict[str, dict]) -> list[str]:
    counts: dict[str, int] = {}
    for node in graph.values():
        ctype = str(node.get("class_type") or "Unknown")
        counts[ctype] = counts.get(ctype, 0) + 1
    return [f"- `{k}` × {v}" for k, v in sorted(counts.items())]


def _key_node_summary(graph: dict[str, dict]) -> list[str]:
    try:
        nodes = comfy.WorkflowTemplate(graph=graph).nodes
    except Exception:  # noqa: BLE001
        nodes = {}
    names = {"prompt": "正向提示词", "negative": "反向提示词",
             "latent": "画幅", "sampler": "采样器", "save": "保存"}
    out = []
    for key, label in names.items():
        nid = nodes.get(key)
        if nid and str(nid) in graph:
            node = graph[str(nid)]
            out.append(f"- {label}：节点 `{nid}` · {node.get('class_type')}")
    return out


def _default_doc(workflow_id: str, title: str, graph: dict[str, dict]) -> str:
    stem = workflow_id.split(":", 1)[1]
    targets = _infer_targets(stem)
    target_names = [TARGETS.get(t, t) for t in targets]
    models = []
    for node in graph.values():
        for key in ("unet_name", "ckpt_name", "clip_name", "vae_name", "lora_name"):
            value = (node.get("inputs") or {}).get(key)
            if isinstance(value, str) and value:
                models.append(f"- {key}：`{value}`")
    models = list(dict.fromkeys(models))
    return f"""# {title}

- **工作流 ID**：`{workflow_id}`
- **来源**：{'项目自带（默认，不可删除）' if workflow_id.startswith('default:') else '用户添加'}
- **适用步骤**：{' / '.join(target_names)}
- **节点数**：{len(graph)}

## 用来做什么

这是「{title}」的 ComfyUI 节点工作流。默认工作流随项目提供，用于对应的绘图步骤；
可以在左侧工作流列表中查看完整节点图、修改节点参数与连线。

## 怎么用

1. 在左侧选择这个工作流；
2. 在右侧勾选「适用步骤」（角色参考图 / 分镜出图），保存应用；
3. 下一次对应步骤出图时，会使用本工作流的节点图；
4. 工作流必须包含可识别的正向提示词、采样器、画幅与保存节点，否则不能应用。

## 关键节点

{chr(10).join(_key_node_summary(graph)) or '（无法识别标准节点）'}

## 用到的模型

{chr(10).join(models) or '（未检测到模型文件名）'}

## 节点清单

{chr(10).join(_node_summary(graph))}
"""


def _ensure_doc(workflow_id: str, title: str, graph: dict[str, dict]) -> str:
    path = _doc_path(workflow_id)
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    text = _default_doc(workflow_id, title, graph)
    path.write_text(text, encoding="utf-8")
    return text


# =============================================================================
# 列表 / 详情
# =============================================================================

def _workflow_public(workflow_id: str, path: Path, is_default: bool,
                     bindings: dict[str, str]) -> dict[str, Any]:
    try:
        raw = _read_json(path)
        graph = _graph_of(raw)
    except Exception as exc:  # noqa: BLE001
        return {"id": workflow_id, "file": _rel(path), "title": path.stem,
                "default": is_default, "error": str(exc), "nodes": 0}
    stem = path.stem
    title = _TITLE_HINTS.get(stem) or stem
    meta = _meta().get(workflow_id, {}) if isinstance(_meta().get(workflow_id), dict) else {}
    title = meta.get("title") or title
    _ensure_doc(workflow_id, title, graph)
    active = [t for t, wid in bindings.items() if wid == workflow_id]
    return {
        "id": workflow_id,
        "file": _rel(path),
        "title": title,
        "default": is_default,
        "nodes": len(graph),
        "links": sum(1 for n in graph.values() for v in (n.get("inputs") or {}).values()
                     if comfy._link_target(graph, v)),  # type: ignore[attr-defined]
        "targets": meta.get("targets") or _infer_targets(stem),
        "active_targets": active,
        "doc": _rel(_doc_path(workflow_id)),
        "backup": (BACKUP_DIR / f"{workflow_id.replace(':', '__')}.json").exists()
        if is_default else False,
    }


def catalog(project: Any) -> dict[str, Any]:
    bindings = (getattr(project, "settings", {}) or {}).get("workflow_bindings") or {}
    if not isinstance(bindings, dict):
        bindings = {}
    items: list[dict[str, Any]] = []
    for path in _default_paths():
        wid = _workflow_id(path, True)
        items.append(_workflow_public(wid, path, True, bindings))
    for path in _custom_paths():
        wid = _workflow_id(path, False)
        items.append(_workflow_public(wid, path, False, bindings))
    info = _node_info()
    return {
        "workflows": items,
        "targets": [{"id": k, "name": v} for k, v in TARGETS.items()],
        "bindings": {k: v for k, v in bindings.items() if k in TARGETS},
        "default_dir": _rel(DEFAULT_DIR),
        "custom_dir": _rel(CUSTOM_DIR),
        "node_info_loaded": bool(info),
    }


def detail(project: Any, workflow_id: str) -> dict[str, Any]:
    path, is_default = _find_path(workflow_id)
    if path is None:
        raise KeyError(f"工作流不存在：{workflow_id}")
    raw = _read_json(path)
    graph = _graph_of(raw)
    meta = _meta().get(workflow_id, {}) if isinstance(_meta().get(workflow_id), dict) else {}
    stem = path.stem
    title = meta.get("title") or _TITLE_HINTS.get(stem) or stem
    payload = _graph_payload(workflow_id, graph, raw)
    t2i_compatible = any(str(n.get("class_type")) in
                         ("EmptyLatentImage", "EmptySD3LatentImage")
                         for n in graph.values())
    bindings = (getattr(project, "settings", {}) or {}).get("workflow_bindings") or {}
    payload.update({
        "id": workflow_id,
        "file": _rel(path),
        "title": title,
        "default": is_default,
        "targets": meta.get("targets") or _infer_targets(stem),
        "active_targets": [t for t, wid in bindings.items() if wid == workflow_id],
        "doc": _ensure_doc(workflow_id, title, graph),
        "backup": (BACKUP_DIR / f"{workflow_id.replace(':', '__')}.json").exists()
        if is_default else False,
        "models": comfy.WorkflowTemplate(graph=graph).models,
        "applicable": t2i_compatible,
        "apply_reason": "" if t2i_compatible else
        "该工作流没有空 Latent 节点（纯图生图 / Edit），暂不能直接应用到文本出图步骤",
    })
    return payload


# =============================================================================
# 保存 / 新建 / 删除 / 文档 / 应用
# =============================================================================

def _validate_graph(graph: dict[str, dict]) -> None:
    if not isinstance(graph, dict) or not graph:
        raise ValueError("工作流节点图为空")
    for nid, node in graph.items():
        if not isinstance(node, dict) or not node.get("class_type"):
            raise ValueError(f"节点 {nid} 缺少 class_type")
        if not isinstance(node.get("inputs"), dict):
            node["inputs"] = {}
    try:
        comfy.WorkflowTemplate(graph=graph).nodes
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"工作流缺少可识别的提示词/采样器/画幅/保存节点：{exc}") from exc


def _backup_default(workflow_id: str, path: Path) -> None:
    if not workflow_id.startswith("default:"):
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"{workflow_id.replace(':', '__')}.json"
    if not target.exists():
        shutil.copy2(path, target)


def save_graph(project: Any, workflow_id: str, nodes: list[dict],
               links: list[dict], positions: dict[str, Any]) -> dict[str, Any]:
    path, is_default = _find_path(workflow_id)
    if path is None:
        raise KeyError(f"工作流不存在：{workflow_id}")
    by_id = {str(n.get("id")): n for n in (nodes or []) if isinstance(n, dict)}
    if not by_id:
        raise ValueError("节点列表为空")
    graph: dict[str, dict] = {}
    for nid, node in by_id.items():
        graph[nid] = {
            "class_type": str(node.get("class_type") or ""),
            "inputs": copy.deepcopy(node.get("inputs") or {}),
            "_meta": copy.deepcopy(node.get("_meta") or {"title": node.get("title") or nid}),
        }
    for link in links or []:
        if not isinstance(link, dict):
            continue
        src = str(link.get("from") or "")
        dst = str(link.get("to") or "")
        key = str(link.get("input") or "")
        if src not in graph or dst not in graph or not key:
            continue
        graph[dst]["inputs"][key] = [src, int(link.get("from_slot", 0))]
    _validate_graph(graph)
    _backup_default(workflow_id, path)
    _write_json(path, graph)
    _save_layout(workflow_id, positions or {})
    return detail(project, workflow_id)


def restore_default(project: Any, workflow_id: str) -> dict[str, Any]:
    if not workflow_id.startswith("default:"):
        raise ValueError("只有默认工作流支持恢复默认版本")
    path, _is_default = _find_path(workflow_id)
    if path is None:
        raise KeyError(f"工作流不存在：{workflow_id}")
    backup = BACKUP_DIR / f"{workflow_id.replace(':', '__')}.json"
    if not backup.exists():
        raise ValueError("还没有备份：保存过一次默认工作流后才可恢复")
    shutil.copy2(backup, path)
    return detail(project, workflow_id)


def save_doc(workflow_id: str, content: str) -> dict[str, Any]:
    path, _is_default = _find_path(workflow_id)
    if path is None:
        raise KeyError(f"工作流不存在：{workflow_id}")
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    _doc_path(workflow_id).write_text(str(content or ""), encoding="utf-8")
    return {"message": "工作流说明已保存", "doc": str(content or "")}


def _comfyui_workflows() -> list[dict[str, Any]]:
    try:
        import requests
        session = requests.Session()
        names = comfy.list_workflows(
            session, config.COMFY_API_HOST.rstrip("/"),
            timeout=getattr(config, "STARTUP_PING_TIMEOUT", 5))
    except Exception:  # noqa: BLE001
        names = []
    return [{"name": n} for n in names]


def comfyui_catalog() -> dict[str, Any]:
    return {"workflows": _comfyui_workflows()}


def _fetch_comfyui_graph(name: str) -> dict[str, dict]:
    import requests
    session = requests.Session()
    raw = comfy.fetch_from_comfyui(
        session, config.COMFY_API_HOST.rstrip("/"), name,
        timeout=getattr(config, "STARTUP_PING_TIMEOUT", 5))
    if not raw:
        raise ValueError(f"ComfyUI 里没有找到工作流：{name}")
    return _graph_of(raw)


def create_workflow(project: Any, name: str, mode: str = "duplicate",
                    base_id: str = "", graph_json: str = "",
                    comfyui_name: str = "") -> dict[str, Any]:
    title = str(name or "").strip() or "新工作流"
    if mode == "duplicate":
        path, _is_default = _find_path(base_id)
        if path is None:
            raise ValueError("请选择一个要复制的工作流")
        graph = _load_graph(base_id)[0]
    elif mode == "comfyui":
        graph = _fetch_comfyui_graph(comfyui_name)
    elif mode == "json":
        try:
            obj = json.loads(graph_json or "")
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 语法错误：{exc}") from exc
        graph = _graph_of(obj)
    else:
        raise ValueError(f"未知新建方式：{mode}")
    _validate_graph(graph)
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    base = _slug(title)
    existing = {p.stem for p in CUSTOM_DIR.glob("*.json")}
    stem = base
    i = 2
    while f"wf_{stem}" in existing:
        stem = f"{base}_{i}"
        i += 1
    wid = f"custom:wf_{stem}"
    path = CUSTOM_DIR / f"wf_{stem}.json"
    _write_json(path, graph)
    meta = _meta()
    meta[wid] = {"title": title, "targets": _infer_targets(base)}
    _save_meta(meta)
    _ensure_doc(wid, title, graph)
    return detail(project, wid)


def delete_workflow(workflow_id: str) -> dict[str, Any]:
    path, is_default = _find_path(workflow_id)
    if path is None:
        raise KeyError(f"工作流不存在：{workflow_id}")
    if is_default:
        raise ValueError("项目自带的默认工作流不能删除；可以复制一份再修改")
    path.unlink(missing_ok=True)
    _doc_path(workflow_id).unlink(missing_ok=True)
    _layout_path(workflow_id).unlink(missing_ok=True)
    meta = _meta()
    meta.pop(workflow_id, None)
    _save_meta(meta)
    return {"message": "工作流已删除"}


def apply_workflow(project: Any, workflow_id: str,
                   targets: list[str] | None = None) -> dict[str, Any]:
    path, _is_default = _find_path(workflow_id)
    if path is None:
        raise KeyError(f"工作流不存在：{workflow_id}")
    graph = _load_graph(workflow_id)[0]
    _validate_graph(graph)
    if not any(str(n.get("class_type")) in
               ("EmptyLatentImage", "EmptySD3LatentImage")
               for n in graph.values()):
        raise ValueError("该工作流没有空 Latent 节点（可能是纯图生图 / Edit 流程），"
                         "暂不支持直接应用到文本出图步骤")
    chosen = [t for t in (targets or []) if t in TARGETS]
    settings = getattr(project, "settings", None)
    if not isinstance(settings, dict):
        settings = {}
        project.settings = settings
    bindings = settings.get("workflow_bindings")
    if not isinstance(bindings, dict):
        bindings = {}
    # 先清掉该工作流的旧绑定，再写入新选择
    for t in list(bindings):
        if bindings[t] == workflow_id:
            bindings.pop(t, None)
    for t in chosen:
        bindings[t] = workflow_id
    settings["workflow_bindings"] = bindings
    if hasattr(project, "touch"):
        project.touch()
    return {
        "message": f"已应用到：{' / '.join(TARGETS[t] for t in chosen) or '无'}",
        "bindings": bindings,
    }
