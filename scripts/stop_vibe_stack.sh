#!/usr/bin/env bash
# Stop OpenAlgo + Vibe API + Vibe UI background processes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/stack_lib.sh"

STACK_ROOT="$ROOT"
stack_stop_vibe_stack "$@"
if [[ "${1:-}" == "--all" || "${1:-}" == "-a" ]]; then
  echo "[stack] stopped app tier + all hub Docker + tunnels"
else
  echo "[stack] stopped OpenAlgo + Vibe services (hub Docker: graceful stop unless STACK_STOP_DOCKER=0)"
  echo "[stack] SearXNG left running — stop with: trade stop-docker searxng"
  echo "[stack] full teardown: trade stop --all"
fi
