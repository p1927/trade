#!/usr/bin/env bash
# Update openalgo/.env HOST_SERVER to a public tunnel URL.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$ROOT/lib/common.sh"

usage() {
  cat <<'EOF'
Usage: ./exposure/sync-host-server.sh <public-url>

Sets HOST_SERVER in openalgo/.env so OpenAlgo webhook pages (TradingView,
GoCharting, Chartink) generate externally reachable URLs.

Examples:
  ./exposure/sync-host-server.sh https://abc123.trycloudflare.com
  ./exposure/sync-host-server.sh https://openalgo.yourdomain.com
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

url="${1:-}"
if [[ -z "$url" ]]; then
  url="$(read_exposure_state public_url)"
fi

if [[ -z "$url" ]]; then
  exposure_fail "No public URL provided and no active tunnel state found"
  usage >&2
  exit 1
fi

update_host_server "$url"
