#!/bin/zsh
set -euo pipefail

readonly PROJECT_ROOT="/Volumes/HermesSSD/AntigravityData/量化监测-股票"
readonly LAUNCHD_DIR="$PROJECT_ROOT/ops/launchd"
readonly BACKEND_RUNNER="$LAUNCHD_DIR/run-backend.sh"
readonly NGROK_RUNNER="$LAUNCHD_DIR/run-ngrok.sh"
readonly BACKEND_SOURCE="$LAUNCHD_DIR/com.linjian.stock-monitor.fastapi.plist"
readonly BACKEND_RADAR_DISABLED_SOURCE="$LAUNCHD_DIR/com.linjian.stock-monitor.fastapi.radar-disabled.plist"
readonly BACKEND_SECTOR_ENABLED_SOURCE="$LAUNCHD_DIR/com.linjian.stock-monitor.fastapi.sector-shadow-enabled.plist"
readonly BACKEND_MARKET_ENABLED_SOURCE="$LAUNCHD_DIR/com.linjian.stock-monitor.fastapi.market-shadow-enabled.plist"
readonly NGROK_SOURCE="$LAUNCHD_DIR/com.linjian.stock-monitor.ngrok.plist"
readonly LAUNCH_AGENTS_DIR="/Users/linjian/Library/LaunchAgents"
readonly ARCHIVE_ROOT="$LAUNCH_AGENTS_DIR/stock-monitor-archive"
readonly LOG_DIR="/Users/linjian/Library/Logs/stock-monitor"
readonly RUNTIME_DIR="/Users/linjian/Library/Application Support/stock-monitor/launchd"
readonly RADAR_RUNTIME_DIR="/Users/linjian/Library/Application Support/stock-monitor/runtime"
readonly BACKEND_DEST="$LAUNCH_AGENTS_DIR/com.linjian.stock-monitor.fastapi.plist"
readonly NGROK_DEST="$LAUNCH_AGENTS_DIR/com.linjian.stock-monitor.ngrok.plist"
readonly BACKEND_RUNTIME_RUNNER="$RUNTIME_DIR/run-backend.sh"
readonly NGROK_RUNTIME_RUNNER="$RUNTIME_DIR/run-ngrok.sh"
readonly BACKEND_LABEL="com.linjian.stock-monitor.fastapi"
readonly NGROK_LABEL="com.linjian.stock-monitor.ngrok"
readonly GUI_DOMAIN="gui/501"
readonly BACKEND_SCREEN="stock-monitor-backend"
readonly NGROK_SCREEN="stock-monitor-ngrok"
readonly BACKEND_DIR="$PROJECT_ROOT/backend"
readonly PYTHON_BIN="$BACKEND_DIR/venv/bin/python"
readonly NGROK_BIN="$PROJECT_ROOT/ngrok"
readonly NGROK_CONFIG="/Users/linjian/Library/Application Support/ngrok/ngrok.yml"
readonly NGROK_DOMAIN="banister-drilling-jawless.ngrok-free.dev"
readonly BACKEND_HEALTH_URL="http://127.0.0.1:8001/docs"

usage() {
  print -- "用法：ops/launchd/manage.sh <validate|preflight|install|reload-backend|enable-radar|disable-radar|enable-sector-shadow|disable-sector-shadow|enable-market-shadow|disable-market-shadow|status|uninstall|rollback-screen>"
}

assert_macos() {
  if [[ "$(/usr/bin/uname -s)" != "Darwin" ]]; then
    print -u2 -- "launchd资产只能在macOS验证或使用"
    return 69
  fi
}

