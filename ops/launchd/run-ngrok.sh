#!/bin/zsh
set -euo pipefail

readonly PROJECT_ROOT="/Volumes/HermesSSD/AntigravityData/量化监测-股票"
readonly NGROK_BIN="/Volumes/HermesSSD/AntigravityData/量化监测-股票/ngrok"
readonly NGROK_CONFIG="/Users/linjian/Library/Application Support/ngrok/ngrok.yml"
readonly NGROK_DOMAIN="banister-drilling-jawless.ngrok-free.dev"
readonly BACKEND_HEALTH_URL="http://127.0.0.1:8001/docs"
readonly MODE="${1:-run}"

if [[ "$MODE" != "run" && "$MODE" != "--check" ]]; then
  print -u2 -- "用法：run-ngrok.sh [--check]"
  exit 64
fi
if [[ ! -x "$NGROK_BIN" ]]; then
  print -u2 -- "ngrok不存在或不可执行：$NGROK_BIN"
  exit 69
fi
if [[ ! -f "$NGROK_CONFIG" ]]; then
  print -u2 -- "ngrok配置不存在：$NGROK_CONFIG"
  exit 69
fi
cd "$PROJECT_ROOT"
if /usr/bin/pgrep -x ngrok >/dev/null 2>&1; then
  print -u2 -- "ngrok已运行，拒绝启动第二个隧道"
  exit 75
fi
if [[ "$MODE" == "--check" ]]; then
  print -- "ngrok启动条件检查通过"
  exit 0
fi

integer attempt=0
while (( attempt < 60 )); do
  if /usr/bin/curl --max-time 2 -fsS "$BACKEND_HEALTH_URL" >/dev/null 2>&1; then
    break
  fi
  attempt=$((attempt + 1))
  /bin/sleep 1
done
if (( attempt >= 60 )); then
  print -u2 -- "FastAPI在60秒内未恢复健康，拒绝启动ngrok"
  exit 75
fi

exec "$NGROK_BIN" http "127.0.0.1:8001" \
  "--url=$NGROK_DOMAIN" \
  "--config=$NGROK_CONFIG"
