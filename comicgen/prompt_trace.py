# -*- coding: utf-8 -*-
"""提示词回溯（prompt trace）。

每一次真正发给模型的提示词（分析 / 人设 / 分镜 / 导演 / 旁白 / 出图 / 返修）
都会落盘到：

    data/projects/<工程id>/prompts/<阶段>/<序号>_<名称>.json
    data/projects/<工程id>/prompts/<阶段>/<序号>_<名称>.md

json 是给程序/对比用的原始结构，md 是给人看的（带系统提示、用户提示、变量）。
工程目录下还有 `prompts/index.md` 汇总本工程产生过的全部提示词。

这样做的好处：
  · 任何一张图都能倒查"当时用的是哪版提示词"；
  · 改规则/改代码后，可以对比新旧 trace，定位是哪一段导致画面变化；
  · 不需要复现模型，也能检查"气泡坐标、人数、身份卡"有没有写进去。

开关：config.PROMPT_TRACE_ENABLED（默认开）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import config
from .logger import get_logger

log = get_logger(__name__)

_COUNTER: dict[str, int] = {}


def _safe(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", str(name or "")).strip("_")
    return name[:60] or "prompt"


def trace_root(project_id: str) -> Path:
    return Path(config.PROJECT_DIR) / project_id / "prompts"


def _next_seq(stage: str) -> int:
    _COUNTER[stage] = _COUNTER.get(stage, 0) + 1
    return _COUNTER[stage]


def record(project_id: str, stage: str, name: str,
           messages: Sequence[dict], meta: dict[str, Any] | None = None,
           overwrite: bool = False) -> Path | None:
    """
    记录一次提示词调用。

    messages 与传给 textclient 的完全一致：[{"role": "system"/"user", "content": ...}]
    返回写出的 json 路径；关闭或失败时返回 None（绝不影响主流程）。
    """
    if not bool(getattr(config, "PROMPT_TRACE_ENABLED", True)):
        return None
    try:
        root = trace_root(project_id) / stage
        root.mkdir(parents=True, exist_ok=True)
        seq = 1 if overwrite else _next_seq(stage)
        base = root / f"{seq:03d}_{_safe(name)}"
        payload = {
            "project_id": project_id,
            "stage": stage,
            "name": name,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "meta": meta or {},
            "messages": [{"role": m.get("role", ""), "content": m.get("content", "")}
                         for m in messages],
        }
        base.with_suffix(".json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        base.with_suffix(".md").write_text(render_markdown(payload), encoding="utf-8")
        _append_index(project_id, stage, base.with_suffix(".json").name, meta or {})
        return base.with_suffix(".json")
    except Exception as exc:  # noqa: BLE001
        log.debug("提示词回溯写入失败：%s", exc)
        return None


def record_data(project_id: str, stage: str, name: str,
                data: dict[str, Any], overwrite: bool = False) -> Path | None:
    """记录结构化数据（如气泡计划/检测结果），不要求是 messages。"""
    if not bool(getattr(config, "PROMPT_TRACE_ENABLED", True)):
        return None
    try:
        root = trace_root(project_id) / stage
        root.mkdir(parents=True, exist_ok=True)
        seq = 1 if overwrite else _next_seq(stage)
        base = root / f"{seq:03d}_{_safe(name)}"
        payload = {
            "project_id": project_id,
            "stage": stage,
            "name": name,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data": data,
        }
        base.with_suffix(".json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [f"# {stage} / {name}", "",
                 f"- 时间：{payload['created_at']}", ""]
        for key, value in (data or {}).items():
            lines.append(f"## {key}\n")
            lines.append("```json")
            lines.append(json.dumps(value, ensure_ascii=False, indent=2))
            lines.append("```\n")
        base.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
        _append_index(project_id, stage, base.with_suffix(".json").name, {})
        return base.with_suffix(".json")
    except Exception as exc:  # noqa: BLE001
        log.debug("结构化回溯写入失败：%s", exc)
        return None


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [f"# {payload.get('stage', '')} / {payload.get('name', '')}", "",
             f"- 工程：`{payload.get('project_id', '')}`",
             f"- 时间：{payload.get('created_at', '')}"]
    for key, value in (payload.get("meta") or {}).items():
        lines.append(f"- {key}：`{value}`")
    lines.append("")
    for msg in payload.get("messages") or []:
        role = msg.get("role", "")
        lines.append(f"## {role}")
        lines.append("")
        lines.append("```text")
        lines.append(str(msg.get("content", "")))
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def _append_index(project_id: str, stage: str, filename: str,
                  meta: dict[str, Any]) -> None:
    try:
        root = trace_root(project_id)
        index = root / "index.md"
        header = "# 本工程提示词回溯索引\n\n> 每次真正发给模型的提示词都会记在这里。\n\n"
        if not index.exists():
            index.write_text(header + f"| 时间 | 阶段 | 文件 | 备注 |\n| --- | --- | --- | --- |\n",
                             encoding="utf-8")
        note = "、".join(f"{k}={v}" for k, v in (meta or {}).items() if v not in (None, ""))
        line = (f"| {datetime.now().strftime('%H:%M:%S')} | {stage} | "
                f"[{filename}]({stage}/{filename}) | {note} |\n")
        with index.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:  # noqa: BLE001
        pass


def record_text(project_id: str, stage: str, name: str,
                system: str, user: str, meta: dict[str, Any] | None = None) -> Path | None:
    """便捷入口：直接给 system/user 文本。"""
    return record(project_id, stage, name,
                  [{"role": "system", "content": system},
                   {"role": "user", "content": user}], meta=meta)