validate_assets() {
  assert_macos
  local required
  for required in \
    "$BACKEND_RUNNER" \
    "$NGROK_RUNNER" \
    "$BACKEND_SOURCE" \
    "$BACKEND_RADAR_DISABLED_SOURCE" \
    "$BACKEND_SECTOR_ENABLED_SOURCE" \
    "$BACKEND_MARKET_ENABLED_SOURCE" \
    "$NGROK_SOURCE" \
    "$PYTHON_BIN" \
    "$NGROK_BIN" \
    "$NGROK_CONFIG"; do
    if [[ ! -e "$required" ]]; then
      print -u2 -- "缺少运行资产：$required"
      return 69
    fi
  done
  if [[ ! -x "$BACKEND_RUNNER" || ! -x "$NGROK_RUNNER" ]]; then
    print -u2 -- "启动包装脚本必须可执行"
    return 69
  fi
  /bin/zsh -n "$BACKEND_RUNNER" "$NGROK_RUNNER" "$LAUNCHD_DIR/manage.sh"
  /usr/bin/plutil -lint \
    "$BACKEND_SOURCE" \
    "$BACKEND_RADAR_DISABLED_SOURCE" \
    "$BACKEND_SECTOR_ENABLED_SOURCE" \
    "$BACKEND_MARKET_ENABLED_SOURCE" \
    "$NGROK_SOURCE" >/dev/null
  "$NGROK_BIN" config check >/dev/null
  print -- "launchd资产验证通过"
}

port_in_use() {
  /usr/sbin/lsof -nP -iTCP:8001 -sTCP:LISTEN >/dev/null 2>&1
}

ngrok_running() {
  /usr/bin/pgrep -x ngrok >/dev/null 2>&1
}

launchd_loaded() {
  /bin/launchctl print "$GUI_DOMAIN/$1" >/dev/null 2>&1
}

screen_session_exists() {
  local listing
  listing="$(/usr/bin/screen -ls 2>/dev/null || true)"
  [[ "$listing" == *".$1"* ]]
}

preflight_install() {
  validate_assets
  if launchd_loaded "$BACKEND_LABEL" || launchd_loaded "$NGROK_LABEL"; then
    print -u2 -- "同名LaunchAgent已经加载，拒绝重复安装"
    return 75
  fi
  if [[ -e "$BACKEND_DEST" || -e "$NGROK_DEST" ||
        -e "$BACKEND_RUNTIME_RUNNER" || -e "$NGROK_RUNTIME_RUNNER" ]]; then
    print -u2 -- "目标LaunchAgent运行资产已经存在，拒绝覆盖"
    return 75
  fi
  if port_in_use; then
    print -u2 -- "8001端口正被现有服务使用；本脚本不会停止它"
    return 75
  fi
  if ngrok_running; then
    print -u2 -- "ngrok正在运行；本脚本不会停止它"
    return 75
  fi
  if screen_session_exists "$BACKEND_SCREEN" || screen_session_exists "$NGROK_SCREEN"; then
    print -u2 -- "现有stock-monitor screen会话仍存在；拒绝并行安装"
    return 75
  fi
  print -- "LaunchAgent安装前检查通过"
}

wait_for_backend() {
  integer attempt=0
  while (( attempt < 30 )); do
    if /usr/bin/curl --max-time 2 -fsS "$BACKEND_HEALTH_URL" >/dev/null 2>&1; then
      return 0
    fi
    attempt=$((attempt + 1))
    /bin/sleep 1
  done
  print -u2 -- "FastAPI在30秒内未恢复健康"
  return 1
}

wait_for_backend_to_stop() {
  integer attempt=0
  while (( attempt < 30 )); do
    if ! port_in_use; then
      return 0
    fi
    attempt=$((attempt + 1))
    /bin/sleep 1
  done
  print -u2 -- "FastAPI在30秒内未停止，拒绝覆盖运行资产"
  return 1
}

wait_for_ngrok() {
  integer attempt=0
  while (( attempt < 20 )); do
    if ngrok_running; then
      return 0
    fi
    attempt=$((attempt + 1))
    /bin/sleep 1
  done
  print -u2 -- "ngrok在20秒内未启动"
  return 1
}

bootout_if_loaded() {
  local label="$1"
  if launchd_loaded "$label"; then
    /bin/launchctl bootout "$GUI_DOMAIN/$label"
  fi
}

bootstrap_with_retry() {
  local domain="$1"
  local plist_path="$2"
  local label="$3"
  integer attempt=1
  while (( attempt <= 5 )); do
    if /bin/launchctl bootstrap "$domain" "$plist_path"; then
      return 0
    fi
    if launchd_loaded "$label"; then
      return 0
    fi
    if (( attempt < 5 )); then
      /bin/sleep 1
    fi
    attempt=$((attempt + 1))
  done
  print -u2 -- "LaunchAgent在5次bootstrap尝试后仍未加载：$label"
  return 1
}

