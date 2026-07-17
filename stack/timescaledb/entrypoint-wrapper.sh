#!/bin/bash
# Remove stale/corrupt postmaster.pid before PostgreSQL starts.
# Unclean Docker stops (kill -9, Desktop crash) can leave an empty or invalid lock file.
# Apply Timescale-friendly memory/checkpoint tuning via -c overrides (see postgresql.custom.conf).
set -euo pipefail

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
TUNING_CONF="${TIMESCALE_TUNING_CONF:-/stack/timescaledb/postgresql.custom.conf}"

_clean_stale_postmaster_pid() {
  local pidfile="$PGDATA/postmaster.pid"
  [[ -f "$pidfile" ]] || return 0

  local pid
  pid="$(head -1 "$pidfile" 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -z "$pid" || ! "$pid" =~ ^[0-9]+$ ]]; then
    echo "[timescale-entrypoint] removing corrupt postmaster.pid"
    rm -f "$pidfile"
    return 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "[timescale-entrypoint] removing stale postmaster.pid (pid $pid not running)"
    rm -f "$pidfile"
  fi
}

_read_tuning_flag() {
  local key="$1"
  local default="$2"
  local line value
  if [[ -f "$TUNING_CONF" ]]; then
    line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$TUNING_CONF" | tail -1 || true)"
    if [[ -n "$line" ]]; then
      value="${line#*=}"
      value="${value%;*}"
      value="$(echo "$value" | tr -d '[:space:]')"
      if [[ -n "$value" ]]; then
        echo "$value"
        return
      fi
    fi
  fi
  echo "$default"
}

_pg_tuning_args() {
  local shared_buffers max_wal_size
  shared_buffers="${TIMESCALE_SHARED_BUFFERS:-$(_read_tuning_flag shared_buffers 256MB)}"
  max_wal_size="${TIMESCALE_MAX_WAL_SIZE:-$(_read_tuning_flag max_wal_size 2GB)}"

  cat <<EOF
-c
shared_buffers=${shared_buffers}
-c
max_wal_size=${max_wal_size}
-c
min_wal_size=$(_read_tuning_flag min_wal_size 512MB)
-c
checkpoint_completion_target=$(_read_tuning_flag checkpoint_completion_target 0.9)
-c
wal_buffers=$(_read_tuning_flag wal_buffers 16MB)
-c
effective_cache_size=$(_read_tuning_flag effective_cache_size 1GB)
-c
maintenance_work_mem=$(_read_tuning_flag maintenance_work_mem 128MB)
-c
work_mem=$(_read_tuning_flag work_mem 16MB)
-c
log_checkpoints=$(_read_tuning_flag log_checkpoints on)
EOF
}

_clean_stale_postmaster_pid

if [[ $# -eq 0 ]]; then
  set -- postgres
fi

if [[ "${1:-}" == "postgres" ]]; then
  shift
  mapfile -t _tuning < <(_pg_tuning_args)
  exec /usr/local/bin/docker-entrypoint.sh postgres "${_tuning[@]}" "$@"
fi

exec /usr/local/bin/docker-entrypoint.sh "$@"
