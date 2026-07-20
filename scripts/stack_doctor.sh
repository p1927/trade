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
hub_only=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --hub) hub_only=1 ;;
  esac
  shift
done

echo "══════════════════════════════════════════════════════════"
echo "  Trade stack doctor"
echo "══════════════════════════════════════════════════════════"

stack_print_ports_summary
stack_reconcile_all

if (( ! hub_only )); then
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
      echo "  ✗ setup_nautilus.sh --verify failed — run: trade setup"
      failures=$((failures + 1))
    fi
  else
    echo "  ✗ setup_nautilus.sh missing"
    failures=$((failures + 1))
  fi

  if "$py" "$ROOT/scripts/verify_hub_integration.py"; then
    echo "  ✓ verify_hub_integration.py"
  else
    echo "  ✗ verify_hub_integration.py failed"
    failures=$((failures + 1))
  fi
fi

echo "──────────────────────────────────────────────────────────"
echo "  Hub dependency probes (read-only)"
echo "──────────────────────────────────────────────────────────"

if stack_preflight_dependencies --strict $([[ $hub_only -eq 1 ]] && printf '%s' '--hub-only'); then
  echo "  ✓ hub dependency preflight"
else
  echo "  ✗ hub dependency preflight failed"
  failures=$((failures + 1))
fi

if stack_searxng_enabled; then
  if stack_probe_searxng; then
    echo "  ✓ SearXNG     $(stack_searxng_url)"
  else
    echo "  ✗ SearXNG     not reachable at $(stack_searxng_url)"
    failures=$((failures + 1))
  fi
else
  echo "  · SearXNG     skipped (STACK_START_SEARXNG=0)"
fi

if stack_timescale_should_ensure; then
  if stack_probe_timescale; then
    echo "  ✓ TimescaleDB $(stack_timescale_url)"
  else
    echo "  ✗ TimescaleDB not reachable"
    failures=$((failures + 1))
  fi
else
  echo "  · TimescaleDB disabled (TIMESCALE_ENABLED not set)"
fi

if stack_redis_enabled; then
  if stack_probe_redis; then
    echo "  ✓ Redis       $(stack_redis_url)"
  else
    echo "  ✗ Redis       not reachable at $(stack_redis_url)"
    failures=$((failures + 1))
  fi
else
  echo "  · Redis       skipped (NAUTILUS_WATCH_ENABLE=0)"
fi

echo "══════════════════════════════════════════════════════════"
if (( failures == 0 )); then
  echo "  Doctor: all critical checks passed"
  echo "  Start stack: trade up   |   Dev: trade dev"
  exit 0
fi
echo "  Doctor: $failures critical check(s) failed — fix above, then: trade setup && trade up"
exit 1
