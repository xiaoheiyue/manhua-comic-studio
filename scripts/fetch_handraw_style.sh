#!/usr/bin/env bash
# 拉取 handraw-style 风格库（365 画风 / 170 版式 / 36 主题色）
# 用法： bash scripts/fetch_handraw_style.sh
# 许可：MIT + 署名要求（作者 yang0，https://github.com/yang0/handraw-style）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$ROOT/handraw-style}"
MARKER="$DEST/skills/handdraw-style-prompter/references/styles.json"

if [ -f "$MARKER" ]; then
  echo "[OK] handraw-style 已存在：$DEST"
  exit 0
fi

if [ -e "$DEST" ]; then
  echo "[WARN] 目录已存在但不完整：$DEST，请先删除"
  exit 1
fi

git clone --depth 1 https://github.com/yang0/handraw-style.git "$DEST"

if [ ! -f "$MARKER" ]; then
  echo "[ERR] 缺少 $MARKER，目录不完整"
  exit 1
fi
echo "[OK] 完成。保留 LICENSE 与署名：yang0 / https://github.com/yang0/handraw-style"
