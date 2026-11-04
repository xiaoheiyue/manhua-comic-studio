# -*- coding: utf-8 -*-
"""提示词语义编译器（Qwen-Image 专用）。

把用户的中文长主题先交给文本模型，改写成 Qwen-Image 文本编码器更容易解析的
“首句定框架 + 观察式视觉描述”，并把需要精确呈现的中文文字剥离成 overlay 计划。

设计原则：
- 不直接让 T2I 模型“理解”抽象概念，而是先做语义降维；
- 信息图不再硬写“节点/连线/标签”，只生成低对比背景，结构由程序排版；
- 社媒图文把抽象主题翻译成人物、动作、空间、光影；
- 任何 LLM 失败都回退到本地确定性模板，不阻塞出图。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import config
from .logger import get_logger

log = get_logger(__name__)

_SUPPORTED = {"social", "infographic", "poster", "ecommerce"}
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_MAX = 96

_SYSTEM = """你是 Qwen-Image-2.1 的提示词语义编译器。
你的任务不是“教模型怎么画”，而是把中文需求改写成一条成品画面的观察式描述，
让 Qwen3-VL 文本编码器能直接理解。

必须遵守：
1. 第一句先定框架：媒介/版式、主体、背景底色、整体风格与配色，控制在 60 字内。
2. 后续只写成品图里看得见的东西：人物、动作、空间关系、材质、光影、颜色。
3. 禁止抽象判断词：表达、体现、象征、层次、命运、悲凉、氛围感、高级感、故事感。
4. 禁止 SD 标签堆叠；用“｜”分隔视觉层级，用“→”表示视线/动作方向。
5. 不写采样参数、模型名、负面词、画幅像素。
6. 不写任何需要精确呈现的中文文字；标题、标签、说明全部留给程序 overlay。
7. 信息图模式只描述“低对比纸张/水墨底图 + 大面积留白”，不要描述节点、连线、
   箭头、标签、图表结构；这些由程序绘制。
