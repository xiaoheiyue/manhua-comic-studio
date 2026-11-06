#!/usr/bin/env bash
# 一键推送到 GitHub + Gitee
# 用法： bash scripts/push_all.sh [branch]
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"
REMOTES="$(git remote)"

targets=()
for r in origin gitee; do
  if echo "$REMOTES" | grep -qx "$r"; then targets+=("$r"); fi
done

if [ ${#targets[@]} -eq 0 ]; then
  echo "[ERR] 没有配置 origin / gitee remote"
  exit 1
fi

echo "分支：$BRANCH"
echo "目标：${targets[*]}"
echo

failed=()
for r in "${targets[@]}"; do
  echo "==> $r  $(git remote get-url "$r")"
  if ! git push "$r" "$BRANCH"; then failed+=("$r"); fi
done

echo
if [ ${#failed[@]} -gt 0 ]; then
  echo "[FAIL] 推送失败：${failed[*]}"
  exit 1
fi

echo "[OK] 两个仓库都已更新："
git --no-pager log -1 --oneline
