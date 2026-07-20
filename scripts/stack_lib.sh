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
  local src lib_dir
  for src in "${BASH_SOURCE[@]}"; do
    if [[ "$(basename "$src")" == "stack_lib.sh" ]]; then
      lib_dir="$(cd "$(dirname "$src")" && pwd)"
      STACK_ROOT="$(cd "$lib_dir/.." && pwd)"
      echo "$STACK_ROOT"
      return
    fi
  done
  # Fallback when stack_lib is executed directly
  local here="${BASH_SOURCE[0]}"
  STACK_ROOT="$(cd "$(dirname "$here")/.." && pwd)"
  echo "$STACK_ROOT"
}

stack_log_dir() {
  echo "$(stack_root)/log"
}

stack_mode_file() {
  echo "$(stack_log_dir)/stack.mode"
}

stack_stack_mode() {
  local file
  file="$(stack_mode_file)"
  [[ -f "$file" ]] && tr -d '[:space:]' <"$file" || true
}

stack_dev_mode_flagged() {
  [[ "$(stack_stack_mode)" == "dev" ]]
}

# True when dev tier is actually responding (OpenAlgo + Vibe API minimum).
stack_dev_tier_alive() {
  local api_port openalgo_port
  stack_load_env
  api_port="$(stack_vibe_api_port)"
  openalgo_port="$(stack_openalgo_port)"
  stack_http_ok "http://127.0.0.1:${openalgo_port}/" || return 1
  # Vibe API root is 404; /health or a listening uvicorn on the port counts as up.
  curl -sf -o /dev/null -m 3 "http://127.0.0.1:${api_port}/health" 2>/dev/null && return 0
  stack_vibe_api_listener_alive "$api_port"
}

stack_vibe_api_http_ok() {
  local port="${1:-$(stack_vibe_api_port)}"
  curl -sf -o /dev/null -m 3 "http://127.0.0.1:${port}/health" 2>/dev/null && return 0
  stack_vibe_api_listener_alive "$port"
}

# Clear orphaned dev flag when the dev tier is down (terminal closed, Ctrl+C, etc.).
stack_reconcile_stale_dev_mode() {
  if stack_dev_tier_alive || ! stack_dev_mode_flagged; then
    return 0
  fi
  local api_port openalgo_port ui_port
  stack_load_env
  api_port="$(stack_vibe_api_port)"
  openalgo_port="$(stack_openalgo_port)"
  ui_port="$(stack_vibe_ui_port)"
  if [[ -n "$(stack_port_listener_pid "$openalgo_port")" ]] \
    || [[ -n "$(stack_port_listener_pid "$api_port")" ]] \
    || [[ -n "$(stack_port_listener_pid "$ui_port")" ]]; then
    return 0
  fi
  echo "[stack] clearing stale dev mode (dev tier not running — run: ./trade dev)" >&2
  stack_clear_stack_mode
}

stack_dev_mode_active() {
  stack_reconcile_stale_dev_mode
  stack_dev_mode_flagged && stack_dev_tier_alive
}

stack_set_stack_mode() {
  local mode="$1"
  mkdir -p "$(stack_log_dir)"
  printf '%s\n' "$mode" >"$(stack_mode_file)"
}

stack_clear_stack_mode() {
  rm -f "$(stack_mode_file)"
}

stack_refuse_if_dev_mode() {
  stack_reconcile_stale_dev_mode
  if stack_dev_mode_flagged && stack_dev_tier_alive; then
    echo "[stack] dev mode active — keep this terminal open, or stop with Ctrl+C then: ./trade dev" >&2
    exit 1
  fi
}