archive_installed_assets() {
  if [[ ! -e "$BACKEND_DEST" && ! -e "$NGROK_DEST" &&
        ! -e "$BACKEND_RUNTIME_RUNNER" && ! -e "$NGROK_RUNTIME_RUNNER" ]]; then
    return 0
  fi
  local timestamp archive_dir
  timestamp="$(/bin/date +%Y%m%d-%H%M%S)"
  archive_dir="$ARCHIVE_ROOT/$timestamp"
  /bin/mkdir -p "$archive_dir"
  if [[ -e "$BACKEND_DEST" ]]; then
    /bin/mv "$BACKEND_DEST" "$archive_dir/"
  fi
  if [[ -e "$NGROK_DEST" ]]; then
    /bin/mv "$NGROK_DEST" "$archive_dir/"
  fi
  if [[ -e "$BACKEND_RUNTIME_RUNNER" ]]; then
    /bin/mv "$BACKEND_RUNTIME_RUNNER" "$archive_dir/"
  fi
  if [[ -e "$NGROK_RUNTIME_RUNNER" ]]; then
    /bin/mv "$NGROK_RUNTIME_RUNNER" "$archive_dir/"
  fi
  print -- "已归档LaunchAgent运行资产：$archive_dir"
}

rollback_failed_install() {
  set +e
  bootout_if_loaded "$NGROK_LABEL"
  bootout_if_loaded "$BACKEND_LABEL"
  archive_installed_assets
  set -e
}

install_services() {
  preflight_install
  /bin/mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR" "$RUNTIME_DIR"
  if ! /usr/bin/install -m 700 "$BACKEND_RUNNER" "$BACKEND_RUNTIME_RUNNER"; then
    rollback_failed_install
    return 1
  fi
  if ! /usr/bin/install -m 700 "$NGROK_RUNNER" "$NGROK_RUNTIME_RUNNER"; then
    rollback_failed_install
    return 1
  fi
  if ! /usr/bin/install -m 600 "$BACKEND_SOURCE" "$BACKEND_DEST"; then
    rollback_failed_install
    return 1
  fi
  if ! /usr/bin/install -m 600 "$NGROK_SOURCE" "$NGROK_DEST"; then
    rollback_failed_install
    return 1
  fi

  if ! /bin/launchctl bootstrap "$GUI_DOMAIN" "$BACKEND_DEST"; then
    rollback_failed_install
    return 1
  fi
  if ! wait_for_backend; then
    rollback_failed_install
    return 1
  fi
  if ! /bin/launchctl bootstrap "$GUI_DOMAIN" "$NGROK_DEST"; then
    rollback_failed_install
    return 1
  fi
  if ! wait_for_ngrok; then
    rollback_failed_install
    return 1
  fi
  print -- "FastAPI与ngrok LaunchAgent安装并启动完成"
}

preflight_backend_reload() {
  validate_assets
  if ! launchd_loaded "$BACKEND_LABEL"; then
    print -u2 -- "FastAPI LaunchAgent未加载，不能执行受控重载"
    return 75
  fi
  if ! launchd_loaded "$NGROK_LABEL"; then
    print -u2 -- "ngrok LaunchAgent未加载，不能执行受控重载"
    return 75
  fi
  if [[ ! -f "$BACKEND_DEST" || ! -x "$BACKEND_RUNTIME_RUNNER" ]]; then
    print -u2 -- "当前FastAPI LaunchAgent运行资产不完整"
    return 75
  fi
  if screen_session_exists "$BACKEND_SCREEN"; then
    print -u2 -- "后端screen会话仍存在，拒绝重载为第二实例"
    return 75
  fi
  if ! /usr/bin/curl --max-time 5 -fsS "$BACKEND_HEALTH_URL" >/dev/null; then
    print -u2 -- "当前FastAPI不健康，拒绝直接覆盖运行资产"
    return 75
  fi
  print -- "FastAPI受控重载前检查通过"
}

typeset -g BACKEND_RELOAD_ARCHIVE=""

