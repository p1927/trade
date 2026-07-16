#!/usr/bin/env bash
# Restart OpenAlgo + Vibe Trading (API + Vite frontend) for local trade stack.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/stack_lib.sh"

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force|-f) FORCE=1 ;;
  esac
done

STACK_ROOT="$ROOT"
mkdir -p "$(stack_log_dir)"
stack_load_env

if (( FORCE )); then
  echo "[stack] stopping existing services (--force) ..."
  stack_stop_vibe_stack
else
  echo "[stack] healing stack (only starts services that are down) ..."
fi

if ! stack_ensure_vibe_stack; then
  echo "[stack] one or more services failed to start — see log/" >&2
  exit 1
fi

stack_print_ready
stack_status_vibe_stack
