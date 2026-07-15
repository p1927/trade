#!/usr/bin/env bash
# GoCharting webhook paths for OpenAlgo.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=_common.sh
source "$ROOT/platforms/_common.sh"

usage() {
  cat <<'EOF'
Usage: ./exposure/platforms/gocharting.sh [public-base-url]

Prints GoCharting webhook URLs for the current exposure setup.

OpenAlgo UI: /gocharting
Docs:        https://docs.openalgo.in/trading-platform/gocharting

Requires GoCharting Premium for webhook alerts.
Endpoint:    /api/v1/placeorder
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

platform_print_urls "GoCharting" "$@" \
  "Alert webhook" "/api/v1/placeorder"

echo ""
echo "Configure alerts in GoCharting with the webhook URL and JSON from OpenAlgo /gocharting"