archive_backend_assets() {
  local timestamp archive_dir
  timestamp="$(/bin/date +%Y%m%d-%H%M%S)"
  archive_dir="$ARCHIVE_ROOT/$timestamp-radar-2b6b2b"
  if [[ -e "$archive_dir" ]]; then
    print -u2 -- "FastAPI运行资产归档目录已存在：$archive_dir"
    return 75
  fi
  /bin/mkdir -p "$archive_dir"
  /usr/bin/install -m 600 "$BACKEND_DEST" "$archive_dir/com.linjian.stock-monitor.fastapi.plist"
  /usr/bin/install -m 700 "$BACKEND_RUNTIME_RUNNER" "$archive_dir/run-backend.sh"
  BACKEND_RELOAD_ARCHIVE="$archive_dir"
  print -- "已备份当前FastAPI运行资产：$archive_dir"
}

rollback_backend_reload() {
  local archive_dir="$1"
  print -u2 -- "FastAPI重载失败，开始恢复原运行资产"
  bootout_if_loaded "$BACKEND_LABEL"
  wait_for_backend_to_stop || true
  if ! /usr/bin/install -m 700 "$archive_dir/run-backend.sh" "$BACKEND_RUNTIME_RUNNER"; then
    print -u2 -- "原FastAPI启动脚本恢复失败：$archive_dir"
    return 1
  fi
  if ! /usr/bin/install -m 600 "$archive_dir/com.linjian.stock-monitor.fastapi.plist" "$BACKEND_DEST"; then
    print -u2 -- "原FastAPI plist恢复失败：$archive_dir"
    return 1
  fi
  if ! bootstrap_with_retry "$GUI_DOMAIN" "$BACKEND_DEST" "$BACKEND_LABEL"; then
    print -u2 -- "原FastAPI LaunchAgent重新加载失败：$archive_dir"
    return 1
  fi
  if ! wait_for_backend; then
    print -u2 -- "原FastAPI恢复后仍不健康：$archive_dir"
    return 1
  fi
  print -- "已恢复原FastAPI运行资产并重新启动"
}

reload_backend() {
  local source_plist="${1:-$BACKEND_SOURCE}"
  if [[ "$source_plist" != "$BACKEND_SOURCE" &&
        "$source_plist" != "$BACKEND_RADAR_DISABLED_SOURCE" &&
        "$source_plist" != "$BACKEND_SECTOR_ENABLED_SOURCE" &&
        "$source_plist" != "$BACKEND_MARKET_ENABLED_SOURCE" ]]; then
    print -u2 -- "FastAPI重载来源不在允许列表中"
    return 64
  fi
  preflight_backend_reload
  archive_backend_assets
  /bin/mkdir -p "$RADAR_RUNTIME_DIR"
  /bin/chmod 700 "$RADAR_RUNTIME_DIR"

  bootout_if_loaded "$BACKEND_LABEL"
  if ! wait_for_backend_to_stop; then
    rollback_backend_reload "$BACKEND_RELOAD_ARCHIVE"
    return 1
  fi
  if ! /usr/bin/install -m 700 "$BACKEND_RUNNER" "$BACKEND_RUNTIME_RUNNER"; then
    rollback_backend_reload "$BACKEND_RELOAD_ARCHIVE"
    return 1
  fi
  if ! /usr/bin/install -m 600 "$source_plist" "$BACKEND_DEST"; then
    rollback_backend_reload "$BACKEND_RELOAD_ARCHIVE"
    return 1
  fi
  if ! bootstrap_with_retry "$GUI_DOMAIN" "$BACKEND_DEST" "$BACKEND_LABEL"; then
    rollback_backend_reload "$BACKEND_RELOAD_ARCHIVE"
    return 1
  fi
  if ! wait_for_backend; then
    rollback_backend_reload "$BACKEND_RELOAD_ARCHIVE"
    return 1
  fi
  print -- "FastAPI已使用新运行资产受控重载；ngrok未停止"
}

