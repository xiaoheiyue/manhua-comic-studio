# -*- coding: utf-8 -*-
"""规则加载器：把 ``rules/`` 目录里的机器可读规则交给业务代码。

设计原则
--------
1. **单一来源**：规则内容只放在 ``rules/data/*.json``，代码里只保留"怎么执行"。
2. **缺省可用**：文件缺失/损坏时回退到内置缺省值，绝不让整条流水线崩在"规则文件读不到"。
3. **热更新**：按文件 mtime 缓存，改完 JSON 下次读取即生效，不用重启服务。
4. **只读**：本模块不会写 ``rules/``，也不会把规则复制到 outputs。

使用示例
--------
>>> from comicgen import rules
>>> rules.card_max_chars()
88
>>> rules.subject("count_one")
'画面里有且只有 1 个人'
>>> rules.trait_patterns()["hair"][:3]
['长直发', '黑长直', '及腰长发']
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import config
from .logger import get_logger

log = get_logger(__name__)

RULES_DIR: Path = Path(getattr(config, "RULES_DIR", Path(config.BASE_DIR) / "rules"))
DATA_DIR: Path = RULES_DIR / "data"

# ---------------------------------------------------------------------------
# 内置缺省值（只保留"没有它就跑不动"的键；详细规则见 rules/*.md）
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, dict[str, Any]] = {
    "identity_rules": {
        "version": "1.0.0",
        "card": {
            "max_chars": 88,
            "max_keywords": 8,
            "separator": "、",
            "fields": ["hair", "face", "eyes", "outfit", "signature"],
            "template": "{name}：{gender}{age}，{traits}",
            "require_identity_tag": True,
        },
        "collision": {
            "compare_buckets": ["face", "eyes", "hair", "outfit"],
            "max_shared_buckets": 2,
            "design_similarity_limit": 0.62,
            "ignore_terms": ["冷白肤色", "白皙", "低饱和"],
        },
        "trait_patterns": {
            "face": ["鹅蛋脸", "圆脸", "方脸", "长方脸", "瘦长脸", "瓜子脸", "菱形脸", "窄长脸"],
            "eyes": ["单眼皮", "双眼皮", "内双", "杏眼", "圆眼", "细长眼", "下垂眼", "上挑"],
            "hair": ["长直发", "长发", "短发", "低马尾", "高马尾", "马尾", "卷发", "齐肩",
                     "及腰", "束发", "三七分", "偏分", "中分", "齐刘海", "花白", "银发",
                     "黑发", "棕发"],
            "outfit": ["风衣", "夹克", "衬衫", "毛衣", "西装", "制服", "棉袄", "工装",
                       "大衣", "针织衫", "T恤", "卫衣", "长裤", "裙", "围裙", "背心"],
            "build": ["瘦", "微胖", "清瘦", "高挑", "佝偻", "精瘦", "纤细", "宽厚"],
        },
        "color_words": ["黑色", "白色", "灰色", "深灰", "浅灰", "蓝色", "藏青", "深蓝",
                        "炭黑", "米色", "驼色", "棕色", "暗红", "红色", "绿色", "银色",
                        "金色", "墨绿", "雾蓝"],
        "differentiation_pool": {
            "accessory": ["右腕一串深色木珠", "领口别一枚铜质小徽章", "肩上斜挎一台旧相机",
                          "腰间挂一串旧钥匙", "耳后夹一支铅笔", "手里攥着一只旧打火机"],
            "hair_accent": ["侧边别一枚木簪", "用深色发绳扎起一束", "发尾编成细辫"],
            "outfit_accent": ["肩上搭一件米白薄开衫", "围一条暗酒红围巾",
                              "外套一件浅卡其马甲", "披一件雾灰短披肩"],
            "eyewear": ["细金属框方眼镜", "无框方片眼镜", "圆框金丝眼镜"],
            "build": ["清瘦", "高挑", "微胖", "精瘦", "纤细", "肩背宽厚"],
        },
        "identity_prompt": {
            "require_unique_tag": True,
            "require_cross_character_difference": True,
            "min_distinct_buckets": 2,
        },
    },
    "shot_prompt_rules": {
        "version": "1.0.0",
        "max_chars": 760,
        "style_detail_max_chars": 40,
        "order": ["style_anchor", "style", "count", "cast", "isolation", "speaker",
                  "lens", "emphasis", "vertical", "action", "expression", "focus",
                  "content", "scene", "time", "mood", "bubble", "tail"],
        "drop_order": ["style_detail", "focus", "scene", "expression",
                       "vertical", "mood", "time"],
        "subjects": {
            "count_one": "画面里只有一位人物，画面里出现的其他东西都是环境与道具",
            "count_two": "画面里只有两位人物，画面里出现的其他东西都是环境与道具",
            "count_many": "画面里只有{n}位人物，画面里出现的其他东西都是环境与道具",
            "count_zero": "画面里只有物件与它所在的空间",
            "cast": "{side}侧是{name}：{card}",
            "cast_no_side": "画面里是{name}：{card}",
            "speaker": "这一格里说话的是{speaker}，只有{speaker}张嘴；{others}闭着嘴，看向{speaker}",
            "speaker_solo": "这一格里说话的是{speaker}，嘴微微张开",
            "difference": "两张脸各自独立，脸型、眼型、发型与服装轮廓互不相同",
            "gaze": "{name}看向画面{dir}",
            "object_subject": ("画面主体是手机、屏幕、桌面、纸张、墙面、U盘这类物件，"
                               "以及它们所在的空间；屏幕与纸张上的内容用抽象光晕、图标、色块表示"),
            "tail": "这一张图是一个完整画面：一个镜头、一层景、一个瞬间",
        },
        "lens": {},
        "forbidden": {
            "words": ["不要", "禁止", "分镜", "一格", "漫画格", "拼贴", "写实", "照片", "3D"],
            "replace": {"写实": "插画", "照片": "插画", "3D": "2D手绘",
                        "分镜": "画面", "一格": "画面", "漫画格": "画面"},
        },
        "bubble": {"require_only_when_dialogue": True, "keep_top_clear_ratio": 0.08,
                   "anchor_to_speaker": True},
    },
    "storyboard_rules": {
        "version": "1.0.0",
        "description": {
            "min_chars": 18,
            "target_min_chars": 28,
            "max_chars": 110,
            "camera_words": ["平视", "俯视", "仰视", "斜侧", "过肩", "主观", "背影",
                             "远景", "全景", "中景", "近景", "特写", "俯拍", "仰拍"],
            "meta_patterns": ["作为视觉主体", "出现在画面中", "（对话密集", "（有一句台词",
                              "（无台词", "（人物反应", "（说话人近景", "（听者反应近景"],
            "forbidden_phrases": ["她想", "他想", "她觉得", "他觉得", "仿佛", "好像",
                                  "像是", "心里", "心情", "感觉", "似乎"],
            "slots": ["谁", "在哪", "做什么", "视线表情", "前后景", "光"],
        },
        "cast_policy": {
            "max_characters": 4,
            "dialogue_prefer_solo": True,
            "two_shot_types": ["远景", "全景", "中景"],
            "three_shot_types": ["远景", "全景", "中景"],
            "two_shot_max_speech": 1,
            "multi_shot_max_speech": 1,
            "forbid_three_or_more": False,
            "split_above": 4,
            "detail_shot_character_policy": "remove",
        },
        "dialogue": {
            "max_per_panel": 3,
            "max_chars": 20,
            "narration_max_chars": 24,
            "sfx_max_chars": 6,
            "empty_speaker_policy": "offscreen",
            "speaker_must_be_visible_or_offscreen": True,
        },
        "bubble": {
            "slot_side_must_match": True,
            "max_speech_per_panel": 3,
            "anchor_y_by_type": {"特写": 0.30, "近景": 0.28, "中景": 0.30,
                                 "全景": 0.32, "远景": 0.34},
            "anchor_x_by_side": {"左": 0.28, "右": 0.72, "中": 0.50},
            "tail_none_for": ["narration", "sfx", "offscreen"],
            "reading_order": ["top", "mid", "bottom"],
        },
        "validation": {
            "max_same_shot_run": 2,
            "max_same_camera_run": 2,
            "closeup_min_ratio": 0.24,
            "silent_ratio_min": 0.12,
            "silent_ratio_max": 0.40,
            "duplicate_similarity": 0.88,
            "max_dialogue_run": 3,
        },
        "two_shot_policy": {
            "enabled": True,
            "split_on_speech_over": 1,
            "keep_shot_types": ["远景", "全景", "中景"],
        },
        "multi_shot_policy": {
            "enabled": True,
            "max_characters": 4,
            "max_speech": 1,
            "keep_shot_types": ["远景", "全景", "中景"],
        },
    },
}

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------

def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """把用户 JSON 合并到缺省值上（dict 递归，list/标量直接覆盖）。"""
    out = dict(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load(name: str) -> dict[str, Any]:
    """读取 ``rules/data/<name>.json``（带 mtime 缓存与缺省回退）。"""
    name = (name or "").removesuffix(".json")
    path = DATA_DIR / f"{name}.json"
    default = _DEFAULTS.get(name, {})
    try:
        mtime = path.stat().st_mtime
    except OSError:
        if name not in _CACHE:
            _CACHE[name] = (0.0, default)
        return _CACHE[name][1]
    cached = _CACHE.get(name)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("规则文件顶层必须是对象")
        data = _deep_merge(default, raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("规则文件 %s 读取失败，使用内置缺省值：%s", path.name, exc)
        data = default
    _CACHE[name] = (mtime, data)
    return data


def identity() -> dict[str, Any]:
    return load("identity_rules")


def shot_prompt() -> dict[str, Any]:
    return load("shot_prompt_rules")


def storyboard() -> dict[str, Any]:
    return load("storyboard_rules")


# ---------------------------------------------------------------------------
# 便捷读取
# ---------------------------------------------------------------------------

def get(path: str, default: Any = None, source: str = "") -> Any:
    """按 ``a.b.c`` 取值；``source`` 为空时依次在三个规则文件里找。"""
    sources = [source] if source else ["identity_rules", "shot_prompt_rules", "storyboard_rules"]
    for src in sources:
        node: Any = load(src)
        ok = True
        for part in (path or "").split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                ok = False
                break
        if ok:
            return node
    return default


def card_rules() -> dict[str, Any]:
    return identity().get("card", {})


def card_max_chars() -> int:
    try:
        return int(card_rules().get("max_chars", 88))
    except (TypeError, ValueError):
        return 88


def max_keywords() -> int:
    try:
        return int(card_rules().get("max_keywords", 8))
    except (TypeError, ValueError):
        return 8


def collision_rules() -> dict[str, Any]:
    return identity().get("collision", {})


def trait_patterns() -> dict[str, list[str]]:
    raw = identity().get("trait_patterns", {}) or {}
    return {str(k): [str(x) for x in (v or [])] for k, v in raw.items()}


def color_words() -> list[str]:
    return [str(x) for x in (identity().get("color_words", []) or [])]


def differentiation_pool() -> dict[str, list[str]]:
    raw = identity().get("differentiation_pool", {}) or {}
    return {str(k): [str(x) for x in (v or [])] for k, v in raw.items()}


def subjects() -> dict[str, str]:
    return {str(k): str(v) for k, v in (shot_prompt().get("subjects", {}) or {}).items()}


def subject(key: str, **kwargs: Any) -> str:
    text = subjects().get(key, "")
    if not text:
        return ""
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return text


def prompt_max_chars() -> int:
    try:
        return int(shot_prompt().get("max_chars", 760))
    except (TypeError, ValueError):
        return 760


def style_detail_max_chars() -> int:
    try:
        return int(shot_prompt().get("style_detail_max_chars", 40))
    except (TypeError, ValueError):
        return 40


def lens_map(kind: str) -> dict[str, str]:
    raw = (shot_prompt().get("lens", {}) or {}).get(kind, {}) or {}
    return {str(k): str(v) for k, v in raw.items()}


def forbidden() -> dict[str, Any]:
    return shot_prompt().get("forbidden", {}) or {}


def desc_rules() -> dict[str, Any]:
    return storyboard().get("description", {}) or {}


def cast_policy() -> dict[str, Any]:
    return storyboard().get("cast_policy", {}) or {}


def dialogue_rules() -> dict[str, Any]:
    return storyboard().get("dialogue", {}) or {}


def bubble_rules() -> dict[str, Any]:
    return storyboard().get("bubble", {}) or {}


def validation_rules() -> dict[str, Any]:
    return storyboard().get("validation", {}) or {}


def two_shot_policy() -> dict[str, Any]:
    return storyboard().get("two_shot_policy", {}) or {}


def multi_shot_policy() -> dict[str, Any]:
    return storyboard().get("multi_shot_policy", {}) or {}


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = {
    "identity_rules": ["card", "collision", "trait_patterns", "differentiation_pool"],
    "shot_prompt_rules": ["max_chars", "order", "subjects", "forbidden"],
    "storyboard_rules": ["description", "cast_policy", "dialogue", "bubble", "validation"],
}


def self_check() -> dict[str, Any]:
    """检查规则文件是否可用（界面/CLI 都能调）。"""
    report: dict[str, Any] = {"rules_dir": str(RULES_DIR), "ok": True,
                              "files": {}, "errors": [], "warnings": []}
    for name, required in _REQUIRED_KEYS.items():
        path = DATA_DIR / f"{name}.json"
        entry: dict[str, Any] = {"exists": path.exists()}
        try:
            data = load(name)
            missing = [k for k in required if k not in data]
            entry["missing_keys"] = missing
            entry["version"] = data.get("version", "")
            if missing:
                report["ok"] = False
                report["errors"].append(f"{name}.json 缺少键：{', '.join(missing)}")
        except Exception as exc:  # noqa: BLE001
            report["ok"] = False
            entry["error"] = str(exc)
            report["errors"].append(f"{name}.json 无法加载：{exc}")
        report["files"][name] = entry

    order = shot_prompt().get("order", [])
    subj = subjects()
    for key in ("count_one", "count_two", "count_zero", "cast"):
        if key not in subj:
            report["ok"] = False
            report["errors"].append(f"shot_prompt_rules.subjects 缺少模板：{key}")
    if "count" not in order or "cast" not in order:
        report["ok"] = False
        report["errors"].append("shot_prompt_rules.order 必须包含 count 与 cast")
    if card_max_chars() > 140:
        report["warnings"].append(f"身份卡上限 {card_max_chars()} 偏长，建议 ≤100")
    if prompt_max_chars() > 1000:
        report["warnings"].append(f"提示词上限 {prompt_max_chars()} 偏长，建议 ≤800")
    traits = trait_patterns()
    for bucket in ("face", "eyes", "hair", "outfit"):
        if not traits.get(bucket):
            report["warnings"].append(f"trait_patterns.{bucket} 为空，撞车检测会漏判")
    return report


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(self_check(), ensure_ascii=False, indent=2))

