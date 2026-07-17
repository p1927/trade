#!/usr/bin/env bash
# Unified preflight for trade stack + hub integration.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/stack_lib.sh"

STACK_ROOT="$ROOT"
stack_load_env

py="$(stack_pick_python)"
failures=0

echo "══════════════════════════════════════════════════════════"
echo "  Trade stack doctor"
echo "══════════════════════════════════════════════════════════"

stack_print_ports_summary

if stack_preflight_start; then
  echo "  ✓ stack preflight (ports, vibe-trading, frontend, OpenAlgo venv)"
else
  echo "  ✗ stack preflight failed"
  failures=$((failures + 1))
fi

if "$py" "$ROOT/scripts/setup_vibe.py" --verify; then
  echo "  ✓ setup_vibe.py --verify"
else
  echo "  ✗ setup_vibe.py --verify failed — run: trade setup vibe"
  failures=$((failures + 1))
fi

if [[ -x "$ROOT/scripts/setup_nautilus.sh" ]]; then
  if bash "$ROOT/scripts/setup_nautilus.sh" --verify 2>/dev/null; then
    echo "  ✓ setup_nautilus.sh --verify"
  else
    echo "  ⚠ setup_nautilus.sh --verify (optional — poll loop fallback available)"
  fi
fi

if "$py" "$ROOT/scripts/verify_hub_integration.py"; then
  echo "  ✓ verify_hub_integration.py"
else
  echo "  ✗ verify_hub_integration.py failed"
  failures=$((failures + 1))
fi

echo "══════════════════════════════════════════════════════════"
if (( failures == 0 )); then
  echo "  Doctor: all critical checks passed"
  echo "  Start stack: trade up"
  exit 0
fi
echo "  Doctor: $failures critical check(s) failed — fix above, then: trade up"
exit 1
