#!/usr/bin/env bash
# Chartink webhook paths for OpenAlgo.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=_common.sh
source "$ROOT/platforms/_common.sh"

usage() {
  cat <<'EOF'
Usage: ./exposure/platforms/chartink.sh [public-base-url] [webhook-id]

Prints Chartink webhook URLs for the current exposure setup.

OpenAlgo UI: /chartink
Docs:        https://docs.openalgo.in/trading-platform/chartink

Endpoint pattern:
  /chartink/webhook/<webhook-id>

Chartink alert body example:
  {"webhook_id": "<webhook-id>", "stocks": "{stocks}"}
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

base_override=""
webhook_id=""

if [[ -n "${1:-}" && "$1" == http* ]]; then
  base_override="$1"
  webhook_id="${2:-}"
elif [[ -n "${1:-}" ]]; then
  webhook_id="$1"
fi

base="$(platform_require_public_url "$base_override")" || exit 1

if platform_is_localhost "$base"; then
  exposure_warn "Chartink webhooks will not work with localhost — start a tunnel first"
fi

echo ""
echo "Chartink webhook endpoints (base: $base)"
if [[ -n "$webhook_id" ]]; then
  echo "  Strategy webhook -> ${base}/chartink/webhook/${webhook_id}"
else
  echo "  Strategy webhook -> ${base}/chartink/webhook/<webhook-id>"
  echo "  Create a strategy in OpenAlgo /chartink to get your webhook-id"
fi

echo ""
echo "Chartink scanner webhook body:"
if [[ -n "$webhook_id" ]]; then
  echo "  {\"webhook_id\": \"${webhook_id}\", \"stocks\": \"{stocks}\"}"
else
  echo "  {\"webhook_id\": \"<webhook-id>\", \"stocks\": \"{stocks}\"}"
fi
