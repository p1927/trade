#!/usr/bin/env bash
# Entry point for exposing local OpenAlgo to external webhook platforms.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$ROOT/lib/common.sh"

usage() {
  cat <<'EOF'
Usage: ./exposure/start.sh <command> [options]

Expose the local OpenAlgo instance so TradingView, GoCharting, and Chartink
can deliver webhook alerts.

Commands:
  quick                 Ephemeral trycloudflare.com tunnel (good for dev)
  named                 Persistent tunnel on your Cloudflare domain
  stop                  Stop active tunnel
  status                Show tunnel and HOST_SERVER state
  sync <url>            Set openalgo/.env HOST_SERVER manually
  urls [platform]       Print webhook URLs (tradingview|gocharting|chartink|all)

Examples:
  ./start.sh --openalgo-only          # in another terminal, start OpenAlgo first
  ./exposure/start.sh quick
  ./exposure/start.sh urls all
  ./exposure/start.sh named --hostname openalgo.yourdomain.com

Environment (optional):
  OPENALGO_PORT              Local OpenAlgo port override
  CLOUDFLARE_TUNNEL_NAME     Named tunnel (default: trade-openalgo)
  EXPOSURE_HOSTNAME          Public hostname for named tunnel / HOST_SERVER sync
  CLOUDFLARE_CONFIG          Path to cloudflared config.yml
EOF
}

cmd="${1:-}"
shift || true

case "$cmd" in
  quick)
    exec "$ROOT/cloudflare/quick-tunnel.sh" "$@"
    ;;
  named)
    exec "$ROOT/cloudflare/named/start.sh" "$@"
    ;;
  stop)
    exec "$ROOT/cloudflare/stop.sh" "$@"
    ;;
  status)
    exec "$ROOT/status.sh" "$@"
    ;;
  sync)
    exec "$ROOT/sync-host-server.sh" "$@"
    ;;
  urls)
    platform="${1:-all}"
    shift || true
    case "$platform" in
      tradingview) exec "$ROOT/platforms/tradingview.sh" "$@" ;;
      gocharting)  exec "$ROOT/platforms/gocharting.sh" "$@" ;;
      chartink)    exec "$ROOT/platforms/chartink.sh" "$@" ;;
      all)
        "$ROOT/platforms/tradingview.sh" "$@" || true
        "$ROOT/platforms/gocharting.sh" "$@" || true
        "$ROOT/platforms/chartink.sh" "$@" || true
        ;;
      -h|--help|"")
        usage
        ;;
      *)
        exposure_fail "Unknown platform: $platform"
        usage >&2
        exit 1
        ;;
    esac
    ;;
  -h|--help|"")
    usage
    ;;
  *)
    exposure_fail "Unknown command: $cmd"
    usage >&2
    exit 1
    ;;
esac
