#!/usr/bin/env bash
# TradingView webhook paths for OpenAlgo.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=_common.sh
source "$ROOT/platforms/_common.sh"

usage() {
  cat <<'EOF'
Usage: ./exposure/platforms/tradingview.sh [public-base-url]

Prints TradingView webhook URLs for the current exposure setup.

OpenAlgo UI: /tradingview
Docs:        https://docs.openalgo.in/trading-platform/tradingview

Endpoints:
  Strategy alerts -> /api/v1/placesmartorder
  Line alerts     -> /api/v1/placeorder
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

platform_print_urls "TradingView" "$@" \
  "Strategy alert" "/api/v1/placesmartorder" \
  "Line alert" "/api/v1/placeorder"

echo ""
echo "Configure alerts in TradingView with the webhook URL and JSON from OpenAlgo /tradingview"
