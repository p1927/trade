#!/usr/bin/env bash
# Check hub Docker tier + manifest readiness (heals hub tier first).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/stack_lib.sh"

STACK_ROOT="$ROOT"
stack_load_env
stack_reconcile_all
stack_ensure_dependencies hub || true
stack_status_hub_docker
