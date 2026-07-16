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

stack_api_index_prediction_ok() {
  local port="${1:-$(stack_vibe_api_port)}"
  curl -sf -H "Accept: application/json" \
    "http://127.0.0.1:${port}/trade/index-prediction?ticker=NIFTY" 2>/dev/null \
    | grep -q '"status":"ok"'
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

stack_port_listener_pid() {
  local port="$1"
  lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -1 || true
}

stack_sync_pidfile_from_port() {
  local pidfile="$1" port="$2"
  local listener
  listener="$(stack_port_listener_pid "$port")"
  if [[ -n "$listener" ]]; then
    echo "$listener" >"$pidfile"
  fi
}

stack_service_up() {
  local port="$1"
  stack_http_ok "http://127.0.0.1:${port}/"
}

# Start a detached process; writes the child PID to pidfile.
stack_launch_detached() {
  local pidfile="$1" logfile="$2" workdir="$3"
  shift 3
  local expect_port="${STACK_LAUNCH_EXPECT_PORT:-}"

  mkdir -p "$(dirname "$pidfile")" "$(dirname "$logfile")"

  local existing
  existing="$(stack_read_pid "$pidfile")"
  if stack_pid_alive "$existing"; then
    if [[ -z "$expect_port" ]] || stack_service_up "$expect_port"; then
      echo "[stack] already running (pid $existing)"
      return 0
    fi
    echo "[stack] pid $existing alive but :${expect_port} down — replacing ..."
    kill "$existing" 2>/dev/null || true
    sleep 0.5
    stack_pid_alive "$existing" && kill -9 "$existing" 2>/dev/null || true
  fi

  : >>"$logfile"

  local prev="$PWD" pid
  cd "$workdir" || return 1
  nohup "$@" >>"$logfile" 2>&1 < /dev/null &
  pid=$!
  disown "$pid" 2>/dev/null || true
  echo "$pid" >"$pidfile"
  cd "$prev" || true

  sleep 2
  existing="$(stack_read_pid "$pidfile")"
  if stack_pid_alive "$existing"; then
    return 0
  fi
  if [[ -n "$expect_port" ]]; then
    local listener
    listener="$(stack_port_listener_pid "$expect_port")"
    if [[ -n "$listener" ]]; then
      echo "$listener" >"$pidfile"
      return 0
    fi
  fi

  echo "[stack] failed to start in $workdir: $*" >&2
  tail -8 "$logfile" 2>/dev/null >&2 || true
  return 1
}

stack_stop_pidfile() {
  local name="$1" pidfile="$2" pkill_pattern="${3:-}"

  local pid stopped=0
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
    stopped=1
  fi
  rm -f "$pidfile"

  # Also stop stray listeners when pidfile was stale or parent forked.
  if [[ -n "$pkill_pattern" ]]; then
    if pgrep -f "$pkill_pattern" >/dev/null 2>&1; then
      echo "[stack] stopping stray $name processes ..."
      pkill -f "$pkill_pattern" 2>/dev/null || true
    fi
  fi
}

stack_pick_openalgo_cmd() {
  local root openalgo_dir
  root="$(stack_root)"
  openalgo_dir="$root/openalgo"

  if [[ -x "$openalgo_dir/.venv/bin/python" ]]; then
    echo "$openalgo_dir/.venv/bin/python app.py"
    return
  fi
  if command -v uv >/dev/null 2>&1; then
    echo "uv run app.py"
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
    stack_sync_pidfile_from_port "$pidfile" "$port"
    echo "[stack] OpenAlgo already up at $base"
    return 0
  fi

  stack_kill_port "$port"
  stack_kill_port 8765

  echo "[stack] starting OpenAlgo on :$port ..."
  STACK_LAUNCH_EXPECT_PORT="$port"
  if [[ -x "$root/openalgo/.venv/bin/python" ]]; then
    stack_launch_detached "$pidfile" "$logfile" "$root/openalgo" \
      "$root/openalgo/.venv/bin/python" app.py
  else
    runner="$(stack_pick_openalgo_cmd)"
    # shellcheck disable=SC2086
    stack_launch_detached "$pidfile" "$logfile" "$root/openalgo" bash -lc "exec $runner"
  fi
  unset STACK_LAUNCH_EXPECT_PORT
  stack_wait_for_url "OpenAlgo" "$base/" 90
  stack_sync_pidfile_from_port "$pidfile" "$port"
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

  if stack_http_ok "$base/" && stack_api_index_prediction_ok "$port"; then
    stack_sync_pidfile_from_port "$pidfile" "$port"
    echo "[stack] Vibe API already up at $base"
    return 0
  fi

  if stack_http_ok "$base/"; then
    echo "[stack] Vibe API on :$port is stale (missing /trade/index-prediction) — restarting ..."
  fi

  stack_kill_port "$port"

  if [[ ! -x "$root/.venv/bin/vibe-trading" ]]; then
    echo "[stack] vibe-trading not installed — run: pip install -e vibetrading/" >&2
    return 1
  fi

  echo "[stack] starting Vibe API on :$port ..."
  STACK_LAUNCH_EXPECT_PORT="$port"
  stack_launch_detached \
    "$pidfile" "$logfile" "$agent_dir" \
    "$py" -m cli._legacy serve --port "$port"
  unset STACK_LAUNCH_EXPECT_PORT
  stack_wait_for_url "Vibe API" "$base/" 60
  stack_sync_pidfile_from_port "$pidfile" "$port"
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
    stack_sync_pidfile_from_port "$pidfile" "$port"
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
  STACK_LAUNCH_EXPECT_PORT="$port"
  stack_launch_detached \
    "$pidfile" "$logfile" "$frontend" \
    "$frontend/node_modules/.bin/vite" --port "$port" --host 127.0.0.1
  unset STACK_LAUNCH_EXPECT_PORT
  stack_wait_for_url "Vibe UI" "$url/" 60
  stack_sync_pidfile_from_port "$pidfile" "$port"
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
  stack_stop_nautilus_watch
  stack_stop_pidfile "OpenAlgo" "$log_dir/openalgo.pid" "openalgo.*app.py"

  stack_kill_port "$ui_port"
  stack_kill_port "$api_port"
  stack_kill_port "$openalgo_port"
  stack_kill_port 8765

  sleep 1
}

stack_start_vibe_stack() {
  stack_ensure_vibe_stack
}

# Start only services that are down (no full stop — avoids killing healthy processes).
stack_primary_nautilus_agent_id() {
  local root py
  root="$(stack_root)"
  py="$(stack_pick_python)"
  "$py" - "$root" <<'PY' 2>/dev/null || true
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, str(root / "integrations"))
try:
    from trade_integrations.autonomous_agents.store import list_agents
    from trade_integrations.execution.profile import resolve_profile
except Exception:
    raise SystemExit(0)
for agent in list_agents():
    if str(agent.get("status")) != "running":
        continue
    try:
        profile = resolve_profile(agent=agent)
    except Exception:
        continue
    if profile.uses_nautilus_handoff:
        aid = str(agent.get("id") or "").strip()
        if aid:
            print(aid)
            raise SystemExit(0)
PY
}

