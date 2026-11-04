# -*- coding: utf-8 -*-
"""界面里的「规则与工具」工作台。

把 ``rules/``（规则文档、运行期 JSON、参考报告）与 ``tools/``（压测 / 封面脚本）
映射到界面对应步骤，允许直接查看、编辑并写回磁盘。

- 规则 JSON 保存后会由 ``comicgen.rules`` / ``stylelib`` 的 mtime 缓存自动热更新；
- 规则 Markdown 是说明文档，保存只更新文档，不直接改变运行逻辑；
- 工具脚本保存会替换 ``tools/`` 下的文件，但不会自动执行。
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import config


BASE_DIR = Path(config.BASE_DIR)
BACKUP_DIR = Path(config.DATA_DIR) / "resource_backups"

STEPS: dict[str, dict[str, str]] = {
    "2": {"title": "内容分析"},
    "3": {"title": "角色三视图与俯视图"},
    "4": {"title": "分镜拆解"},
    "5": {"title": "25格分镜"},
    "6": {"title": "姿态图"},
    "7": {"title": "出图与打包"},
}

GROUP_NAMES = {
    "rule-doc": "规则文档",
    "rule-data": "运行期规则 JSON",
    "tool": "工具脚本",
    "reference": "参考文档",
}

_KIND_BY_SUFFIX = {".md": "markdown", ".json": "json", ".py": "python"}


def _tool(name: str, steps: list[str], purpose: str, usage: str) -> dict[str, Any]:
    return {
        "id": f"tool.{name.removesuffix('.py')}",
        "group": "tool", "steps": steps, "path": f"tools/{name}",
        "title": name, "purpose": purpose, "usage": usage,
        "effect": "保存后替换 tools/ 下的脚本文件；不会自动执行，需按命令手动运行",
    }


RESOURCES: list[dict[str, Any]] = [
    # ---- 规则文档 ----
    {
        "id": "rule.readme", "group": "reference", "steps": ["7"],
        "path": "rules/README.md", "title": "规则库总览",
        "purpose": "规则目录索引、运行时读取关系、维护约定与变更记录",
        "effect": "参考文档：不影响运行",
    },
    {
        "id": "rule.diagnosis", "group": "reference", "steps": ["7"],
        "path": "rules/00_诊断报告.md", "title": "诊断报告",
        "purpose": "现状问题、证据、根因与修复清单；写新规则前先看",
        "effect": "参考文档：不影响运行",
    },
    {
        "id": "rule.identity", "group": "rule-doc", "steps": ["3"],
        "path": "rules/01_角色一致性规则.md", "title": "角色一致性规则",
        "purpose": "身份卡、辨识度、多角色同框、差异化与升级路线",
        "effect": "说明文档：运行时以 rules/data/identity_rules.json 为准",
    },
    {
        "id": "rule.storyboard", "group": "rule-doc", "steps": ["4", "5"],
        "path": "rules/02_分镜与镜头规则.md", "title": "分镜与镜头规则",
        "purpose": "可拍性描述公式、人数政策、台词归属、节奏与分页",
        "effect": "说明文档：运行时以 rules/data/storyboard_rules.json 为准",
    },
    {
        "id": "rule.prompt", "group": "rule-doc", "steps": ["7"],
        "path": "rules/03_提示词工程规则.md", "title": "提示词工程规则",
        "purpose": "拼装顺序、长度预算、正 / 负向提示词、handraw 风格块与模板",
        "effect": "说明文档：运行时以 rules/data/shot_prompt_rules.json 为准",
    },
    {
        "id": "rule.repair", "group": "rule-doc", "steps": ["7"],
        "path": "rules/04_校验与返修规则.md", "title": "校验与返修规则",
        "purpose": "出图前后门禁、负面条件、分页校验、返修措辞与已知限制",
        "effect": "说明文档：运行时规则由 comicgen/quality.py 执行",
    },
    {
        "id": "rule.object", "group": "rule-doc", "steps": ["4", "5"],
        "path": "rules/06_物件格语义.md", "title": "物件格语义",
        "purpose": "物证照 / 屏幕界面 / 环境细节三类物件镜头的写法与肤色自检",
        "effect": "说明文档：影响物件格提示词的写法",
    },
    {
        "id": "rule.style", "group": "rule-doc", "steps": ["2", "4", "5"],
        "path": "rules/07_风格与分镜版式.md", "title": "风格与分镜版式",
        "purpose": "handraw-style 画风 / 主题色 / 阶调 / 触发词 / 版式 / 工作流",
        "effect": "说明文档：运行时以 stylelib 与 data/*.json 为准",
    },
    {
        "id": "rule.refworkflow", "group": "rule-doc", "steps": ["3", "6"],
        "path": "rules/08_角色参考工作流选型.md", "title": "角色参考工作流选型",
        "purpose": "参考工作流取舍（Flux Kontext / FLUX.2 Klein / 8GB / LoRA）",
        "effect": "参考文档：影响角色参考方案的选择",
    },
    {
        "id": "rule.pose", "group": "rule-doc", "steps": ["3", "6"],
        "path": "rules/09_主视图与姿态图.md", "title": "主视图与姿态图",
        "purpose": "主视图 + 姿态总图 + 自定义骨架的当前角色参考方案",
        "effect": "说明文档：姿态控制由 pipeline / poseplan 执行",
    },
    # ---- 运行期规则 JSON ----
    {
        "id": "data.identity", "group": "rule-data", "steps": ["3"],
        "path": "rules/data/identity_rules.json", "title": "身份卡与撞车规则",
        "purpose": "身份卡字数、撞车判定、特征词、差异化词池",
        "effect": "运行时规则：保存后按文件修改时间热更新，下一次生成生效",
    },
    {
        "id": "data.shot_prompt", "group": "rule-data", "steps": ["7"],
        "path": "rules/data/shot_prompt_rules.json", "title": "出图提示词规则",
        "purpose": "段落顺序、长度预算、人数 / 角色 / 说话人措辞、禁用词替换",
        "effect": "运行时规则：保存后按文件修改时间热更新，下一次生成生效",
    },
    {
        "id": "data.storyboard", "group": "rule-data", "steps": ["4"],
        "path": "rules/data/storyboard_rules.json", "title": "分镜校验规则",
        "purpose": "描述校验、人数政策、气泡锚点、节奏阈值",
        "effect": "运行时规则：保存后按文件修改时间热更新，下一次生成生效",
    },
    {
        "id": "data.layouts", "group": "rule-data", "steps": ["4", "5"],
        "path": "rules/data/storyboard_layouts.json", "title": "分镜版式目录",
        "purpose": "SB-* / WB-* 版式的格数、几何、适配度与强项标签",
        "effect": "运行时规则：保存后热更新，分页规划与版式选择下一次生效",
    },
    {
        "id": "data.tones", "group": "rule-data", "steps": ["2"],
        "path": "rules/data/tones_extended.json", "title": "扩展阶调数据",
        "purpose": "T-17–T-40 扩展阶调：明暗 / 反差 / 氛围 / 光线 / 色彩",
        "effect": "运行时规则：保存后热更新，风格选择与触发词下一次生效",
    },
    # ---- 工具脚本 ----
    _tool("cast_multi_bench.py", ["3"],
          "三人 / 四人同框压测：验证多人不是被拆光，而是真的进同一张图。",
          "env\\Scripts\\python.exe tools\\cast_multi_bench.py --count 3"),
    _tool("guard_bench.py", ["4"],
          "分镜提示词压测台：固定角色 / 种子 / 画幅，A/B 对照撞脸与乱入。",
          "env\\Scripts\\python.exe tools\\guard_bench.py --tag baseline"),
    _tool("pipeline_guard_check.py", ["6", "7"],
          "真实链路抽检：走 Pipeline.generate_one_shot，验证姿态 / 参考图 / 提示词一起工作。",
          "env\\Scripts\\python.exe tools\\pipeline_guard_check.py"),
    _tool("cover_72h.py", ["7"],
          "《第七十二小时》封面底图初版：先出无字底图，再叠标题。",
          "env\\Scripts\\python.exe tools\\cover_72h.py"),
    _tool("cover_72h_v3.py", ["7"],
          "封面底图 v3：为标题留白，避免模型写中文标题。",
          "env\\Scripts\\python.exe tools\\cover_72h_v3.py"),
    _tool("cover_72h_v4.py", ["7"],
          "封面底图 v4：室内干燥，雨只出现在窗外。",
          "env\\Scripts\\python.exe tools\\cover_72h_v4.py"),
    _tool("cover_72h_v5.py", ["7"],
          "封面底图 v5：半开卷帘门，用干湿分界线解决空间逻辑。",
          "env\\Scripts\\python.exe tools\\cover_72h_v5.py"),
    _tool("cover_72h_v6.py", ["7"],
          "封面底图 v6：无窗干燥档案室，彻底去掉雨与窗。",
          "env\\Scripts\\python.exe tools\\cover_72h_v6.py"),
    _tool("cover_72h_v7.py", ["7"],
          "封面底图 v7：改用不带雨的 FI-016，干燥档案室场景。",
          "env\\Scripts\\python.exe tools\\cover_72h_v7.py"),
    _tool("cover_72h_compose.py", ["7"],
          "封面排版合成：裁切、冷调压暗、标题排版、颗粒扫描线、输出三尺寸。",
          "env\\Scripts\\python.exe tools\\cover_72h_compose.py"),
]

_BY_ID = {r["id"]: r for r in RESOURCES}


# =============================================================================
# 公共信息
# =============================================================================

def _kind(path: str) -> str:
    return _KIND_BY_SUFFIX.get(Path(path).suffix.lower(), "text")


def _entry(res: dict[str, Any]) -> dict[str, Any]:
    target = BASE_DIR / res["path"]
    try:
        stat = target.stat()
        size = stat.st_size
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        size = 0
        mtime = ""
    return {
        "id": res["id"],
        "title": res["title"],
        "group": res["group"],
        "group_name": GROUP_NAMES.get(res["group"], res["group"]),
        "path": res["path"],
        "kind": _kind(res["path"]),
        "purpose": res["purpose"],
        "effect": res["effect"],
        "usage": res.get("usage", ""),
        "size": size,
        "mtime": mtime,
        "exists": target.exists(),
        "backup_exists": _backup_path(res).exists(),
    }


def _backup_path(res: dict[str, Any]) -> Path:
    return BACKUP_DIR / f"{res['id']}.bak"


def _invalidate_cache(path: str) -> None:
    if not path.startswith("rules/data/"):
        return
    try:
        from . import rules as rules_mod
        name = Path(path).stem
        rules_mod._CACHE.pop(name, None)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import stylelib
        stylelib._CACHE.clear()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


def _validate(res: dict[str, Any], content: str) -> list[str]:
    """保存前校验；返回警告列表，抛 ValueError 表示拒绝保存。"""
    warnings: list[str] = []
    kind = _kind(res["path"])
    if kind == "json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 语法错误：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}") from exc
        path = res["path"]
        if path.endswith("tones_extended.json") and not isinstance(data, list):
            raise ValueError("tones_extended.json 顶层必须是数组")
        if path.endswith("storyboard_layouts.json"):
            if not isinstance(data, dict) or not isinstance(data.get("layouts"), dict):
                raise ValueError("storyboard_layouts.json 必须包含 layouts 对象")
            if not data["layouts"]:
                warnings.append("layouts 为空，分页规划将没有可用版式")
        if path.endswith(("identity_rules.json", "shot_prompt_rules.json",
                          "storyboard_rules.json")) and not isinstance(data, dict):
            raise ValueError("该规则文件顶层必须是对象")
    elif kind == "python":
        try:
            compile(content, res["path"], "exec")
        except SyntaxError as exc:
            raise ValueError(f"Python 语法错误：第 {exc.lineno} 行，{exc.msg}") from exc
    return warnings


def _read(res: dict[str, Any]) -> str:
    target = BASE_DIR / res["path"]
    try:
        return target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _write(res: dict[str, Any], content: str) -> None:
    target = BASE_DIR / res["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)


def _backup(res: dict[str, Any]) -> bool:
    target = BASE_DIR / res["path"]
    if not target.exists():
        return False
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, _backup_path(res))
    return True


# =============================================================================
# 目录 / 详情 / 保存 / 恢复
# =============================================================================

def catalog(step: str) -> dict[str, Any]:
    step = str(step)
    if step not in STEPS:
        raise KeyError(f"未知步骤：{step}")
    entries = [_entry(r) for r in RESOURCES if step in r["steps"]]
    order = {"rule-data": 0, "rule-doc": 1, "reference": 2, "tool": 3}
    entries.sort(key=lambda e: (order.get(e["group"], 9), e["title"]))
    return {
        "step": step,
        **STEPS[step],
        "entries": entries,
        "counts": {g: sum(1 for e in entries if e["group"] == g)
                   for g in GROUP_NAMES},
    }


def detail(resource_id: str) -> dict[str, Any]:
    res = _BY_ID.get(str(resource_id))
    if res is None:
        raise KeyError(f"未知规则 / 工具：{resource_id}")
    content = _read(res)
    target = BASE_DIR / res["path"]
    return {
        "entry": _entry(res),
        "content": content,
        "path": str(target),
        "backup": _backup_path(res).exists(),
    }


def save(resource_id: str, content: str) -> dict[str, Any]:
    res = _BY_ID.get(str(resource_id))
    if res is None:
        raise KeyError(f"未知规则 / 工具：{resource_id}")
    text = str(content or "")
    if _kind(res["path"]) in ("json", "python") and not text.strip():
        raise ValueError("文件内容不能为空")
    warnings = _validate(res, text)
    backed_up = _backup(res)
    _write(res, text)
    _invalidate_cache(res["path"])
    data = detail(resource_id)
    data["message"] = ("已保存，规则会在下一次生成时热更新"
                       if res["group"] == "rule-data"
                       else ("已保存脚本文件，不会自动执行"
                             if res["group"] == "tool" else "已保存文档"))
    data["warnings"] = warnings
    data["backed_up"] = backed_up
    return data


def restore(resource_id: str) -> dict[str, Any]:
    res = _BY_ID.get(str(resource_id))
    if res is None:
        raise KeyError(f"未知规则 / 工具：{resource_id}")
    backup = _backup_path(res)
    if not backup.exists():
        raise ValueError("还没有可恢复的上次版本；先保存一次才会生成备份")
    content = backup.read_text(encoding="utf-8")
    _validate(res, content)
    _write(res, content)
    _invalidate_cache(res["path"])
    data = detail(resource_id)
    data["message"] = "已恢复到上次保存前的版本"
    data["warnings"] = []
    return data
