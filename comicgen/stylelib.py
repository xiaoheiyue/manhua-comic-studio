# -*- coding: utf-8 -*-
"""handraw-style 风格库适配层。

把 handraw-style 这套「编号化画风资产」接进漫画流水线，提供四件事：

1. **风格 / 主题色目录**：365 种风格（FA-001 … FI-034）、36 种主题色（C-01–C-36），
   旧编号 001–365 自动映射到新编号；
2. **风格提示词**：拼出"视觉语言 + 主题色"（默认不写风格名，避免被模型画成标题），
   并过滤掉含有"避免/不要/禁止"的负向特征；
3. **风格参考图**：解析 `images/individual/<组>/<编号>.webp`，按需转成 PNG 缓存
   （给 `style_color` 工作流做 ColorTransfer，或给 Omni 条件做参考图）；
4. **分镜版式目录**：把 `rules/data/storyboard_layouts.json` 与 handraw-style 的
   SB-001–SB-071 合起来，给出格数、几何、适配度与版式提示词。

设计原则：
- handraw-style 目录**只读**，不修改它的任何文件；
- 所有提示词只用正向描述（Z-Image-Turbo cfg=1，负向词无效）；
- 任何一步拿不到资产都返回空/缺省，不让主流程崩掉。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Sequence

import config
from .logger import get_logger

log = get_logger(__name__)

# 参考图隔离说明（handraw-style SKILL.md 原文，未删改）
REFERENCE_ISOLATION_ZH = (
    "所附图片仅用于参考画风。只提取参考图的风格特征，例如线条、笔触、媒介、材质、"
    "色彩倾向和整体视觉语言；不要使用、复制或延续参考图中的任何主体、人物、动物、"
    "服装、道具、动作、姿态、场景、背景、构图、布局、文字或故事。"
    "最终画面内容完全以用户提供的主题为准。"
)
REFERENCE_ISOLATION_SHORT = (
    "第1张参考图只用于参考画风：只取线条、笔触、媒介、材质、色彩倾向与整体视觉语言，"
    "画面内容完全按本格描述"
)
REFERENCE_COLOR_ONLY = (
    "第1张参考图只用于匹配色彩倾向与整体色调，不取其中的人物、场景、构图、文字或故事"
)

_NEGATIVE_TRAIT_RE = re.compile(r"(避免|不要|不准|禁止|严禁|不得|不能)")
_ID_RE = re.compile(r"(#\s*\d{1,3}|[A-Z]{2}-\d{3}|C-\d{2})")


# =============================================================================
# 读取（带 mtime 缓存）
# =============================================================================

_CACHE: dict[str, tuple[float, Any]] = {}


def _read_json(path: Path, default: Any) -> Any:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return default
    cached = _CACHE.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.warning("风格库文件读取失败 %s：%s", path, exc)
        data = default
    _CACHE[str(path)] = (mtime, data)
    return data


def _ref_file(name: str) -> Path:
    return Path(config.HANDRAW_REF_DIR) / name


def _rule_file(name: str) -> Path:
    return Path(config.BASE_DIR) / "rules" / "data" / name


def available() -> bool:
    """风格库是否可用（handraw-style 是否存在且 styles.json 可读）。"""
    if not bool(getattr(config, "HANDRAW_ENABLED", True)):
        return False
    path = _ref_file("styles.json")
    return path.exists() and bool(style_entries())


def style_entries() -> list[dict[str, Any]]:
    raw = _read_json(_ref_file("styles.json"), [])
    return [x for x in raw if isinstance(x, dict) and x.get("number")] if isinstance(raw, list) else []


def color_entries() -> list[dict[str, Any]]:
    raw = _read_json(_ref_file("colors.json"), [])
    return [x for x in raw if isinstance(x, dict) and x.get("id")] if isinstance(raw, list) else []


def tone_entries() -> list[dict[str, Any]]:
    raw = _read_json(_ref_file("tones.json"), [])
    base = [x for x in raw if isinstance(x, dict) and x.get("id")] if isinstance(raw, list) else []
    # 本地扩展阶调（T-17+）：不修改 handraw 原始文件，保留 T-01~T-16 不变。
    extra_path = Path(config.RULES_DIR) / "data" / "tones_extended.json"
    extra = _read_json(extra_path, [])
    extra = [x for x in extra if isinstance(x, dict) and x.get("id")] if isinstance(extra, list) else []
    seen = {str(t.get("id", "")).upper() for t in base}
    base.extend(t for t in extra if str(t.get("id", "")).upper() not in seen)
    return base


def tone_meta(tone_id: Any) -> dict[str, Any]:
    tid = str(tone_id or "").strip().upper()
    for t in tone_entries():
        if str(t.get("id", "")).upper() == tid:
            return t
    return {}


# handraw 风格家族的"媒介特征"（来自 styles.json 的 group 名称，不是凭空发明）
GROUP_MEDIUM: dict[str, str] = {
    "FA": "社论漫画与手绘涂鸦",
    "FB": "绘本与水彩插画",
    "FC": "现代平面与丝网印刷",
    "FD": "日本当代漫画与插画",
    "FE": "新国风与传统工艺",
    "FF": "3D 黏土、毛毡与纸雕",
    "FG": "二维赛璐璐动画与复古游戏",
    "FH": "综合媒介与厚涂",
    "FI": "电影级写实摄影与视觉大片",
}

# "漫画分级特征词"：只在风格 traits 里真实出现时才采用，避免给模型塞无关词
COMIC_FEATURE_WORDS: tuple[str, ...] = (
    "线稿", "墨线", "轮廓线", "描边", "网点", "排线", "平涂", "赛璐璐",
    "水彩", "水墨", "厚涂", "拼贴", "胶片噪点", "噪点", "留白", "剪影",
    "高对比", "低饱和", "暗调", "复古", "手绘", "版画", "丝网", "颗粒",
)


def comic_feature_words(meta: dict[str, Any], limit: int = 6) -> list[str]:
    """从该风格的 traits 里提取真实存在的媒介/画面特征词。"""
    blob = positive_traits(str(meta.get("traits", "") or ""), max_chars=4000)
    out: list[str] = []
    for w in COMIC_FEATURE_WORDS:
        if w in blob and w not in out:
            out.append(w)
    # "胶片噪点"已包含"噪点"时不要重复
    if "胶片噪点" in out and "噪点" in out:
        out.remove("噪点")
    return out[:max(1, int(limit or 6))]


def style_medium(meta: dict[str, Any]) -> str:
    sid = str(meta.get("id", "") or "")
    prefix = sid.split("-")[0].upper() if "-" in sid else ""
    return GROUP_MEDIUM.get(prefix, "")


def recommend_tone(text: str, style_id: Any = "") -> str:
    """
    按"故事气质 + 风格 traits"挑一个原始 handraw 阶调（T-01…T-16）；
    扩展的 T-17…T-40 由用户手选，自动推荐不擅自引入。

    只做确定性打分，不调用模型；结果可在界面上手改。
    """
    meta = style_meta(style_id) if style_id else {}
    blob = f"{text or ''} {positive_traits(str(meta.get('traits', '')), max_chars=4000)}"
    scores: dict[str, float] = {}

    def add(tid: str, weight: float, words: Sequence[str]) -> None:
        if any(w and w in blob for w in words):
            scores[tid] = scores.get(tid, 0.0) + weight

    add("T-14", 3.0, ("胶片", "噪点", "颗粒", "复古", "旧电影"))
    add("T-15", 2.6, ("暗沉", "压抑", "幽暗", "阴冷", "雨夜", "悬疑", "恐怖", "低照度"))
    add("T-03", 2.0, ("低调", "暗调", "夜景", "阴影", "黑色电影"))
    add("T-05", 2.0, ("硬光", "硬朗", "强反差", "光影交界", "剪影轮廓"))
    add("T-06", 2.2, ("高反差", "爆炸", "冲击", "战斗", "动作场面"))
    add("T-01", 2.0, ("明亮", "白天", "清爽", "通透", "阳光"))
    add("T-13", 1.6, ("清澈", "纯净", "空气感", "水彩"))
    add("T-04", 1.8, ("柔和", "温柔", "治愈", "软调", "细腻过渡"))
    add("T-16", 1.5, ("低饱和", "灰阶", "柔灰", "克制"))
    add("T-11", 1.6, ("戏剧光", "舞台光", "戏剧性"))
    add("T-12", 1.6, ("逆光", "剪影"))
    add("T-09", 1.3, ("灰调", "中间灰", "高级灰"))
    add("T-10", 1.3, ("雾", "烟", "朦胧", "空气透视"))

    if not scores:
        return "T-02"
    return max(scores.items(), key=lambda kv: kv[1])[0]


def tone_prompt(tone_id: Any) -> str:
    t = tone_meta(tone_id)
    if not t:
        return ""
    name = str(t.get("name_zh", "") or "")
    desc = str(t.get("desc_zh", "") or "")
    return f"{name}（{desc}）" if name else desc


def _style_signature(meta: dict[str, Any], limit: int = 3) -> str:
    """从该风格 traits 里抽 2~3 个最有区分度的短句，做触发词主体。"""
    raw = positive_traits(str(meta.get("traits", "") or ""), max_chars=500)
    clauses = [re.sub(r"[《》#*`]", "", x).strip("。；，、 ")
               for x in re.split(r"[；;。]", raw) if x.strip()]
    # 去掉纯抽象/空泛词，保留有画面感的特征
    skip = {"风格", "整体", "画面", "视觉", "质感", "氛围"}
    picked = [x for x in clauses if x not in skip and len(x) >= 2][:max(1, limit)]
    return "、".join(picked)


def trigger_phrase(style_id: Any, tone_id: Any = "", text: str = "") -> str:
    """界面展示/可手改的"触发词"：风格家族 + 该风格真实 traits + 阶调。"""
    meta = style_meta(style_id)
    if not meta:
        return ""
    parts: list[str] = []
    medium = style_medium(meta)
    if medium:
        parts.append(medium)
    sig = _style_signature(meta)
    if not sig:
        # 极少数没有 traits 的条目：用中文风格名/家族执行词兜底，
        # 保证触发词仍能区分到具体风格，而不是全篇一个模板。
        sig = str(meta.get("name_zh") or "") or style_execution_note(meta)
    if sig:
        parts.append(sig)
    t = tone_meta(tone_id or recommend_tone(text, style_id))
    if t:
        parts.append(str(t.get("name_zh", "")))
    return "；".join(p for p in parts if p)


def _alias_map() -> dict[str, str]:
    raw = _read_json(_ref_file("style_alias_map.json"), {})
    legacy = raw.get("legacy_to_new") if isinstance(raw, dict) else {}
    return {str(k): str(v) for k, v in (legacy or {}).items()}


def _handraw_layouts() -> dict[str, dict[str, Any]]:
    raw = _read_json(_ref_file("layouts.json"), [])
    if not isinstance(raw, list):
        return {}
    return {str(x["id"]): x for x in raw if isinstance(x, dict) and x.get("id")}


# =============================================================================
# 风格 / 主题色
# =============================================================================

def normalize_style_id(value: Any) -> str:
    """
    把用户输入归一成 handraw 风格编号（FA-001 这类）。

    接受：FA-001 / fa1 / FA_1 / 001（旧编号）/ 1 / 纯数字编号。
    找不到返回空串。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    # 先按原样/别名查
    index = {s["number"].upper(): s for s in style_entries()}
    upper = text.upper().replace("_", "-")
    if upper in index:
        return index[upper]["number"]
    legacy = _alias_map()
    m = re.fullmatch(r"#?(\d{1,3})", text)
    if m:
        num = f"{int(m.group(1)):03d}"
        target = legacy.get(num, num)
        if target.upper() in index:
            return index[target.upper()]["number"]
    m = re.fullmatch(r"([A-Za-z]{2})[-_\s]*(\d{1,3})", text)
    if m:
        cand = f"{m.group(1).upper()}-{int(m.group(2)):03d}"
        if cand in index:
            return index[cand]["number"]
    # 按风格名/参考作者名匹配（界面传来的可能是 generation_name）
    for s in style_entries():
        if text and (text == s.get("generation_name") or text == s.get("reference")):
            return s["number"]
    return ""