stack_ensure_nautilus_watch() {
  local agent_id="${1:-$(stack_primary_nautilus_agent_id)}"
  if [[ -z "$agent_id" ]]; then
    return 0
  fi
  stack_start_nautilus_watch "$agent_id" || {
    echo "[stack] Nautilus watch not started — bootstrap poll ticks still run via Vibe scheduler" >&2
    return 0
  }
}

stack_ensure_vibe_stack() {
  local ok=0
  stack_start_openalgo || ok=1
  stack_start_vibe_api || ok=1
  stack_start_vibe_ui || ok=1
  stack_ensure_nautilus_watch || true
  return "$ok"
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
    local name port pidfile pid http_code alive="dead"
    name="${svc%%:*}"
    port="${svc#*:}"; port="${port%%:*}"
    pidfile="${svc##*:}"
    pid="$(stack_read_pid "$pidfile")"
    http_code="$(curl -sf -o /dev/null -w "%{http_code}" -m 5 "http://127.0.0.1:${port}/" 2>/dev/null || true)"
    if [[ -z "$http_code" ]]; then
      http_code="000"
    fi
    local listener
    listener="$(stack_port_listener_pid "$port")"
    if [[ -n "$listener" ]] && kill -0 "$listener" 2>/dev/null; then
      alive="alive"
      pid="$listener"
      stack_sync_pidfile_from_port "$pidfile" "$port"
    elif stack_pid_alive "$pid"; then
      alive="alive"
    fi

    if [[ "$http_code" == "200" ]]; then
      echo "  ✓ $name  :$port  HTTP $http_code  pid=${pid:-?} ($alive)"
    elif [[ -n "$listener" ]] && stack_pid_alive "$listener"; then
      echo "  ⚠ $name  :$port  HTTP $http_code  pid=${listener} (alive, not ready)"
      ok=0
    else
      echo "  ✗ $name  :$port  HTTP $http_code  pid=${pid:-none} ($alive)"
      ok=0
    fi
  done

  local nautilus_pid
  nautilus_pid="$(stack_read_pid "$log_dir/nautilus-watch.pid")"
  if stack_pid_alive "$nautilus_pid"; then
    echo "  ✓ Nautilus watch  pid=${nautilus_pid} (alive)"
  elif [[ "${NAUTILUS_WATCH_ENABLE:-1}" == "0" || "${NAUTILUS_WATCH_ENABLE:-}" == "false" ]]; then
    echo "  · Nautilus watch  disabled (NAUTILUS_WATCH_ENABLE=0)"
  else
    echo "  ✗ Nautilus watch  pid=${nautilus_pid:-none} (expected — enabled by default)"
    ok=0
  fi

  echo "══════════════════════════════════════════════════════════"
  if (( ok )); then return 0; fi
  echo "  Fix: trade restart   (starts only what's down)"
  echo "  Full reset: trade restart --force"
  echo "══════════════════════════════════════════════════════════"
  return 1
}

