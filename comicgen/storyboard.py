# -*- coding: utf-8 -*-
"""第四阶段后处理：把"原文搬运式"分镜整理成观众读得懂的分镜。

问题背景（实测于"痕迹"工程，17 格 / 2895 字原文）：
    文本模型经常把小说原文整段抄进 description（连引号台词一起抄），于是连锁出三个故障：
      1) 台词进入图像提示词 → 图像模型把中文台词直接"画"进画面（还常是错字）；
      2) 分镜的 dialogues 为空 → 成品图没有气泡，观众拿不到任何剧情信息；
      3) dialogues 为空 → "对话过多就拆格"的节奏控制失效 → 一格塞进整场戏；
         再加上镜头/情绪/场景全部退化成同一个值，成品就成了"一堆无厘头图片"。
    实测该工程：11/17 格的描述里含引号台词（45 处）、真正 dialogues 只有 5 条、
    旁白 0 条、中景 15/17、最长连续同镜头 8 格、17 格共用一个场景、情绪全是"平静"。

本模块用确定性规则做后处理（不指望模型听话），并产出可读的质量报告：
    · extract_dialogues      从画面描述里抽出台词、归位说话人、清理描述
    · split_long_silent_shots 把"一格塞多个动作"的无台词格按句子拆开
    · enforce_shot_rhythm    建立镜头 + 打断同镜头长段 + 保证近景/特写占比
    · diversify_moods        情绪去退化
    · detect_scene_shifts    按原文地点线索识别场景切换（并给换场处补建立镜头）
    · draft_narrations       规则兜底补旁白（时间/地点/心理），承担叙事黏合
    · validate               输出可读的校验报告（界面可见）
    · normalize              以上全流程编排，返回整理报告
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

import config
from .logger import get_logger
from .models import Dialogue, Project, Shot
from .textutil import unescape_text

log = get_logger(__name__)


# =============================================================================
# 一、台词抽取
# =============================================================================

# 中文/英文引号里的内容都算"可能是台词"
_QUOTED_RE = re.compile(r'[“「『]([^”」』]{1,160})[”」』]|"([^"\n]{1,160})"')

# 说话动词（用于识别"X说""X问"这类归属）
_SAY = "说|道|问|答|喊|叫|笑|念|回|应|嘟囔|嘀咕|低声|冷声|追问|解释|补充|开口"

# 引号后面紧跟的 "X说/道/问"
_AFTER_SPEAKER_RE = re.compile(rf"^[，,。：:；;、\s]{{0,3}}([\u4e00-\u9fa5]{{1,4}})(?:{_SAY})")
# 引号前面紧邻的 "X说：/ X问，"
_BEFORE_SPEAKER_RE = re.compile(rf"([\u4e00-\u9fa5]{{1,4}})(?:{_SAY})[：:，,]?\s*$")
# 声音性别线索（"那边是个男人的声音"）
_VOICE_MALE_RE = re.compile(r"男(?:人|孩|子|性)?(?:的)?(?:声音|嗓音|声)|男声")
_VOICE_FEMALE_RE = re.compile(r"女(?:人|孩|子|性)?(?:的)?(?:声音|嗓音|声)|女声")

# 拟声字（短引号内容全是这些字时判为音效）
_SFX_CHARS = set("咻哒咔砰哗轰咚嗒嘀哐嗤唰嗡沙簌叮咚轰隆噼啪呲当啷")
_SFX_RE = re.compile(r"^([\u4e00-\u9fa5])\1*$")

# 描述清理：去掉残留的"某人说/道"碎片
_PUNCT_RUN_RE = re.compile(r"[，,。、；;：:]{2,}")
_WS_RE = re.compile(r"[ \t\u3000]{2,}")


def _iter_quotes(text: str) -> Iterable[tuple[int, int, str]]:
    """按出现顺序产出 (start, end, 引号内文本)。"""
    for m in _QUOTED_RE.finditer(text or ""):
        body = m.group(1) if m.group(1) is not None else m.group(2)
        if body is None:
            continue
        yield m.start(), m.end(), body.strip()


def _looks_like_sfx(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) > config.STORYBOARD_SFX_MAX_CHARS:
        return False
    if _SFX_RE.match(t):                       # 咻咻 / 哒哒哒
        return True
    return all(ch in _SFX_CHARS for ch in t)   # 咔哒 / 砰咚


def _match_character(name: str, characters: Sequence) -> str:
    """把模型写出的称呼匹配到正式角色名。"""
    name = (name or "").strip()
    if not name:
        return ""
    for c in characters:
        if c.name and c.name == name:
            return c.name
    for c in characters:
        if c.name and (c.name in name or name in c.name) and len(name) >= 2:
            return c.name
    return ""


def _find_speaker(before: str, after: str, panel_chars: Sequence,
                  all_chars: Sequence) -> str:
    """
    按"引号后 → 引号前 → 声音性别"的顺序推断说话人。

    性别线索只在**本格出场角色**里找唯一候选（用全部角色找会同时命中多个同性角色，
    反而推不出人，实测"老港(男)"和"房东侄子(男)"就会互相干扰）。
    """
    pool = list(panel_chars) or list(all_chars)
    m = _AFTER_SPEAKER_RE.match(after or "")
    if m:
        hit = _match_character(m.group(1), all_chars)
        if hit:
            return hit
    m = _BEFORE_SPEAKER_RE.search(before or "")
    if m:
        hit = _match_character(m.group(1), all_chars)
        if hit:
            return hit
    window = (before or "")[-24:] + (after or "")[:24]
    if _VOICE_MALE_RE.search(window):
        cands = [c.name for c in pool if (c.gender or "").startswith("男")]
        if len(cands) == 1:
            return cands[0]
    if _VOICE_FEMALE_RE.search(window):
        cands = [c.name for c in pool if (c.gender or "").startswith("女")]
        if len(cands) == 1:
            return cands[0]
    return ""


# 强调引号：作者用引号"标注某个说法"，不是台词。
# 判据：引号内没有句末标点且很短，并且后面跟着"这个/是/字…"或前面是"把/所谓/挂名…"
_EMPHASIS_AFTER_RE = re.compile(
    r"^\s*[，,]?\s*(?:这个|那个|的|是|字|词|动作|说法|这种|这类|一名|两字|两字|这)")
_EMPHASIS_BEFORE_RE = re.compile(
    r"(?:把|所谓|叫|称为|叫做|挂名|写着|名字叫|代号)\s*[\"“]?$")
_SENT_END_CHARS = "。！？…~～"


def _strip_attribution(text: str, names: Sequence[str]) -> str:
    """删掉描述里残留的"陆鸣说""她说"这类归属碎片。"""
    for n in names:
        if not n:
            continue
        text = re.sub(rf"{re.escape(n)}\s*(?:{_SAY})[。！？；，,]?", "", text)
    # 人称代词做主语时同样会留下"她说，"这种碎片
    text = re.sub(r"(?:她|他|对方|那人|女人|男人|老港)(?:说|道|问|答)[，,。]?", "", text)
    return text


# 相邻两个引号之间如果只是这种"归属/语气"文字，说明是同一个人在连续说话
_SAME_TURN_RE = re.compile(r"声音|嗓音|说|道|问|低声|冷声|停顿|停了|顿了|哑|语气")
_MERGE_GAP_MAX = 24
_MERGE_TEXT_MAX = 60


def _merge_adjacent_same_speaker(items: Sequence[Dialogue], desc: str,
                                 quotes: Sequence[tuple[int, int, str]]) -> list[Dialogue]:
    """
    把"同一个人连续说的两句"合成一个气泡。

    原文常写成 `"陆鸣。"那边是个男人的声音，低，哑。"有人打听你。"`：
    这是同一次说话，拆成两个气泡既不自然、也会让"你一句我一句"的轮转判断错位。
    """
    if len(items) < 2:
        return list(items)
    # 记录每条台词在原文里对应的引号区间，用来判断中间隔了什么
    spans: list[tuple[int, int]] = []
    for s0, e0, body in quotes:
        if any(d.text == body for d in items):
            spans.append((s0, e0))
    out: list[Dialogue] = []
    for d in items:
        if (out and d.kind == "speech" and out[-1].kind == "speech"
                and d.speaker and d.speaker == out[-1].speaker
                and len(out[-1].text) + len(d.text) <= _MERGE_TEXT_MAX):
            gap = _gap_between(desc, out[-1].text, d.text, spans)
            if gap is not None and len(gap) <= _MERGE_GAP_MAX and (
                    not gap.strip() or _SAME_TURN_RE.search(gap)):
                out[-1].text = out[-1].text + d.text
                continue
        out.append(d)
    return out


def _gap_between(desc: str, first: str, second: str,
                 spans: Sequence[tuple[int, int]]) -> str | None:
    """取两段台词在原文里的间隔文字（找不到返回 None）。"""
    i1 = desc.find(first)
    i2 = desc.find(second, i1 + len(first)) if i1 >= 0 else -1
    if i1 < 0 or i2 < 0:
        return None
    return desc[i1 + len(first):i2]


def _fix_turn_taking(shot: Shot) -> None:
    """
    两人对话时按"你一句我一句"修正说话人。

    逐条独立推断在电话/对谈场景里很容易把同一个人判成连说三句（实测"老港"连吃三条），
    而两人对谈天然是交替的：保留第一句的推断结果，其余按顺序轮转。
    """
    names = [n for n in shot.characters if n]
    if len(names) != 2:
        return
    speech = [d for d in shot.dialogues if d.kind == "speech" and (d.text or "").strip()]
    if len(speech) < 3:
        return
    a, b = names
    first = speech[0].speaker
    if first not in (a, b):
        # 第一句没推断出来，就用出现最多的那个
        counts: dict[str, int] = {}
        for d in speech:
            if d.speaker in (a, b):
                counts[d.speaker] = counts.get(d.speaker, 0) + 1
        if not counts:
            return
        first = max(counts.items(), key=lambda kv: kv[1])[0]
    other = b if first == a else a
    for i, d in enumerate(speech):
        d.speaker = first if i % 2 == 0 else other


def _clean_description(text: str) -> str:
    """清理被抽走台词后的描述：去掉多余标点与空白。"""
    t = text or ""
    t = t.replace("“", "").replace("”", "").replace("「", "").replace("」", "")
    t = t.replace("『", "").replace("』", "")
    t = re.sub(r'"', "", t)
    t = _PUNCT_RUN_RE.sub("。", t)
    t = re.sub(r"^[。，、；;：:\s]+", "", t)
    t = re.sub(r"[。，、；;：:\s]+$", "", t)
    t = _WS_RE.sub(" ", t)
    return t.strip()


def _framing_hint(dialogues: Sequence[Dialogue]) -> str:
    """给拆格后的画面补一句构图提示，帮助图像模型取对镜头。"""
    speech = [d for d in dialogues if d.kind == "speech"]
    if len(speech) >= 2:
        return "（对话密集：说话人近景与听者反应交替）"
    if len(speech) == 1:
        return "（有一句台词：取说话人近景，突出表情）"
    if any(d.kind == "narration" for d in dialogues):
        return "（无台词：以场景与物件细节为主）"
    return ""


# 按镜头类型准备多套"看得见"的描述，用于描述句子不够分时逐格区分，
# 避免拆出来的几格复用同一句描述、画出一组雷同的图。
_FRAME_BY_TYPE: dict[str, tuple[str, ...]] = {
    "近景": ("说话人的面部表情与口型", "听者的反应与视线方向", "手部小动作与手里拿的东西"),
    "特写": ("眼神与情绪细节", "嘴唇与下颌的细微变化", "手指与身边物件的局部"),
    "中景": ("人物上半身的姿态与手势", "两人之间的距离与位置关系", "转身或前倾的瞬间"),
    "全景": ("人物与房间的空间关系", "人物在场景中的站位与环境比例", "门口与通道方向的构图"),
    "远景": ("整个空间的全貌与纵深", "窗外与室内的光线对比", "环境氛围与时间感"),
}


def variant_description(shot_type: str, idx: int) -> str:
    """句子不够分时，按镜头类型给出差异化的画面描述。"""
    pool = _FRAME_BY_TYPE.get(shot_type) or _FRAME_BY_TYPE["中景"]
    return pool[idx % len(pool)]


def _is_emphasis_quote(body: str, before: str, after: str) -> bool:
    """
    判断引号是"强调术语"而不是台词。

    中文小说里 `把"问"这个动作磨掉了`、`挂名"数据顾问"` 这类引号只是标注说法，
    一旦被当成台词，就会凭空多出"问""数据顾问"这种无意义气泡（实测原工程就有）。
    """
    b = (body or "").strip()
    if not b or len(b) > 8:
        return False
    if any(ch in b for ch in _SENT_END_CHARS):
        return False
    if _EMPHASIS_AFTER_RE.match(after or ""):
        return True
    return bool(_EMPHASIS_BEFORE_RE.search(before or ""))


def extract_dialogues(project: Project) -> int:
    """
    把每个分镜 description 里的引号台词抽到 dialogues 里。

    返回发生改动的格数。抽完后：
      · description 只剩"看得见的东西"，不会再让图像模型把中文画进画面；
      · dialogues 有内容后，后续"对话过多就拆格"的节奏控制才真正生效；
      · 被误当台词的"强调引号"会被清理掉（引号去掉、文字保留在描述里）。
    """
    all_chars = list(project.characters)
    names = project.character_names()
    changed = 0

    for shot in project.shots:
        desc = shot.description or ""
        quotes = list(_iter_quotes(desc))
        if not quotes:
            continue

        panel_chars = [c for c in (project.find_character(n) for n in shot.characters)
                       if c is not None]
        existing = {(d.text or "").strip() for d in shot.dialogues if (d.text or "").strip()}
        extracted: list[Dialogue] = []
        emphasis_texts: set[str] = set()
        remove_spans: list[tuple[int, int]] = []
        touched = False

        for start, end, body in quotes:
            if not body or len(body) > 120:
                continue
            body = unescape_text(body)
            before = desc[max(0, start - 20):start]
            after = desc[end:end + 20]
            if _is_emphasis_quote(body, before, after):
                emphasis_texts.add(body)
                touched = True
                continue
            remove_spans.append((start, end))
            touched = True
            if body in existing:          # 这条已经抽过了，只做描述清理
                continue
            speaker = _find_speaker(before, after, panel_chars, all_chars)
            kind = "sfx" if _looks_like_sfx(body) else "speech"
            extracted.append(Dialogue(speaker=speaker, text=body, kind=kind))

        # 清掉"把术语误当台词"的垃圾条目（拟声词保留）
        if emphasis_texts:
            kept = [d for d in shot.dialogues
                    if (d.text or "").strip() not in emphasis_texts or d.kind == "sfx"]
            if len(kept) != len(shot.dialogues):
                shot.dialogues = kept

        if extracted:
            merged = _merge_adjacent_same_speaker(extracted, desc, quotes)
            shot.dialogues = (list(shot.dialogues) + merged)[:config.MAX_BUBBLES_PER_SHOT]
        _fix_turn_taking(shot)

        if config.STORYBOARD_STRIP_QUOTES_FROM_DESC and touched:
            # 先按原串切掉台词片段（一次性拼接，避免多次改动导致偏移错乱）
            parts: list[str] = []
            idx = 0
            for s0, e0 in sorted(remove_spans):
                parts.append(desc[idx:s0])
                idx = e0
            parts.append(desc[idx:])
            clean = "".join(parts)
            # 剩下的引号都是"强调引号"，去掉引号符号本身、保留文字
            for ch in ("“", "”", "「", "」", "『", "』", '"'):
                clean = clean.replace(ch, "")
            clean = _clean_description(_strip_attribution(clean, names))
            clean = unescape_text(clean)
            if len(clean) < 12:
                clean = (clean + " " + _framing_hint(shot.dialogues)).strip()
            elif not any(ch in clean for ch in "。！？"):
                clean = clean + "。" + _framing_hint(shot.dialogues)
            shot.description = clean or desc
        changed += 1

    if changed:
        log.info("台词抽取：%d 格从画面描述里归位了台词", changed)
    return changed


# =============================================================================
# 二、把"一格塞多个动作"的格拆开
# =============================================================================

_SENT_SPLIT_RE = re.compile(r"(?<=[。！？…])")


def _sentences(text: str) -> list[str]:
    return [x.strip() for x in _SENT_SPLIT_RE.split(text or "") if x.strip()]


def _assign_types(count: int, base: str) -> list[str]:
    """给拆出来的 N 格分镜头类型，避免全一样。"""
    order = ["中景", "近景", "特写", "中景", "全景"]
    out = []
    for i in range(count):
        t = order[i % len(order)]
        out.append(t if t in config.SHOT_TYPES else base)
    return out


def split_long_silent_shots(project: Project, max_sentences: int = 3,
                            max_chars: int = 150) -> int:
    """
    无台词且"句子太多"的格按句子拆成多格。

    一格只能是一个瞬间：一段文字里如果写了四五个连续动作，画出来必然四不像，
    观众也接不上因果。这里按句子边界拆开，并给每格分配不同镜头。
    """
    out: list[Shot] = []
    added = 0
    for shot in project.shots:
        sents = _sentences(shot.description)
        has_dialogue = any((d.text or "").strip() for d in shot.dialogues)
        if has_dialogue or len(sents) <= max_sentences or len(shot.description) <= max_chars:
            out.append(shot)
            continue
        groups = [sents[i:i + max_sentences]
                  for i in range(0, len(sents), max_sentences)]
        # 过短的碎格没有画面信息，合并回上一组，避免产出"一句话空格"
        merged_groups: list[list[str]] = []
        for g in groups:
            if merged_groups and len("".join(g)) < 12:
                merged_groups[-1].extend(g)
            else:
                merged_groups.append(list(g))
        groups = merged_groups
        if len(groups) < 2:
            out.append(shot)
            continue
        types = _assign_types(len(groups), shot.shot_type)
        for gi, group in enumerate(groups):
            clone = Shot(
                shot_type=types[gi],
                characters=list(shot.characters),
                scene=shot.scene,
                description="".join(group),
                mood=shot.mood,
                source_text=shot.source_text,
                camera=(shot.camera if gi == 0 else ""),
                composition=(shot.composition if gi == 0 else ""),
                lighting=shot.lighting,
                panel_scale=("S" if types[gi] in ("近景", "特写") else "M"),
                char_sides=dict(shot.char_sides),
                time_of_day=shot.time_of_day,
            )
            out.append(clone)
        added += len(groups) - 1
    if added:
        project.shots = out
        project.reindex_shots()
        log.info("动作拆格：多拆出 %d 格，让每格只讲一个瞬间", added)
    return added


# =============================================================================
# 三、镜头节奏
# =============================================================================

_ENV_WORDS = ("整个", "远处", "窗外", "街道", "房间", "走廊", "楼梯", "全景",
              "环境", "天台", "屋顶", "城市", "夜色", "巷", "大厅", "门口")
_CLOSE_WORDS = ("眼神", "眼睛", "嘴角", "表情", "泪水", "皱眉", "瞳孔", "手指",
                "脸", "神情", "目光", "嘴唇")


def _is_establishing(shot_type: str) -> bool:
    return shot_type in config.STORYBOARD_ESTABLISHING_TYPES


def _content_bias(shot: Shot) -> str:
    """判断这格更适合"大景"还是"小景"。"""
    speech = [d for d in shot.dialogues if d.kind == "speech" and (d.text or "").strip()]
    desc = shot.description or ""
    if any(w in desc for w in _CLOSE_WORDS) or len(speech) >= 2:
        return "close"
    if any(w in desc for w in _ENV_WORDS):
        return "wide"
    return "mid"


def _pick_varied(shot: Shot, avoid: str, prefer_establishing: bool = False) -> str:
    """挑一个与 avoid 不同的镜头类型。"""
    bias = _content_bias(shot)
    if prefer_establishing:
        order = ["全景", "远景", "中景", "近景", "特写"]
    elif bias == "close":
        order = ["近景", "特写", "中景", "全景"]
    elif bias == "wide":
        order = ["全景", "远景", "中景", "近景"]
    else:
        order = ["中景", "近景", "全景", "特写"]
    for t in order:
        if t != avoid and t in config.SHOT_TYPES:
            return t
    return avoid


def enforce_shot_rhythm(project: Project) -> int:
    """
    强制镜头节奏：

      1. 换场后的第一格必须是建立镜头（远景/全景），否则观众不知道"这是哪"；
      2. 同一种镜头最多连续 MAX_SAME_SHOT_RUN 格，打断"每格都长一样"；
      3. 近景+特写占比不得低于 CLOSEUP_MIN_RATIO，保证情绪有落点。
    """
    shots = project.shots
    if not shots:
        return 0
    changed = 0
    max_run = max(1, int(config.STORYBOARD_MAX_SAME_SHOT_RUN))

    prev_scene = None
    prev_type = None
    run = 0
    for i, s in enumerate(shots):
        new_scene = (s.scene or "") != (prev_scene or "")
        target = s.shot_type

        # 规则 1：换场第一格用建立镜头（先让观众知道"这是哪"）
        if new_scene and i > 0 and not _is_establishing(target):
            target = "远景" if _content_bias(s) == "wide" else "全景"

        # 规则 2：打断同镜头长段
        if target == prev_type:
            run += 1
            if run > max_run:
                target = _pick_varied(s, prev_type)
                run = 1
        else:
            run = 1

        if target != s.shot_type:
            s.shot_type = target
            changed += 1
        prev_type = s.shot_type
        prev_scene = s.scene

    # 近景+特写占比兜底：情绪必须有落点
    close = sum(1 for s in shots if s.shot_type in ("近景", "特写"))
    need = int(len(shots) * max(0.0, config.STORYBOARD_CLOSEUP_MIN_RATIO))
    if close < need:
        # 优先把"有台词的 中景"提升为近景，情绪才有落点
        for s in shots:
            if close >= need:
                break
            if s.shot_type == "中景" and any(
                    d.kind == "speech" and (d.text or "").strip() for d in s.dialogues):
                s.shot_type = "近景"
                close += 1
                changed += 1
        # 还不够就把中景按位置间隔提升
        for i, s in enumerate(shots):
            if close >= need:
                break
            if s.shot_type == "中景" and i % 2 == 1:
                s.shot_type = "近景"
                close += 1
                changed += 1

    if changed:
        log.info("镜头节奏：调整了 %d 格的镜头类型", changed)
    return changed


# =============================================================================
# 四、情绪去退化
# =============================================================================

_MOOD_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("悲伤", ("死", "尸", "泪", "哭", "葬", "遗", "再也", "最后一面")),
    ("压抑", ("阴", "冷", "暗", "闷", "沉默", "喘息", "沉重", "压")),
    ("悬疑", ("？", "?", "谁", "为什么", "查", "线索", "秘密", "不对劲", "奇怪")),
    ("紧张", ("！", "!", "快", "跑", "抓", "追", "急", "突然", "猛地", "危险")),
    ("愤怒", ("怒", "吼", "骂", "摔", "咬牙", "恨")),
    ("搞笑", ("笑", "调侃", "开玩笑", "吐槽", "滑稽")),
    ("温馨", ("温暖", "谢谢", "家", "拥抱", "笑意", "安心")),
    ("浪漫", ("喜欢", "心跳", "脸红", "牵手")),
    ("激动", ("兴奋", "颤抖", "喊出", "终于")),
)


def _guess_mood_by_text(text: str) -> str:
    t = text or ""
    for mood, keys in _MOOD_HINTS:
        if any(k in t for k in keys):
            return mood
    return ""


def diversify_moods(project: Project) -> int:
    """情绪去退化：单一情绪占比过高时，按文本线索重排。"""
    shots = project.shots
    if not shots:
        return 0
    counts: dict[str, int] = {}
    for s in shots:
        counts[s.mood] = counts.get(s.mood, 0) + 1
    top = max(counts.values())
    if top / len(shots) <= config.STORYBOARD_MAX_MOOD_RATIO:
        return 0

    changed = 0
    cycle = ["平静", "悬疑", "紧张", "压抑", "温馨"]
    for i, s in enumerate(shots):
        text = (s.description or "") + " " + " ".join(
            (d.text or "") for d in s.dialogues)
        guess = _guess_mood_by_text(text)
        if not guess:
            tones = [d.tone for d in s.dialogues if d.tone in config.MOOD_OPTIONS]
            guess = tones[0] if tones else ""
        if not guess:
            guess = cycle[i % len(cycle)]
        if guess != s.mood:
            s.mood = guess
            changed += 1

    # 关键词规则仍让某一情绪占多数时，强制摊开（每隔一格换成别的情绪），
    # 保证读者能感到情绪起伏，而不是通篇一个调子。
    for _ in range(4):
        counts: dict[str, int] = {}
        for s in shots:
            counts[s.mood] = counts.get(s.mood, 0) + 1
        top_mood, top_n = max(counts.items(), key=lambda kv: kv[1])
        if top_n / max(1, len(shots)) <= config.STORYBOARD_MAX_MOOD_RATIO:
            break
        others = [m for m in config.MOOD_OPTIONS if m != top_mood]
        i = 0
        for s in shots:
            if s.mood != top_mood:
                continue
            i += 1
            if i % 2 == 0 and others:
                s.mood = others[(i // 2) % len(others)]
                changed += 1

    if changed:
        log.info("情绪去退化：调整了 %d 格的情绪", changed)
    return changed


# =============================================================================
# 五、场景切换识别
# =============================================================================

_SHIFT_CUES = ("上楼", "下楼", "走进", "走出", "推开", "推门", "来到", "回到",
               "离开", "进门", "出门", "拐进", "穿过", "沿着", "门外", "门口",
               "楼梯", "楼道", "楼下", "楼上", "巷口", "街上", "店内", "柜台")


def _scene_tokens(project: Project) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []
    for sc in project.scenes:
        name = sc.name or ""
        toks = [t for t in re.split(r"[（）()、\s/·]+", name) if len(t) >= 2]
        if len(name) >= 3:
            toks.append(name[-3:])
        seen: list[str] = []
        for t in toks:
            if t not in seen and len(t) >= 2:
                seen.append(t)
        if seen:
            out.append((name, seen))
    return out


def detect_scene_shifts(project: Project) -> int:
    """
    按原文的地点线索把场景切出来。

    模型常把整章都标成同一个场景（实测 17 格共用一个场景），于是画面毫无空间推进。
    这里用原文里的位移线索（上楼/走出/推门…）配合工程里已声明的场景名做切换。
    """
    tokens = _scene_tokens(project)
    if not tokens:
        return 0
    changed = 0
    current = project.shots[0].scene if project.shots else ""
    for shot in project.shots:
        text = (shot.source_text or "") + (shot.description or "")
        # 先看是否出现了别的场景名
        hit_scene = ""
        for name, toks in tokens:
            if name == current:
                continue
            if any(t in text for t in toks):
                hit_scene = name
                break
        if hit_scene:
            if shot.scene != hit_scene:
                shot.scene = hit_scene
                changed += 1
            current = hit_scene
            continue
        # 没有场景名但有明显位移线索 → 至少标记为"移动到该场景"，交给建立镜头规则处理
        if any(c in text for c in _SHIFT_CUES) and shot.scene != current:
            shot.scene = current
    if changed:
        log.info("场景识别：%d 格的场景被纠正", changed)
    return changed


# =============================================================================
# 六、旁白兜底（时间 / 地点 / 心理）
# =============================================================================

_NARRATION_CUES = ("凌晨", "深夜", "傍晚", "清晨", "这天", "那天", "三年前",
                   "一九", "2019", "分钟", "小时", "点", "分", "第二天", "后来")
_PSYCH_CUES = ("从不", "总是", "规矩", "其实", "原来", "已经", "再也", "不知道",
               "明白", "以为", "决定", "习惯", "向来", "从来")


_TITLE_RE = re.compile(r"^\s*第\s*[0-9一二三四五六七八九十百]+\s*[章话回节卷]")


def _trim_narration(text: str, limit: int) -> str:
    """旁白截断：尽量在标点处收断，避免出现"半句话"的观感。"""
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    cut = t[:limit]
    best = max((cut.rfind(p) for p in "。！？…，、；："), default=-1)
    if best >= max(6, int(limit * 0.5)):
        tail = cut[best]
        # 断在逗号/冒号上仍是"半句话"，补省略号收尾
        return cut[:best] + ("…" if tail in "，、；：" else cut[best])
    return cut[:max(1, limit - 1)] + "…"


def _narration_from_source(shot: Shot, limit: int) -> str:
    """
    从原文里挑一句适合当旁白的话。

    优先取带时间/地点/心理线索的句子（旁白该干的三件事），
    并跳过"第 1 章 · 委托"这类标题，以及已经当台词用了的句子。
    """
    src = (shot.source_text or "").strip()
    if not src:
        return ""
    lines = [ln.strip() for ln in src.split("\n") if ln.strip()]
    lines = [ln for ln in lines if not _TITLE_RE.match(ln)]
    if not lines:
        return ""
    body = " ".join(lines[:2])
    used = {(d.text or "").strip() for d in shot.dialogues}
    sents = [s for s in _sentences(body)
             if not _TITLE_RE.match(s) and s.strip() not in used and len(s.strip()) >= 6]
    for sent in sents:
        if any(c in sent for c in _NARRATION_CUES) or any(c in sent for c in _PSYCH_CUES):
            return _trim_narration(sent, limit)
    return _trim_narration(sents[0], limit) if sents else ""


def draft_narrations(project: Project) -> int:
    """
    规则兜底补旁白：换场处交代时间地点、无台词格交代心理。

    旁白是条漫的"叙事黏合剂"：没有它，观众只能靠画面猜剧情，格子之间就会
    显得毫无因果。总条数不足时按"每 N 格至少 1 条"补齐。
    """
    shots = project.shots
    if not shots:
        return 0
    per = max(1, int(config.STORYBOARD_NARRATION_PER_SHOTS))
    need_total = max(1, len(shots) // per + (1 if len(shots) % per else 0))
    have = sum(1 for s in shots for d in s.dialogues
               if d.kind == "narration" and (d.text or "").strip())
    if have >= need_total:
        return 0

    added = 0
    prev_scene = None
    for i, shot in enumerate(shots):
        if added + have >= need_total:
            break
        if any(d.kind == "narration" and (d.text or "").strip() for d in shot.dialogues):
            continue
        new_scene = (shot.scene or "") != (prev_scene or "")
        silent = not any(d.kind == "speech" and (d.text or "").strip()
                         for d in shot.dialogues)
        # 只在"换场开头"或"无台词格"上补，且均匀铺开，避免连环旁白
        if not (new_scene or (silent and i % per == 1)):
            prev_scene = shot.scene
            continue
        text = _narration_from_source(shot, config.STORYBOARD_NARRATION_MAX_CHARS)
        if not text:
            prev_scene = shot.scene
            continue
        shot.dialogues.insert(0, Dialogue(speaker="旁白", text=text, kind="narration",
                                          tone="旁白"))
        added += 1
        prev_scene = shot.scene
    if added:
        log.info("旁白兜底：补了 %d 条旁白", added)
    return added


def ensure_min_description(project: Project, min_chars: int = 12) -> int:
    """
    兜底：画面描述过短的格子补一句构图提示。

    拆格后常留下"对方的语速没有变快"这类极短描述，出图会失控（模型自由发挥）。
    补一句构图提示至少把镜头和主体框住。
    """
    fixed = 0
    for shot in project.shots:
        desc = (shot.description or "").strip()
        if len(desc) >= min_chars:
            continue
        hint = _framing_hint(shot.dialogues) or "（人物反应与环境细节）"
        shot.description = f"{desc} {hint}".strip() if desc else hint
        fixed += 1
    if fixed:
        log.info("描述兜底：%d 格描述过短，已补构图提示", fixed)
    return fixed


# =============================================================================
# 七、质量校验
# =============================================================================

def validate(project: Project) -> list[dict[str, Any]]:
    """输出可读的分镜质量报告（供界面展示与日志）。"""
    shots = project.shots
    issues: list[dict[str, Any]] = []
    if not shots:
        return [{"level": "warn", "code": "empty", "message": "还没有分镜",
                 "shots": []}]

    # 1) 台词还留在描述里
    stuck = [s.index for s in shots if list(_iter_quotes(s.description or ""))]
    if stuck:
        issues.append({
            "level": "danger", "code": "dialogue_in_description",
            "message": f"{len(stuck)} 格的画面描述里还留着引号台词，"
                       f"会被图像模型画进画面（第 {stuck[:8]} 格…）",
            "shots": stuck,
        })

    # 2) 旁白太少
    nar = sum(1 for s in shots for d in s.dialogues
              if d.kind == "narration" and (d.text or "").strip())
    need = max(1, len(shots) // max(1, config.STORYBOARD_NARRATION_PER_SHOTS))
    if nar < need:
        issues.append({
            "level": "warn", "code": "too_few_narration",
            "message": f"旁白只有 {nar} 条（建议至少 {need} 条）："
                       f"时间、地点、心理没人交代，格子之间会失去因果感",
            "shots": [],
        })

    # 3) 同镜头长段
    worst = 1
    run = 1
    worst_at = 1
    for i in range(1, len(shots)):
        if shots[i].shot_type == shots[i - 1].shot_type:
            run += 1
            if run > worst:
                worst, worst_at = run, shots[i].index
        else:
            run = 1
    if worst > config.STORYBOARD_MAX_SAME_SHOT_RUN:
        issues.append({
            "level": "warn", "code": "same_shot_run",
            "message": f"第 {worst_at} 格附近连续 {worst} 格同一种镜头，"
                       f"画面会显得重复、没有节奏",
            "shots": [worst_at],
        })

    # 4) 镜头类型退化
    kinds = {t: sum(1 for s in shots if s.shot_type == t) for t in config.SHOT_TYPES}
    if kinds.get("中景", 0) / len(shots) > 0.6:
        issues.append({
            "level": "warn", "code": "shot_type_degenerate",
            "message": f"中景占 {kinds.get('中景', 0)}/{len(shots)}，"
                       f"缺少建立镜头与情绪特写：{kinds}",
            "shots": [],
        })

    # 5) 场景从未变化
    scenes = {s.scene for s in shots if s.scene}
    if len(scenes) <= 1 and len(shots) >= 8:
        issues.append({
            "level": "warn", "code": "scene_never_changes",
            "message": f"{len(shots)} 格全部发生在同一个场景「{list(scenes)[0] if scenes else '未标注'}」，"
                       f"缺少空间推进",
            "shots": [],
        })

    # 6) 情绪退化
    moods: dict[str, int] = {}
    for s in shots:
        moods[s.mood] = moods.get(s.mood, 0) + 1
    if moods:
        top_mood, top_n = max(moods.items(), key=lambda kv: kv[1])
        if top_n / len(shots) > config.STORYBOARD_MAX_MOOD_RATIO:
            issues.append({
                "level": "warn", "code": "mood_degenerate",
                "message": f"{top_n}/{len(shots)} 格情绪都是「{top_mood}」，缺少情绪起伏",
                "shots": [],
            })

    # 7) 空台词 / 空描述
    empty_dlg = [s.index for s in shots
                 if any(not (d.text or "").strip() for d in s.dialogues)]
    if empty_dlg:
        issues.append({
            "level": "warn", "code": "empty_dialogue",
            "message": f"{len(empty_dlg)} 格存在空白台词条目（第 {empty_dlg[:8]} 格…）",
            "shots": empty_dlg,
        })
    thin = [s.index for s in shots if len((s.description or "").strip()) < 12]
    if thin:
        issues.append({
            "level": "warn", "code": "thin_description",
            "message": f"{len(thin)} 格的画面描述过短，出图会失控"
                       f"（第 {thin[:8]} 格…）",
            "shots": thin,
        })

    # 8) 无文字格比例（太高会变成"看图猜剧情"）
    silent = sum(1 for s in shots
                 if not any((d.text or "").strip() for d in s.dialogues))
    if shots and silent / len(shots) > 0.7:
        issues.append({
            "level": "info", "code": "too_many_silent",
            "message": f"{silent}/{len(shots)} 格完全没有文字："
                       f"叙事主要靠画面，建议补旁白或补充对话",
            "shots": [],
        })
    return issues


# =============================================================================
# 八、编排
# =============================================================================

def normalize(project: Project,
              extract: bool | None = None,
              split_long: bool = True,
              rhythm: bool | None = None,
              moods: bool | None = None,
              scenes: bool | None = None,
              narration: bool | None = None) -> dict[str, Any]:
    """
    跑完整理流程，返回报告。

    顺序很重要：必须先抽出台词，后面的"按对话拆格"和"镜头节奏"才有依据。
    """
    extract = config.STORYBOARD_EXTRACT_DIALOGUE if extract is None else extract
    rhythm = config.STORYBOARD_ENFORCE_RHYTHM if rhythm is None else rhythm
    moods = config.STORYBOARD_DIVERSIFY_MOOD if moods is None else moods
    scenes = config.STORYBOARD_DETECT_SCENE_SHIFT if scenes is None else scenes
    narration = config.STORYBOARD_DRAFT_NARRATION if narration is None else narration

    before = len(project.shots)
    report: dict[str, Any] = {"before": before}

    if extract:
        report["extracted"] = extract_dialogues(project)
    if split_long:
        report["split"] = split_long_silent_shots(project)
    if scenes:
        report["scenes"] = detect_scene_shifts(project)
    if rhythm:
        report["rhythm"] = enforce_shot_rhythm(project)
    if moods:
        report["moods"] = diversify_moods(project)
    if narration:
        report["narration"] = draft_narrations(project)

    report["min_desc"] = ensure_min_description(project)
    project.reindex_shots()
    report["after"] = len(project.shots)
    report["issues"] = validate(project)
    return report
