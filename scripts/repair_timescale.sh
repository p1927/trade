#!/usr/bin/env bash
# Repair TimescaleDB when postmaster.pid is stale or corrupt after an unclean shutdown.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_ROOT="$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/stack_docker_lib.sh"
# shellcheck disable=SC1091
source "$ROOT/scripts/stack_lib.sh"

stack_load_env

if ! stack_timescale_enabled; then
  echo "[stack] TIMESCALE_ENABLED is off — nothing to repair"
  exit 0
fi

if ! stack_docker_available; then
  echo "[stack] Docker is not running" >&2
  exit 1
fi

stack_timescale_repair_stale_pid
stack_timescale_start_container

echo "[stack] waiting for TimescaleDB ..."
py="$(stack_pick_python)"
probe_py='(cd "'"$ROOT"'" && "'"$py"'" - <<'"'"'PY'"'"' >/dev/null 2>&1
import sys
sys.path.insert(0, "integrations")
from trade_integrations.env import load_trade_env
from trade_integrations.hub_storage.timescale_ticks import timescale_health
load_trade_env()
raise SystemExit(0 if timescale_health().get("ok") else 1)
PY
)'

if stack_timescale_wait_ready "$probe_py" 60 2; then
  echo "[stack] TimescaleDB is healthy at $(stack_timescale_url)"
  exit 0
fi

if stack_timescale_logs_indicate_recovery; then
  echo "[stack] WAL recovery still running — extending wait (do not interrupt container) ..."
  if stack_timescale_wait_ready "$probe_py" 180 2; then
    echo "[stack] TimescaleDB is healthy after WAL recovery at $(stack_timescale_url)"
    exit 0
  fi
fi

echo "[stack] TimescaleDB still not healthy — check logs:" >&2
echo "  docker compose -f docker-compose.stack.yml logs timescaledb" >&2
exit 1
