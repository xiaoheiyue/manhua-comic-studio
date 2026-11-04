#!/usr/bin/env bash
# 启动脚本（macOS / Linux）
# 用法： ./start.sh
set -e
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif [ -x "env/bin/python" ]; then
  PY="env/bin/python"
else
  PY="python3"
fi

echo "使用解释器：$PY"
echo "启动后请打开 http://127.0.0.1:7870"
exec "$PY" server.py
