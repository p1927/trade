#!/usr/bin/env bash
# Stop and restart Cloudflare quick tunnel; verify reachability and print URLs.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../lib/common.sh
source "$ROOT/lib/common.sh"

usage() {
  cat <<'EOF'
Usage: ./exposure/cloudflare/restart-tunnel.sh [options]

Stops any active tunnel, starts a fresh quick tunnel, waits until the public
URL responds, then prints working links.

Options:
  --no-sync       Do not update openalgo/.env HOST_SERVER
  --port PORT     Override local OpenAlgo port (default: from openalgo/.env)
  -h, --help      Show this help

Requires OpenAlgo to be running locally (./start.sh --openalgo-only).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      break
      ;;
  esac
  shift
done

exposure_log "Restarting Cloudflare quick tunnel..."
stop_all_cloudflared

REQUIRE_READY=1 exec "$ROOT/cloudflare/quick-tunnel.sh" "$@"
