#!/usr/bin/env bash
# Stop active Cloudflare tunnel processes started by exposure scripts.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../lib/common.sh
source "$ROOT/lib/common.sh"

stop_recorded_pids

if pgrep -x cloudflared >/dev/null 2>&1; then
  exposure_warn "Other cloudflared processes may still be running"
  echo "  To stop all: pkill cloudflared" >&2
else
  exposure_ok "No cloudflared tunnel processes running"
fi

clear_exposure_state
