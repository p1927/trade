#!/usr/bin/env bash
# Ephemeral Cloudflare quick tunnel (trycloudflare.com) for local webhook testing.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../lib/common.sh
source "$ROOT/lib/common.sh"

SYNC_HOST=1
LOG_FILE="$TRADE_ROOT/openalgo/log/cloudflare-quick-tunnel.log"

usage() {
  cat <<'EOF'
Usage: ./exposure/cloudflare/quick-tunnel.sh [options]

Starts a Cloudflare quick tunnel to the local OpenAlgo port. The public URL
changes each run — update platform webhooks after restart, or use a named tunnel.

Options:
  --no-sync       Do not update openalgo/.env HOST_SERVER
  --port PORT     Override local OpenAlgo port (default: from openalgo/.env)
  -h, --help      Show this help

Requires OpenAlgo to be running locally (./start.sh --openalgo-only).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-sync) SYNC_HOST=0 ;;
    --port)
      shift
      OPENALGO_PORT="${1:?--port requires a value}"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      exposure_fail "Unknown option: $1"
      usage >&2
      exit 1
      ;;
  esac
  shift
done

require_cloudflared

local_url="$(openalgo_local_url)"
if ! probe_openalgo; then
  exposure_fail "OpenAlgo is not reachable at $local_url"
  echo "  Start it first: ./start.sh --openalgo-only" >&2
  exit 1
fi

mkdir -p "$(dirname "$LOG_FILE")"
stop_recorded_pids
: >"$LOG_FILE"

exposure_log "Starting quick tunnel -> $local_url"
# Detach from parent shell (macOS has no setsid; trap HUP keeps tunnel alive after script exits)
(
  trap '' HUP
  export TUNNEL_TRANSPORT_PROTOCOL="${TUNNEL_TRANSPORT_PROTOCOL:-http2}"
  exec cloudflared tunnel --url "$local_url" >>"$LOG_FILE" 2>&1
) </dev/null >/dev/null 2>&1 &
tunnel_pid=$!
record_pid "cloudflare-quick" "$tunnel_pid"

public_url="$(wait_for_tunnel_url "$LOG_FILE" 90 || true)"
if [[ -z "$public_url" ]]; then
  exposure_fail "Timed out waiting for trycloudflare.com URL"
  echo "  See log: $LOG_FILE" >&2
  kill "$tunnel_pid" 2>/dev/null || true
  exit 1
fi

write_exposure_state "quick" "$public_url" "$tunnel_pid"
exposure_ok "Tunnel active: $public_url"

if (( SYNC_HOST )); then
  update_host_server "$public_url"
fi

echo ""
echo "Platform setup pages in OpenAlgo:"
echo "  TradingView  -> /tradingview"
echo "  GoCharting   -> /gocharting"
echo "  Chartink     -> /chartink"
echo ""
echo "Stop tunnel: ./exposure/cloudflare/stop.sh"
