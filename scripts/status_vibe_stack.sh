#!/usr/bin/env bash
# Check whether OpenAlgo + Vibe API + Vibe UI are running.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/stack_lib.sh"

STACK_ROOT="$ROOT"
stack_status_vibe_stack