def _style_zh_path() -> Path:
    return Path(config.DATA_DIR) / "cache" / "style_zh.json"


def style_zh_map() -> dict[str, dict[str, str]]:
    """中文风格名缓存（由 translate_styles.py 生成；缺失时用 traits 兜底）。"""
    raw = _read_json(_style_zh_path(), {})
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for sid, item in raw.items():
        if isinstance(item, dict):
            out[str(sid).upper()] = {
                "name_zh": str(item.get("name_zh") or "").strip(),
                "reference_zh": str(item.get("reference_zh") or "").strip(),
            }
    return out


def style_group_zh(meta: dict[str, Any]) -> str:
    group = str(meta.get("group", "") or "")
    return group.split("/")[0].strip()


def _fallback_name_zh(meta: dict[str, Any]) -> str:
    """没有翻译缓存时，用"风格家族 + 第一条 traits"生成可读中文名。"""
    group = style_group_zh(meta)
    group = re.sub(r"^[A-Z]{2}\s*", "", group).strip()
    trait = positive_traits(meta.get("traits", ""), 90).split("；")[0].strip()
    trait = re.sub(r"[《》#*`]", "", trait)[:18]
    if group and trait:
        return f"{group} · {trait}"
    return group or trait or str(meta.get("generation_name", "") or "")


