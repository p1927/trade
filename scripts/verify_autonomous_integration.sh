#!/usr/bin/env bash
# Thorough integration verification: Nautilus watch ON, alert→Vibe, US Alpaca optional.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

export NAUTILUS_WATCH_ENABLE="${NAUTILUS_WATCH_ENABLE:-true}"
export TRADE_INTEGRATIONS_SKIP_APPLY=1

exec python3 "$ROOT/scripts/verify_autonomous_integration.py" "$@"
