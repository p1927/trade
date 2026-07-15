#!/usr/bin/env bash
# Run a persistent Cloudflare named tunnel (stable hostname on your domain).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../../lib/common.sh
source "$ROOT/lib/common.sh"

SYNC_HOST=1
TUNNEL_NAME="${CLOUDFLARE_TUNNEL_NAME:-trade-openalgo}"
PUBLIC_HOST="${EXPOSURE_HOSTNAME:-}"
CONFIG_FILE="${CLOUDFLARE_CONFIG:-$HOME/.cloudflared/config.yml}"

usage() {
  cat <<'EOF'
Usage: ./exposure/cloudflare/named/start.sh [options]

Runs a named Cloudflare tunnel using ~/.cloudflared/config.yml by default.
Use this for stable webhook URLs on TradingView, GoCharting, and Chartink.

Options:
  --hostname HOST   Public hostname (also sets HOST_SERVER when --sync)
  --tunnel NAME     Tunnel name (default: trade-openalgo or CLOUDFLARE_TUNNEL_NAME)
  --config FILE     cloudflared config path (default: ~/.cloudflared/config.yml)
  --no-sync         Do not update openalgo/.env HOST_SERVER
  -h, --help        Show this help

One-time setup:
  cloudflared tunnel login
  cloudflared tunnel create trade-openalgo
  cloudflared tunnel route dns trade-openalgo openalgo.yourdomain.com
  cp exposure/cloudflare/named/config.yml.example ~/.cloudflared/config.yml
  # edit tunnel UUID, credentials path, hostname, and local port
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hostname)
      shift
      PUBLIC_HOST="${1:?--hostname requires a value}"
      ;;
    --tunnel)
      shift
      TUNNEL_NAME="${1:?--tunnel requires a value}"
      ;;
    --config)
      shift
      CONFIG_FILE="${1:?--config requires a value}"
      ;;
    --no-sync) SYNC_HOST=0 ;;
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

if [[ ! -f "$CONFIG_FILE" ]]; then
  exposure_fail "Missing config: $CONFIG_FILE"
  echo "  Copy and edit: exposure/cloudflare/named/config.yml.example" >&2
  exit 1
fi

local_url="$(openalgo_local_url)"
if ! probe_openalgo; then
  exposure_fail "OpenAlgo is not reachable at $local_url"
  echo "  Start it first: ./start.sh --openalgo-only" >&2
  exit 1
fi

if [[ -z "$PUBLIC_HOST" ]]; then
  PUBLIC_HOST="$(grep -E '^[[:space:]]*-?[[:space:]]*hostname:' "$CONFIG_FILE" 2>/dev/null | head -1 | sed -E 's/.*hostname:[[:space:]]*//')"
fi

stop_recorded_pids

exposure_log "Starting named tunnel '$TUNNEL_NAME' (config: $CONFIG_FILE)"
nohup cloudflared tunnel --config "$CONFIG_FILE" run "$TUNNEL_NAME" >>"$TRADE_ROOT/openalgo/log/cloudflare-named-tunnel.log" 2>&1 &
tunnel_pid=$!
disown "$tunnel_pid" 2>/dev/null || true
record_pid "cloudflare-named" "$tunnel_pid"

if [[ -n "$PUBLIC_HOST" ]]; then
  public_url="https://${PUBLIC_HOST}"
  write_exposure_state "named" "$public_url" "$tunnel_pid"
  exposure_ok "Tunnel running for https://$PUBLIC_HOST"
  if (( SYNC_HOST )); then
    update_host_server "$public_url"
  fi
else
  write_exposure_state "named" "" "$tunnel_pid"
  exposure_warn "Could not detect hostname — set EXPOSURE_HOSTNAME or use --hostname"
fi

echo ""
echo "Stop tunnel: ./exposure/cloudflare/stop.sh"