def style_name_zh(meta: dict[str, Any]) -> str:
    sid = str(meta.get("id", "") or "").upper()
    cached = style_zh_map().get(sid, {})
    return cached.get("name_zh") or _fallback_name_zh(meta)


def style_reference_display(meta: dict[str, Any]) -> str:
    """参考作者显示：有中文译名就“中文 / 外文”，没有就明确标成外文署名。"""
    ref = str(meta.get("reference", "") or "").strip()
    zh = str(meta.get("reference_zh", "") or "").strip()
    if zh and re.search(r"[\u4e00-\u9fff]", zh):
        return f"{zh} / {ref}" if ref else zh
    if ref:
        return f"外文署名（{ref}）"
    return ""


def style_meta(style_id: Any) -> dict[str, Any]:
    """按编号取风格元数据；找不到返回空 dict。"""
    sid = normalize_style_id(style_id)
    if not sid:
        return {}
    for s in style_entries():
        if s["number"] == sid:
            meta = dict(s)
            meta["id"] = sid
            meta["image_rel"] = style_image_rel(sid)
            meta["image_abs"] = str(style_image_path(sid) or "")
            meta["traits_positive"] = positive_traits(meta.get("traits", ""))
            zh = style_zh_map().get(sid.upper(), {})
            meta["name_en"] = meta.get("generation_name", "")
            meta["name_zh"] = zh.get("name_zh") or _fallback_name_zh(meta)
            meta["reference_zh"] = zh.get("reference_zh", "")
            meta["reference_display"] = style_reference_display(meta)
            meta["group_zh"] = style_group_zh(meta)
            return meta
    return {}


