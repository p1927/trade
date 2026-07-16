#!/usr/bin/env bash
# Start OpenAlgo + Vibe API + Vibe UI as detached background processes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/stack_lib.sh"

STACK_ROOT="$ROOT"
mkdir -p "$(stack_log_dir)"
stack_load_env

stack_start_vibe_stack
stack_print_ready
