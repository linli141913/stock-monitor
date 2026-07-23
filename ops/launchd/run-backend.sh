#!/bin/zsh
set -euo pipefail

readonly BACKEND_DIR="/Volumes/HermesSSD/AntigravityData/量化监测-股票/backend"
readonly PYTHON_BIN="/Volumes/HermesSSD/AntigravityData/量化监测-股票/backend/venv/bin/python"
readonly BACKEND_ENTRY="/Volumes/HermesSSD/AntigravityData/量化监测-股票/backend/main.py"
readonly MODE="${1:-run}"

if [[ "$MODE" != "run" && "$MODE" != "--check" ]]; then
  print -u2 -- "用法：run-backend.sh [--check]"
  exit 64
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  print -u2 -- "后端Python不存在或不可执行：$PYTHON_BIN"
  exit 69
fi
if [[ ! -f "$BACKEND_ENTRY" ]]; then
  print -u2 -- "后端入口不存在：$BACKEND_ENTRY"
  exit 69
fi
cd "$BACKEND_DIR"
if /usr/sbin/lsof -nP -iTCP:8001 -sTCP:LISTEN >/dev/null 2>&1; then
  print -u2 -- "8001端口已有监听，拒绝启动第二个FastAPI实例"
  exit 75
fi
if [[ "$MODE" == "--check" ]]; then
  print -- "FastAPI启动条件检查通过"
  exit 0
fi

exec "$PYTHON_BIN" main.py