stack_load_env() {
  local root env_file
  root="$(stack_root)"
  env_file="$root/.env"
  # shellcheck disable=SC1091
  source "$root/scripts/stack_ports.sh"
  stack_ensure_ports_env || true
  if [[ -f "$env_file" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
  fi
  # shellcheck disable=SC1091
  source "$root/scripts/stack_docker_lib.sh"
  stack_ensure_docker_path
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
  curl -sf -m 10 -H "Accept: application/json" \
    "http://127.0.0.1:${port}/trade/index-prediction?ticker=NIFTY" 2>/dev/null \
    | grep -q '"status":"ok"'
}

stack_vibe_api_listener_alive() {
  local port="${1:-$(stack_vibe_api_port)}"
  local listener
  listener="$(stack_port_listener_pid "$port")"
  [[ -n "$listener" ]] && stack_pid_alive "$listener"
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
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  local stat
  stat="$(ps -p "$pid" -o stat= 2>/dev/null | tr -d ' ' || true)"
  [[ -n "$stat" && "$stat" != *Z* ]]
}

stack_read_pid() {
  local pidfile="$1" raw
  if [[ ! -f "$pidfile" ]]; then
    return 0
  fi
  raw="$(head -1 "$pidfile" 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ "$raw" =~ ^[0-9]+$ ]]; then
    echo "$raw"
    return 0
  fi
  raw="$(grep -Eo '[0-9]+' "$pidfile" 2>/dev/null | head -1 || true)"
  [[ -n "$raw" ]] && echo "$raw"
}

stack_write_pidfile() {
  printf '%s\n' "$2" >"$1"
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
    stack_write_pidfile "$pidfile" "$listener"
  fi
}

stack_claims_dir() {
  echo "$(stack_log_dir)/claims"
}

stack_claim_file() {
  local service="$1"
  echo "$(stack_claims_dir)/${service}.claim"
}

stack_read_claim_field() {
  local service="$1" field="$2"
  local file
  file="$(stack_claim_file "$service")"
  [[ -f "$file" ]] || return 1
  awk -F= -v want="$field" '$1 == want { print substr($0, index($0, "=") + 1); exit }' "$file"
}

stack_claim_pid() {
  stack_read_claim_field "$1" "pid" 2>/dev/null || true
}

stack_write_claim() {
  local service="$1" pid="$2" port="${3:-}" cmd="${4:-}"
  local dir file
  dir="$(stack_claims_dir)"
  mkdir -p "$dir"
  file="$(stack_claim_file "$service")"
  {
    echo "pid=$pid"
    echo "port=$port"
    echo "root=$(stack_root)"
    echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "command=$cmd"
  } >"$file"
}

stack_release_claim() {
  local service="$1"
  rm -f "$(stack_claim_file "$service")"
}

stack_reconcile_stale_claims() {
  local service port claimed_pid listener health_url
  for claim in "$(stack_claims_dir)"/*.claim; do
    [[ -f "$claim" ]] || continue
    service="$(basename "$claim" .claim)"
    claimed_pid="$(stack_claim_pid "$service")"
    port="$(stack_read_claim_field "$service" "port" 2>/dev/null || true)"
    if [[ -n "$claimed_pid" ]] && stack_pid_alive "$claimed_pid"; then
      if [[ -n "$port" ]] && stack_listener_matches_claim "$claimed_pid" "$port"; then
        continue
      fi
      if [[ -z "$port" ]]; then
        continue
      fi
      if [[ -n "$port" ]] && stack_http_ok "http://127.0.0.1:${port}/"; then
        listener="$(stack_port_listener_pid "$port")"
        if [[ -n "$listener" ]]; then
          stack_write_claim "$service" "$listener" "$port" "$(stack_read_claim_field "$service" "command" 2>/dev/null || echo adopted)"
        fi
        continue
      fi
    fi
    listener=""
    if [[ -n "$port" ]]; then
      listener="$(stack_port_listener_pid "$port")"
    fi
    if [[ -n "$listener" ]] && stack_process_in_trade_repo "$listener"; then
      stack_write_claim "$service" "$listener" "$port" "$(stack_read_claim_field "$service" "command" 2>/dev/null || echo adopted)"
      continue
    fi
    echo "[stack] clearing stale claim for $service (pid ${claimed_pid:-none})"
    stack_release_claim "$service"
  done
}

stack_service_for_pid() {
  local want_pid="$1" svc file pid
  for file in "$(stack_claims_dir)"/*.claim; do
    [[ -f "$file" ]] || continue
    svc="$(basename "$file" .claim)"
    pid="$(awk -F= '$1 == "pid" { print $2; exit }' "$file")"
    if [[ "$pid" == "$want_pid" ]]; then
      echo "$svc"
      return 0
    fi
  done
  return 1
}

stack_claim_valid() {
  local service="$1" claimed_pid port listener
  claimed_pid="$(stack_claim_pid "$service")"
  [[ -n "$claimed_pid" ]] || return 1
  port="$(stack_read_claim_field "$service" "port" 2>/dev/null || true)"
  if [[ -n "$port" ]]; then
    if stack_listener_matches_claim "$claimed_pid" "$port"; then
      listener="$(stack_port_listener_pid "$port")"
      if [[ -n "$listener" && "$listener" != "$claimed_pid" ]]; then
        stack_write_claim "$service" "$listener" "$port" "$(stack_read_claim_field "$service" "command" 2>/dev/null || echo adopted)"
      fi
      return 0
    fi
    return 1
  fi
  stack_pid_alive "$claimed_pid"
}

stack_process_in_trade_repo() {
  local pid="$1"
  local root args cwd
  root="$(stack_root)"
  args="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  if [[ -n "$args" ]]; then
    if [[ "$args" == *"$root"* ]]; then
      return 0
    fi
    if [[ "$args" == *"cli._legacy"* || "$args" == *"app.py"* || "$args" == *"/vite"* ]]; then
      return 0
    fi
  fi
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1 || true)"
  if [[ -n "$cwd" && "$cwd" == "$root"* ]]; then
    return 0
  fi
  return 1
}

stack_listener_matches_claim() {
  local claimed_pid="$1" port="$2"
  local listener ppid
  [[ -n "$claimed_pid" ]] || return 1
  listener="$(stack_port_listener_pid "$port")"
  [[ -z "$listener" ]] && stack_pid_alive "$claimed_pid"
  [[ "$listener" == "$claimed_pid" ]] && return 0
  ppid="$(ps -p "$listener" -o ppid= 2>/dev/null | tr -d ' ' || true)"
  [[ -n "$ppid" && "$ppid" == "$claimed_pid" ]] && return 0
  if stack_pid_alive "$claimed_pid" && stack_process_in_trade_repo "$listener"; then
    return 0
  fi
  return 1
}

stack_assert_port_for_start() {
  local service="$1" port="$2"
  local listener claimed_pid other_service

  listener="$(stack_port_listener_pid "$port")"
  [[ -z "$listener" ]] && return 0

  if stack_claim_valid "$service"; then
    return 0
  fi

  claimed_pid="$(stack_claim_pid "$service")"
  if [[ -n "$claimed_pid" ]] && stack_listener_matches_claim "$claimed_pid" "$port"; then
    return 0
  fi

  other_service="$(stack_service_for_pid "$listener" 2>/dev/null || true)"
  if [[ -n "$other_service" && "$other_service" != "$service" ]]; then
    echo "[stack] cannot start $service: :$port held by $other_service (pid $listener)" >&2
    echo "[stack] run: trade down  (or trade restart --force)" >&2
    return 1
  fi

  if stack_process_in_trade_repo "$listener"; then
    echo "[stack] cannot start $service: :$port held by unclaimed trade pid $listener" >&2
    echo "[stack] run: trade down  (or trade restart --force)" >&2
    return 1
  fi

  echo "[stack] cannot start $service: :$port held by foreign pid $listener" >&2
  echo "[stack] stop that process, then: trade up" >&2
  return 1
}

stack_adopt_running_service() {
  local service="$1" port="$2" pidfile="$3" health_url="${4:-}"
  local listener other

  if [[ -n "$health_url" ]] && ! stack_http_ok "$health_url"; then
    return 1
  fi

  if stack_claim_valid "$service"; then
    listener="$(stack_claim_pid "$service")"
    echo "$listener" >"$pidfile"
    return 0
  fi

  listener="$(stack_port_listener_pid "$port")"
  [[ -n "$listener" ]] || return 1

  other="$(stack_service_for_pid "$listener" 2>/dev/null || true)"
  if [[ -n "$other" && "$other" != "$service" ]]; then
    return 1
  fi

  if ! stack_process_in_trade_repo "$listener"; then
    return 1
  fi

  stack_write_claim "$service" "$listener" "$port" "adopted"
  echo "$listener" >"$pidfile"
  return 0
}

stack_wait_port_free() {
  local port="$1" attempts="${2:-15}" i listener
  for ((i = 1; i <= attempts; i++)); do
    listener="$(stack_port_listener_pid "$port")"
    [[ -z "$listener" ]] && return 0
    sleep 1
  done
  return 1
}

stack_stop_claimed() {
  local name="$1" service="$2" pidfile="$3" port="${4:-}"
  local claimed_pid listener

  claimed_pid="$(stack_claim_pid "$service")"
  if [[ -z "$claimed_pid" ]]; then
    claimed_pid="$(stack_read_pid "$pidfile")"
  fi

  if [[ -n "$claimed_pid" ]] && stack_pid_alive "$claimed_pid"; then
    echo "[stack] stopping $name (claimed pid $claimed_pid) ..."
    kill "$claimed_pid" 2>/dev/null || true
    for _ in $(seq 1 15); do
      stack_pid_alive "$claimed_pid" || break
      sleep 0.5
    done
    if stack_pid_alive "$claimed_pid"; then
      kill -9 "$claimed_pid" 2>/dev/null || true
    fi
  fi

  if [[ -n "$port" ]]; then
    listener="$(stack_port_listener_pid "$port")"
    if [[ -n "$listener" ]]; then
      if [[ -n "$claimed_pid" && "$listener" == "$claimed_pid" ]]; then
        stack_wait_port_free "$port" 15 || {
          echo "[stack] force-releasing :$port (pid $listener) ..."
          kill -9 "$listener" 2>/dev/null || true
        }
      elif [[ -z "$claimed_pid" ]] && stack_process_in_trade_repo "$listener"; then
        echo "[stack] stopping unclaimed $name on :$port (pid $listener) ..."
        kill "$listener" 2>/dev/null || true
        stack_wait_port_free "$port" 10 || kill -9 "$listener" 2>/dev/null || true
      elif [[ -n "$listener" ]]; then
        echo "[stack] leaving foreign listener on :$port (pid $listener) — not owned by trade" >&2
      fi
    fi
  fi

  stack_release_claim "$service"
  rm -f "$pidfile"
}

stack_write_instance_manifest() {
  local file log_dir pid
  log_dir="$(stack_log_dir)"
  file="$log_dir/stack.instance"
  {
    echo "updated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "root=$(stack_root)"
    echo "openalgo_pid=$(stack_claim_pid openalgo)"
    echo "vibe_api_pid=$(stack_claim_pid vibe-api)"
    echo "vibe_ui_pid=$(stack_claim_pid vibe-ui)"
    pid="$(stack_claim_pid nautilus-watch)"
    if [[ -z "$pid" ]]; then
      pid="$(stack_read_pid "$log_dir/nautilus-watch.pid")"
    fi
    echo "nautilus_watch_pid=$pid"
  } >"$file"
}

stack_sync_service_claim() {
  local service="$1" pidfile="$2" port="${3:-}" cmd="${4:-}"
  local pid
  pid="$(stack_read_pid "$pidfile")"
  if [[ -n "$pid" ]] && stack_pid_alive "$pid"; then
    stack_write_claim "$service" "$pid" "$port" "$cmd"
  fi
}

stack_reconcile_nautilus_watch_pid() {
  local py root
  root="$(stack_root)"
  py="$(stack_pick_python)"
  PYTHONPATH="$root/integrations" "$py" -c "
from trade_integrations.autonomous_agents.nautilus_watch import (
    get_watch_process_status,
    reconcile_stale_watch_pid,
)
reconcile_stale_watch_pid()
st = get_watch_process_status(reconcile=False)
if st.get('alive'):
    print(f\"[stack] Nautilus watch pid={st.get('pid')} (alive — left running)\")
elif st.get('enabled'):
    print('[stack] Nautilus watch not running (registry reconciled if stale)')
" 2>/dev/null || true
}

stack_sync_nautilus_claim() {
  local log_dir pidfile reg_file py reg_pid
  log_dir="$(stack_log_dir)"
  pidfile="$log_dir/nautilus-watch.pid"
  reg_file="$log_dir/nautilus-watch.agents.json"
  py="$(stack_pick_python)"
  reg_pid="$("$py" - "$reg_file" <<'PY' 2>/dev/null || true
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.is_file():
    raise SystemExit(0)
try:
    data = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(0)
pid = data.get("node_pid")
if isinstance(pid, int) and pid > 0:
    print(pid)
PY
)"
  if [[ -n "$reg_pid" ]] && stack_pid_alive "$reg_pid"; then
    stack_write_pidfile "$pidfile" "$reg_pid"
    stack_write_claim "nautilus-watch" "$reg_pid" "" "nautilus watch"
    return 0
  fi
  stack_sync_service_claim "nautilus-watch" "$pidfile" "" "nautilus watch"
}

stack_clear_instance_manifest() {
  rm -f "$(stack_log_dir)/stack.instance"
}

stack_service_up() {
  local port="$1"
  stack_http_ok "http://127.0.0.1:${port}/"
}

stack_wait_for_service_port() {
  local port="$1" attempts="${2:-20}"
  local i listener
  for ((i = 1; i <= attempts; i++)); do
    if stack_service_up "$port"; then
      return 0
    fi
    listener="$(stack_port_listener_pid "$port")"
    if [[ -n "$listener" ]] && stack_pid_alive "$listener"; then
      sleep 1
      continue
    fi
    return 1
  done
  stack_service_up "$port"
}

# Start a detached process; writes the child PID to pidfile and claims it (STACK_LAUNCH_SERVICE).
stack_launch_detached() {
  local pidfile="$1" logfile="$2" workdir="$3"
  shift 3
  local expect_port="${STACK_LAUNCH_EXPECT_PORT:-}"
  local service="${STACK_LAUNCH_SERVICE:-}"

  mkdir -p "$(dirname "$pidfile")" "$(dirname "$logfile")"

  if [[ -n "$service" && -n "$expect_port" ]]; then
    if stack_claim_valid "$service"; then
      local cpid
      cpid="$(stack_claim_pid "$service")"
      stack_write_pidfile "$pidfile" "$cpid"
      echo "[stack] $service already claimed (pid $cpid)"
      return 0
    fi
    if ! stack_assert_port_for_start "$service" "$expect_port"; then
      return 1
    fi
  fi

  local existing
  existing="$(stack_read_pid "$pidfile")"
  if stack_pid_alive "$existing"; then
    if [[ -z "$expect_port" ]] || stack_service_up "$expect_port"; then
      if [[ -n "$service" ]]; then
        stack_write_claim "$service" "$existing" "$expect_port" "$*"
      fi
      echo "[stack] already running (pid $existing)"
      return 0
    fi
    if [[ -n "$expect_port" ]]; then
      stack_sync_pidfile_from_port "$pidfile" "$expect_port"
      existing="$(stack_read_pid "$pidfile")"
      if [[ -n "$existing" ]] && stack_wait_for_service_port "$expect_port" 20; then
        if [[ -n "$service" ]]; then
          stack_write_claim "$service" "$existing" "$expect_port" "$*"
        fi
        echo "[stack] synced pid $existing from :${expect_port} — already listening"
        return 0
      fi
    fi
    echo "[stack] pid $existing alive but :${expect_port} still down after wait — replacing ..."
    kill "$existing" 2>/dev/null || true
    sleep 0.5
    stack_pid_alive "$existing" && kill -9 "$existing" 2>/dev/null || true
    [[ -n "$service" ]] && stack_release_claim "$service"
  fi

  : >>"$logfile"

  local prev="$PWD" pid owner_pid listener
  cd "$workdir" || return 1
  nohup "$@" >>"$logfile" 2>&1 < /dev/null &
  pid=$!
  disown "$pid" 2>/dev/null || true
  stack_write_pidfile "$pidfile" "$pid"
  cd "$prev" || true

  sleep 2
  existing="$(stack_read_pid "$pidfile")"
  owner_pid="$existing"
  if [[ -n "$expect_port" ]]; then
    listener="$(stack_port_listener_pid "$expect_port")"
    if [[ -n "$listener" ]]; then
      owner_pid="$listener"
      stack_write_pidfile "$pidfile" "$listener"
    fi
  fi
  if stack_pid_alive "$owner_pid" || { [[ -n "$expect_port" ]] && stack_service_up "$expect_port"; }; then
    if [[ -n "$service" ]]; then
      stack_write_claim "$service" "$owner_pid" "$expect_port" "$*"
    fi
    return 0
  fi

  echo "[stack] failed to start in $workdir: $*" >&2
  tail -8 "$logfile" 2>/dev/null >&2 || true
  [[ -n "$service" ]] && stack_release_claim "$service"
  return 1
}

stack_stop_pidfile() {
  local name="$1" service="$2" pidfile="$3" port="${4:-}"
  stack_stop_claimed "$name" "$service" "$pidfile" "$port"
}

stack_lock_dir() {
  echo "$(stack_log_dir)/.stack.lock.d"
}

stack_with_lock() {
  local lockdir waited=0
  lockdir="$(stack_lock_dir)"
  mkdir -p "$(stack_log_dir)"
  while ! mkdir "$lockdir" 2>/dev/null; do
    if (( waited >= 120 )); then
      echo "[stack] another stack operation is in progress (lock: $lockdir)" >&2
      echo "[stack] if no other trade command is running, remove: rm -rf $lockdir" >&2
      exit 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
  trap 'rmdir "'"$lockdir"'" 2>/dev/null || true' EXIT INT TERM
  "$@"
}

stack_preflight_start() {
  local root py frontend failures=0
  root="$(stack_root)"
  py="$(stack_pick_python)"
  frontend="${VIBE_FRONTEND_DIR:-$root/vibetrading/frontend}"

  echo "[stack] preflight ..."
  stack_reconcile_stale_claims

  if ! stack_validate_ports_registry; then
    failures=$((failures + 1))
  fi

  local api_port ui_port openalgo_port
  api_port="$(stack_vibe_api_port)"
  ui_port="$(stack_vibe_ui_port)"
  openalgo_port="$(stack_openalgo_port)"
  if stack_http_ok "http://127.0.0.1:${openalgo_port}/" \
    && stack_vibe_api_http_ok "$api_port" \
    && stack_http_ok "http://127.0.0.1:${ui_port}/"; then
    echo "[stack] preflight: stack ports already serving — skipping foreign-port check"
  elif ! STACK_PORTS_STRICT=1 stack_check_port_listeners; then
    failures=$((failures + 1))
  fi

  if [[ ! -x "$root/.venv/bin/vibe-trading" ]]; then
    echo "[stack] preflight: vibe-trading missing — pip install -e vibetrading/" >&2
    failures=$((failures + 1))
  fi

  if [[ ! -f "$frontend/package.json" ]]; then
    echo "[stack] preflight: Vibe frontend missing at $frontend" >&2
    failures=$((failures + 1))
  elif [[ ! -x "$frontend/node_modules/.bin/vite" ]]; then
    echo "[stack] preflight: Vite not installed — run: ./scripts/ensure_vibe_frontend.sh" >&2
    failures=$((failures + 1))
  fi

  if [[ -x "$root/scripts/setup_vibe.py" ]]; then
    if ! "$py" "$root/scripts/setup_vibe.py" --verify 2>/dev/null; then
      echo "[stack] preflight: setup_vibe.py --verify failed — run: trade setup vibe" >&2
      failures=$((failures + 1))
    fi
  fi

  if [[ ! -x "$root/openalgo/.venv/bin/python" ]]; then
    echo "[stack] preflight: OpenAlgo venv missing — run setup in openalgo/ (uv sync or python -m venv .venv)" >&2
    failures=$((failures + 1))
  fi

  if (( failures > 0 )); then
    echo "[stack] preflight failed ($failures issue(s)) — run: trade doctor" >&2
    return 1
  fi

  echo "[stack] preflight ok"
  return 0
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

  if stack_adopt_running_service "openalgo" "$port" "$pidfile" "$base/"; then
    if [[ "${STACK_DEV_FLASK_DEBUG:-0}" == "1" || "${STACK_DEV_FLASK_DEBUG:-}" == "true" ]]; then
      echo "[stack] OpenAlgo running without dev reload — restarting with FLASK_DEBUG ..."
      stack_stop_claimed "OpenAlgo" "openalgo" "$pidfile" "$port"
      stack_wait_port_free "$port" 15 || true
    else
      echo "[stack] OpenAlgo already up at $base (pid $(stack_claim_pid openalgo))"
      return 0
    fi
  fi

  if ! stack_assert_port_for_start "openalgo" "$port"; then
    return 1
  fi

  echo "[stack] starting OpenAlgo on :$port ..."
  STACK_LAUNCH_SERVICE=openalgo
  STACK_LAUNCH_EXPECT_PORT="$port"
  local -a launch_cmd=()
  if [[ -x "$root/openalgo/.venv/bin/python" ]]; then
    launch_cmd=("$root/openalgo/.venv/bin/python" app.py)
  else
    runner="$(stack_pick_openalgo_cmd)"
    launch_cmd=(bash -lc "exec $runner")
  fi
  if [[ "${STACK_DEV_FLASK_DEBUG:-0}" == "1" || "${STACK_DEV_FLASK_DEBUG:-}" == "true" ]]; then
    echo "[stack] OpenAlgo FLASK_DEBUG=1 (code auto-reload)"
    stack_launch_detached "$pidfile" "$logfile" "$root/openalgo" env FLASK_DEBUG=1 "${launch_cmd[@]}"
  else
    stack_launch_detached "$pidfile" "$logfile" "$root/openalgo" "${launch_cmd[@]}"
  fi
  unset STACK_LAUNCH_EXPECT_PORT STACK_LAUNCH_SERVICE
  stack_wait_for_url "OpenAlgo" "$base/" 90
  stack_sync_pidfile_from_port "$pidfile" "$port"
  stack_write_claim "openalgo" "$(stack_read_pid "$pidfile")" "$port" "openalgo app.py"
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
    if stack_adopt_running_service "vibe-api" "$port" "$pidfile" "$base/"; then
      echo "[stack] Vibe API already up at $base (pid $(stack_claim_pid vibe-api))"
      return 0
    fi
    stack_sync_service_claim "vibe-api" "$pidfile" "$port" "cli._legacy serve"
    echo "[stack] Vibe API healthy at $base — leaving running (synced claim)"
    return 0
  fi

  # Root responds but index probe failed — often a busy worker (SSE analysis), not a dead API.
  if stack_http_ok "$base/" && stack_vibe_api_listener_alive "$port"; then
    if stack_adopt_running_service "vibe-api" "$port" "$pidfile" "$base/"; then
      echo "[stack] Vibe API on :$port is busy but listening — leaving it running"
      return 0
    fi
    stack_sync_service_claim "vibe-api" "$pidfile" "$port" "cli._legacy serve"
    echo "[stack] Vibe API on :$port is busy — leaving running (synced claim)"
    return 0
  fi

  if stack_http_ok "$base/"; then
    echo "[stack] Vibe API on :$port responds but is not ready — use: trade restart --force" >&2
    return 1
  fi

  if ! stack_assert_port_for_start "vibe-api" "$port"; then
    return 1
  fi

  if [[ ! -x "$root/.venv/bin/vibe-trading" ]]; then
    echo "[stack] vibe-trading not installed — run: pip install -e vibetrading/" >&2
    return 1
  fi

  echo "[stack] starting Vibe API on :$port ..."
  local -a serve_args=(serve --port "$port")
  if [[ "${STACK_DEV_RELOAD:-0}" == "1" || "${STACK_DEV_RELOAD:-}" == "true" ]]; then
    serve_args+=(--reload)
    echo "[stack] Vibe API auto-reload enabled (integrations + agent code)"
  fi
  STACK_LAUNCH_SERVICE=vibe-api
  STACK_LAUNCH_EXPECT_PORT="$port"
  stack_launch_detached \
    "$pidfile" "$logfile" "$agent_dir" \
    "$py" -m cli._legacy "${serve_args[@]}"
  unset STACK_LAUNCH_EXPECT_PORT STACK_LAUNCH_SERVICE
  stack_wait_for_url "Vibe API" "$base/" 60
  stack_sync_pidfile_from_port "$pidfile" "$port"
  stack_write_claim "vibe-api" "$(stack_read_pid "$pidfile")" "$port" "cli._legacy serve"
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

  if stack_adopt_running_service "vibe-ui" "$port" "$pidfile" "$url/"; then
    echo "[stack] Vibe UI already up at $url (pid $(stack_claim_pid vibe-ui))"
    return 0
  fi

  if ! stack_assert_port_for_start "vibe-ui" "$port"; then
    return 1
  fi

  if [[ ! -f "$frontend/package.json" ]]; then
    echo "[stack] Vibe frontend missing at $frontend" >&2
    return 1
  fi
  if [[ ! -x "$frontend/node_modules/.bin/vite" ]]; then
    echo "[stack] Vite not installed — run: ./scripts/ensure_vibe_frontend.sh" >&2
    return 1
  fi

  echo "[stack] starting Vibe UI (Vite) on :$port ..."
  STACK_LAUNCH_SERVICE=vibe-ui
  STACK_LAUNCH_EXPECT_PORT="$port"
  stack_launch_detached \
    "$pidfile" "$logfile" "$frontend" \
    "$frontend/node_modules/.bin/vite" --port "$port" --host 127.0.0.1
  unset STACK_LAUNCH_EXPECT_PORT STACK_LAUNCH_SERVICE
  stack_wait_for_url "Vibe UI" "$url/" 60
  stack_sync_pidfile_from_port "$pidfile" "$port"
  stack_write_claim "vibe-ui" "$(stack_read_pid "$pidfile")" "$port" "vite"
}

stack_kill_unclaimed_port() {
  local port="$1"
  local pid other pids=() skipped=()
  for pid in $(lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true); do
    other="$(stack_service_for_pid "$pid" 2>/dev/null || true)"
    if [[ -n "$other" ]]; then
      skipped+=("$pid:$other")
      continue
    fi
    pids+=("$pid")
  done
  if ((${#skipped[@]} > 0)); then
    echo "[stack] skip :$port — claimed listener(s): ${skipped[*]}"
  fi
  if ((${#pids[@]} == 0)); then
    return 0
  fi
  echo "[stack] stopping unclaimed listener(s) on :$port (pids: ${pids[*]}) ..."
  local waited=0
  # shellcheck disable=SC2068
  kill ${pids[@]} 2>/dev/null || true
  while (( waited < 15 )); do
    pids=()
    for pid in $(lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true); do
      other="$(stack_service_for_pid "$pid" 2>/dev/null || true)"
      [[ -z "$other" ]] && pids+=("$pid")
    done
    ((${#pids[@]} == 0)) && return 0
    sleep 1
    waited=$((waited + 1))
  done
  pids=()
  for pid in $(lsof -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true); do
    other="$(stack_service_for_pid "$pid" 2>/dev/null || true)"
    [[ -z "$other" ]] && pids+=("$pid")
  done
  if ((${#pids[@]} > 0)); then
    echo "[stack] force-killing unclaimed listener(s) on :$port (pids: ${pids[*]}) ..."
    # shellcheck disable=SC2068
    kill -9 ${pids[@]} 2>/dev/null || true
  fi
}

stack_kill_port() {
  stack_kill_unclaimed_port "$1"
}

stack_kill_openalgo_ws_proxy() {
  stack_kill_unclaimed_port 8765
}

stack_heal_daemon_enabled() {
  local v="${STACK_HEAL_DAEMON:-1}"
  v="$(_stack_lc "$v")"
  [[ "$v" != "0" && "$v" != "false" && "$v" != "no" && "$v" != "off" ]]
}

stack_stop_heal_daemon() {
  local log_dir pidfile pid
  log_dir="$(stack_log_dir)"
  pidfile="$log_dir/stack-heal.pid"
  pid="$(stack_read_pid "$pidfile")"
  if [[ -n "$pid" ]] && stack_pid_alive "$pid"; then
    echo "[stack] stopping stack heal daemon (pid $pid) ..."
    kill "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
}

stack_start_heal_daemon() {
  if ! stack_heal_daemon_enabled; then
    return 0
  fi
  if stack_dev_mode_flagged; then
    return 0
  fi
  local root log_dir pidfile logfile pid interval
  root="$(stack_root)"
  log_dir="$(stack_log_dir)"
  pidfile="$log_dir/stack-heal.pid"
  logfile="$log_dir/stack-heal.log"
  interval="${STACK_HEAL_INTERVAL_SEC:-60}"
  pid="$(stack_read_pid "$pidfile")"
  if [[ -n "$pid" ]] && stack_pid_alive "$pid"; then
    return 0
  fi
  echo "[stack] starting stack heal daemon (every ${interval}s) ..."
  : >>"$logfile"
  nohup bash -c "
    while true; do
      sleep $interval
      if [[ -f '$root/log/stack.mode' ]] && [[ \"\$(tr -d '[:space:]' <'$root/log/stack.mode')\" == 'dev' ]]; then
        continue
      fi
      '$root/trade' heal >>'$logfile' 2>&1 || true
    done
  " >>"$logfile" 2>&1 < /dev/null &
  pid=$!
  disown "$pid" 2>/dev/null || true
  stack_write_pidfile "$pidfile" "$pid"
}

stack_stop_vibe_stack() {
  local log_dir api_port ui_port openalgo_port stop_docker stop_all=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --all|-a) stop_all=1 ;;
    esac
    shift
  done
  log_dir="$(stack_log_dir)"
  stack_reconcile_stale_claims
  stop_docker="${STACK_STOP_DOCKER:-0}"
  stop_docker="$(_stack_lc "$stop_docker")"
  api_port="$(stack_vibe_api_port)"
  ui_port="$(stack_vibe_ui_port)"
  openalgo_port="$(stack_openalgo_port)"

  stack_stop_claimed "Vibe UI" "vibe-ui" "$log_dir/vibe-ui.pid" "$ui_port"
  stack_stop_claimed "Vibe API" "vibe-api" "$log_dir/vibe-api.pid" "$api_port"
  stack_stop_claimed "vibe-trading (legacy)" "vibe-trading" "$log_dir/vibe-trading.pid"
  stack_stop_nautilus_watch
  stack_stop_claimed "OpenAlgo" "openalgo" "$log_dir/openalgo.pid" "$openalgo_port"
  stack_kill_openalgo_ws_proxy
  stack_stop_heal_daemon
  stack_clear_stack_mode
  stack_clear_instance_manifest

  # shellcheck disable=SC1091
  source "$(stack_root)/scripts/stack_docker_lib.sh"
  if (( stop_all )); then
    stack_docker_stop_all
    local exposure_stop
    exposure_stop="$(stack_root)/exposure/start.sh"
    if [[ -x "$exposure_stop" ]]; then
      echo "[stack] stopping exposure tunnels ..."
      "$exposure_stop" stop 2>/dev/null || true
    fi
  elif [[ "$stop_docker" == "1" || "$stop_docker" == "true" || "$stop_docker" == "yes" || "$stop_docker" == "on" ]]; then
    stack_hub_docker_stop_graceful
  fi

  sleep 1
}

_stack_lc() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

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
    if profile.uses_nautilus_watch:
        aid = str(agent.get("id") or "").strip()
        if aid:
            print(aid)
            raise SystemExit(0)
PY
}

stack_ensure_redis() {
  stack_ensure_redis_docker
}

stack_ensure_vibe_config() {
  local root py
  root="$(stack_root)"
  py="$(stack_pick_python)"
  if [[ -x "$root/scripts/setup_vibe.py" ]]; then
    echo "[stack] syncing Vibe operator config ..."
    "$py" "$root/scripts/setup_vibe.py" 2>/dev/null || true
  fi
}

stack_ensure_vibe_stack() {
  local ok=0
  stack_validate_ports_registry || ok=1
  stack_check_port_listeners || true
  stack_ensure_hub_docker || ok=1
  stack_ensure_hub_storage || true
  stack_ensure_vibe_config || true
  stack_start_openalgo || ok=1
  stack_start_vibe_api || ok=1
  stack_start_vibe_ui || ok=1
  stack_ensure_nautilus_watch || true
  stack_sync_nautilus_claim
  return "$ok"
}

stack_ensure_nautilus_watch() {
  local agent_id="${1:-$(stack_primary_nautilus_agent_id)}"
  if [[ -z "$agent_id" ]]; then
    return 0
  fi
  stack_ensure_redis || true
  stack_start_nautilus_watch "$agent_id" || {
    echo "[stack] Nautilus watch not started — bootstrap poll ticks still run via Vibe scheduler" >&2
    return 0
  }
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
  echo "Claims: $(stack_claims_dir)/"
  echo "Stop: trade down"
  echo "Heal: trade restart"
  echo "Status: trade status"
}

stack_status_vibe_stack() {
  local log_dir openalgo_port api_port ui_port ok=1
  stack_load_env
  log_dir="$(stack_log_dir)"
  stack_reconcile_stale_dev_mode
  stack_reconcile_stale_claims
  openalgo_port="$(stack_openalgo_port)"
  api_port="$(stack_vibe_api_port)"
  ui_port="$(stack_vibe_ui_port)"

  echo "══════════════════════════════════════════════════════════"
  echo "  Vibe stack status"
  echo "══════════════════════════════════════════════════════════"

  for svc in "OpenAlgo:openalgo:$openalgo_port:$log_dir/openalgo.pid" \
             "Vibe API:vibe-api:$api_port:$log_dir/vibe-api.pid" \
             "Vibe UI:vibe-ui:$ui_port:$log_dir/vibe-ui.pid"; do
    local name service port pidfile pid http_code alive="dead" claimed=""
    name="${svc%%:*}"
    service="${svc#*:}"; service="${service%%:*}"
    port="${svc#*:}"; port="${port#*:}"; port="${port%%:*}"
    pidfile="${svc##*:}"
    pid="$(stack_read_pid "$pidfile")"
    claimed="$(stack_claim_pid "$service")"
    local probe_url="http://127.0.0.1:${port}/"
    if [[ "$service" == "vibe-api" ]]; then
      probe_url="http://127.0.0.1:${port}/health"
    fi
    http_code="$(curl -sf -o /dev/null -w "%{http_code}" -m 5 "$probe_url" 2>/dev/null || true)"
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
      if [[ -n "$claimed" && -n "$pid" && "$claimed" != "$pid" ]]; then
        echo "  ✓ $name  :$port  HTTP $http_code  pid=${pid} claim=${claimed} ($alive)"
      else
        echo "  ✓ $name  :$port  HTTP $http_code  pid=${pid:-${claimed:-?}} ($alive)"
      fi
    elif [[ -n "$listener" ]] && stack_pid_alive "$listener"; then
      echo "  ⚠ $name  :$port  HTTP $http_code  pid=${listener} (alive, not ready)"
      ok=0
    else
      echo "  ✗ $name  :$port  HTTP $http_code  pid=${pid:-none} ($alive)"
      ok=0
    fi
  done

  local redis_url="${NAUTILUS_REDIS_URL:-redis://127.0.0.1:6379/0}"
  if [[ "${NAUTILUS_WATCH_ENABLE:-1}" != "0" && "${NAUTILUS_WATCH_ENABLE:-}" != "false" ]]; then
    if command -v redis-cli >/dev/null 2>&1 && redis-cli -u "$redis_url" ping 2>/dev/null | grep -q PONG; then
      echo "  ✓ Redis       $redis_url  (PONG)"
    else
      echo "  ✗ Redis       $redis_url  (Nautilus watch needs Redis)"
      ok=0
    fi
  fi

  local nautilus_pid registry_file registry_summary=""
  nautilus_pid="$(stack_read_pid "$log_dir/nautilus-watch.pid")"
  registry_file="$log_dir/nautilus-watch.agents.json"
  if [[ -f "$registry_file" ]]; then
    local py
    py="$(stack_pick_python)"
    registry_summary="$("$py" - "$registry_file" <<'PY' 2>/dev/null || true
import json, sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text())
agents = [str(r.get("agent_id") or "") for r in (data.get("agents") or []) if r.get("agent_id")]
print(", ".join(agents) if agents else "")
PY
)"
  fi
  if stack_pid_alive "$nautilus_pid"; then
    local nautilus_claim
    nautilus_claim="$(stack_claim_pid nautilus-watch)"
    if [[ -n "$registry_summary" ]]; then
      echo "  ✓ Nautilus watch  pid=${nautilus_pid} claim=${nautilus_claim:-?} (alive, agents: ${registry_summary})"
    else
      echo "  ✓ Nautilus watch  pid=${nautilus_pid} claim=${nautilus_claim:-?} (alive)"
    fi
  elif [[ "${NAUTILUS_WATCH_ENABLE:-1}" == "0" || "${NAUTILUS_WATCH_ENABLE:-}" == "false" ]]; then
    echo "  · Nautilus watch  disabled (NAUTILUS_WATCH_ENABLE=0)"
  else
    echo "  ✗ Nautilus watch  pid=${nautilus_pid:-none} (expected — enabled by default)"
    ok=0
  fi

  echo "══════════════════════════════════════════════════════════"
  local hub_ok=0
  if stack_status_hub_docker; then
    hub_ok=1
  fi
  if (( ok && hub_ok )); then return 0; fi
  if stack_dev_mode_flagged && stack_dev_tier_alive; then
    echo "  Dev mode: keep the ./trade dev terminal open for hot reload"
  elif stack_dev_mode_flagged; then
    echo "  Dev mode flag was stale — run: ./trade dev"
  else
    echo "  Fix: ./trade dev   (hot reload while coding)"
    echo "  Or:  ./trade restart   (background daemon)"
  fi
  echo "  Full reset: ./trade restart --force"
  echo "  Full stop: ./trade down"
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
  local root log_dir pidfile logfile agent_id_file agent_id launch_script
  root="$(stack_root)"
  log_dir="$(stack_log_dir)"
  pidfile="$log_dir/nautilus-watch.pid"
  logfile="$log_dir/nautilus-watch.log"
  agent_id_file="$log_dir/nautilus-watch.agent_id"
  agent_id="${NAUTILUS_AGENT_ID:-}"
  launch_script="$root/scripts/run_nautilus_watch.sh"

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

  if [[ ! -x "$launch_script" ]]; then
    echo "[stack] missing $launch_script" >&2
    return 1
  fi

  existing="$(stack_read_pid "$pidfile")"
  bound_agent=""
  if [[ -f "$agent_id_file" ]]; then
    bound_agent="$(tr -d '[:space:]' <"$agent_id_file")"
  fi
  if stack_pid_alive "$existing"; then
    if [[ -f "$log_dir/nautilus-watch.agents.json" ]] && [[ -n "$(stack_read_pid "$pidfile")" ]]; then
      stack_write_claim "nautilus-watch" "$existing" "" "nautilus watch --registry"
      echo "[stack] Nautilus watch already running (pid $existing, registry mode)"
      return 0
    fi
    if [[ -n "$agent_id" && -n "$bound_agent" && "$bound_agent" != "$agent_id" ]]; then
      echo "[stack] Nautilus watch bound to $bound_agent — restarting for $agent_id ..."
      stack_stop_nautilus_watch
    elif [[ -n "$agent_id" && -z "$bound_agent" ]]; then
      echo "$agent_id" >"$agent_id_file"
      stack_write_claim "nautilus-watch" "$existing" "" "nautilus watch"
      echo "[stack] Nautilus watch already running (pid $existing) — bound to $agent_id"
      return 0
    else
      stack_write_claim "nautilus-watch" "$existing" "" "nautilus watch"
      echo "[stack] Nautilus watch already running (pid $existing${bound_agent:+, agent $bound_agent})"
      return 0
    fi
  elif [[ -n "$existing" ]]; then
    echo "[stack] clearing stale Nautilus watch pid $existing"
    rm -f "$pidfile" "$agent_id_file"
  fi

  echo "[stack] starting Nautilus watch node ..."
  local cmd=("$launch_script")
  if [[ -n "$agent_id" ]]; then
    "$(stack_pick_python)" - "$root" "$agent_id" <<'PY' 2>/dev/null || true
import sys
from pathlib import Path
root = Path(sys.argv[1])
agent_id = sys.argv[2]
sys.path.insert(0, str(root / "integrations"))
try:
    from trade_integrations.autonomous_agents.nautilus_watch import add_agent_to_registry
    add_agent_to_registry(agent_id)
except Exception:
    pass
PY
  fi
  if [[ -f "$log_dir/nautilus-watch.agents.json" ]]; then
    cmd+=(--registry)
  elif [[ -n "$agent_id" ]]; then
    cmd+=(--agent-id "$agent_id")
    echo "$agent_id" >"$agent_id_file"
  fi
  STACK_LAUNCH_SERVICE=nautilus-watch
  stack_launch_detached "$pidfile" "$logfile" "$root" "${cmd[@]}"
  unset STACK_LAUNCH_SERVICE

  sleep 2
  existing="$(stack_read_pid "$pidfile")"
  if ! stack_pid_alive "$existing"; then
    echo "[stack] Nautilus watch failed to stay up — see $logfile" >&2
    tail -8 "$logfile" 2>/dev/null >&2 || true
    rm -f "$pidfile" "$agent_id_file"
    stack_release_claim "nautilus-watch"
    return 1
  fi
  stack_write_claim "nautilus-watch" "$existing" "" "${cmd[*]}"
}

stack_stop_nautilus_watch() {
  stack_stop_claimed "Nautilus watch" "nautilus-watch" "$(stack_log_dir)/nautilus-watch.pid"
  rm -f "$(stack_log_dir)/nautilus-watch.agent_id"
}
