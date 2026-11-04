# -*- coding: utf-8 -*-
"""结果输出与打包（需求 4.10 / 六）。

输出内容（分目录，见 09_清单/README.md）：
    00_输入/         小说原文、工程信息
    01_分析/         分析结果、梗概、角色/场景清单
    02_角色/         人设卡、角色设定 JSON、主视觉/三视图
    03_分镜/         分镜 JSON/MD/TXT、台词表、统计
    04_提示词/       逐格提示词、气泡计划、运行期原始提示词
    05_分镜图/       带气泡成品；原图/ 为纯画面
    06_条漫/         条漫成品
    07_阅读/         PDF / 阅读文件
    08_回溯/         质量报告与问题格
    09_清单/         manifest、工程数据、目录说明
    xxx.zip          全部内容的压缩包
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from PIL import Image

import config
from . import episode as episode_mod
from . import pagelayout
from . import prompt_overrides
from . import prompts as prompts_mod
from . import stylelib
from .logger import get_logger
from .models import PagePlan, Project, Shot

log = get_logger(__name__)


class ExportError(RuntimeError):
    """导出失败。"""


# =============================================================================
# 目录与命名
# =============================================================================

def _safe(name: str, fallback: str = "unnamed") -> str:
    name = (name or "").strip() or fallback
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", name)
    name = name.strip(" .")
    return name[:60] or fallback


def _img_ext() -> str:
    fmt = (config.OUTPUT_IMAGE_FORMAT or "PNG").upper()
    return "jpg" if fmt in ("JPG", "JPEG") else "png"


def _save_image(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in (".jpg", ".jpeg"):
        img.convert("RGB").save(path, format="JPEG", quality=config.OUTPUT_JPEG_QUALITY)
    else:
        img.save(path, format="PNG")


# =============================================================================
# 分镜描述文档
# =============================================================================

def build_storyboard_markdown(project: Project) -> str:
    """生成分镜描述文档（markdown）。"""
    lines: list[str] = []
    lines.append(f"# {project.title} —— 分镜脚本")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")
    if project.chapter_no or project.chapter_title:
        lines.append(f"- 话数：第 {project.chapter_no or '01'} 话"
                     f"{' · ' + project.chapter_title if project.chapter_title else ''}")
    if project.tagline:
        lines.append(f"- 封面钩子：{project.tagline}")
    lines.append(f"- 整体画风：{project.style}")
    if project.style_id:
        meta = stylelib.style_meta(project.style_id)
        lines.append(f"- 绘制风格：#{project.style_id} "
                     f"{meta.get('name_zh') or meta.get('generation_name', '')}"
                     + (f"（{meta.get('generation_name', '')}）"
                        if meta.get("generation_name") else "")
                     + (f"；参考作者/风格：{meta.get('reference_display') or meta.get('reference', '')}"
                        if meta.get("reference") else ""))
    if project.style_color_ids:
        lines.append(f"- 主题色：{'、'.join(project.style_color_ids)}")
    if project.style_tone:
        tone = stylelib.tone_meta(project.style_tone)
        lines.append(f"- 阶调：{project.style_tone} {tone.get('name_zh', '')}"
                     + (f"（{tone.get('desc_zh', '')}）" if tone.get("desc_zh") else ""))
    if project.style_trigger:
        lines.append(f"- 触发词：{project.style_trigger}")
    if project.style_rationale:
        lines.append(f"- 风格理由：{project.style_rationale}")
    if project.style_detail:
        lines.append(f"- 风格细化：{project.style_detail}")
    stats = project.statistics()
    lines.append(f"- 角色 {stats['角色数']} 个 / 场景 {stats['场景数']} 个 / "
                 f"分镜 {stats['分镜数']} 格（已完成 {stats['已完成']} 格）")
    if project.pages:
        lines.append(f"- 分镜形态：{project.render_mode or config.render_mode()}"
                     f"，共 {len(project.pages)} 页"
                     + (f"（{project.page_plan_note}）" if project.page_plan_note else ""))
    lines.append("")

    if project.summary:
        lines.append("## 故事梗概")
        lines.append("")
        lines.append(project.summary)
        lines.append("")

    if project.key_points:
        lines.append("## 关键节点")
        lines.append("")
        for kp in project.key_points:
            lines.append(f"- {kp}")
        lines.append("")

    lines.append("## 角色设定")
    lines.append("")
    lines.append("| 姓名 | 性别 | 年龄感 | 身份 | 性格 | 作用 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for c in project.characters:
        lines.append("| {} | {} | {} | {} | {} | {} |".format(
            c.name or "-", c.gender or "-", c.age or "-",
            (c.identity or "-").replace("|", "/"),
            (c.personality or "-").replace("|", "/"),
            (c.role or "-").replace("|", "/")))
    lines.append("")
    for c in project.characters:
        if c.design:
            lines.append(f"### {c.name} 人设")
            lines.append("")
            lines.append(c.design)
            lines.append("")

    if project.scenes:
        lines.append("## 场景设定")
        lines.append("")
        lines.append("| 场景 | 环境特点 | 氛围 | 出现频率 |")
        lines.append("| --- | --- | --- | --- |")
        for s in project.scenes:
            lines.append("| {} | {} | {} | {} |".format(
                s.name or "-", (s.features or "-").replace("|", "/"),
                (s.atmosphere or "-").replace("|", "/"), s.frequency or "-"))
        lines.append("")

    lines.append("## 分镜列表")
    lines.append("")
    for shot in project.shots:
        lens = " · ".join(x for x in (
            shot.shot_type, shot.camera, shot.composition, shot.lighting,
            f"画幅{shot.panel_scale}" if shot.panel_scale else "",
            shot.mood,
        ) if x)
        lines.append(f"### 第 {shot.index:03d} 格 · {lens}")
        lines.append("")
        if shot.time_of_day:
            lines.append(f"- 时间：{shot.time_of_day}")
        lines.append(f"- 出场角色：{'、'.join(shot.characters) or '无'}"
                     + (f"（站位：{'、'.join(f'{k}{v}' for k, v in shot.char_sides.items())}）"
                        if shot.char_sides else ""))
        lines.append(f"- 场景：{shot.scene or '未指定'}")
        lines.append(f"- 转场：{shot.transition or '未标注'}")
        lines.append(f"- 状态：{shot.status}" + (f"（{shot.error}）" if shot.error else ""))
        lines.append("")
        if shot.action or shot.expression or shot.focus:
            lines.append(f"- 动作：{shot.action or '—'}")
            lines.append(f"- 表情：{shot.expression or '—'}")
            lines.append(f"- 焦点：{shot.focus or '—'}")
            lines.append("")
        lines.append(f"**画面描述**：{shot.description}")
        lines.append("")
        if shot.dialogue_text():
            lines.append("**对话内容**：")
            lines.append("")
            for d in shot.dialogues:
                if not d.text.strip():
                    continue
                tag = "（画外音）" if d.offscreen else ""
                if d.kind == "narration":
                    lines.append(f"> [旁白] {d.text}")
                elif d.kind == "sfx":
                    lines.append(f"> [音效] {d.text}")
                elif d.speaker:
                    lines.append(f"> {d.speaker}{tag}：{d.text}")
                else:
                    lines.append(f"> {d.text}")
            lines.append("")
        if shot.source_text:
            lines.append("**对应原文**：")
            lines.append("")
            lines.append("> " + shot.source_text.replace("\n", "\n> "))
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def build_storyboard_txt(project: Project) -> str:
    """纯文本版分镜描述文档。"""
    md = build_storyboard_markdown(project)
    text = re.sub(r"^#+\s*", "", md, flags=re.MULTILINE)
    text = text.replace("**", "").replace("> ", "    ")
    return text


# =============================================================================
# 导出主流程
# =============================================================================



# =============================================================================
# 分目录输出：输入 / 分析 / 角色 / 分镜 / 提示词 / 回溯 / 清单
# =============================================================================

def _write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    def cell(value: object) -> str:
        return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join(cell(v) for v in row) + " |")
    return "\n".join(out)


def _write_input_bundle(project: Project, out_dir: Path) -> list[Path]:
    root = out_dir / config.OUTPUT_SUBDIR_INPUT
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if project.novel_text:
        p = root / "小说原文.txt"
        p.write_text(project.novel_text, encoding="utf-8")
        written.append(p)
    info = {
        "工程id": project.id,
        "标题": project.title,
        "来源文件": project.source_filename,
        "字数": len(project.novel_text or ""),
        "画风": project.style,
        "画风说明": project.style_detail,
        "创建时间": project.created_at,
        "角色数": len(project.characters),
        "分镜数": len(project.shots),
        "图片工作流": getattr(config, "COMFY_WORKFLOW_NAME", ""),
        "文本模型": getattr(config, "TEXT_MODEL", ""),
    }
    written.append(_write_json(root / "工程信息.json", info))
    readme = root / "README.md"
    readme.write_text(
        "# 00_输入\n\n"
        "- `小说原文.txt`：本次生成使用的完整正文，逐字保存，便于回溯。\n"
        "- `工程信息.json`：工程 id、标题、来源、字数、画风、模型与工作流。\n",
        encoding="utf-8")
    written.append(readme)
    return written


def _write_analysis_bundle(project: Project, out_dir: Path) -> list[Path]:
    root = out_dir / config.OUTPUT_SUBDIR_ANALYSIS
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    written.append(_write_json(root / "分析结果.json", {
        "title": project.title,
        "summary": project.summary,
        "style": project.style,
        "style_detail": project.style_detail,
        "characters": [c.to_dict() for c in project.characters],
        "scenes": [s.to_dict() for s in project.scenes],
    }))
    (root / "故事梗概.md").write_text(
        f"# {project.title}\n\n## 故事梗概\n\n{project.summary or '（无）'}\n\n"
        f"## 画风\n\n{project.style or '（未设置）'}\n\n{project.style_detail or ''}\n",
        encoding="utf-8")
    chars = ["# 角色清单\n", _md_table(
        ["姓名", "性别", "年龄", "身份", "性格", "类型"],
        [(c.name, c.gender, c.age, c.identity, c.personality,
          "群众" if c.is_extra else "主要") for c in project.characters])]
    (root / "角色清单.md").write_text("\n".join(chars), encoding="utf-8")
    scenes = ["# 场景清单\n", _md_table(
        ["场景", "特征", "氛围"],
        [(s.name, s.features, s.atmosphere) for s in project.scenes])]
    (root / "场景清单.md").write_text("\n".join(scenes), encoding="utf-8")
    written.extend([root / "故事梗概.md", root / "角色清单.md", root / "场景清单.md"])
    if project.style_id:
        meta = stylelib.style_meta(project.style_id)
        style_doc = {
            "style_id": project.style_id,
            "generation_name": meta.get("generation_name", ""),
            "name_zh": meta.get("name_zh", ""),
            "name_en": meta.get("generation_name", ""),
            "reference": meta.get("reference", ""),
            "reference_zh": meta.get("reference_zh", ""),
            "reference_display": meta.get("reference_display", ""),
            "group": meta.get("group", ""),
            "group_zh": meta.get("group_zh", ""),
            "traits": meta.get("traits_positive") or meta.get("traits", ""),
            "color_ids": list(project.style_color_ids or []),
            "colors": [{"id": c, "name": (stylelib.color_meta(c) or {}).get("name_zh", c)}
                       for c in project.style_color_ids or []],
            "rationale": project.style_rationale,
            "tone_id": project.style_tone,
            "tone": stylelib.tone_meta(project.style_tone) if project.style_tone else {},
            "trigger": project.style_trigger,
            "medium": stylelib.style_medium(meta),
            "comic_features": stylelib.comic_feature_words(meta),
            "prompt_block": stylelib.style_prompt_block(
                project.style_id, project.style_color_ids or [],
                include_reference_note=False,
                tone=project.style_tone,
                trigger=project.style_trigger,
                text=f"{project.summary} {project.style_detail}"),
            "reference_image": meta.get("image_rel", ""),
        }
        written.append(_write_json(root / "绘制风格.json", style_doc))
        tone_meta = style_doc["tone"]
        md = ["# 绘制风格", "",
              f"- 编号：#{project.style_id}",
              f"- 风格名：{meta.get('name_zh') or meta.get('generation_name', '')}"
              + (f"（{meta.get('generation_name', '')}）"
                 if meta.get("generation_name") else ""),
              f"- 参考作者/风格：{meta.get('reference_display') or meta.get('reference', '')}",
              f"- 主题色：{'、'.join(project.style_color_ids or []) or '（未指定）'}",
              f"- 阶调：{project.style_tone or '—'} {tone_meta.get('name_zh', '')}".rstrip(),
              f"- 触发词：{project.style_trigger or '—'}",
              f"- 推荐理由：{project.style_rationale or '—'}", "",
              "## 送进图像模型的风格块", "",
              f"```text\n{style_doc['prompt_block']}\n```"]
        p = root / "绘制风格.md"
        p.write_text("\n".join(md), encoding="utf-8")
        written.append(p)
    return written


def _write_character_bundle(project: Project, out_dir: Path) -> list[Path]:
    root = out_dir / config.OUTPUT_SUBDIR_CHARACTERS
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    data = []
    for c in project.characters:
        item = c.to_dict()
        item["身份卡"] = c.identity_card()
        data.append(item)
        lines = [
            f"# {c.name} · 人设卡",
            "",
            f"- 性别/年龄：{c.gender} / {c.age}",
            f"- 身份：{c.identity}",
            f"- 性格：{c.personality}",
            f"- 身份卡（逐字复用）：{c.identity_card()}",
            f"- 唯一记忆点：{c.derived_identity_tag() or '（无）'}",
            f"- 身份参考：主视图 + 面部特写；侧身/背身镜头用单视图",
            f"- 预设资产：主视图={Path(c.main_visual).name if c.main_visual else '（无）'}；"
            f"面部特写={Path(c.face_ref).name if c.face_ref else '（无）'}；"
            f"单视图={'、'.join(sorted((c.views or {}).keys())) if c.views else '（无）'}",
            "",
            "## 硬特征",
            "",
            json.dumps(c.hard_features or {}, ensure_ascii=False, indent=2),
            "",
            "## 关键词",
            "",
            "、".join(c.keywords or []) or "（无）",
        ]
        p = root / f"人设卡_{_safe(c.name, '角色')}.md"
        p.write_text("\n".join(lines), encoding="utf-8")
        written.append(p)
    written.append(_write_json(root / "角色设定.json", data))
    return written


def _write_storyboard_bundle(project: Project, out_dir: Path) -> list[Path]:
    root = out_dir / config.OUTPUT_SUBDIR_STORYBOARD
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    written.append(_write_json(root / "分镜脚本.json", {
        "title": project.title,
        "shots": [s.to_dict() for s in project.shots],
    }))
    # 台词表：Excel 可直接打开
    csv_path = root / "台词表.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["分镜", "类型", "说话人", "台词", "槽位", "画外", "气泡槽位"])
        for s in project.shots:
            for d in s.dialogues:
                w.writerow([s.index, d.kind, d.speaker, d.text, d.slot,
                            "是" if d.offscreen else "", (s.bubble_plan or [{}])[0].get("slot", "") if s.bubble_plan else ""])
    written.append(csv_path)
    # 统计
    close = sum(1 for s in project.shots if s.shot_type in ("近景", "特写"))
    two = sum(1 for s in project.shots if len(s.characters or []) == 2)
    speech = sum(1 for s in project.shots for d in s.dialogues if d.kind == "speech")
    lines = [
        "# 分镜统计",
        "",
        f"- 总格数：{len(project.shots)}",
        f"- 页数：{len(project.pages)}（{project.render_mode or config.render_mode()}）",
        f"- 绘制风格：{project.style_id or project.style}"
        + (f"（{'、'.join(project.style_color_ids)}）" if project.style_color_ids else ""),
        f"- 双人同框：{two}（{two / max(1, len(project.shots)):.0%}）",
        f"- 近景+特写：{close}（{close / max(1, len(project.shots)):.0%}）",
        f"- 台词条数：{speech}",
        "",
        "## 逐格",
        "",
        _md_table(["格", "页/格位", "版式", "叙事功能", "景别", "画幅", "人物", "台词"],
                  [(s.index,
                    (f"P{project.page_by_id(s.page_id).index}-{s.panel_index + 1}"
                     if project.page_by_id(s.page_id) else "—"),
                    (project.page_by_id(s.page_id).layout_id
                     if project.page_by_id(s.page_id) else "—"),
                    s.beat or "—", s.shot_type, s.panel_scale,
                    "、".join(s.characters or []) or "—",
                    " / ".join(f"{d.speaker}:{d.text}" for d in s.dialogues if (d.text or '').strip()) or "—")
                   for s in project.shots]),
    ]
    p = root / "分镜统计.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    written.append(p)

    # 分页与版式（机器可读 + 人读）
    if project.pages:
        written.append(_write_json(root / "分页与版式.json", {
            "render_mode": project.render_mode or config.render_mode(),
            "note": project.page_plan_note,
            "pages": [pg.to_dict() for pg in project.pages],
        }))
        rows = []
        for pg in project.pages:
            rows.append([pg.index, pg.layout_id, pg.layout_name, pg.panel_count,
                         "、".join(str(i) for i in pg.shot_indices(project)),
                         pg.status, pg.rationale])
        md = ["# 分页与版式", "",
              f"- 分镜形态：{project.render_mode or config.render_mode()}",
              f"- 规划说明：{project.page_plan_note or '—'}", "",
              _md_table(["页", "版式", "名称", "格数", "包含分镜", "状态", "选择理由"], rows)]
        p = root / "分页与版式.md"
        p.write_text("\n".join(md), encoding="utf-8")
        written.append(p)
    return written


def _write_prompt_bundle(project: Project, out_dir: Path) -> list[Path]:
    root = out_dir / config.OUTPUT_SUBDIR_PROMPTS
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    # 1) 运行期原始提示词（每次调用的 system/user 原样）
    trace_src = Path(config.PROJECT_DIR) / project.id / "prompts"
    if trace_src.exists():
        trace_dst = root / "运行期"
        if trace_dst.exists():
            shutil.rmtree(trace_dst, ignore_errors=True)
        shutil.copytree(trace_src, trace_dst)
        written.append(trace_dst / "index.md" if (trace_dst / "index.md").exists() else trace_dst)
    # 2) 逐格出图提示词（从 06_image 回溯文件里抽）
    rows: list[dict] = []
    img_dir = trace_src / "06_image"
    if img_dir.exists():
        for f in sorted(img_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            messages = data.get("messages") or []
            prompt = ""
            for m in messages:
                if m.get("role") == "user":
                    prompt = m.get("content") or ""
            if prompt:
                rows.append({"shot": (data.get("meta") or {}).get("shot"),
                             "file": f.name, "prompt": prompt,
                             "meta": data.get("meta") or {},
                             "_mtime": f.stat().st_mtime})
    # 旧工程没有运行时回溯：按当前提示词构建器补一份，保证输出目录里一定有
    have = {r.get("shot") for r in rows}
    for s in project.shots:
        if s.index in have:
            continue
        cast = []
        for name in s.characters or []:
            c = project.find_character(name)
            if not c:
                continue
            cast.append({"name": c.name,
                         "card": prompts_mod.character_feature_lock(c) or c.identity_card(),
                         "side": (s.char_sides or {}).get(c.name, ""),
                         "position": (s.char_positions or {}).get(c.name, "")})
        try:
            text = prompts_mod.shot_image_prompt(
                project, s, cast=cast, include_bubbles=bool(s.bubble_plan))
            text = prompt_overrides.text(
                project, "image.shot_prompt", f"shot:{s.id}", text)
        except Exception:  # noqa: BLE001
            text = ""
        if text:
            rows.append({"shot": s.index, "file": "（构建器补）", "prompt": text,
                         "meta": {"shot": s.index, "shot_type": s.shot_type,
                                  "characters": "、".join(s.characters or []) or "（无）"}})
    if rows:
        # 回朔目录会保留每次返修 prompt；导出时每格只留最后一版，
        # 避免旧版（没接风格触发块）混进成品说明。
        latest: dict[Any, dict] = {}
        order: list[Any] = []
        for r in rows:
            key = r.get("shot")
            if key not in latest:
                order.append(key)
                latest[key] = r
            elif float(r.get("_mtime", 0)) >= float(latest[key].get("_mtime", 0)):
                latest[key] = r
        rows = [latest[k] for k in order]
        for r in rows:
            r.pop("_mtime", None)
        p = root / "逐格提示词.jsonl"
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                     encoding="utf-8")
        written.append(p)
    # 3) 气泡计划
    bubbles = [{"shot": s.index, "plan": s.bubble_plan or [],
                "debug": s.bubble_debug or {}, "layouts": s.bubble_layout or []}
               for s in project.shots]
    written.append(_write_json(root / "气泡计划.json", bubbles))
    # 4) 索引
    lines = ["# 04_提示词", "",
             "- `运行期/`：每次真正发给文本/图像模型的提示词（按阶段分目录）。",
             "- `逐格提示词.jsonl`：每格最终出图提示词（含返修轮次）。",
             "- `气泡计划.json`：每格气泡坐标、检测结果与回填布局。",
             "- 规则库：项目根目录 `rules/`（角色一致性 / 分镜 / 提示词 / 校验）。",
             ""]
    p = root / "README.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    written.append(p)
    return written




def _write_trace_bundle(project: Project, out_dir: Path) -> list[Path]:
    root = out_dir / config.OUTPUT_SUBDIR_TRACE
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    rows = []
    for s in project.shots:
        rows.append({
            "分镜": s.index,
            "状态": s.status,
            "自检分": s.quality_score,
            "问题": list(s.quality_issues or []),
            "气泡": s.bubble_debug or {},
            "尝试次数": s.attempts,
            "返修备注": s.image_note or "",
            "提示词长度": len(s.prompt or "") if hasattr(s, "prompt") else None,
            "图片": s.image_path,
            "原图": s.raw_image_path,
        })
    written.append(_write_json(root / "质量报告.json", rows))
    bad = [r for r in rows if r["问题"] or r["状态"] == "失败"]
    lines = ["# 08_回溯", "",
             f"- 分镜总数：{len(rows)}",
             f"- 有问题/失败：{len(bad)}",
             "",
             "## 有问题的格子", ""]
    if bad:
        lines += [f"- 第 {r['分镜']} 格：{r['状态']} / {r['自检分']} 分 / "
                  f"{'、'.join(r['问题']) or '无'} / 尝试 {r['尝试次数']} 次"
                  for r in bad]
    else:
        lines.append("- 无")
    lines += ["", "提示词原文见 `04_提示词/运行期/`。", ""]
    p = root / "README.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    written.append(p)
    return written

def _write_manifest(project: Project, out_dir: Path, written: Sequence[Path],
                    counts: dict) -> Path:
    root = out_dir / config.OUTPUT_SUBDIR_MANIFEST
    root.mkdir(parents=True, exist_ok=True)
    files = []
    for p in sorted(out_dir.rglob("*")):
        if not p.is_file():
            continue
        try:
            rel = p.resolve().relative_to(out_dir.resolve())
        except ValueError:
            rel = Path(p.name)
        files.append(str(rel).replace("\\", "/"))
    manifest = {
        "生成时间": datetime.now().isoformat(timespec="seconds"),
        "工程id": project.id,
        "标题": project.title,
        "来源文件": project.source_filename,
        "字数": len(project.novel_text or ""),
        "画风": project.style,
        "文本模型": getattr(config, "TEXT_MODEL", ""),
        "图像工作流": getattr(config, "COMFY_WORKFLOW_NAME", ""),
        "分镜数": len(project.shots),
        "角色数": len(project.characters),
        "统计": counts,
        "文件": sorted(set(files)),
    }
    p = _write_json(root / "导出清单.json", manifest)
    readme = root / "README.md"
    readme.write_text(
        "# 输出目录说明\n\n"
        "```\n"
        "00_输入/      小说原文、工程信息\n"
        "01_分析/      分析结果、梗概、角色/场景清单\n"
        "02_角色/      人设卡、角色设定 JSON、主视图/姿态总图\n"
        "03_分镜/      分镜脚本 JSON/MD/TXT、台词表、统计\n"
        "04_提示词/    逐格提示词、气泡计划、运行期完整回溯\n"
        "05_分镜图/    带气泡成品；原图/ 为无气泡纯画面\n"
        "06_条漫/      条漫成品\n"
        "07_阅读/      PDF/阅读文件\n"
        "08_回溯/      （预留）\n"
        "09_清单/      manifest 与目录说明\n"
        "```\n", encoding="utf-8")
    return p


def export_project(project: Project,
                   folder_name: str | None = None,
                   progress_cb: Callable[[str, float], None] | None = None,
                   generate_reader: bool | None = None,
                   make_zip: bool = True) -> dict:
    """
    把所有结果整理并输出到输出文件夹。

    返回 {"output_dir":..., "zip_path":..., "files":[...], "counts":{...}}
    """
    def emit(msg: str, value: float) -> None:
        log.info(msg)
        if progress_cb:
            try:
                progress_cb(msg, max(0.0, min(1.0, value)))
            except Exception:  # noqa: BLE001
                pass

    out_dir = config.resolve_output_dir(folder_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    project.output_dir = str(out_dir)
    project.output_folder_name = out_dir.name

    generate_reader = config.GENERATE_READER_FILE if generate_reader is None else generate_reader
    ext = _img_ext()
    written: list[Path] = []
    counts = {"输入": 0, "分析": 0, "角色文档": 0, "分镜文档": 0, "提示词": 0,
              "分镜图": 0, "分镜原图": 0, "分格页": 0, "角色图": 0,
              "阅读文件": 0, "文档": 0, "条漫长图": 0}

    # ---- 0) 输入 / 分析 / 角色 / 分镜 / 提示词（分目录，先于图片写）----
    emit("正在归档小说输入与分析结果…", 0.02)
    written.extend(_write_input_bundle(project, out_dir))
    counts["输入"] = 2 if project.novel_text else 1
    written.extend(_write_analysis_bundle(project, out_dir))
    counts["分析"] = 4 + (2 if project.style_id else 0)
    written.extend(_write_character_bundle(project, out_dir))
    counts["角色文档"] = len(project.characters) + 1
    written.extend(_write_storyboard_bundle(project, out_dir))
    counts["分镜文档"] = 3
    written.extend(_write_prompt_bundle(project, out_dir))
    counts["提示词"] = 2 + (1 if (Path(config.PROJECT_DIR) / project.id / "prompts").exists() else 0)

    # ---- 1) 分镜成品 ----
    emit("正在整理分镜成品图…", 0.05)
    shots_dir = out_dir / config.OUTPUT_SUBDIR_SHOTS
    raw_dir = out_dir / config.OUTPUT_SUBDIR_SHOTS_RAW
    shots_dir.mkdir(parents=True, exist_ok=True)
    for shot in project.shots:
        src = shot.image_path or shot.raw_image_path
        if not (src and Path(src).exists()):
            continue
        target = shots_dir / f"{config.SHOT_FILENAME_TEMPLATE.format(index=shot.index)}.{ext}"
        try:
            _save_image(Image.open(src), target)
            written.append(target)
            counts["分镜图"] += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("导出第 %s 格失败：%s", shot.index, exc)

    # ---- 2) 纯画面原图 ----
    if config.EXPORT_RAW_SHOTS:
        emit("正在整理无气泡原图…", 0.2)
        raw_dir.mkdir(parents=True, exist_ok=True)
        for shot in project.shots:
            src = shot.raw_image_path
            if not (src and Path(src).exists()):
                continue
            target = raw_dir / f"{config.SHOT_FILENAME_TEMPLATE.format(index=shot.index)}.{ext}"
            try:
                _save_image(Image.open(src), target)
                written.append(target)
                counts["分镜原图"] += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("导出第 %s 格原图失败：%s", shot.index, exc)

    # ---- 2.5) 分格页（一页多格）----
    if project.pages:
        emit("正在整理分格页…", 0.28)
        pages_dir = shots_dir / "页面"
        pages_dir.mkdir(parents=True, exist_ok=True)
        try:
            pagelayout.compose_all_pages(project)
        except Exception as exc:  # noqa: BLE001
            log.warning("导出前合成页面失败：%s", exc)
        for page in project.pages:
            src = page.image_path
            if not (src and Path(src).exists()):
                continue
            target = pages_dir / f"{config.PAGE_FILENAME_TEMPLATE.format(index=page.index)}.{ext}"
            try:
                _save_image(Image.open(src), target)
                written.append(target)
                counts["分格页"] += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("导出第 %s 页失败：%s", page.index, exc)

    # ---- 3) 角色设定图 ----
    emit("正在整理角色设定图…", 0.35)
    char_root = out_dir / config.OUTPUT_SUBDIR_CHARACTERS
    for c in project.characters:
        cdir = char_root / _safe(c.name, "角色")
        for label, src, fname in (
            ("主视图", c.main_visual, config.CHARACTER_MAIN_FILENAME),
            ("面部特写", c.face_ref, "面部特写"),
            ("侧面单视图", (c.views or {}).get("side"), "侧面"),
            ("背面单视图", (c.views or {}).get("back"), "背面"),
            ("姿态总图", c.pose_sheet, config.CHARACTER_POSE_FILENAME),
            ("俯视图", c.top_view, config.CHARACTER_TOP_FILENAME),
        ):
            if src and Path(src).exists():
                target = cdir / f"{fname}.{ext}"
                try:
                    _save_image(Image.open(src), target)
                    written.append(target)
                    counts["角色图"] += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("导出角色 %s 的%s失败：%s", c.name, label, exc)
        # 其他造型
        for look_name, look in (c.extra_looks or {}).items():
            ldir = char_root / _safe(c.name, "角色") / _safe(look_name, "造型")
            for key, fname in (("main_visual", config.CHARACTER_MAIN_FILENAME),
                               ("pose_sheet", config.CHARACTER_POSE_FILENAME),
                               ("top_view", config.CHARACTER_TOP_FILENAME)):
                src = (look or {}).get(key)
                if src and Path(src).exists():
                    target = ldir / f"{fname}.{ext}"
                    try:
                        _save_image(Image.open(src), target)
                        written.append(target)
                        counts["角色图"] += 1
                    except Exception as exc:  # noqa: BLE001
                        log.warning("导出造型 %s 失败：%s", look_name, exc)

    # ---- 4) 文档 ----
    emit("正在生成分镜描述文档…", 0.5)
    docs_dir = out_dir / config.OUTPUT_SUBDIR_DOCS
    docs_dir.mkdir(parents=True, exist_ok=True)
    md_text = build_storyboard_markdown(project)
    fmt = (config.STORYBOARD_DOC_FORMAT or "both").lower()
    if fmt in ("markdown", "md", "both"):
        p = docs_dir / "分镜脚本.md"
        p.write_text(md_text, encoding="utf-8")
        written.append(p)
        counts["文档"] += 1
    if fmt in ("txt", "text", "both"):
        p = docs_dir / "分镜脚本.txt"
        p.write_text(build_storyboard_txt(project), encoding="utf-8")
        written.append(p)
        counts["文档"] += 1
    if config.EXPORT_PROJECT_JSON:
        p = out_dir / config.OUTPUT_SUBDIR_MANIFEST / "工程数据.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        written.append(p)
        counts["文档"] += 1

    # ---- 5) 漫画阅读文件 ----
    if generate_reader:
        emit("正在生成漫画阅读文件（PDF / 条漫长图）…", 0.62)
        reader_dir = out_dir / config.OUTPUT_SUBDIR_READER
        reader_dir.mkdir(parents=True, exist_ok=True)
        shot_files = sorted(shots_dir.glob(f"*{ext}"), key=lambda p: p.name)
        # 分格页优先：有页面成品就按页拼长条；没有页面的分镜再按单格补进来
        page_tiles = pagelayout.strip_tiles(project)
        strip_sources = page_tiles or shot_files
        if project.pages:
            pending = [p.index for p in project.pages if p.status != "已合成"]
            if pending:
                log.warning("有 %d 页未合成（第 %s 页），本次条漫会跳过这些页；"
                            "补完缺失格子后点「合成页面」再导出",
                            len(pending), "、".join(str(i) for i in pending[:12]))
        strip_files: list[Path] = []
        if shot_files:
            webtoon = bool(getattr(config, "WEBTOON_MODE", False))

            # 5.1) 条漫成品：封面 + 话标题条 + 墨线分格 + 尾页
            if webtoon and getattr(config, "WEBTOON_STRIP_ENABLED", True):
                emit("正在排版条漫成品（封面 / 分格 / 尾页）…", 0.64)
                try:
                    strip_dir = out_dir / config.OUTPUT_SUBDIR_STRIP
                    # 封面底图优先用"没有气泡计划"的纯画面格，避免把台词模糊在封面上
                    cover_backdrop = None
                    for s in project.shots:
                        if s.raw_image_path and Path(s.raw_image_path).exists() \
                                and not (s.bubble_plan or []):
                            cover_backdrop = s.raw_image_path
                            break
                    ep = episode_mod.build_episode_strip(
                        strip_sources, project, strip_dir,
                        cover_backdrop=cover_backdrop,
                        progress_cb=lambda m: log.info("  · 条漫 %s", m))
                    strip_files = [Path(m) for m in ep.get("files") or []]
                    for p in strip_files:
                        written.append(p)
                        counts["条漫长图"] += 1
                    cover_path = ep.get("cover") or ""
                    if cover_path and Path(cover_path).exists():
                        target = reader_dir / "封面.png"
                        _save_image(Image.open(cover_path), target)
                        written.append(target)
                        counts["阅读文件"] += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("条漫成品排版失败，退回无缝拼接：%s", exc)
                    try:
                        strip_dir = out_dir / config.OUTPUT_SUBDIR_STRIP
                        made = build_webtoon_strip(
                            strip_sources, strip_dir,
                            progress_cb=lambda m: log.info("  · 条漫 %s", m))
                        strip_files = [Path(m) for m in made]
                        for p in strip_files:
                            written.append(p)
                            counts["条漫长图"] += 1
                    except Exception as exc2:  # noqa: BLE001
                        log.warning("条漫长图生成失败：%s", exc2)

            # 5.2) PDF：条漫模式按"一个分块一页"排版，手机上可以顺着往下读
            try:
                pdf_path = reader_dir / "漫画阅读.pdf"
                by_strip = (strip_files and
                            getattr(config, "WEBTOON_PDF_BY_STRIP", True))
                build_pdf(strip_files if by_strip else strip_sources, pdf_path)
                written.append(pdf_path)
                counts["阅读文件"] += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("PDF 生成失败：%s", exc)

            # 5.3) 旧版长条图：仅非条漫模式（一格一张方图）保留
            if not webtoon:
                try:
                    strip_path = reader_dir / "漫画长条图.png"
                    build_long_strip(shot_files, strip_path)
                    written.append(strip_path)
                    counts["阅读文件"] += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("长条图生成失败：%s", exc)

    # ---- 5.4) 回溯 ----
    written.extend(_write_trace_bundle(project, out_dir))

    # ---- 5.5) 清单 ----
    try:
        written.append(_write_manifest(project, out_dir, written, counts))
    except Exception as exc:  # noqa: BLE001
        log.warning("导出清单生成失败：%s", exc)

    # ---- 6) 打包 ----
    zip_path = ""
    if make_zip:
        emit("正在打包压缩…", 0.85)
        try:
            zip_path = str(make_archive(out_dir))
            counts["压缩包"] = 1
        except Exception as exc:  # noqa: BLE001
            log.warning("打包失败：%s", exc)

    project.zip_path = zip_path
    project.save()
    emit(f"导出完成，共 {len(written)} 个文件，输出目录：{out_dir}", 1.0)
    return {
        "output_dir": str(out_dir),
        "zip_path": zip_path,
        "files": [str(p) for p in written],
        "counts": counts,
    }


# =============================================================================
# PDF
# =============================================================================

def build_pdf(image_paths: Sequence[str | Path], pdf_path: str | Path,
              mode: str | None = None) -> str:
    """把分镜图按顺序生成 PDF（一页一图）。"""
    paths = [Path(p) for p in image_paths if Path(p).exists()]
    if not paths:
        raise ExportError("没有可用于生成 PDF 的图片")
    mode = mode or config.PDF_PAGE_MODE

    try:
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover
        raise ExportError("缺少 reportlab，无法生成 PDF。请执行："
                          "env\\Scripts\\python.exe -m pip install reportlab") from exc

    target = Path(pdf_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    c = canvas.Canvas(str(target))
    c.setTitle(target.stem)
    for p in paths:
        img = Image.open(p)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        # 每页使用图片自身尺寸：条漫分块高度不同，绝不能拿第一张的页面尺寸去拉伸。
        pw, ph = img.size
        c.setPageSize((pw, ph))
        c.drawImage(ImageReader(img), 0, 0, width=pw, height=ph,
                    preserveAspectRatio=True, mask=None)
        c.showPage()
    c.save()
    log.info("PDF 已生成：%s（%d 页）", target, len(paths))
    return str(target)


# =============================================================================
# 长条图
# =============================================================================

def build_long_strip(image_paths: Sequence[str | Path],
                     out_path: str | Path,
                     width: int | None = None,
                     gap: int | None = None) -> str:
    """把分镜图按阅读顺序竖向拼接成长条图。"""
    paths = [Path(p) for p in image_paths if Path(p).exists()]
    if not paths:
        raise ExportError("没有可用于生成长条图的图片")
    width = width or config.LONG_STRIP_WIDTH
    gap = config.LONG_STRIP_GAP if gap is None else gap

    tiles: list[Image.Image] = []
    for p in paths:
        img = Image.open(p).convert("RGB")
        ratio = width / img.width
        tiles.append(img.resize((width, max(1, int(img.height * ratio))), Image.LANCZOS))

    total_h = sum(t.height for t in tiles) + gap * (len(tiles) - 1)
    canvas_img = Image.new("RGB", (width, total_h), (255, 255, 255))
    y = 0
    for t in tiles:
        canvas_img.paste(t, (0, y))
        y += t.height + gap

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas_img.save(target, format="PNG")
    log.info("长条图已生成：%s（%d×%d）", target, canvas_img.width, canvas_img.height)
    return str(target)


# =============================================================================
# 条漫长图（无缝竖拼）
# =============================================================================

def _pack_strip_chunks(tiles: Sequence[Image.Image],
                       max_height: int) -> list[list[Image.Image]]:
    """把拼图块按上限高度分块；保证每块至少有一格。"""
    chunks: list[list[Image.Image]] = []
    cur: list[Image.Image] = []
    cur_h = 0
    for t in tiles:
        if cur and cur_h + t.height > max_height:
            chunks.append(cur)
            cur, cur_h = [], 0
        cur.append(t)
        cur_h += t.height
    if cur:
        chunks.append(cur)
    return chunks


def build_webtoon_strip(image_paths: Sequence[str | Path],
                        out_dir: str | Path,
                        width: int | None = None,
                        max_height: int | None = None,
                        name_template: str | None = None,
                        progress_cb: Callable[[str], None] | None = None) -> list[str]:
    """
    把分镜成品按阅读顺序无缝竖拼成条漫长图。

    与旧 build_long_strip 的区别：
      1. 所有格子统一宽度、格子之间**零间距**（无缝衔接），而不是留白边；
      2. 总高超过 max_height 时自动切成多块（条漫_01.png / 条漫_02.png …），
         既符合手机阅读习惯，也避免单文件过大打不开。

    返回生成的文件路径列表。
    """
    paths = [Path(p) for p in image_paths if Path(p).exists()]
    if not paths:
        raise ExportError("没有可用于生成条漫长图的图片")

    width = int(width or config.WEBTOON_PANEL_WIDTH)
    max_height = int(max_height or config.WEBTOON_STRIP_MAX_HEIGHT)
    template = name_template or config.WEBTOON_STRIP_FILENAME_TEMPLATE
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tiles: list[Image.Image] = []
    for p in paths:
        img = Image.open(p).convert("RGB")
        if img.width != width:
            ratio = width / img.width
            img = img.resize((width, max(1, int(img.height * ratio))), Image.LANCZOS)
        tiles.append(img)

    chunks = _pack_strip_chunks(tiles, max_height)
    written: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        total_h = sum(t.height for t in chunk)
        canvas_img = Image.new("RGB", (width, total_h), (0, 0, 0))
        y = 0
        for t in chunk:
            canvas_img.paste(t, (0, y))     # 零间距：无缝相接
            y += t.height
        target = out_dir / f"{template.format(index=i)}.png"
        canvas_img.save(target, format="PNG")
        written.append(str(target))
        if progress_cb:
            try:
                progress_cb(f"{target.name}（{width}×{total_h}，{len(chunk)} 格）")
            except Exception:  # noqa: BLE001
                pass
        log.info("条漫长图已生成：%s（%d×%d，%d 格）",
                 target, width, total_h, len(chunk))
    return written


# =============================================================================
# 压缩包
# =============================================================================

def make_archive(folder: str | Path, zip_path: str | Path | None = None) -> str:
    """把输出文件夹压成一个 zip（放在输出根目录下）。"""
    folder = Path(folder)
    if not folder.exists():
        raise ExportError(f"输出目录不存在：{folder}")
    target = Path(zip_path) if zip_path else \
        Path(config.OUTPUT_ROOT) / config.ZIP_FILENAME_TEMPLATE.format(
            folder_name=folder.name)
    target.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in folder.rglob("*"):
            if p.is_file():
                zf.write(p, arcname=str(Path(folder.name) / p.relative_to(folder)))
    log.info("压缩包已生成：%s", target)
    return str(target)


def zip_specific_files(paths: Iterable[str | Path], zip_path: str | Path,
                       arc_prefix: str = "") -> str:
    """把指定的若干文件单独打包（单张图片 / 某角色的三视图下载用）。"""
    target = Path(zip_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for item in paths:
            p = Path(item)
            if not p.exists() or not p.is_file():
                continue
            name = p.name
            if name in used:
                name = f"{p.parent.name}_{name}"
            used.add(name)
            arc = f"{arc_prefix}/{name}" if arc_prefix else name
            zf.write(p, arcname=arc)
    return str(target)


# =============================================================================
# 单独下载
# =============================================================================

def copy_single(src: str | Path, dest_dir: str | Path | None = None) -> str:
    """把单张图片复制到"单张下载"目录，返回新路径（供界面下载组件使用）。"""
    src = Path(src)
    if not src.exists():
        raise ExportError(f"文件不存在：{src}")
    dest_dir = Path(dest_dir) if dest_dir else Path(config.OUTPUT_ROOT) / "_单张下载"
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / src.name
    if target.exists():
        target = dest_dir / f"{src.stem}_{datetime.now():%H%M%S}{src.suffix}"
    shutil.copyfile(src, target)
    return str(target)
