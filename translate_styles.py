# -*- coding: utf-8 -*-
"""
为 handraw 的 365 个风格生成中文显示名（缓存到 data/cache/style_zh.json）。

用法：
    env\\Scripts\\python.exe -X utf8 translate_styles.py
    env\\Scripts\\python.exe -X utf8 translate_styles.py --force

不修改 handraw-style 原始文件；只写缓存。缓存缺失或某个风格翻译失败时，
界面会用“风格家族 + traits 第一条”自动生成中文名，不会退回纯英文。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import logging
import config
from comicgen import stylelib
from comicgen.textclient import TextClient

log = logging.getLogger("comicgen")

BATCH = 20
OUT = Path(config.DATA_DIR) / "cache" / "style_zh.json"

SYSTEM = (
    "你是漫画/插画风格编辑。把英文风格名翻译成中文时，优先表达画风特征，"
    "不要逐字硬译；人名、作品名用中文常用译名或音译。只输出 JSON，不要解释。"
)


def load_cache() -> dict[str, dict[str, str]]:
    if OUT.exists():
        try:
            raw = json.loads(OUT.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {str(k).upper(): v for k, v in raw.items() if isinstance(v, dict)}
        except Exception as exc:  # noqa: BLE001
            log.warning("读取中文风格缓存失败：%s", exc)
    return {}


def save_cache(data: dict[str, dict[str, str]]) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")


def translate_batch(client: TextClient, rows: list[dict]) -> dict[str, dict[str, str]]:
    payload = [
        {"id": s["number"], "name": s.get("generation_name", ""),
         "reference": s.get("reference", ""), "group": s.get("group", "")}
        for s in rows
    ]
    prompt = (
        "把下面每个风格的英文名翻译成中文显示名，并给出参考作者/作品的中文名。\n"
        "输出 JSON 对象：{\"编号\": {\"name_zh\": \"8~18个汉字\", "
        "\"reference_zh\": \"中文名/音译\"}}。\n"
        "要求：\n"
        "1. name_zh 要像中文画风名，保留媒介/线条/色彩/题材特征，不要只写“某某风格”；\n"
        "2. reference_zh 是作者或作品的中文常用译名；不确定就音译，不要留空；\n"
        "3. 编号必须原样返回，一个都不能少。\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=1)
    )
    parsed = client.chat_json(
        [{"role": "system", "content": SYSTEM},
         {"role": "user", "content": prompt}],
        expect="object", temperature=0.2, max_tokens=3500,
    )
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for sid, item in parsed.items():
        if not isinstance(item, dict):
            continue
        name = str(item.get("name_zh") or "").strip()
        ref = str(item.get("reference_zh") or "").strip()
        if name:
            out[str(sid).upper()] = {"name_zh": name[:40], "reference_zh": ref[:60]}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="重译已有条目")
    ap.add_argument("--refs-only", action="store_true",
                    help="只补参考作者中文名（用于外文 handle 没被音译的情况）")
    args = ap.parse_args()

    entries = stylelib.style_entries()
    cache = load_cache()
    if args.refs_only:
        import re
        todo = [s for s in entries
                if not re.search(r"[\u4e00-\u9fff]",
                                 cache.get(str(s["number"]).upper(), {}).get("reference_zh", ""))]
    else:
        todo = [s for s in entries
                if args.force or not (cache.get(str(s["number"]).upper(), {}).get("name_zh"))]
    if not todo:
        print(f"已有 {len(cache)} 条中文名，无需翻译")
        return 0

    client = TextClient()
    if not client.key:
        print("没有文本模型密钥，无法翻译；界面仍会显示 traits 兜底中文名", file=sys.stderr)
        return 1

    total = len(todo)
    for i in range(0, total, BATCH):
        rows = todo[i:i + BATCH]
        try:
            got = translate_batch(client, rows)
        except Exception as exc:  # noqa: BLE001
            log.warning("翻译第 %s~%s 条失败：%s", i + 1, i + len(rows), exc)
            continue
        if args.refs_only:
            for sid, item in got.items():
                ref = item.get("reference_zh", "").strip()
                if ref:
                    cache.setdefault(sid.upper(), {})["reference_zh"] = ref
        else:
            cache.update(got)
        save_cache(cache)
        print(f"[{min(i + BATCH, total)}/{total}] 已翻译 {len(got)} 条，缓存 {len(cache)} 条")
    print(f"完成：{OUT}（{len(cache)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