stack_nautilus_python() {
  local root py
  root="$(stack_root)"
  py="$root/.venv-nautilus/bin/python"
  if [[ -x "$py" ]]; then
    echo "$py"
    return
  fi
  stack_pick_python
}

stack_start_nautilus_watch() {
  local root log_dir pidfile logfile agent_id_file py agent_id legacy=0
  root="$(stack_root)"
  log_dir="$(stack_log_dir)"
  pidfile="$log_dir/nautilus-watch.pid"
  logfile="$log_dir/nautilus-watch.log"
  agent_id_file="$log_dir/nautilus-watch.agent_id"
  agent_id="${NAUTILUS_AGENT_ID:-}"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --agent-id)
        agent_id="${2:-}"
        shift 2
        ;;
      --agent-id=*)
        agent_id="${1#*=}"
        shift
        ;;
      *)
        if [[ -z "$agent_id" && "$1" == aa_* ]]; then
          agent_id="$1"
        fi
        shift
        ;;
    esac
  done

  if [[ "${NAUTILUS_WATCH_ENABLE:-1}" == "0" || "${NAUTILUS_WATCH_ENABLE:-}" == "false" ]]; then
    echo "[stack] NAUTILUS_WATCH_ENABLE=0 — skip Nautilus watch node"
    return 0
  fi

  existing="$(stack_read_pid "$pidfile")"
  bound_agent=""
  if [[ -f "$agent_id_file" ]]; then
    bound_agent="$(tr -d '[:space:]' <"$agent_id_file")"
  fi
  if stack_pid_alive "$existing"; then
    if [[ -n "$agent_id" && -n "$bound_agent" && "$bound_agent" != "$agent_id" ]]; then
      echo "[stack] Nautilus watch bound to $bound_agent — restarting for $agent_id ..."
      stack_stop_nautilus_watch
    elif [[ -n "$agent_id" && -z "$bound_agent" ]]; then
      echo "$agent_id" >"$agent_id_file"
      echo "[stack] Nautilus watch already running (pid $existing) — bound to $agent_id"
      return 0
    else
      echo "[stack] Nautilus watch already running (pid $existing${bound_agent:+, agent $bound_agent})"
      return 0
    fi
  fi

  if [[ -x "$root/.venv-nautilus/bin/python" ]]; then
    py="$root/.venv-nautilus/bin/python"
  elif [[ -x "$root/.venv/bin/python" ]]; then
    py="$root/.venv/bin/python"
    legacy=1
    echo "[stack] .venv-nautilus missing — starting legacy poll watch (run ./scripts/setup_nautilus.sh for TradingNode)"
  else
    echo "[stack] No Python venv found for Nautilus watch" >&2
    return 1
  fi

  echo "[stack] starting Nautilus watch node ..."
  local cmd=("$py" -m nautilus_openalgo_bridge.runtime.run_watch_node)
  if (( legacy )); then
    cmd+=(--legacy-poll)
  fi
  if [[ -n "$agent_id" ]]; then
    cmd+=(--agent-id "$agent_id")
    echo "$agent_id" >"$agent_id_file"
  fi
  stack_launch_detached "$pidfile" "$logfile" "$root" "${cmd[@]}"
}

stack_stop_nautilus_watch() {
  stack_stop_pidfile "Nautilus watch" "$(stack_log_dir)/nautilus-watch.pid" "run_watch_node"
  rm -f "$(stack_log_dir)/nautilus-watch.agent_id"
}
