#!/usr/bin/env bash
# Shared helpers for running OpenAlgo + Vibe stack as detached background services.
# Works on macOS (no setsid) and Linux.

if [[ -n "${STACK_LIB_SOURCED:-}" ]]; then
  return 0 2>/dev/null || exit 0
fi
STACK_LIB_SOURCED=1

stack_root() {
  if [[ -n "${STACK_ROOT:-}" ]]; then
    echo "$STACK_ROOT"
    return
  fi
  local here="${BASH_SOURCE[${#BASH_SOURCE[@]} - 1]}"
  STACK_ROOT="$(cd "$(dirname "$here")/.." && pwd)"
  echo "$STACK_ROOT"
}

stack_log_dir() {
  echo "$(stack_root)/log"
}

stack_load_env() {
  local root env_file
  root="$(stack_root)"
  env_file="$root/.env"
  if [[ -f "$env_file" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
  fi
}

stack_openalgo_port() {
  local url="${OPENALGO_HOST:-http://127.0.0.1:5001}"
  url="${url#*://}"
  url="${url%%/*}"
  echo "${url##*:}"
}

stack_vibe_api_port() {
  echo "${VIBE_BACKEND_PORT:-8899}"
}

stack_vibe_ui_port() {
  echo "${VIBE_FRONTEND_PORT:-5899}"
}

stack_http_ok() {
  curl -sf -o /dev/null -m 3 "$1" 2>/dev/null
}

stack_wait_for_url() {
  local label="$1" url="$2" attempts="${3:-45}"
  for ((i = 1; i <= attempts; i++)); do
    if stack_http_ok "$url"; then
      return 0
    fi
    sleep 1
  done
  echo "[stack] timed out waiting for $label at $url" >&2
  return 1
}

stack_pid_alive() {
  local pid="${1:-}"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

stack_read_pid() {
  local pidfile="$1"
  if [[ -f "$pidfile" ]]; then
    tr -d '[:space:]' <"$pidfile"
  fi
}

# Start a detached process; writes the child PID to pidfile.
stack_launch_detached() {
  local pidfile="$1" logfile="$2" workdir="$3"
  shift 3

  mkdir -p "$(dirname "$pidfile")" "$(dirname "$logfile")"

  local existing
  existing="$(stack_read_pid "$pidfile")"
  if stack_pid_alive "$existing"; then
    echo "[stack] already running (pid $existing)"
    return 0
  fi

  : >>"$logfile"

  (
    cd "$workdir" || exit 1
    nohup "$@" >>"$logfile" 2>&1 &
    echo $! >"$pidfile"
  )

  sleep 1
  existing="$(stack_read_pid "$pidfile")"
  if stack_pid_alive "$existing"; then
    return 0
  fi

  echo "[stack] failed to start in $workdir: $*" >&2
  tail -5 "$logfile" 2>/dev/null >&2 || true
  return 1
}

stack_stop_pidfile() {
  local name="$1" pidfile="$2" pkill_pattern="${3:-}"

  local pid
  pid="$(stack_read_pid "$pidfile")"
  if stack_pid_alive "$pid"; then
    echo "[stack] stopping $name (pid $pid) ..."
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
      stack_pid_alive "$pid" || break
      sleep 0.5
    done
    if stack_pid_alive "$pid"; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
  rm -f "$pidfile"

  if [[ -n "$pkill_pattern" ]]; then
    pkill -f "$pkill_pattern" 2>/dev/null || true
  fi
}

stack_pick_openalgo_cmd() {
  local root openalgo_dir
  root="$(stack_root)"
  openalgo_dir="$root/openalgo"

  if command -v uv >/dev/null 2>&1; then
    echo "uv run app.py"
    return
  fi
  if [[ -x "$openalgo_dir/.venv/bin/python" ]]; then
    echo "$openalgo_dir/.venv/bin/python app.py"
    return
  fi
  echo "python3 app.py"
}

stack_pick_python() {
  local root py
  root="$(stack_root)"
  py="$root/.venv/bin/python"
  if [[ -x "$py" ]]; then
    echo "$py"
    return
  fi
  echo "python3"
}

stack_start_openalgo() {
  local root log_dir pidfile logfile runner port base
  root="$(stack_root)"
  log_dir="$(stack_log_dir)"
  pidfile="$log_dir/openalgo.pid"
  logfile="$root/openalgo/log/stack-openalgo.log"
  port="$(stack_openalgo_port)"
  base="${OPENALGO_HOST:-http://127.0.0.1:$port}"
  base="${base%/}"

  if stack_http_ok "$base/"; then
    echo "[stack] OpenAlgo already up at $base"
    return 0
  fi

  stack_kill_port "$port"

  runner="$(stack_pick_openalgo_cmd)"
  echo "[stack] starting OpenAlgo on :$port ..."
  # shellcheck disable=SC2086
  stack_launch_detached "$pidfile" "$logfile" "$root/openalgo" bash -lc "exec $runner"
  stack_wait_for_url "OpenAlgo" "$base/" 90
}

stack_start_vibe_api() {
  local root log_dir pidfile logfile py port base agent_dir
  root="$(stack_root)"
  log_dir="$(stack_log_dir)"
  pidfile="$log_dir/vibe-api.pid"
  logfile="$log_dir/vibe-api.log"
  py="$(stack_pick_python)"
  port="$(stack_vibe_api_port)"
  base="http://127.0.0.1:$port"
  agent_dir="$root/vibetrading/agent"

  if stack_http_ok "$base/"; then
    echo "[stack] Vibe API already up at $base"
    return 0
  fi

  stack_kill_port "$port"

  if [[ ! -x "$root/.venv/bin/vibe-trading" ]]; then
    echo "[stack] vibe-trading not installed — run: pip install -e vibetrading/" >&2
    return 1
  fi

  echo "[stack] starting Vibe API on :$port ..."
  stack_launch_detached \
    "$pidfile" "$logfile" "$agent_dir" \
    "$py" -m cli._legacy serve --port "$port"
  stack_wait_for_url "Vibe API" "$base/" 60
}

stack_start_vibe_ui() {
  local root log_dir pidfile logfile frontend port url
  root="$(stack_root)"
  log_dir="$(stack_log_dir)"
  pidfile="$log_dir/vibe-ui.pid"
  logfile="$log_dir/vibe-ui.log"
  frontend="${VIBE_FRONTEND_DIR:-$root/vibetrading/frontend}"
  port="$(stack_vibe_ui_port)"
  url="http://127.0.0.1:$port"

  if stack_http_ok "$url/"; then
    echo "[stack] Vibe UI already up at $url"
    return 0
  fi

  stack_kill_port "$port"

  if [[ ! -f "$frontend/package.json" ]]; then
    echo "[stack] Vibe frontend missing at $frontend" >&2
    return 1
  fi
  if [[ ! -x "$frontend/node_modules/.bin/vite" ]]; then
    echo "[stack] Vite not installed — run: ./scripts/ensure_vibe_frontend.sh" >&2
    return 1
  fi

  echo "[stack] starting Vibe UI (Vite) on :$port ..."
  stack_launch_detached \
    "$pidfile" "$logfile" "$frontend" \
    "$frontend/node_modules/.bin/vite" --port "$port" --host 127.0.0.1
  stack_wait_for_url "Vibe UI" "$url/" 60
}

stack_kill_port() {
  local port="$1"
  local pids
  pids="$(lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 0.5
    pids="$(lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "$pids" ]]; then
      # shellcheck disable=SC2086
      kill -9 $pids 2>/dev/null || true
    fi
  fi
}

stack_stop_vibe_stack() {
  local log_dir api_port ui_port openalgo_port
  log_dir="$(stack_log_dir)"
  api_port="$(stack_vibe_api_port)"
  ui_port="$(stack_vibe_ui_port)"
  openalgo_port="$(stack_openalgo_port)"

  stack_stop_pidfile "Vibe UI" "$log_dir/vibe-ui.pid" "vite --port ${ui_port}"
  stack_stop_pidfile "Vibe API" "$log_dir/vibe-api.pid" "cli._legacy serve"
  stack_stop_pidfile "vibe-trading (legacy)" "$log_dir/vibe-trading.pid" "vibe-trading dev"
  stack_stop_pidfile "OpenAlgo" "$log_dir/openalgo.pid" "openalgo.*app.py"

  stack_kill_port "$ui_port"
  stack_kill_port "$api_port"
  stack_kill_port "$openalgo_port"

  sleep 1
}

stack_start_vibe_stack() {
  stack_start_openalgo
  stack_start_vibe_api
  stack_start_vibe_ui
}

stack_print_ready() {
  local openalgo_port api_port ui_port
  openalgo_port="$(stack_openalgo_port)"
  api_port="$(stack_vibe_api_port)"
  ui_port="$(stack_vibe_ui_port)"

  echo ""
  echo "Ready:"
  echo "  OpenAlgo  http://127.0.0.1:${openalgo_port}"
  echo "  Vibe UI   http://127.0.0.1:${ui_port}"
  echo "  Vibe API  http://127.0.0.1:${api_port}"
  echo ""
  echo "Logs: $(stack_log_dir)/"
  echo "Stop: ./scripts/stop_vibe_stack.sh"
}

stack_status_vibe_stack() {
  local log_dir openalgo_port api_port ui_port ok=1
  log_dir="$(stack_log_dir)"
  openalgo_port="$(stack_openalgo_port)"
  api_port="$(stack_vibe_api_port)"
  ui_port="$(stack_vibe_ui_port)"

  echo "══════════════════════════════════════════════════════════"
  echo "  Vibe stack status"
  echo "══════════════════════════════════════════════════════════"

  for svc in "OpenAlgo:$openalgo_port:$log_dir/openalgo.pid" \
             "Vibe API:$api_port:$log_dir/vibe-api.pid" \
             "Vibe UI:$ui_port:$log_dir/vibe-ui.pid"; do
    local name port pidfile pid http_code
    name="${svc%%:*}"
    port="${svc#*:}"; port="${port%%:*}"
    pidfile="${svc##*:}"
    pid="$(stack_read_pid "$pidfile")"
    http_code="$(curl -sf -o /dev/null -w "%{http_code}" -m 3 "http://127.0.0.1:${port}/" 2>/dev/null || echo "---")"
    local alive="dead"
    if stack_pid_alive "$pid"; then alive="alive"; fi

    if [[ "$http_code" == "200" ]]; then
      echo "  ✓ $name  :$port  HTTP $http_code  pid=${pid:-?} ($alive)"
    else
      echo "  ✗ $name  :$port  HTTP $http_code  pid=${pid:-none} ($alive)"
      ok=0
    fi
  done

  echo "══════════════════════════════════════════════════════════"
  if (( ok )); then return 0; fi
  return 1
}