8. 只输出 JSON，不要 Markdown，不要解释。"""


def supported(mode_id: str) -> bool:
    return str(mode_id or "") in _SUPPORTED


def _clean(text: Any, limit: int = 0) -> str:
    s = str(text or "").strip()
    s = re.sub(r"^[\s\"'“”‘’]+|[\s\"'“”‘’]+$", "", s)
    s = re.sub(r"\s+", " ", s)
    if limit and len(s) > limit:
        s = s[:limit].rstrip("，,；;。 ")
    return s


def _split_topic(topic: str, limit: int = 5) -> list[str]:
    # 全角/半角冒号也要切开：用户常写“主题：步骤1、步骤2”，
    # 旧正则不切冒号，信息图第一个节点会变成整句主题。
    parts = re.split(r"[，,。；;：:、|｜/]+|(?:\s*(?:以及|与|和|及|→|->)\s*)",
                     str(topic or ""))
    out: list[str] = []
    for part in parts:
        part = _clean(part, 18)
        if len(part) < 2:
            continue
        if part not in out:
            out.append(part)
    return out[:limit]


def style_hint(style_id: str, colors: list[str]) -> str:
    try:
        from . import stylelib
        meta = stylelib.style_meta(style_id)
        bits = [stylelib.style_name_zh(meta), stylelib.style_medium(meta)]
        traits = stylelib.positive_traits(meta.get("traits") or "", 45)
        if traits:
            bits.append(traits)
        if colors:
            names = []
            for c in colors[:2]:
                meta = stylelib.color_meta(c)
                names.append(str(meta.get("name_zh") or meta.get("hex") or c))
            bits.append("主色 " + "、".join(names))
        return "；".join(x for x in bits if x)
    except Exception:  # noqa: BLE001
        return ""


def _fallback(payload: dict[str, Any], mode_id: str, style_hint: str) -> dict[str, Any]:
    topic = _clean(payload.get("topic"), 80) or "未命名主题"
    title = _clean(payload.get("title"), 18)
    if not title and mode_id == "infographic" and re.search(r"[:：]", topic):
        title = _clean(re.split(r"[:：]", topic, 1)[0], 18)
    title = title or topic[:18]
    subtitle = _clean(payload.get("subtitle"), 30)
    parts = _split_topic(topic)
    key_points = parts[:3] or [topic[:30]]
    if mode_id == "infographic":
        # 离线兜底：把主题拆成可排版的节点；实际语义仍以用户主题为准。
        # “主题：步骤1、步骤2”里的冒号前面是主题名，不是第一个步骤，不能当节点。
        if re.search(r"[:：]", topic):
            _head, _tail = re.split(r"[:：]", topic, 1)
            _steps = _split_topic(_tail)
            if len(_steps) >= 2:
                parts = _steps
        labels = parts[:5] or ["主题", "关键人物", "关系变化"]
        nodes = [{"label": _clean(x, 12), "caption": "", "id": f"n{i}"}
                 for i, x in enumerate(labels)]
        edges = [{"from": nodes[i]["id"], "to": nodes[i + 1]["id"], "label": ""}
                 for i in range(len(nodes) - 1)]
        visual = (
            "一张竖版信息图底图，米白宣纸底色，四周淡墨晕染，中央大面积留白，"
            "极简出版物排版；只保留纸张纹理与墨色层次。"
            + (f"画风：{style_hint}。" if style_hint else "")
        )
        return {
            "source": "fallback", "mode": mode_id,
            "title": title, "subtitle": subtitle,
            "visual_prompt": visual,
            "style_lock": style_hint,
            "key_points": [],
            "infographic": {"layout": "vertical", "nodes": nodes, "edges": edges},
        }
    visual = (
        "一张竖版社媒图文插画，米白底，主体位于画面中部，背景简洁，"
        f"留出干净的标题区域；画面围绕“{topic}”展开，用人物、动作、空间关系和光影呈现。"
        + (f"画风：{style_hint}。" if style_hint else "")
    )
    return {
        "source": "fallback", "mode": mode_id,
        "title": title, "subtitle": subtitle,
        "visual_prompt": visual, "style_lock": style_hint,
        "key_points": key_points, "infographic": None,
    }


def _normalize(raw: Any, payload: dict[str, Any], mode_id: str,
               style_hint: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    visual = _clean(raw.get("visual_prompt") or raw.get("prompt"))
    if len(visual) < 30:
        return None
    # 防止模型把具体中文标题写回提示词
    for text in (_clean(payload.get("title")), _clean(payload.get("subtitle"))):
        if text and text in visual:
            visual = visual.replace(text, "")
    visual = re.sub(r"[“\"][^”\"]{1,20}[”\"]", "", visual)
    visual = _clean(visual, 700)
    title = _clean(raw.get("title") or payload.get("title"), 18) \
        or _clean(payload.get("topic"), 18)
    subtitle = _clean(raw.get("subtitle") or payload.get("subtitle"), 30)
    key_raw = raw.get("key_points")
    key_points = [_clean(x, 28) for x in key_raw if _clean(x, 28)][:3] \
        if isinstance(key_raw, list) else []
    info = raw.get("infographic") if isinstance(raw.get("infographic"), dict) else None
    if mode_id == "infographic":
        key_points = []
        nodes_raw = (info or {}).get("nodes") or []
        max_nodes = int(getattr(config, "STUDIO_INFOGRAPHIC_NODE_MAX", 5))
        nodes: list[dict[str, Any]] = []
        for i, n in enumerate(nodes_raw[:max_nodes]):
            if not isinstance(n, dict):
                continue
            label = _clean(n.get("label"), 12)
            if not label:
                continue
            nodes.append({
                "id": f"n{i}",
                "label": label,
                "caption": _clean(n.get("caption"), 36),
            })
        if len(nodes) < 2:
            return None
        edges: list[dict[str, Any]] = []
        for e in (info or {}).get("edges") or []:
            if not isinstance(e, dict):
                continue
            try:
                a, b = int(e.get("from")), int(e.get("to"))
            except (TypeError, ValueError):
                continue
            if 0 <= a < len(nodes) and 0 <= b < len(nodes) and a != b:
                edges.append({"from": nodes[a]["id"], "to": nodes[b]["id"],
                              "label": _clean(e.get("label"), 12)})
        if not edges:
            edges = [{"from": nodes[i]["id"], "to": nodes[i + 1]["id"], "label": ""}
                     for i in range(len(nodes) - 1)]
        info = {"layout": str((info or {}).get("layout") or "vertical"),
                "nodes": nodes, "edges": edges}
    else:
        info = None
    if not key_points and mode_id != "infographic":
        key_points = _split_topic(_clean(payload.get("topic")), 3)
    return {
        "source": "llm", "mode": mode_id,
        "title": title, "subtitle": subtitle,
        "visual_prompt": visual,
        "style_lock": _clean(raw.get("style_lock") or style_hint, 80),
        "key_points": key_points,
        "infographic": info,
    }


def _prompt_for_llm(payload: dict[str, Any], mode_id: str,
                    style_hint: str, layout_hint: str) -> str:
    topic = _clean(payload.get("topic"), 120)
    title = _clean(payload.get("title"), 30)
    subtitle = _clean(payload.get("subtitle"), 50)
    extra = _clean(payload.get("extra"), 120)
    colors = payload.get("color_ids") or []
    schema = {
        "title": "主标题（可留空，程序叠加）",
        "subtitle": "副标题（可留空）",
        "visual_prompt": "成品画面的观察式描述，第一句定框架；信息图只写底图",
        "style_lock": "一句简短的画风/配色约束",
        "key_points": ["社媒图文的 2-3 个要点；信息图可留空"],
        "infographic": {
            "layout": "vertical",
            "nodes": [{"label": "节点短标签", "caption": "节点说明（可空）"}],
            "edges": [{"from": 0, "to": 1, "label": "关系（可空）"}],
        },
    }
    return f"""模式：{mode_id}（{'信息图：只写底图，不要节点/连线/标签' if mode_id == 'infographic' else '社媒图文：写一张完整插画'}）
