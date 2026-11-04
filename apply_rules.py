# -*- coding: utf-8 -*-
"""把 rules/ 里的规则应用到已有工程（离线可用）。

它做三件事：
  1. 导演层整理：清洗描述里的镜头词/注入语、撤掉物件格里的角色、
     锁左右站位、把说话人不在画面的台词标成画外音、重算气泡槽位与尾巴；
  2. 角色辨识度：检测撞车角色，追加"唯一点缀"（可选让文本模型重写人设）；
  3. 打印整理后的自检结果与提示词长度，方便决定要不要重出图。

用法：
    # 只看会改什么，不落盘（默认）
    env\\Scripts\\python.exe apply_rules.py 931dec78d0

    # 真正写回工程（自动备份原文件）
    env\\Scripts\\python.exe apply_rules.py 931dec78d0 --apply

    # 允许调用 DeepSeek 重写撞车角色的人设（需要密钥；失败自动退回点缀）
    env\\Scripts\\python.exe apply_rules.py 931dec78d0 --apply --rewrite

    # 人设改了以后，把已出的图作废、标记为待生成
    env\\Scripts\\python.exe apply_rules.py 931dec78d0 --apply --rewrite --invalidate-images

注意：不调用图像模型；只读/写工程 JSON。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config                                        # noqa: E402
from comicgen import director, prompts, rules        # noqa: E402
from comicgen.models import Project                  # noqa: E402
from comicgen.pipeline import Pipeline               # noqa: E402


def resolve(project_id: str) -> Path:
    path = Path(project_id)
    if path.suffix == ".json" and path.exists():
        return path
    candidate = Path(config.PROJECT_DIR) / f"{project_id}.json"
    if candidate.exists():
        return candidate
    raise SystemExit(f"找不到工程：{project_id}（{candidate}）")


def sample_prompts(project: Project, n: int = 3) -> list[tuple[int, int, int]]:
    """返回 [(格号, 提示词长度, 人数)]，用来确认没有超预算。"""
    out: list[tuple[int, int, int]] = []
    for shot in project.shots[:n]:
        cast = []
        for name in shot.characters:
            c = project.find_character(name)
            if c:
                cast.append({"name": c.name, "card": c.identity_card(),
                             "side": shot.char_sides.get(c.name, ""),
                             "position": shot.char_positions.get(c.name, "")})
        text = prompts.shot_image_prompt(project, shot, cast=cast)
        out.append((shot.index, len(text), len(cast)))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="把 rules/ 的规则应用到已有工程")
    parser.add_argument("project", help="工程 id 或 .json 路径")
    parser.add_argument("--apply", action="store_true", help="写回工程（默认只预览）")
    parser.add_argument("--rewrite", action="store_true",
                        help="允许调用 DeepSeek 重写撞车角色（需要密钥）")
    parser.add_argument("--no-normalize", action="store_true", help="跳过导演层整理")
    parser.add_argument("--invalidate-images", action="store_true",
                        help="把人设改过的角色的已出图作废（标记待生成）")
    args = parser.parse_args()

    path = resolve(args.project)
    project = Project.load(path)
    print(f"工程：{project.id} / {project.title} / {len(project.shots)} 格 / "
          f"{len(project.characters)} 角色")
    check = rules.self_check()
    print(f"规则库：{'OK' if check['ok'] else '异常'}（{check['rules_dir']}）")
    for err in check.get("errors", []):
        print(f"  ！{err}")

    changed_roles: set[str] = set()

    if not args.no_normalize:
        report = director.normalize(project)
        print("\n[1] 导演层整理：" + director.summary_text(report))
        for issue in report.get("issues", []):
            print(f"    [{issue['level']}] {issue['code']}: {issue['message']}")

    pipe = Pipeline()
    if args.rewrite:
        print("\n[2] 角色辨识度：允许调用文本模型重写撞车人设…")
    else:
        print("\n[2] 角色辨识度：只做确定性点缀（不联网）…")
    drep = pipe.ensure_character_distinctness(project, rewrite=args.rewrite)
    print(f"    撞车 {len(drep.get('collisions', []))} 对；"
          f"重写 {drep.get('rewritten', []) or '无'}；"
          f"点缀 {drep.get('patched', []) or '无'}")
    changed_roles.update(drep.get("patched", []) or [])
    changed_roles.update(drep.get("rewritten", []) or [])

    print("\n[3] 整理后的自检：")
    issues = director.validate(project)
    if not issues:
        print("    没有发现问题 ✅")
    for issue in issues:
        print(f"    [{issue['level']}] {issue['code']}: {issue['message']}")

    print("\n[4] 提示词抽样（长度 / 上限 / 人数）：")
    for idx, length, n_cast in sample_prompts(project, 5):
        flag = "OK" if length <= config.PROMPT_MAX_CHARS else "超预算"
        print(f"    第 {idx} 格：{length} / {config.PROMPT_MAX_CHARS} 字，"
              f"{n_cast} 人  [{flag}]")

    if changed_roles and args.invalidate_images:
        n = 0
        for shot in project.shots:
            if changed_roles & set(shot.characters or []):
                shot.status = "待生成"
                shot.image_path = ""
                shot.raw_image_path = ""
                shot.bubble_layout = []
                shot.bubble_plan = []
                shot.quality_score = 0
                shot.quality_issues = []
                n += 1
        print(f"\n[5] 已作废 {n} 格与改动角色相关的成品图，需要重新出图")

    if not args.apply:
        print("\n（预览模式，未写入。加 --apply 才会保存，保存前会自动备份）")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(f".bak-{stamp}.json")
    shutil.copyfile(path, backup)
    project.save(path)
    print(f"\n已保存：{path}\n已备份：{backup}")


if __name__ == "__main__":
    main()