def style_image_path(style_id: Any) -> Path | None:
    sid = normalize_style_id(style_id)
    if not sid:
        return None
    group = sid.split("-")[0]
    path = Path(config.HANDRAW_IMAGE_DIR) / "individual" / group / f"{sid}.webp"
    if path.exists():
        return path
    grid = path.with_name(f"{sid}_grid.webp")
    return grid if grid.exists() else None


def style_image_rel(style_id: Any) -> str:
    path = style_image_path(style_id)
    if not path:
        return ""
    try:
        return path.resolve().relative_to(Path(config.BASE_DIR).resolve()).as_posix()
    except Exception:  # noqa: BLE001
        return str(path)


def style_reference_png(style_id: Any) -> str:
    """
    返回一张可直接送进 ComfyUI 的 PNG 画风参考图（带缓存）。

    webp 在部分 ComfyUI 环境里 LoadImage 会失败，这里统一转 PNG；
    同时把最长边压到 HANDRAW_STYLE_REF_MAX_EDGE，避免 VAE 编码把显存吃满。
    """
    src = style_image_path(style_id)
    if not src:
        return ""
    sid = normalize_style_id(style_id)
    out_dir = Path(config.DATA_DIR) / "cache" / "style_refs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{sid}.png"
    try:
        if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
            return str(out)
        from PIL import Image

        img = Image.open(src).convert("RGB")
        limit = int(getattr(config, "HANDRAW_STYLE_REF_MAX_EDGE", 768) or 768)
        img.thumbnail((limit, limit), Image.LANCZOS)
        img.save(out, format="PNG")
        return str(out)
    except Exception as exc:  # noqa: BLE001
        log.warning("风格参考图转换失败 %s：%s", src, exc)
        return ""