用户主题：{topic}
用户标题：{title or '（未提供）'}
用户副标题：{subtitle or '（未提供）'}
画风与配色：{style_hint or '按项目默认'}
主色：{', '.join(str(c) for c in colors) or '（未指定）'}
版式参考：{layout_hint or '（未指定）'}
额外要求：{extra or '（无）'}

请输出 JSON，结构如下（不要输出其它内容）：
{json.dumps(schema, ensure_ascii=False, indent=2)}

信息图要求：
- visual_prompt 只描述“纸张/水墨/留白”的底图，不要出现节点、连线、箭头、标签、图表结构；
- infographic.nodes 给 3-5 个准确的短标签，必须覆盖主题中的关键人物/阶段；
- edges 先形成从第一个节点到最后一个节点的主链，再补充关系；不要引用不存在的下标；
- 节点标签和 caption 是给人看的中文，会由程序排版，不会送进 T2I。

社媒图文要求：
- visual_prompt 必须把抽象主题翻译成人物/动作/空间/光影；
- key_points 给 2-3 个短句，供程序排版。"""


def compile_studio(payload: dict[str, Any], mode_id: str,
                   style_hint: str = "", layout_hint: str = "",
                   client: Any | None = None) -> dict[str, Any]:
    """返回编译后的提示词计划；失败时返回本地兜底。"""
    key_src = json.dumps({
        "mode": mode_id, "topic": payload.get("topic"),
        "title": payload.get("title"), "subtitle": payload.get("subtitle"),
        "extra": payload.get("extra"), "colors": payload.get("color_ids"),
        "style": style_hint, "layout": layout_hint,
    }, ensure_ascii=False, sort_keys=True)
    key = hashlib.sha1(key_src.encode("utf-8")).hexdigest()
    if key in _CACHE:
        return dict(_CACHE[key])

    fallback = _fallback(payload, mode_id, style_hint)
    if not bool(getattr(config, "STUDIO_PROMPT_REWRITE", True)):
        return fallback

    try:
        if client is None:
            from .textclient import TextClient
            client = TextClient()
        text = client.chat(
            [{"role": "system", "content": _SYSTEM},
             {"role": "user", "content": _prompt_for_llm(payload, mode_id,
                                                         style_hint, layout_hint)}],
            temperature=float(getattr(config, "STUDIO_REWRITE_TEMPERATURE", 0.3)),
            max_tokens=int(getattr(config, "STUDIO_REWRITE_MAX_TOKENS", 1200)),
            json_mode=True,
        )
        from .textutil import parse_json_block
        raw = parse_json_block(text, expect="object")
        result = _normalize(raw, payload, mode_id, style_hint)
        if result is None:
            raise ValueError("改写结果缺少有效 visual_prompt / 信息图节点")
        result["raw"] = text[:4000]
        result["model"] = getattr(client, "model", "")
    except Exception as exc:  # noqa: BLE001
        log.info("提示词改写失败，使用本地兜底：%s", exc)
        fallback["error"] = str(exc)[:240]
        result = fallback
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = result
    return dict(result)


def clear_cache() -> None:
    _CACHE.clear()
