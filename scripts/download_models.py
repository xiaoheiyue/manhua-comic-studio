#!/usr/bin/env python
"""国内直连下载模型权重（默认走 ModelScope 镜像，不需要代理）。

用法：
    python scripts/download_models.py --list
    python scripts/download_models.py                 # 只下「必需」的 3 个（约 11.3 GB）
    python scripts/download_models.py --all           # 下全部有镜像的 7 个（约 29.5 GB）
    python scripts/download_models.py --file ae.safetensors
    python scripts/download_models.py --out D:/ComfyUI/models --flat
    python scripts/download_models.py --verify-only   # 只校验已有文件

设计：
- 只依赖标准库，不需要 huggingface_hub / modelscope SDK。
- 断点续传（HTTP Range），下载完校验字节数与 SHA256。
- 已存在且大小一致的文件默认跳过；加 --verify 才重算哈希（6GB 文件较慢）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "models" / "manifest.json"
CHUNK = 1024 * 1024


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(CHUNK * 8)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def download(url: str, dest: Path, total: int, retries: int = 3) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    for attempt in range(1, retries + 1):
        have = part.stat().st_size if part.exists() else 0
        if have and total and have >= total:
            break
        headers = {"User-Agent": "manhua-comic-studio/0.1"}
        if have:
            headers["Range"] = f"bytes={have}-"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                mode = "ab" if (have and resp.status == 206) else "wb"
                if mode == "wb":
                    have = 0
                done = have
                start = time.time()
                last = 0.0
                with part.open(mode) as f:
                    while True:
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        now = time.time()
                        if now - last > 1.0:
                            last = now
                            speed = done / max(now - start, 0.1)
                            pct = f"{done * 100 / total:5.1f}%" if total else "  ?  "
                            print(f"\r    {pct}  {human(done)} / {human(total)}  {human(speed)}/s   ",
                                  end="", flush=True)
                print()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            print(f"\n    [retry {attempt}/{retries}] {exc}")
            time.sleep(3 * attempt)
            continue

        size = part.stat().st_size
        if total and size != total:
            print(f"    [warn] 大小不符：{size} != {total}，重试")
            continue
        os.replace(part, dest)
        return True

    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="从国内镜像下载模型权重")
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--out", default=str(ROOT / "models" / "weights"))
    ap.add_argument("--all", action="store_true", help="下载全部有镜像的模型（默认只下必需）")
    ap.add_argument("--file", action="append", default=[], help="只下载文件名包含该字符串的条目，可重复")
    ap.add_argument("--list", action="store_true", help="只列出清单，不下载")
    ap.add_argument("--verify", action="store_true", help="下载前重算已有文件的 SHA256")
    ap.add_argument("--verify-only", action="store_true", help="只校验，不下载")
    ap.add_argument("--flat", action="store_true", help="不按 diffusion_models/ 等子目录，直接放到 --out 下")
    ap.add_argument("--retries", type=int, default=3)
    args = ap.parse_args()

    data = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    models = data["models"]
    out_root = Path(args.out)

    def selected(m: dict) -> bool:
        if args.file:
            return any(s.lower() in m["file"].lower() for s in args.file)
        return True if args.all else bool(m.get("required"))

    todo = [m for m in models if selected(m)]
    if not todo:
        print("没有匹配的条目")
        return 1

    print(f"{'文件':<62} {'大小':>9}  状态")
    print("-" * 92)
    total_bytes = 0
    for m in todo:
        rel = Path(m["file"])
        dest = (out_root / rel.name) if args.flat else (out_root / rel)
        have = dest.exists()
        ok = have and (not args.verify or dest.stat().st_size == m["bytes"])
        if have and args.verify and ok:
            ok = sha256_of(dest) == m["sha256"]
        status = "已存在" if ok else ("无镜像" if not m.get("download") else "待下载")
        print(f"{m['file']:<62} {m['size_human']:>9}  {status}")
        if not ok and m.get("download"):
            total_bytes += m["bytes"]

    if args.list:
        print(f"\n合计待下载：{human(total_bytes)}")
        return 0

    failures: list[str] = []
    for idx, m in enumerate(todo, 1):
        rel = Path(m["file"])
        dest = (out_root / rel.name) if args.flat else (out_root / rel)
        if dest.exists() and dest.stat().st_size == m["bytes"]:
            if args.verify and sha256_of(dest) != m["sha256"]:
                print(f"[{idx}/{len(todo)}] {m['file']} 哈希不符，重新下载")
            else:
                print(f"[{idx}/{len(todo)}] {m['file']} 已存在，跳过")
                continue
        if args.verify_only:
            failures.append(m["file"] + "（缺失）")
            continue
        if not m.get("download"):
            print(f"[{idx}/{len(todo)}] {m['file']} 无国内镜像，跳过（见 models/README.md）")
            continue

        url = m["download"]["url"]
        print(f"[{idx}/{len(todo)}] {m['file']}  {m['size_human']}  ← {url.split('/models/')[-1].split('/resolve')[0]}")
        if not download(url, dest, m["bytes"], args.retries):
            failures.append(m["file"])
            continue
        digest = sha256_of(dest)
        if digest != m["sha256"]:
            print(f"    [ERR] SHA256 不匹配：{digest[:16]}… != {m['sha256'][:16]}…")
            failures.append(m["file"] + "（哈希不符）")
        else:
            print("    [OK] 校验通过")

    print()
    if failures:
        print("失败 / 缺失：")
        for f in failures:
            print("  -", f)
        return 1
    print("全部完成。放到 ComfyUI 后，用界面「检查工作流」核对文件名。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