def positive_traits(traits: str, max_chars: int | None = None) -> str:
    """
    只保留正向可见特征（去掉带"避免/不要/禁止"的分句与所有编号）。

    另外去掉《作品名》：保留它会诱导模型把片名当文字画进画面。
    """
    text = _ID_RE.sub("", str(traits or ""))
    text = re.sub(r"《[^》]{1,40}》", "", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    parts: list[str] = []
    for chunk in re.split(r"[；;。\n]", text):
        chunk = chunk.strip("，、, ")
        if not chunk or _NEGATIVE_TRAIT_RE.search(chunk):
            continue
        parts.append(chunk)
    out = "；".join(dict.fromkeys(parts))
    limit = int(max_chars if max_chars is not None
                else getattr(config, "HANDRAW_STYLE_TRAITS_MAX_CHARS", 150) or 150)
    if len(out) <= limit:
        return out
    buf = ""
    for part in out.split("；"):
        if len(buf) + len(part) + 1 > limit:
            break
        buf = f"{buf}；{part}" if buf else part
    return buf or out[:limit]


def normalize_color_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    query = text.upper().replace(" ", "")
    for c in color_entries():
        cid = str(c["id"]).upper()
        if query == cid:
            return c["id"]
    m = re.fullmatch(r"C[-_]?(\d{1,2})", query)
    if m:
        cand = f"C-{int(m.group(1)):02d}"
        for c in color_entries():
            if str(c["id"]).upper() == cand:
                return c["id"]
    for c in color_entries():
        if text in (c.get("name_zh"), c.get("name_en")):
            return c["id"]
    return ""


def parse_colors(value: Any) -> list[str]:
    """把 "C-01 + C-12" / "C-01,C-12" / "克莱因蓝" 解析成编号列表（最多 3 个）。"""
    if isinstance(value, (list, tuple)):
        tokens = [str(x) for x in value]
    else:
        tokens = re.split(r"[,，+＋、/\s]+", str(value or ""))
    out: list[str] = []
    for token in tokens:
        cid = normalize_color_id(token)
        if cid and cid not in out:
            out.append(cid)
    return out[:3]


def color_meta(color_id: Any) -> dict[str, Any]:
    cid = normalize_color_id(color_id)
    if not cid:
        return {}
    for c in color_entries():
        if c["id"] == cid:
            return dict(c)
    return {}


_STYLE_EXECUTION: dict[str, str] = {
    "FA": "手工墨线，线宽不匀、边缘毛糙、有刮擦与断线；头小、颈长、腿长，"
          "身体明显拉长到 8~9 头身，四肢细长、肩线尖锐、动作幅度大；"
          "用大面积黑白或有限套色",
    "FB": "纸面水彩/铅笔/拼贴质感，边缘柔化、有纸纹与颗粒，适当留白；低饱和、安静",
    "FC": "丝网/孔版/平面构成，套色错位、粗颗粒、硬边色块",
    "FD": "漫画墨线 + 网点/排线，表情与人体适度夸张，分镜感强",
    "FE": "宣纸/水墨/工笔/民间工艺质感，笔触与留白清楚，矿物色或墨色",
    "FF": "黏土/毛毡/纸雕/布料的手作材质，边缘不规则、有体积与指纹感",
    "FG": "赛璐璐平涂 + 硬边阴影 + 复古动画色，轮廓线清楚",
    "FH": "厚涂/综合媒介，笔触与刮痕可见，色彩有层次",
    "FI": "电影摄影质感，真实材质、镜头光与景深，精确构图",
}


def style_execution_note(meta: dict[str, Any]) -> str:
    """把风格家族翻译成模型可执行的"线条/材质/比例"指令。"""
    sid = str(meta.get("id", "") or "")
    prefix = sid.split("-")[0].upper() if "-" in sid else ""
    return _STYLE_EXECUTION.get(prefix, "")


def color_prompt(colors: Sequence[str]) -> str:
    metas = [color_meta(c) for c in colors]
    metas = [m for m in metas if m]
    if not metas:
        return ""
    names = [f"{m.get('name_zh', m['id'])}（{m.get('name_en', '')}）"
             if m.get("name_en") else str(m.get("name_zh", m["id"])) for m in metas]
    if len(metas) == 1:
        return (f"主题色锁定：画面色彩基调以{names[0]}为主，用于背景、环境光与点缀；"
                f"不改变角色服装主色、发色与肤色")
    if len(metas) == 2:
        return (f"主题色锁定：主色为{names[0]}、点缀色为{names[1]}；"
                f"只用于背景、环境光与关键物件，不改变角色服装主色、发色与肤色")
    return (f"主题色锁定：{' 搭配 '.join(names)}；"
            f"只用于背景/环境光/点缀，不改变角色服装主色、发色与肤色")


def style_prompt_block(style_id: Any, colors: Sequence[str] | None = None,
                       *, include_reference_note: bool = True,
                       ref_mode: str = "style",
                       max_chars: int | None = None,
                       tone: str | None = None,
                       include_trigger: bool | None = None,
                       text: str = "",
                       trigger: str | None = None) -> str:
    """
    拼一段可直接塞进出图提示词的风格描述（中文、纯正向）。

    ⚠️ 默认**不写风格名 / 作者名**：Z-Image-Turbo 常把 "…Cinema""…Illustration"
    这类标题式名字当成画面标题文字画上去（实测 FI-030 会在底部排出片名）。
    人看的名字在界面里；给模型的是可执行的视觉特征 + 主题色。

    形如：
      漫画分级特征：电影级写实摄影；低饱和、暗调；暗沉调（…）。
      视觉语言：…（线条/笔触/媒介/色彩）。主题色：…
    """
    meta = style_meta(style_id)
    if not meta:
        return ""
    include_name = bool(getattr(config, "HANDRAW_PROMPT_INCLUDE_NAME", False))
    include_author = bool(getattr(config, "HANDRAW_PROMPT_INCLUDE_AUTHOR", False))
    if include_trigger is None:
        include_trigger = bool(getattr(config, "HANDRAW_PROMPT_TRIGGER", True))
    parts: list[str] = []
    traits = positive_traits(meta.get("traits", ""), max_chars=max_chars)
    if traits:
        # 风格特征必须最先出现：模型对开头最敏感，不能先塞"韩漫/条漫"模板。
        parts.append(
            f"视觉语言（本图必须执行）：{traits}；"
            "线条、人体比例、动态、材质与背景都服从这个风格")
    execution = style_execution_note(meta)
    if execution:
        parts.append(f"画风执行：{execution}")
    if include_name:
        parts.append(f"风格触发：{meta.get('generation_name', '')}")
    if include_author and meta.get("reference"):
        parts.append(f"参考作者：{meta.get('reference', '')}")
    if include_trigger:
        custom = str(trigger or "").strip()
        tone_id = tone or recommend_tone(text, meta.get("id"))
        # 自动生成的触发词（短版，界面展示）与完整版（带阶调说明）分开：
        # 只有用户真正改成别的文本时才原样使用，避免把自动短版塞回提示词而丢掉阶调说明。
        auto_short = trigger_phrase(meta.get("id"), tone_id, text)
        if custom and custom != auto_short:
            parts.append("漫画分级特征：" + custom)
        else:
            trig: list[str] = []
            medium = style_medium(meta)
            if medium:
                trig.append(medium)
            words = comic_feature_words(meta)
            if words:
                trig.append("、".join(words))
            tp = tone_prompt(tone_id)
            if tp:
                trig.append(tp)
            if trig:
                parts.append("漫画分级特征：" + "；".join(trig))
    color_text = color_prompt(colors or [])
    if color_text:
        parts.append(color_text)
    if include_reference_note and bool(getattr(config, "HANDRAW_STYLE_REF_ENABLED", True)) \
            and style_image_path(meta["id"]):
        parts.append(REFERENCE_COLOR_ONLY if str(ref_mode).lower() == "color"
                     else REFERENCE_ISOLATION_SHORT)
    return "。".join(p for p in parts if p) + "。"


# =============================================================================
# 风格推荐（本地确定性打分；AI 决策在 pipeline 里做）
# =============================================================================

# 题材/情绪 -> 视觉语汇（只用于本地打分，不是"固定映射表"）
_MOOD_WORDS: dict[str, list[str]] = {
    "悬疑": ["悬疑", "紧张", "暗", "阴影", "冷", "诡异", "压迫", "犯罪", "推理"],
    "温柔": ["治愈", "温暖", "柔", "日常", "生活", "安静", "细腻", "手绘"],
    "古风": ["水墨", "古", "东方", "宣纸", "工笔", "留白", "雅致"],
    "奇幻": ["奇幻", "魔法", "幻想", "史诗", "光效", "梦"],
    "都市": ["都市", "现代", "城市", "霓虹", "通勤", "职场"],
    "校园": ["校园", "青春", "少年", "少女", "明亮", "清爽"],
    "幽默": ["搞笑", "幽默", "吐槽", "喜剧", "轻松", "怪"],
    "黑白": ["黑白", "墨线", "线稿", "单色", "素描", "版画"],
    "彩色": ["彩色", "饱和", "鲜明", "通透", "赛璐璐"],
}


def _bigrams(text: str) -> list[str]:
    t = re.sub(r"[\s\W_]+", "", str(text or "").lower())
    return [t[i:i + 2] for i in range(len(t) - 1)] if len(t) > 1 else []


def shortlist_style_ids(text: str, limit: int | None = None) -> list[str]:
    """按文本关键词给 365 种风格打分，返回前 N 个编号（给 AI 做最终挑选）。"""
    k = int(limit or getattr(config, "HANDRAW_STYLE_SHORTLIST", 28) or 28)
    styles = style_entries()
    if not styles:
        return []
    hay = str(text or "")
    grams = set(_bigrams(hay))
    words = [w for w in re.findall(r"[\u4e00-\u9fa5]{2,6}|[a-zA-Z]{3,}", hay)]
    # 题材提示词
    mood_hits: list[str] = []
    for mood, keys in _MOOD_WORDS.items():
        if any(k2 in hay for k2 in keys):
            mood_hits.extend(keys)
    scored: list[tuple[float, str]] = []
    for s in styles:
        name = f"{s.get('generation_name', '')} {s.get('reference', '')}"
        traits = str(s.get("traits", ""))
        group = str(s.get("group", ""))
        text_all = f"{name} {traits} {group}"
        score = 0.0
        for g in grams:
            if g in name:
                score += 3.0
            elif g in traits:
                score += 1.4
            elif g in group:
                score += 0.8
        for w in words:
            if w in name:
                score += 4.0
            elif w in traits:
                score += 2.0
            elif w in group:
                score += 1.0
        for m in mood_hits:
            if m and m in text_all:
                score += 2.2
        # 轻微偏好"漫画/插画/线稿"类语汇
        for handy in ("线", "笔", "手绘", "插画", "漫画", "赛璐璐", "平涂"):
            if handy in traits:
                score += 0.35
        if score > 0:
            scored.append((score, s["number"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    ids = [sid for _, sid in scored[:k]]
    if len(ids) < k:  # 文本太短/太泛：补足确定性多样本，避免候选不足
        start = sum(ord(c) for c in hay) % max(1, len(styles)) if hay else 0
        for i in range(len(styles)):
            sid = styles[(start + i * 7) % len(styles)]["number"]
            if sid not in ids:
                ids.append(sid)
            if len(ids) >= k:
                break
    return ids[:k]


def recommend(text: str, *, style_hint: Any = "", color_hint: Any = "",
              shortlist_limit: int | None = None) -> dict[str, Any]:
    """
    本地风格推荐（AI 不可用时的兜底，也用于给 AI 收敛候选）。

    返回 ``{style_id, color_ids, rationale, shortlist}``；风格库不可用时 style_id 为空。
    """
    if not available():
        return {"style_id": "", "color_ids": [], "rationale": "", "shortlist": []}
    hint_id = normalize_style_id(style_hint)
    shortlist = shortlist_style_ids(text, shortlist_limit)
    style_id = hint_id or (shortlist[0] if shortlist else "")

    hints = parse_colors(color_hint)
    if hints:
        colors = hints
    else:
        colors = _auto_colors(text, style_id)
    meta = style_meta(style_id)
    rationale = ""
    if meta:
        first = positive_traits(meta.get("traits", "")).split("；")[0]
        cname = "、".join(color_meta(c).get("name_zh", c) for c in colors)
        tone_id = tone_for(style_id, text)
        tone_name = str(tone_meta(tone_id).get("name_zh", ""))
        rationale = (f"按故事气质匹配风格「{meta.get('generation_name', '')}」"
                     f"（{meta.get('reference', '')}），以{first or '其视觉语汇'}承载画面情绪"
                     + (f"，阶调取{tone_name}" if tone_name else "")
                     + (f"，搭配主题色 {cname}" if cname else ""))
    else:
        tone_id = tone_for(style_id, text)
    return {"style_id": style_id, "color_ids": colors,
            "style_tone": tone_id,
            "style_trigger": trigger_phrase(style_id, tone_id, text),
            "rationale": rationale, "shortlist": shortlist}


def tone_for(style_id: Any, text: str = "") -> str:
    forced = str(getattr(config, "HANDRAW_TONE_DEFAULT", "") or "").strip().upper()
    if forced and tone_meta(forced):
        return forced
    if not bool(getattr(config, "HANDRAW_TONE_ENABLED", True)):
        return ""
    return recommend_tone(text, style_id)


def _auto_colors(text: str, style_id: str) -> list[str]:
    """按文本 + 风格特征挑 1–2 个主题色（同色系主色 + 跨色系点缀）。"""
    colors = color_entries()
    if not colors:
        return []
    meta = style_meta(style_id)
    hay = f"{text} {meta.get('traits', '')} {meta.get('group', '')}".lower()
    grams = set(_bigrams(hay))
    scored: list[tuple[float, dict[str, Any]]] = []
    for c in colors:
        c_text = f"{c.get('name_zh', '')} {c.get('name_en', '')} " \
                 f"{c.get('category_zh', '')} {c.get('quote_zh', '')}".lower()
        score = 0.0
        for g in grams:
            if g in c_text:
                score += 1.0
        for key, words in _MOOD_WORDS.items():
            if any(w in text for w in words):
                for w in words:
                    if w in c_text:
                        score += 1.6
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda x: (-x[0], str(x[1]["id"])))
    if not scored:
        idx = sum(ord(ch) for ch in text or "a") % len(colors)
        return [colors[idx]["id"]]
    primary = scored[0][1]
    accent = next((c for _, c in scored[1:]
                   if c.get("category") != primary.get("category")), None)
    out = [primary["id"]]
    if accent:
        out.append(accent["id"])
    return out


# =============================================================================
# 分镜版式
# =============================================================================

def layout_data() -> dict[str, Any]:
    raw = _read_json(_rule_file("storyboard_layouts.json"), {})
    return raw if isinstance(raw, dict) else {}


def geometries() -> dict[str, Any]:
    g = layout_data().get("geometries")
    return g if isinstance(g, dict) else {}


def layout_entries() -> list[dict[str, Any]]:
    """本地版式表 + handraw 名称/关键词（以 handraw 为准，缺了用本地）。"""
    data = layout_data()
    layouts = data.get("layouts")
    if not isinstance(layouts, dict):
        return []
    handraw = _handraw_layouts()
    out: list[dict[str, Any]] = []
    for lid, spec in layouts.items():
        item = dict(spec)
        meta = handraw.get(lid) or {}
        if meta.get("name"):
            item["name"] = meta["name"]
        if meta.get("name_en"):
            item["name_en"] = meta["name_en"]
        if meta.get("keywords"):
            item["keywords"] = list(meta["keywords"])
        item["id"] = lid
        item.setdefault("native", lid.startswith("WB-"))
        item.setdefault("approx", False)
        item["image_rel"] = layout_image_rel(lid)
        out.append(item)
    return out


def layout_index() -> dict[str, dict[str, Any]]:
    return {x["id"]: x for x in layout_entries()}


def layout_by_id(layout_id: Any) -> dict[str, Any]:
    lid = str(layout_id or "").strip().upper().replace("_", "-")
    return layout_index().get(lid, {})


def geometry(name: Any) -> dict[str, Any]:
    g = geometries().get(str(name or ""))
    return dict(g) if isinstance(g, dict) else {}


def layout_image_path(layout_id: Any) -> Path | None:
    lid = str(layout_id or "").strip().upper()
    if not lid.startswith("SB-"):
        return None
    path = Path(config.HANDRAW_IMAGE_DIR) / "layouts" / "comic-storyboards" / f"{lid}.webp"
    return path if path.exists() else None


def layout_image_rel(layout_id: Any) -> str:
    path = layout_image_path(layout_id)
    if not path:
        return ""
    try:
        return path.resolve().relative_to(Path(config.BASE_DIR).resolve()).as_posix()
    except Exception:  # noqa: BLE001
        return str(path)


def layout_prompt(layout_id: Any) -> str:
    """版式说明（handraw 的 md 中文段；原生 WB 版式用内置文案）。"""
    lid = str(layout_id or "").strip().upper()
    entry = layout_by_id(lid)
    if not entry:
        return ""
    if lid.startswith("WB-"):
        return {
            "WB-001": "原生竖屏条漫单格：单幅竖构图、满版出血、一个镜头一层景，"
                      "上下与前后格无缝相接，适合情绪、冲击与转场。",
            "WB-002": "原生竖屏条漫两格：两格等高竖排、同一宽度、无页边距，"
                      "格间留极窄呼吸缝，像手机上一路下滑的连续画面。",
            "WB-003": "原生竖屏条漫三格：三格等高竖排、同一宽度、无页边距，"
                      "适合一段对白或连续动作。",
        }.get(lid, "")
    path = _ref_file("layouts") / f"{lid}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    text = re.sub(r"<!--\s*en\s*-->.*$", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return re.sub(r"\s+", " ", text).strip()


def candidate_layouts(panels: Iterable[int] | None = None,
                      *, native: bool = True) -> list[dict[str, Any]]:
    """按格数筛候选版式（默认 1–6 格全部）。"""
    wanted = set(int(p) for p in panels) if panels else None
    out = []
    for item in layout_entries():
        if not native and item.get("native"):
            continue
        if wanted is not None and int(item.get("panels") or 0) not in wanted:
            continue
        out.append(item)
    return out


def layout_brief_table(limit: int | None = None) -> str:
    """给文本模型的版式清单（编号 / 名称 / 格数 / 适配题材）。"""
    rows: list[str] = []
    for item in layout_entries():
        if limit and len(rows) >= limit:
            break
        beats = "、".join(item.get("beats") or [])
        rows.append(f"{item['id']}｜{item.get('name', '')}｜{item.get('panels', '')}格"
                    f"｜{beats}")
    return "\n".join(rows)


def style_brief_table(style_ids: Sequence[str]) -> str:
    """给文本模型的风格候选清单（编号 / 名称 / 参考作者 / 特征）。"""
    rows: list[str] = []
    for sid in style_ids:
        meta = style_meta(sid)
        if not meta:
            continue
        rows.append(f"{meta['id']}｜{meta.get('generation_name', '')}"
                    f"｜{meta.get('reference', '')}"
                    f"｜{positive_traits(meta.get('traits', ''), 70)}")
    return "\n".join(rows)


def color_brief_table() -> str:
    return "\n".join(
        f"{c['id']}｜{c.get('name_zh', '')}（{c.get('name_en', '')}）｜{c.get('category_zh', '')}"
        for c in color_entries())


def project_style(project: Any) -> dict[str, Any]:
    """读取工程上的风格设置，返回统一结构（旧工程也能用）。"""
    style_id = normalize_style_id(getattr(project, "style_id", "") or "")
    if not style_id and bool(getattr(config, "HANDRAW_ENABLED", True)):
        style_id = normalize_style_id(getattr(config, "HANDRAW_DEFAULT_STYLE", ""))
    colors = parse_colors(getattr(project, "style_color_ids", None)
                          or getattr(config, "HANDRAW_DEFAULT_COLORS", ""))
    meta = style_meta(style_id) if style_id else {}
    tone_id = str(getattr(project, "style_tone", "") or "").strip() \
        or (tone_for(style_id, f"{getattr(project, 'summary', '')} "
                                f"{getattr(project, 'style_detail', '')}") if style_id else "")
    auto_trigger = trigger_phrase(
        style_id, tone_id,
        f"{getattr(project, 'summary', '')} {getattr(project, 'style_detail', '')}") \
        if style_id else ""
    custom_trigger = bool(getattr(project, "style_trigger_custom", False))
    return {
        "style_id": style_id,
        "style": meta,
        "color_ids": colors,
        "auto": bool(getattr(project, "style_auto", False)),
        "rationale": str(getattr(project, "style_rationale", "") or ""),
        "tone_id": tone_id,
        "tone": tone_meta(tone_id) if tone_id else {},
        "trigger": (str(getattr(project, "style_trigger", "") or "")
                    if custom_trigger else auto_trigger),
        "medium": style_medium(meta) if meta else "",
        "comic_features": comic_feature_words(meta) if meta else [],
        "reference_png": (style_reference_png(style_id)
                          if style_id and bool(getattr(config, "HANDRAW_STYLE_REF_ENABLED", True))
                          else ""),
    }


def self_check() -> dict[str, Any]:
    """自检：风格库/版式库是否可读、关键资产是否齐全。"""
    styles = style_entries()
    colors = color_entries()
    layouts = layout_entries()
    sample = styles[0]["number"] if styles else ""
    ref = style_reference_png(sample) if sample else ""
    return {
        "enabled": bool(getattr(config, "HANDRAW_ENABLED", True)),
        "available": available(),
        "root": str(config.HANDRAW_ROOT),
        "styles": len(styles),
        "colors": len(colors),
        "tones": len(tone_entries()),
        "layouts": len(layouts),
        "geometries": len(geometries()),
        "sample_style": sample,
        "sample_reference": ref,
        "sample_layout": layouts[0]["id"] if layouts else "",
        "layout_prompt": layout_prompt("SB-001")[:60],
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(self_check(), ensure_ascii=False, indent=2))
