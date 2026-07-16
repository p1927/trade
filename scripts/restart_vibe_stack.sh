#!/usr/bin/env bash
# Restart OpenAlgo + Vibe Trading (API + Vite frontend) for local trade stack.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/stack_lib.sh"

STACK_ROOT="$ROOT"
mkdir -p "$(stack_log_dir)"
stack_load_env

echo "[stack] stopping existing services ..."
stack_stop_vibe_stack

echo "[stack] starting services ..."
stack_start_vibe_stack
stack_print_ready