status_services() {
  if launchd_loaded "$BACKEND_LABEL"; then
    print -- "FastAPI LaunchAgent：loaded"
  else
    print -- "FastAPI LaunchAgent：not_loaded"
  fi
  if launchd_loaded "$NGROK_LABEL"; then
    print -- "ngrok LaunchAgent：loaded"
  else
    print -- "ngrok LaunchAgent：not_loaded"
  fi
  if port_in_use; then
    print -- "后端8001端口：listening"
  else
    print -- "后端8001端口：not_listening"
  fi
  if ngrok_running; then
    print -- "ngrok进程：running"
  else
    print -- "ngrok进程：not_running"
  fi
  if [[ -x "$BACKEND_RUNTIME_RUNNER" && -x "$NGROK_RUNTIME_RUNNER" ]]; then
    print -- "LaunchAgent运行脚本：installed"
  else
    print -- "LaunchAgent运行脚本：not_installed"
  fi
  if screen_session_exists "$BACKEND_SCREEN"; then
    print -- "后端screen：present"
  else
    print -- "后端screen：absent"
  fi
  if screen_session_exists "$NGROK_SCREEN"; then
    print -- "ngrok screen：present"
  else
    print -- "ngrok screen：absent"
  fi
}

uninstall_services() {
  bootout_if_loaded "$NGROK_LABEL"
  bootout_if_loaded "$BACKEND_LABEL"
  archive_installed_assets
  print -- "LaunchAgent已卸载；数据库、日志、ngrok配置和项目文件均保留"
}

wait_for_services_to_stop() {
  integer attempt=0
  while (( attempt < 30 )); do
    if ! port_in_use && ! ngrok_running; then
      return 0
    fi
    attempt=$((attempt + 1))
    /bin/sleep 1
  done
  print -u2 -- "LaunchAgent进程在30秒内未完全退出，拒绝启动screen副本"
  return 1
}

rollback_to_screen() {
  uninstall_services
  wait_for_services_to_stop
  if screen_session_exists "$BACKEND_SCREEN" || screen_session_exists "$NGROK_SCREEN"; then
    print -u2 -- "同名screen会话已经存在，拒绝重复创建"
    return 75
  fi
  if port_in_use || ngrok_running; then
    print -u2 -- "端口或ngrok进程仍被占用，拒绝创建screen副本"
    return 75
  fi

  /usr/bin/screen -DmS "$BACKEND_SCREEN" /bin/zsh -lc \
    "cd '$BACKEND_DIR' && exec '$PYTHON_BIN' main.py"
  if ! wait_for_backend; then
    print -u2 -- "screen后端未恢复健康；请人工检查该会话"
    return 1
  fi
  /usr/bin/screen -DmS "$NGROK_SCREEN" /bin/zsh -lc \
    "cd '$PROJECT_ROOT' && exec '$NGROK_BIN' http 127.0.0.1:8001 '--url=$NGROK_DOMAIN' '--config=$NGROK_CONFIG'"
  if ! wait_for_ngrok; then
    print -u2 -- "screen ngrok未恢复；FastAPI screen保持运行供人工检查"
    return 1
  fi
  print -- "已恢复stock-monitor-backend与stock-monitor-ngrok screen会话"
}

command="${1:-}"
case "$command" in
  validate)
    validate_assets
    ;;
  preflight)
    preflight_install
    ;;
  install)
    install_services
    ;;
  reload-backend)
    reload_backend "$BACKEND_SOURCE"
    ;;
  enable-radar)
    reload_backend "$BACKEND_SOURCE"
    ;;
  disable-radar)
    reload_backend "$BACKEND_RADAR_DISABLED_SOURCE"
    ;;
  enable-sector-shadow)
    reload_backend "$BACKEND_SECTOR_ENABLED_SOURCE"
    ;;
  disable-sector-shadow)
    reload_backend "$BACKEND_SOURCE"
    ;;
  enable-market-shadow)
    reload_backend "$BACKEND_MARKET_ENABLED_SOURCE"
    ;;
  disable-market-shadow)
    reload_backend "$BACKEND_SECTOR_ENABLED_SOURCE"
    ;;
  status)
    status_services
    ;;
  uninstall)
    uninstall_services
    ;;
  rollback-screen)
    rollback_to_screen
    ;;
  *)
    usage
    exit 64
    ;;
esac
