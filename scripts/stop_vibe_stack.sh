#!/usr/bin/env bash
# Stop OpenAlgo + Vibe API + Vibe UI background processes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/stack_lib.sh"

STACK_ROOT="$ROOT"
stack_stop_vibe_stack
echo "[stack] stopped OpenAlgo + Vibe services (hub Docker: graceful stop unless STACK_STOP_DOCKER=0)"
