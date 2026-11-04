"""创作工坊：复用 handraw-style 的风格 / 版式 / 主题色，生成漫画以外的内容。"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import config
from . import stylelib
from . import prompt_rewriter
from . import infographic as infographic_mod
from .imageclient import ImageClient
from .bubble import load_font
from . import poseutil
from .qwenedit import QwenEditClient


@dataclass(frozen=True)
class StudioMode:
    id: str
    name: str
    desc: str
    size: tuple[int, int]
    layout_category: str = ""
    default_layout: str = ""


MODES: tuple[StudioMode, ...] = (
    StudioMode("poster", "海报", "活动 / 节日 / 主题海报，文字由后期排版", (1024, 1536), "social-card", ""),
    StudioMode("social", "社媒图文", "小红书 / 公众号图文卡", (1024, 1408), "social-card", "SC-001"),
    StudioMode("infographic", "信息图 / 长图", "步骤、清单、对比、数据长图", (1024, 1536), "infographic", "IG-001"),
    StudioMode("emoji", "表情包", "同一角色的 3×3 表情贴纸总表", (1024, 1024), "", ""),
    StudioMode("illustration", "插画 / 封面", "单幅插画、文章题图、封面", (1024, 1408), "", ""),
    StudioMode("ip", "IP / 头像", "角色 IP 设定、头像、三视图", (1024, 1024), "ip-character", "IP-001"),
    StudioMode("ecommerce", "电商图", "产品主图、卖点图、详情页", (1024, 1408), "ecommerce", "EC-001"),
)

MODE_BY_ID = {m.id: m for m in MODES}

_LAYOUT_JSON = Path(config.HANDRAW_REF_DIR) / "layouts.json"
_LAYOUT_DIR = Path(config.HANDRAW_REF_DIR) / "layouts"

_CACHE: dict[str, Any] = {}


def _layouts() -> list[dict[str, Any]]:
    if "layouts" in _CACHE:
        return _CACHE["layouts"]
    try:
        raw = json.loads(_LAYOUT_JSON.read_text(encoding="utf-8"))
        out = [x for x in raw if isinstance(x, dict) and x.get("id")] if isinstance(raw, list) else []
    except Exception:
        out = []
    _CACHE["layouts"] = out
    return out


def _layout_prompt(layout_id: str) -> str:
    if not layout_id:
        return ""
    p = _LAYOUT_DIR / f"{layout_id.upper()}.md"
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return ""
    # 只取中文段
    if "<!-- en -->" in text:
        text = text.split("<!-- en -->", 1)[0]
    text = text.replace("<!-- zh -->", "").strip()
    return text


def _layout_image_rel(item: dict[str, Any]) -> str:
    raw = str(item.get("image") or "")
    if not raw:
        return ""
    try:
        p = (_LAYOUT_JSON.parent / raw).resolve()
        return p.relative_to(Path(config.BASE_DIR).resolve()).as_posix()
    except Exception:
        return ""


def layouts_for(mode_id: str) -> list[dict[str, Any]]:
    mode = MODE_BY_ID.get(mode_id)
    if not mode or not mode.layout_category:
        return []
    out = []
    for x in _layouts():
        if str(x.get("category") or "") != mode.layout_category:
            continue
        lid = str(x.get("id") or "")
        out.append({
            "id": lid,
            "name": str(x.get("name") or lid),
            "name_en": str(x.get("name_en") or ""),
            "keywords": list(x.get("keywords") or []),
            "prompt": _layout_prompt(lid),
            "image": _layout_image_rel(x),
        })
    return out


def catalog() -> dict[str, Any]:
    styles = []
    for s in stylelib.style_entries():
        sid = str(s.get("id") or stylelib.normalize_style_id(s.get("number")))
        if not sid:
            continue
        meta = stylelib.style_meta(sid)
        styles.append({
            "id": sid,
            "name": s.get("generation_name") or sid,
            "name_zh": meta.get("name_zh") or stylelib.style_name_zh(meta),
            "group": s.get("group") or "",
            "group_zh": meta.get("group_zh") or stylelib.style_group_zh(meta),
            "traits": stylelib.positive_traits(s.get("traits") or "", 50),
            "image": stylelib.style_image_rel(sid),
        })
    colors = []
    for c in stylelib.color_entries():
        colors.append({"id": c["id"], "name": c.get("name_zh") or c["id"],
                       "hex": c.get("hex") or ""})
    return {
        "modes": [{"id": m.id, "name": m.name, "desc": m.desc,
                   "size": list(m.size), "layout_category": m.layout_category,
                   "layouts": layouts_for(m.id)} for m in MODES],
        "styles": styles,
        "colors": colors,
        "default_style": config.HANDRAW_DEFAULT_STYLE,
        "default_colors": list(config.HANDRAW_DEFAULT_COLORS or []),
    }


_EMOJI_EXPRESSIONS = [
    "开心大笑", "无语", "震惊", "生气", "委屈", "思考", "比心", "困倦",
    "点赞", "哭泣", "坏笑", "疑惑", "害羞", "得意", "害怕", "打招呼",
]

# 顺序与 _EMOJI_EXPRESSIONS 对齐：每格一个动作，避免只换表情、动作全一样。
_EMOJI_ACTIONS = [
    "双手举高、身体后仰大笑",
    "单手扶额、侧头叹气",
    "双手抱头、后退半步",
    "双手叉腰、跺脚前倾",
    "双手绞在身前、低头缩肩",
    "一只手托腮、歪头",
    "双手在头顶比心、身体微侧",
    "打哈欠、一只手揉眼睛",
    "竖起大拇指、另一只手叉腰",
    "抬手抹眼泪、肩膀微微发抖",
    "一只手捂嘴、侧身偷笑",
    "歪头、双手摊开耸肩",
    "双手捂脸、身体扭向一侧",
    "双臂抱胸、抬下巴",
    "缩成一团、双手抱膝",
    "抬起一只手挥手、身体前倾",
]

# 与表情/动作对齐的短句池；模型直接写中文容易糊字，改为程序叠加随机短句。
_EMOJI_PHRASES = [
    ["哈哈哈", "笑死", "太开心", "好耶"],
    ["无语", "……", "服了", "真的会谢"],
    ["？！", "真的吗", "震惊", "不会吧"],
    ["哼！", "气死", "不行", "过分"],
    ["呜呜", "委屈", "欺负人", "想哭"],
    ["嗯…", "让我想想", "有道理", "等等"],
    ["爱你", "比心", "么么", "贴贴"],
    ["困…", "晚安", "睡了", "好困"],
    ["赞！", "牛", "可以", "厉害"],
    ["呜…", "想哭", "难过", "眼泪"],
    ["嘿嘿", "坏笑", "懂？", "计划通"],
    ["嗯？", "在吗", "什么", "啊？"],
    ["害羞", "不好意思", "别看我", "脸红"],
    ["小意思", "轻松", "那当然", "简单"],
    ["怕…", "不要啊", "救命", "好可怕"],
    ["嗨", "在吗", "你好", "来啦"],
]


def _white_outline_bg(path: str) -> str:
    """把 chibi 底图外侧的桌面/纹理冲成白色；保留白色贴纸描边和角色。"""
    try:
        import cv2
        import numpy as np
        im = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        h, w = im.shape[:2]
        mask = np.zeros((h + 2, w + 2), np.uint8)
        for seed in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
                     (w // 2, 0), (w // 2, h - 1)):
            cv2.floodFill(im, mask, seed, (255, 255, 255),
                          loDiff=(22, 22, 22), upDiff=(22, 22, 22), flags=8)
        out = str(Path(path).with_name(Path(path).stem + "_white.png"))
        cv2.imencode(".png", im)[1].tofile(out)
        return out
    except Exception:
        return path


def _pad_square(path: str, size: int = 1024) -> str:
    """把竖图参考图放到白色方画布上，避免 ImageScale center 裁掉头。"""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGB", (size, size), (255, 255, 255))
    canvas.paste(im, ((size - im.width) // 2, (size - im.height) // 2))
    out = Path(path).with_name(Path(path).stem + "_square.png")
    canvas.save(out, format="PNG")
    return str(out)


def _ref_palette_hint(reference: str) -> str:
    """从参考图粗略提取主色，写进提示词；Qwen 不会自动“看懂”参考图细节。"""
    try:
        from PIL import Image
        import colorsys
        im = Image.open(reference).convert("RGB").resize((64, 64))
        q = im.quantize(colors=8, method=Image.MEDIANCUT)
        pal = q.getpalette() or []
        counts = q.getcolors() or []
        names = []
        for count, idx in sorted(counts, reverse=True)[:6]:
            r, g, b = pal[idx * 3:idx * 3 + 3]
            if max(r, g, b) > 245 and min(r, g, b) > 235:
                continue
            if max(r, g, b) < 25:
                continue
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if s < 0.12:
                names.append("白/灰")
            elif v < 0.3:
                names.append("黑")
            elif h < 0.04 or h >= 0.95:
                names.append("红")
            elif h < 0.12:
                names.append("橙/棕")
            elif h < 0.2:
                names.append("黄/金")
            elif h < 0.45:
                names.append("绿")
            elif h < 0.58:
                names.append("青")
            elif h < 0.72:
                names.append("蓝")
            elif h < 0.85:
                names.append("紫")
            else:
                names.append("粉/洋红")
        uniq = []
        for n in names:
            if n not in uniq:
                uniq.append(n)
        return "、".join(uniq[:4])
    except Exception:
        return ""


def _make_chibi_base(reference: str) -> str:
    """第一步：把写实/立绘参考图转成 Q 版 chibi 角色底图。
    后面所有表情都从这张底图出发，脸和画风才不会被写实参考拉走。
    """
    from .imageclient import ImageClient as _Client
    prompt = (
        "Q版 chibi 表情包角色，2.5 头身，纯白背景，手绘粗描边，水彩与马克笔质感；"
        "保留参考图角色的发型、发色、服装主色、标志性配饰与脸型特征；"
        "正面站姿，角色完整，贴纸感；不要文字、不要水印、不要场景、不要写实照片。"
    )
    neg = ("写实, 3D, 照片, 真实材质, 低质量, 模糊, 文字, 水印, 多个人物, "
           "场景, 灰色背景, 阴影背景, 纸张纹理")
    # 先把参考图抠成白底人像；这样 chibi 底图不会把桌布/森林背景带进来。
    ref = reference
    try:
        import uuid as _uuid
        from . import bgremove
        cut = Path(config.OUTPUT_ROOT) / "创作工坊" / "refs" / f"cutout_{_uuid.uuid4().hex[:8]}.png"
        cut.parent.mkdir(parents=True, exist_ok=True)
        made = bgremove.remove_background(reference, cut, mode="white")
        if made and Path(made).exists():
            ref = _pad_square(made)
    except Exception:
        ref = reference
    c = _Client(workflow="image_z_image_turbo_int8_studio_emoji70")
    r = c.generate(prompt, reference_paths=[ref], negative_prompt=neg,
                   size=(512, 512), seed=888001, use_cache=False)
    import uuid as _uuid
    out = Path(config.OUTPUT_ROOT) / "创作工坊" / "refs" / f"chibi_{_uuid.uuid4().hex[:8]}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    r.image.save(out, format="PNG")
    return str(out)


def _generate_emoji_topic_base(topic: str, style_id: str, colors: list[str],
                               seed: int = 888001) -> str:
    """没有参考图时，先生成一张“同一角色”的 chibi 母本。

    旧的无参考路径让 Qwen 一次直出 3×3；实测模型会把同一张站姿复制九次，
    甚至每格补一个第二角色。这里改为：先出一张单人母本，再走
    ``_generate_emoji_reference_grid`` 逐格 Edit，姿势/表情才会真正变化。
    """
    from .qwenedit import QwenEditClient
    try:
        block = stylelib.style_prompt_block(
            style_id, colors, include_reference_note=False, ref_mode="style",
            text=topic)
        block = block.replace("漫画分级特征", "画面风格特征").replace("漫画", "插画")
    except Exception:  # noqa: BLE001
        block = ""
    prompt = (
        "Q版 chibi 表情包角色设定，2.5 头身，纯白背景，粗描边，水彩马克笔质感；"
        f"角色：{topic}；画面里只有一个角色、一个头、一个身体；"
        "正面站姿，全身完整，贴纸感；不要文字、不要场景、不要多个人物、"
        "不要写实照片、不要3D。"
        + block
    )
    q = QwenEditClient()
    img = q.generate_t2i(
        prompt,
        negative="写实,3D,照片,文字,水印,场景,多个人物,两个角色,"
                 "多余人物,分镜,拼图,低质量",
        size=(768, 768), seed=int(seed), steps=8)
    out_dir = Path(config.OUTPUT_ROOT) / "创作工坊" / time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = out_dir / "emoji_base.png"
    img.save(raw, format="PNG")
    try:
        from . import bgremove
        nobg = out_dir / "emoji_base_nobg.png"
        made = bgremove.remove_background(raw, nobg, mode="white")
        if made and Path(made).exists():
            return str(made)
    except Exception:  # noqa: BLE001
        pass
    return str(raw)


def _emoji_face_ref(reference: str) -> str:
    """把参考图裁成「一个主角的头肩特写」，作为每格表情的 Edit 参考。

    角色主视图经常是「上面一个全身 + 下面一个小图」的拼版；按比例裁会把
    第二个人也带进去，Qwen Edit 就会在每格里复制出两个人。这里固定取
    画面上半部分的中央区域：主角的头部和上半身，同时避开下方第二人。
    """
    from PIL import Image, ImageOps
    im = Image.open(reference).convert("RGB")
    w, h = im.size
    crop = im.crop((int(w * 0.14), 0, int(w * 0.86), int(h * 0.40)))
    crop = ImageOps.fit(crop, (512, 512), method=Image.LANCZOS, centering=(0.5, 0.30))
    out = Path(config.OUTPUT_ROOT) / "创作工坊" / "refs" / f"face_{Path(reference).stem}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    crop.save(out, format="PNG")
    return str(out)


# 顺序必须和 _EMOJI_EXPRESSIONS 对齐：一格表情一格话。
AUTO_EMOJI_LABELS = [
    "哈哈", "……", "？！", "哼！", "呜呜", "嗯…", "爱你", "困…",
    "赞！", "呜…", "嘿嘿", "嗯？", "害羞", "小意思", "怕…", "嗨",
]


DEFAULT_EMOJI_TEXTS = [
    "谢谢", "无语", "真的吗", "不行", "委屈", "让我想想", "爱你", "晚安",
    "好的", "呜呜", "嘿嘿", "在吗", "不好意思", "小意思", "不要啊", "Hi",
]


def _compose_emoji_grid(images: list, grid: int, texts: list[str] | None = None) -> "Any":
    from PIL import Image, ImageDraw, ImageOps
    from .bubble import load_font
    size = 1024
    cell = size // grid
    canvas = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    for i, im in enumerate(images[:grid * grid]):
        x = (i % grid) * cell
        y = (i // grid) * cell
        im = im.convert("RGB")
        # 去掉贴纸外的淡色底纹/水印；只裁很小一圈，不切角色。
        # 顶部少裁一点：Qwen 常把头顶画到画布上沿，裁 5% 会把头发/脸切掉。
        w0, h0 = im.size
        im = im.crop((int(w0 * 0.04), int(h0 * 0.01), int(w0 * 0.96), int(h0 * 0.99)))
        im = ImageOps.fit(im, (cell, cell), method=Image.LANCZOS, centering=(0.5, 0.10))
        canvas.paste(im, (x, y))
        label = (texts[i].strip() if texts and i < len(texts) else "")
        if label:
            f = load_font(max(18, int(cell * 0.13)), bold=True, kind="title")
            bb = draw.textbbox((0, 0), label, font=f)
            tw = bb[2] - bb[0]
            tx = x + (cell - tw) // 2
            ty = y + cell - int(cell * 0.24)
            draw.text((tx, ty), label, font=f, fill=(28, 26, 24),
                      stroke_width=max(3, int(cell * 0.015)), stroke_fill=(255, 255, 255))
    return canvas

def _emoji_reference_sheet(reference: str, grid: int = 3) -> str:
    """把单张角色参考图铺成 grid×grid 的 init sheet，让 img2img 保留网格。"""
    from PIL import Image, ImageOps
    grid = max(2, min(5, int(grid or 3)))
    im = Image.open(reference).convert("RGB")
    size = 1024
    canvas = Image.new("RGB", (size, size), (255, 255, 255))
    cell = size // grid
    pad = max(4, cell // 24)
    for y in range(grid):
        for x in range(grid):
            # 表情包看的是脸和上半身；参考图若是全身立绘，先裁到胸部以上，
            # 否则 img2img 会把“站立全身”复制 16 次。
            crop_h = int(im.height * 0.62)
            head = im.crop((0, 0, im.width, crop_h))
            fit = ImageOps.fit(head, (cell - pad * 2, cell - pad * 2),
                               method=Image.LANCZOS, centering=(0.5, 0.15))
            canvas.paste(fit, (x * cell + pad, y * cell + pad))
    out = Path(config.OUTPUT_ROOT) / "创作工坊" / "refs" / f"emoji_{grid}x{grid}_{Path(reference).stem}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, format="PNG")
    return str(out)


def _mode_instruction(mode: StudioMode, topic: str, title: str, subtitle: str,
                      grid: int = 3) -> str:
    if mode.id == "emoji":
        grid = max(2, min(5, int(grid or 3)))
        n = grid * grid
        return (
            f"生成一张 {grid}×{grid} 表情包贴纸总表：同一个角色、同一套服装、同一画风，"
            f"{n} 个格子分别是 {n} 种不同表情和动作；"
            "白色背景，粗描边，贴纸感，每格独立完整，不画漫画分镜框、不画连续剧情；"
            "格子排布必须严格是整齐的网格，不能只画一个角色；"
            f"角色设定：{topic or '一个可爱的小角色'}。"
        )
    if mode.id == "poster":
        return (
            f"生成一张竖版单幅插画，主题：{topic}。"
            "画面必须有明确视觉中心、完整构图、留白和层级；"
            "画面只画景物/人物/道具，不要画任何标题、文字、字体、排版、印章；"
            "整张画面是单一完整场景，不要分格、不要边框、不要拼图、不要连续剧情。"
        )
    if mode.id == "social":
        return (
            f"生成一张社媒图文卡，主题：{topic}。"
            "画面主体是一张完整插画，上方或下方留出干净的标题/要点面板空间；"
            "整张画面是单一图文卡，不要分格、不要拼图；"
            "画面本身不要写任何文字，标题与要点由后期排版。"
        )
    if mode.id == "infographic":
        return (
            f"生成一张竖版信息图底图，主题：{topic}。"
            "米白纸张/水墨质感，低对比，大面积留白，预留出节点卡片与标题的位置；"
            "只画底图，不要画节点、连线、箭头、图标标签或任何文字；"
            "不要分格、不要拼图。"
        )
    if mode.id == "illustration":
        return (
            f"生成一幅单幅插画，主题：{topic}。"
            "完整构图、主体明确、光影统一、细节丰富，适合作为文章题图或封面；"
            "不要分格、不要对话框、不要拼图。"
            "画面不要出现任何文字、标题、签名或水印。"
        )
    if mode.id == "ip":
        return (
            f"生成一张 IP 角色设定图，主题：{topic}。"
            "同一角色居中全身主形象，右侧或旁边放同一角色的侧面、背面小图，"
            "底部放 3–4 个不同表情的头像；结构清楚、比例一致、可用于后续统一；"
            "纯色/干净背景，规整排版，不要分格、不要剧情；"
            "画面本身不要写任何文字、名称、标签或说明，文字由后期排版。"
        )
    if mode.id == "ecommerce":
        return (
            f"生成一张电商产品图，主题：{topic}。"
            "产品主体清晰、材质可信、光影高级；画面上方或下方留出卖点排版空间；"
            "商品必须严格按主题描述，不能替换成风格里的其他食物/道具；"
            "不要分格、不要拼图；画面本身不要写任何文字，文案由后期排版。"
        )
    return f"主题：{topic}。"


_REFERENCE_LOCK = (
    "参考图是角色身份的唯一基准：物种、脸型、五官、发型、发色、服装、配饰"
    "都必须与参考图一致；主题文字只决定用途/氛围，若主题与参考图冲突，"
    "一律以参考图为准；参考图是人时，不要加猫耳、兽耳、尾巴或把人物动物化。"
)

# 没有参考图时，画风很容易把主题替换成风格库里的常见题材（例如把“陶杯”
# 画成广式早茶、把“雨夜书店”画成古风仕女）。这三类用途必须让主题主体优先。
_SUBJECT_LOCK = (
    "主题中的主体、地点、时代与关键道具必须出现在画面里；"
    "画风只决定线条、笔触、材质与配色，不能把主题替换成风格库里的常见题材。"
)

# 信息图底图只允许纸张/墨色/留白；风格库里的“人物、动物、社论角色”不能进底图。
# 旧版只写“只保留纸张质感”，Qwen 仍会在纸上铺一层淡色人物/动物，叠加卡片后
# 变成脏底。这里用正面描述把可见元素压到纸与墨。
_INFO_BG_LOCK = (
    "本图只是信息图底图：画面里没有任何人物、动物、角色、脸、手、身体、图标、"
    "符号或图案；只有米白纸张纤维、淡墨晕染与大面积留白。"
)

_ILLUSTRATION_LOCK = (
    "必须按主题写的时间和地点画：主题写雨夜就画雨夜，写旧书店就画旧书店，"
    "写现代就画现代；不要因为画风而替换成古装、古代室内、历史人物或风格库常见题材。"
)


def _info_style_block(style_id: str, colors: list[str]) -> str:
    """信息图底图专用画风块：只取纸/墨/配色，不取风格里的人与动物。

    完整 style_prompt_block 会带“圆头人物、面无表情、简单动物”等正向词，
    Qwen 会在纸面上铺一层淡色人物/动物，叠加卡片后变成脏底。
    """
    bits = ["信息图底图：米白宣纸/纸张纤维质感，低对比，大面积留白",
            "淡墨晕染与轻微纸纹，出版物排版感"]
    try:
        meta = stylelib.style_meta(style_id)
        name = stylelib.style_name_zh(meta)
        if name:
            bits.append(f"整体气质参考「{name}」，只作用于纸张、笔触与配色")
    except Exception:  # noqa: BLE001
        pass
    names: list[str] = []
    for cid in colors[:2]:
        try:
            m = stylelib.color_meta(cid)
            names.append(str(m.get("name_zh") or m.get("hex") or cid))
        except Exception:  # noqa: BLE001
            continue
    if names:
        bits.append("主色 " + "、".join(names) + "，只用于墨色、线条与小面积点缀")
    return "；".join(bits)


def _accent_hex(colors: list[str]) -> str:
    for cid in colors or []:
        try:
            meta = stylelib.color_meta(cid)
            if meta.get("hex"):
                return str(meta["hex"])
        except Exception:  # noqa: BLE001
            continue
    return "#5b5f73"


def _truthy(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off", "none", "")


def compile_prompt(payload: dict[str, Any],
                   progress: Callable[[str], None] | None = None
                   ) -> tuple[str, str, dict[str, Any]]:
    """先语义编译，再拼最终 prompt / negative / meta。"""
    mode = MODE_BY_ID.get(str(payload.get("mode") or "poster"), MODES[0])
    if not _truthy(payload.get("rewrite"),
                   bool(getattr(config, "STUDIO_PROMPT_REWRITE", True))) \
            or not prompt_rewriter.supported(mode.id):
        return build_prompt(payload)
    style_id = stylelib.normalize_style_id(payload.get("style_id")) \
        or config.HANDRAW_DEFAULT_STYLE
    colors = stylelib.parse_colors(payload.get("color_ids") or payload.get("colors"))
    layout_id = str(payload.get("layout_id") or "").strip().upper()
    if layout_id and not any(x["id"] == layout_id for x in layouts_for(mode.id)):
        layout_id = ""
    if not layout_id and mode.default_layout:
        layout_id = mode.default_layout
    style_hint = prompt_rewriter.style_hint(style_id, colors)
    layout_hint = _layout_prompt(layout_id) if layout_id else ""
    if progress:
        progress("正在把中文主题编译成 Qwen 观察式描述…")
    compiled = prompt_rewriter.compile_studio(
        payload, mode.id, style_hint=style_hint, layout_hint=layout_hint)
    prompt, negative, meta = build_prompt(payload, compiled=compiled)
    meta["rewrite"] = compiled
    meta["prompt_source"] = compiled.get("source") or "local"
    meta["accent_hex"] = _accent_hex(colors)
    return prompt, negative, meta


def preview_prompt(payload: dict[str, Any]) -> dict[str, Any]:
    prompt, negative, meta = compile_prompt(payload)
    return {"prompt": prompt, "negative": negative, "meta": meta}



def build_prompt(payload: dict[str, Any],
                 compiled: dict[str, Any] | None = None
                 ) -> tuple[str, str, dict[str, Any]]:
    mode = MODE_BY_ID.get(str(payload.get("mode") or "poster"), MODES[0])
    topic = str(payload.get("topic") or "").strip()
    title = str(payload.get("title") or "").strip()
    subtitle = str(payload.get("subtitle") or "").strip()
    style_id = stylelib.normalize_style_id(payload.get("style_id")) or config.HANDRAW_DEFAULT_STYLE
    colors = stylelib.parse_colors(payload.get("color_ids") or payload.get("colors"))
    emoji_grid = max(2, min(5, int(payload.get("emoji_grid") or 3)))
    layout_id = str(payload.get("layout_id") or "").strip().upper()
    if layout_id and not any(x["id"] == layout_id for x in layouts_for(mode.id)):
        layout_id = ""
    if not layout_id and mode.default_layout:
        layout_id = mode.default_layout

    # 没有实际参考图时不要写“第1张参考图只用于参考画风”——Qwen 会以为
    # 真有一张图可看，甚至在表情包里补出第二个角色。
    has_ref = bool(str(payload.get("reference_image") or "").strip())
    style_block = stylelib.style_prompt_block(
        style_id, colors, include_reference_note=has_ref, ref_mode="style",
        text=topic, tone=str(payload.get("tone") or "") or None)
    # 风格库里保留“漫画分级特征”这个词，Z-Image 会把它理解成画分镜格；
    # 创作工坊不是漫画，换成中性词并去掉“漫画”。
    style_block = style_block.replace("漫画分级特征", "画面风格特征")
    style_block = style_block.replace("漫画", "插画")
    if mode.id == "infographic":
        style_block = _info_style_block(style_id, colors)
    # 社媒 / 信息图 / IP：所有精确文字都走程序叠加；信息图必须如此，否则模型
    # 会在底图上画错字或把“节点/连线”画成装饰线。IP 设定卡里的名称/标签
    # 由模型直接写会变成伪汉字，因此也改为程序叠加。
    overlay = mode.id in ("social", "infographic", "ip") or (
        bool(payload.get("overlay_text", True)) and mode.id in ("poster", "ecommerce"))
    parts: list[str] = []
    # 信息图不采用 LLM 的 visual_prompt：它会写人物/动物/情节，底图必然脏。
    # 节点/说明仍用 compiled 的 infographic 计划，画面只走“纸 + 墨 + 留白”。
    if (compiled and str(compiled.get("visual_prompt") or "").strip()
            and mode.id != "infographic"):
        visual = str(compiled["visual_prompt"]).strip()
        parts.append(visual)
        style_lock = str(compiled.get("style_lock") or "").strip()
        if style_lock and style_lock not in visual:
            parts.append(style_lock)
        if mode.id == "infographic":
            parts.append("这是一张信息图底图：只保留纸张/水墨质感与大面积留白，"
                         "标题、标签、节点、连线、箭头全部由后期程序排版。")
        if overlay and "没有任何文字" not in visual \
                and "不要出现任何文字" not in visual:
            parts.append("画面中不要出现任何文字、标题、字体、字母、标签、签名、水印或 logo；"
                         "文字全部由后期排版叠加。")
        if not overlay:
            if title:
                parts.append(f'画面文字：主标题必须准确写“{title}”，不要错字、不要乱码。')
            if subtitle:
                parts.append(f'副标题/说明文字：“{subtitle}”，字号小于主标题，排版整齐。')
    else:
        # 主题/媒介先写，画风后写：Qwen 对前置语义更敏感。旧顺序先铺画风，
        # “雨夜旧书店”会被 FE-034 的“古典服饰/古风场景”先验覆盖成古装仕女。
        parts.append(_mode_instruction(mode, topic, title, subtitle, emoji_grid))
        parts.append(style_block)
        if layout_id and mode.id not in ("ip", "infographic"):
            # IP-001 是“品牌定位卡”，内含大量名称/标签文案；直接送进 T2I
            # 会生成伪汉字。IP 改为固定角色设定版式 + 程序标题，见 _mode_instruction。
            # 信息图版式（IG-*）描述的是“金字塔/图标/标签”等成品结构，
            # 送进 T2I 会让底图长出一堆图标和人物；结构由 infographic.compose 画。
            lp = _layout_prompt(layout_id)
            if lp:
                parts.append("版式要求：" + lp)
        if overlay:
            if mode.id == "infographic":
                parts.append("画面只保留纸张底图与留白，画面中不要出现任何文字、标题、"
                             "字体、字母、图标、标签、签名、水印、logo；"
                             "节点与文字全部由后期排版叠加。")
            else:
                parts.append("画面只保留插画主体，画面中不要出现任何文字、标题、"
                             "字体、字母、签名、水印、logo；文字全部由后期排版叠加。")
        else:
            if title:
                parts.append(f'画面文字：主标题必须准确写“{title}”，不要错字、不要乱码。')
            if subtitle:
                parts.append(f'副标题/说明文字：“{subtitle}”，字号小于主标题，排版整齐。')
    if mode.id == "infographic":
        parts.append(_INFO_BG_LOCK)
    if mode.id == "illustration":
        parts.append(_ILLUSTRATION_LOCK)
    if mode.id in ("illustration", "ecommerce", "ip"):
        parts.append(_SUBJECT_LOCK)
    if payload.get("extra"):
        parts.append(str(payload["extra"]))
    # 有参考图时，主题只作为用途/氛围，不能把参考图角色替换成主题里的物种。
    _ref_path = str(payload.get("reference_image") or "").strip()
    if _ref_path and mode.id != "infographic" \
            and str(payload.get("reference_mode") or "identity") != "style":
        parts.append(_REFERENCE_LOCK)
    prompt = "；".join(x.strip("；。 ") for x in parts if x and x.strip()) + "。"

    negative = (
        "低质量, 模糊, 噪点, 变形, 多余手指, 水印, 签名, 乱码文字, 错字, 低分辨率, "
        "照片, 3D渲染, 塑料皮肤, 过度磨皮, 画面脏乱"
    )
    if overlay:
        negative += ", 文字, 字体, 字母, 标题, 排版, 书法字, 印章, 字母表"
    if mode.id in ("poster", "social", "infographic", "illustration", "ip", "ecommerce"):
        negative += ", 多格漫画, 漫画分镜, 连环画, 分格线, 画中画"
    if mode.id == "infographic":
        negative += ", 人物, 动物, 角色, 人脸, 手, 图标, 图案, 多个画面, 涂鸦人物"
    if mode.id == "emoji":
        # 旧负向里写了“单个人物 / 只有一张脸”，等于要求模型“别只画一个人”，
        # 实测每格会补出第二个角色。这里改为禁止“每格多人/重复同一姿势”。
        negative += (", 每格两个角色, 每格多个人物, 九宫格重复, 同一姿势重复, "
                     "角色复制, 连续剧情, 分镜框")
    # rewrite=off 时 compiled 为空；信息图/社媒仍需要一份本地排版计划，
    # 否则程序只会画出标题，节点/要点全空。这里用本地兜底补齐。
    overlay_plan = dict(compiled or {})
    if not overlay_plan and mode.id in ("social", "infographic"):
        try:
            overlay_plan = prompt_rewriter._fallback(payload, mode.id, "")
        except Exception:  # noqa: BLE001
            overlay_plan = {}
    meta = {"mode": mode.id, "mode_name": mode.name, "style_id": style_id,
            "color_ids": colors, "layout_id": layout_id,
            "title": title, "subtitle": subtitle,
            "size": list(mode.size), "topic": topic, "overlay_text": overlay,
            "emoji_grid": emoji_grid,
            "overlay_plan": overlay_plan,
            "infographic_plan": overlay_plan.get("infographic")}
    return prompt, negative, meta


def _overlay_text(image, title: str, subtitle: str, mode: str):
    """把中文标题叠在画面上，避免模型把“秋分”写成“秋分分”或重复。"""
    if not (title or subtitle):
        return image
    from PIL import Image, ImageDraw
    im = image.convert("RGB")
    w, h = im.size
    draw = ImageDraw.Draw(im, "RGBA")
    title_size = max(36, int(w * 0.085))
    sub_size = max(20, int(w * 0.034))
    margin = int(w * 0.06)
    # 顶部读色，决定字色与底板
    top = im.crop((0, 0, w, int(h * 0.25))).resize((1, 1)).getpixel((0, 0))
    dark = sum(top[:3]) / 3 < 128
    ink = (250, 246, 236, 255) if dark else (24, 22, 20, 255)
    f1 = load_font(title_size, bold=True, kind="title")
    f2 = load_font(sub_size, bold=False, kind="body")
    # 标题块背景
    tb = draw.textbbox((0, 0), title, font=f1)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pad = int(title_size * 0.35)
    box_h = pad * 2 + th + (int(sub_size * 1.6) if subtitle else 0)
    if mode in ("poster", "social", "infographic"):
        # 模型仍会在标题区外写错字；把顶部 30% 连同错字一起裁掉，
        # 重新排成“干净文字区 + 主图”的海报，中文标题由 PIL 统一写。
        from PIL import Image, ImageOps
        band_h = margin * 2 + box_h
        cut = int(h * 0.30)
        content = im.crop((0, cut, w, int(h * 0.88)))
        canvas = Image.new("RGB", (w, h), (250, 247, 240))
        target_h = h - band_h
        content = ImageOps.fit(content, (w, target_h), method=Image.LANCZOS, centering=(0.5, 0.35))
        canvas.paste(content, (0, band_h))
        im = canvas
        draw = ImageDraw.Draw(im, "RGBA")
        draw.line((margin, band_h - 4, w - margin, band_h - 4), fill=(30, 28, 26, 255), width=3)
        ink = (24, 22, 20, 255)
    y = margin + pad
    draw.text(((w - tw) / 2, y), title, font=f1, fill=ink)
    if subtitle:
        sb = draw.textbbox((0, 0), subtitle, font=f2)
        sw = sb[2] - sb[0]
        draw.text(((w - sw) / 2, y + th + int(title_size * 0.25)), subtitle, font=f2, fill=ink)
    return im


def _apply_overlay(image, meta: dict[str, Any]):
    """按模式选择后处理：信息图程序排版 / 社媒图文面板 / 海报旧叠字。"""
    mode = str(meta.get("mode") or "")
    plan = meta.get("rewrite") or meta.get("overlay_plan") or {}
    accent = str(meta.get("accent_hex") or _accent_hex(meta.get("color_ids") or []))
    title = str(meta.get("title") or "")
    subtitle = str(meta.get("subtitle") or "")
    if mode == "infographic":
        info = plan.get("infographic") or meta.get("infographic_plan") or {}
        return infographic_mod.compose(
            image, info, title=title, subtitle=subtitle, accent=accent)
    if mode == "social":
        return infographic_mod.compose_social(
            image, title=title, subtitle=subtitle,
            key_points=list(plan.get("key_points") or []), accent=accent)
    if meta.get("overlay_text"):
        return _overlay_text(image, title, subtitle, mode)
    return image


def _draw_sticker_text(path: str, label: str) -> str:
    """把短句画到单格透明贴纸上，和图里位置一致。"""
    if not label:
        return path
    try:
        from PIL import Image, ImageDraw
        from .bubble import load_font
        im = Image.open(path).convert("RGBA")
        W, H = im.size
        size = max(28, int(W * 0.12))
        while size > 16:
            font = load_font(size, bold=True, kind="title")
            probe = ImageDraw.Draw(im)
            bb = probe.textbbox((0, 0), label, font=font,
                                stroke_width=max(2, int(size * 0.08)))
            if bb[2] - bb[0] <= W * 0.90:
                break
            size -= 4
        font = load_font(size, bold=True, kind="title")
        draw = ImageDraw.Draw(im)
        stroke = max(3, int(size * 0.10))
        bb = draw.textbbox((0, 0), label, font=font, stroke_width=stroke)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        x = (W - tw) // 2 - bb[0]
        y = H - th - int(H * 0.06) - bb[1]
        draw.text((x, y), label, font=font, fill=(28, 26, 24, 255),
                  stroke_width=stroke, stroke_fill=(255, 255, 255, 255))
        im.save(path, format="PNG")
    except Exception:  # noqa: BLE001
        pass
    return path


def _generate_emoji_reference_grid(reference: str, grid: int, style_id: str,
                                  colors: list[str], topic: str,
                                  progress: Callable[[str], None] | None = None,
                                  texts: list[str] | None = None,
                                  qwen: QwenEditClient | None = None,
                                  compose_texts: list[str] | None = None,
                                  seed: int = 888001,
                                  identity_hint: str = "",
                                  style_hint: str = "",
                                  denoise: float = 0.6,
                                  write_text: bool = True,
                                  cell_dir: str = ""):
    """逐个表情生成，再拼成网格；有 Qwen 时优先用 Qwen Edit 保角色。

    cell_dir 非空时，每一格还会落盘一份原图与一份去背景透明 PNG，
    供「导出单格透明 PNG」打包下载。
    """
    from .imageclient import ImageClient as _Client
    # Qwen 用头肩特写当参考，避免整张参考图的站姿被复制到每一格；
    # Z-Image img2img 仍用整张母本，保证画风/服装。
    face = _emoji_face_ref(reference) if qwen is not None else reference
    n = grid * grid
    expressions = (_EMOJI_EXPRESSIONS * 3)[:n]
    actions = (_EMOJI_ACTIONS * 3)[:n]
    imgs = []
    stickers: list[str] = []
    for i, (expr, action) in enumerate(zip(expressions, actions)):
        if progress:
            progress(f"正在画表情 {i + 1}/{n}：{expr} / {action}…")
        label = (texts[i].strip() if texts and i < len(texts) else "")
        if not write_text:
            text_bit = "画面里不要写任何文字、字母、拟声词或签名；"
        elif label:
            text_bit = (f"在画面旁边用手写黑色中文写“{label}”，"
                        "并加与情绪匹配的小爱心/火焰/汗滴/速度线等手绘特效，文字必须清晰可读；")
        else:
            text_bit = ("在画面旁边自己写一句 2–6 个字的自然中文反应词或拟声词，"
                        "每一格用不同的词，不要固定问候语，"
                        "并加与情绪匹配的小爱心/火焰/汗滴/速度线等手绘特效；"
                        "文字必须清晰可读；")
        prompt = (
            "同一角色，Q版 chibi 表情包贴纸，半身或全身（按动作需要），白色背景，"
            "粗描边，水彩马克笔质感；画面里只有一个角色、一个头、一个身体；"
            f"表情：{expr}；动作/姿势：{action}；"
            "严格保留脸型、五官、眼睛、发型、发色、服装、配饰；"
            "每一格的动作必须不同，不要照搬参考图的站姿，不要所有格都用同一个姿势；"
            f"角色主题：{topic or '参考图角色'}；身份特征：{identity_hint or '严格按参考图'}；"
            f"{_REFERENCE_LOCK}；"
            f"画风：{style_hint or '按参考图'}；"
            f"{text_bit}"
            "不要水印、不要分镜框、不要写实照片、不要场景。"
        )
        neg = ("低质量, 模糊, 变形, 多余手指, 换脸, 换人, 不同角色, "
               "发型变化, 服装变化, 水印, 签名, 多格, 拼图, "
               "同一姿势, 复制参考图姿势, 僵硬站姿, 场景, 阴影背景, "
               "双头, 两个人, 重复人脸, 头部复制, 小脸, 分身, "
               "两个身体, 双身体, 人物重叠, 克隆, 复制人物, 多余身体")
        if not write_text:
            neg += ", 文字, 字母, 汉字, 拟声词"
        cell_seed = int(seed) + i * 97
        if qwen is not None:
            img = qwen.generate(prompt, face, negative=neg, size=512,
                                seed=cell_seed, steps=8, denoise=denoise)
        else:
            c = _Client(workflow="image_z_image_turbo_int8_studio_emoji58")
            img = c.generate(prompt, reference_paths=[face], negative_prompt=neg,
                             size=(512, 512), seed=cell_seed, use_cache=False).image
        imgs.append(img)
        if cell_dir:
            try:
                from PIL import Image as _Img
                from . import bgremove
                d = Path(cell_dir)
                d.mkdir(parents=True, exist_ok=True)
                raw = d / f"cell_{i + 1:02d}.png"
                img.save(raw, format="PNG")
                sticker = d / f"sticker_{i + 1:02d}.png"
                made = bgremove.remove_background(raw, sticker, mode="transparent")
                if made and Path(made).exists():
                    probe = _Img.open(made)
                    if probe.mode in ("RGBA", "LA"):
                        label = (compose_texts[i].strip()
                                 if compose_texts and i < len(compose_texts) else "")
                        if label:
                            _draw_sticker_text(made, label)
                        stickers.append(str(made))
            except Exception:  # noqa: BLE001
                pass
    return _compose_emoji_grid(imgs, grid, compose_texts), face, stickers

STUDIO_CACHE_VERSION = "20261104-emoji-base-v4"


def _cache_key_for(payload: dict[str, Any], reference: str,
                   seed: int | None = None) -> str:
    import hashlib, json as _json
    try:
        ref_hash = hashlib.sha1(Path(reference).read_bytes()).hexdigest()
    except Exception:
        ref_hash = reference
    raw = _json.dumps({
        "v": STUDIO_CACHE_VERSION,
        "ref": ref_hash, "mode": payload.get("mode"), "style": payload.get("style_id"),
        "colors": payload.get("color_ids") or [], "grid": payload.get("emoji_grid") or 3,
        "texts": payload.get("emoji_texts") or [], "text_mode": payload.get("emoji_text_mode") or "model",
        "topic": payload.get("topic") or "", "title": payload.get("title") or "",
        "seed": int(seed if seed is not None else (payload.get("seed") or 888001)),
        "ref_mode": payload.get("reference_mode") or "identity",
        "overlay": bool(payload.get("overlay_text")),
        "rewrite": bool(payload.get("rewrite", True)),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _find_cached(key: str) -> dict[str, Any] | None:
    root = Path(config.OUTPUT_ROOT) / "创作工坊"
    if not root.exists():
        return None
    for p in sorted(root.glob("*/meta.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("cache_key") == key and d.get("image") and Path(d["image"]).exists():
                d["cache_hit"] = True
                return d
        except Exception:
            continue
    return None


def generate(payload: dict[str, Any], progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    if progress:
        progress("正在准备提示词…")
    mode_id = str(payload.get("mode") or "poster")
    rewrite_on = _truthy(payload.get("rewrite"),
                         bool(getattr(config, "STUDIO_PROMPT_REWRITE", True))) \
        and prompt_rewriter.supported(mode_id)
    if rewrite_on:
        prompt, negative, meta = compile_prompt(payload, progress)
    else:
        prompt, negative, meta = build_prompt(payload)
    # 默认固定种子：同一张参考图 + 同一组参数，表情/文字结果稳定。
    # 想换一批时，改 seed 或打开“每次随机”。
    seed = int(payload.get("seed") or 0)
    if payload.get("random_seed"):
        seed = random.randint(1, 2 ** 31 - 1)
    if seed <= 0:
        seed = 888001
    style_id = meta["style_id"]
    # 参考图优先：有参考图就走 img2img，保留主体结构后重画；
    # 没有参考图才走“风格参考图 + 文生图”。
    reference = str(payload.get("reference_image") or "").strip()
    use_reference = bool(reference and Path(reference).exists())
    if meta["mode"] == "infographic" and use_reference:
        # 信息图只需要参考图的配色/画风，不能把参考图人物/构图直接编辑进底图。
        if progress:
            progress("信息图：参考图仅用于配色，底图按主题重新生成…")
        reference = ""
        use_reference = False
    _cache_key = _cache_key_for(payload, reference, seed=seed)
    if _cache_key and not payload.get("force"):
        _hit = _find_cached(_cache_key)
        if _hit:
            return _hit
    # 表情包没有参考图：先生成一张单人 chibi 母本，再逐格 Edit。
    # 旧路径让 Qwen 一次直出 3×3，实测九格是同一张站姿，且每格多一个角色。
    if meta["mode"] == "emoji" and not use_reference:
        if progress:
            progress("表情包：先生成同一角色的 chibi 母本…")
        try:
            reference = _generate_emoji_topic_base(
                meta.get("topic") or "一个可爱的小角色",
                style_id, meta.get("color_ids") or [], seed=seed)
            use_reference = True
            meta["emoji_base_auto"] = True
            meta["reference_mode"] = "qwen_two_stage_auto"
        except Exception as exc:  # noqa: BLE001
            meta["emoji_base_error"] = str(exc)[:300]
            if progress:
                progress("母本生成失败，回退到单张九宫格…")
    # 表情包 + 参考图：优先 Qwen Image Edit（真正的参考图编辑），
    # 先锁角色做 chibi 底图，再逐表情生成，不再让 Z-Image 猜。
    if use_reference and meta["mode"] == "emoji":
        grid = int(meta.get("emoji_grid") or 3)
        _text_mode = str(payload.get("emoji_text_mode") or "model")
        _raw_texts = payload.get("emoji_texts")
        _texts = [str(x).strip() for x in (_raw_texts or [])
                  if str(x).strip()][:grid * grid]
        if _text_mode == "model":
            # 不再让 Qwen 直接写中文（会糊成错字）：从短句池随机挑，
            # 再由程序叠加，文字清晰且每次可以不同。
            if not _texts:
                pool = (_EMOJI_PHRASES * 3)[:grid * grid]
                _texts = [random.choice(pool[i]) for i in range(grid * grid)]
            _prompt_texts = []
            _compose_texts = _texts
            _write_text = False
        elif _text_mode == "overlay":
            if not _texts:
                _texts = DEFAULT_EMOJI_TEXTS[:grid * grid]
            _prompt_texts = []
            _compose_texts = _texts
            _write_text = False
        else:
            _prompt_texts = []
            _compose_texts = []
            _write_text = False
        if progress:
            progress("Qwen Edit：先锁定参考图角色，生成 chibi 底图…")
        # 参考图内容参与 seed：换图必换 seed，避免 ComfyUI 命中旧输出。
        import hashlib
        _ref_seed = seed
        qwen = None
        chibi = ""
        _pal = _ref_palette_hint(reference)
        try:
            _sm = stylelib.style_meta(style_id)
            _style_hint = "；".join(x for x in [
                stylelib.style_name_zh(_sm), stylelib.style_medium(_sm),
                stylelib.positive_traits(_sm.get("traits") or "", 80)] if x)
        except Exception:
            _style_hint = ""
        _ref_mode = str(payload.get("reference_mode") or "").strip().lower()
        if not _ref_mode:
            _ref_mode = "identity" if bool(payload.get("identity_first", True)) else "chibi"
        _identity_first = _ref_mode == "identity"
        # 动作要变，denoise 不能太低：0.75 时 Qwen Edit 会基本照着参考图站姿不动。
        _denoise = 0.82
        if _identity_first:
            # 参考图本身是 Q 版/立绘时，直接当母本；低去噪只改表情，
            # 不再先用高去噪“重画一个角色”。
            qwen = QwenEditClient()
            chibi = reference
            # 母本先去背景，避免每格都把原图背景当成角色特征
            try:
                import uuid as _uuid
                from . import bgremove
                cut = (Path(config.OUTPUT_ROOT) / "创作工坊" / "refs"
                       / f"identity_cut_{_uuid.uuid4().hex[:8]}.png")
                cut.parent.mkdir(parents=True, exist_ok=True)
                made = bgremove.remove_background(reference, cut, mode="white")
                if made and Path(made).exists():
                    chibi = made
            except Exception:
                chibi = reference
            meta["identity_mode"] = "img2img"
            meta["reference_mode"] = _ref_mode
            try:
                meta["identity_ref"] = str(Path(reference).resolve())
            except Exception:
                pass
        if not _identity_first:
            try:
                qwen = QwenEditClient()
                base_prompt = (
                    "把参考图角色转成 Q版 chibi 表情包角色，2.5头身，纯白背景，粗描边，"
                    "水彩马克笔质感；严格保留参考图的脸型、发型、发色、服装、配饰；"
                    f"角色主题/特征：{meta.get('topic') or '参考图角色'}；参考图主色：{_pal or '按参考图'}；"
                    "正面站姿，角色完整，贴纸感；不要文字、不要水印、不要场景。"
                )
                qimg = qwen.generate(base_prompt, reference,
                                     negative="写实,3D,照片,文字,水印,场景,多个人物",
                                     size=768, seed=_ref_seed, steps=8)
                out_dir = Path(config.OUTPUT_ROOT) / "创作工坊" / time.strftime("%Y%m%d_%H%M%S")
                out_dir.mkdir(parents=True, exist_ok=True)
                chibi = out_dir / "qwen_chibi.png"
                qimg.save(chibi, format="PNG")
                # 生成出来的 chibi 母本也去背景，后续每格只读角色
                try:
                    from . import bgremove
                    nobg = out_dir / "qwen_chibi_nobg.png"
                    made = bgremove.remove_background(chibi, nobg, mode="white")
                    if made and Path(made).exists():
                        chibi = made
                except Exception:
                    pass
            except Exception as exc:  # noqa: BLE001
                qwen = None
                meta["qwen_error"] = str(exc)[:300]
                chibi = _make_chibi_base(reference)
                chibi = _white_outline_bg(chibi)
        if progress:
            progress(f"Qwen Edit：逐个生成 {grid}×{grid} 格表情…")
        out_dir = Path(config.OUTPUT_ROOT) / "创作工坊" / time.strftime("%Y%m%d_%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        cell_dir = out_dir / "stickers"
        img, face, stickers = _generate_emoji_reference_grid(
            str(chibi), grid, style_id, meta.get("color_ids") or [],
            meta.get("topic") or "", progress, texts=_prompt_texts, qwen=qwen,
            compose_texts=_compose_texts, seed=_ref_seed, identity_hint=_pal,
            denoise=_denoise, style_hint=_style_hint, write_text=_write_text,
            cell_dir=str(cell_dir))
        image_path = out_dir / f"emoji_{style_id}_{seed}.png"
        img.save(image_path, format="PNG")
        meta.update({"image": str(image_path), "seed": seed, "prompt": prompt,
                     "negative": negative, "reference_mode": "qwen_two_stage",
                     "cache_key": _cache_key,
                     "chibi_base": str(chibi), "reference_image": str(chibi),
                     "stickers": stickers, "sticker_dir": str(cell_dir),
                     "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "elapsed": 0})
        (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        if progress:
            progress("完成")
        return meta
    # 非表情包的参考图：也走 Qwen Edit；第 1 张角色，第 2 张画风。
    if use_reference:
        try:
            from .qwenedit import QwenEditClient as _Q
            sty = ""
            try:
                sty = stylelib.style_reference_png(style_id)
            except Exception:
                sty = ""
            q = _Q()
            ref_list = [reference]
            if bool(payload.get("use_style_ref", False)) and sty and Path(sty).exists():
                ref_list.append(sty)
            if progress:
                progress("Qwen Edit：参考图 + 画风…")
            _gmode = str(payload.get("reference_mode") or "identity").lower()
            _gdenoise = 0.75 if _gmode == "identity" else 1.0
            # 必须传 (w,h)，不能传 max(w,h) 的 int：
            # int 会被 Qwen Edit 当成“只给分辨率”，输出跟随参考图比例，
            # 海报/电商图会被参考图拉成方图或竖图。
            qimg = q.generate(prompt, ref_list, negative=negative,
                              size=tuple(meta["size"]), seed=seed, steps=8,
                              denoise=_gdenoise)
            out_dir = Path(config.OUTPUT_ROOT) / "创作工坊" / time.strftime("%Y%m%d_%H%M%S")
            out_dir.mkdir(parents=True, exist_ok=True)
            image_path = out_dir / f"{meta['mode']}_{style_id}_{seed}.png"
            if meta.get("overlay_text") or meta["mode"] in ("infographic", "social"):
                qimg = _apply_overlay(qimg, meta)
            qimg.save(image_path, format="PNG")
            meta.update({"image": str(image_path), "seed": seed, "prompt": prompt,
                         "negative": negative, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                         "cache_key": _cache_key, "reference_mode": "qwen_reference_" + _gmode})
            (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            (out_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
            if progress:
                progress("完成")
            return meta
        except Exception as exc:  # noqa: BLE001
            meta["qwen_error"] = str(exc)[:300]
            if progress:
                progress("Qwen 不可用，回退 Z-Image…")
    refs: list[str] = []
    if use_reference:
        if meta["mode"] == "emoji":
            reference = _emoji_reference_sheet(reference, int(meta.get("emoji_grid") or 3))
            meta["reference_mode"] = "img2img_sheet"
        else:
            meta["reference_mode"] = "img2img"
        refs = [reference]
        client = ImageClient(workflow="image_z_image_turbo_int8_style_img2img")
        meta["reference_image"] = reference
    else:
        if str(getattr(config, "IMAGE_FAMILY", "zimage")).lower() == "qwen":
            try:
                from .qwenedit import QwenEditClient as _Q
                q = _Q()
                qimg = q.generate_t2i(prompt, negative=negative,
                                      size=tuple(meta["size"]), seed=seed, steps=8)
                out_dir = Path(config.OUTPUT_ROOT) / "创作工坊" / time.strftime("%Y%m%d_%H%M%S")
                out_dir.mkdir(parents=True, exist_ok=True)
                image_path = out_dir / f"{meta['mode']}_{style_id}_{seed}.png"
                if meta.get("overlay_text") or meta["mode"] in ("infographic", "social"):
                    qimg = _apply_overlay(qimg, meta)
                qimg.save(image_path, format="PNG")
                meta.update({"image": str(image_path), "seed": seed, "prompt": prompt,
                             "negative": negative, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                             "cache_key": _cache_key, "reference_mode": "qwen_t2i"})
                (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                (out_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
                if progress:
                    progress("完成")
                return meta
            except Exception:
                pass
        client = ImageClient()
        try:
            ref = stylelib.style_reference_png(style_id)
            if ref and Path(ref).exists():
                refs = [ref]
        except Exception:
            refs = []
    if progress:
        progress(f"正在出图（{meta['mode_name']} · {style_id}"
                 + (" · 参考图" if use_reference else "") + "）…")
    t0 = time.time()
    res = client.generate(
        prompt, reference_paths=refs, negative_prompt=negative,
        size=tuple(meta["size"]), seed=seed,
        color_transfer_strength=float(payload.get("color_strength") or 0.25),
    )
    out_dir = Path(config.OUTPUT_ROOT) / "创作工坊" / time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{meta['mode']}_{style_id}_{seed}.png"
    image_path = out_dir / name
    out_img = res.image
    if meta.get("overlay_text") or meta["mode"] in ("infographic", "social"):
        try:
            out_img = _apply_overlay(out_img, meta)
        except Exception:
            pass
    out_img.save(image_path, format="PNG")
    meta.update({
        "image": str(image_path), "seed": seed, "prompt": prompt,
        "negative": negative, "elapsed": round(time.time() - t0, 1),
        "cache_key": _cache_key,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    if progress:
        progress("完成")
    return meta


def history(limit: int = 30) -> list[dict[str, Any]]:
    root = Path(config.OUTPUT_ROOT) / "创作工坊"
    if not root.exists():
        return []
    rows = []
    for p in sorted(root.glob("*/meta.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("image") and Path(d["image"]).exists():
                rows.append(d)
        except Exception:
            continue
    return rows
