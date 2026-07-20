#!/usr/bin/env bash
# Install Crawl4AI + Playwright browser for external-predictions (Miscellaneous tab).
# Runs on the host Python venv — not a Docker service (unlike SearXNG).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/stack_lib.sh"
stack_load_env

PY="$(stack_pick_python)"
PIP="${PY%/python}/pip"
VERIFY_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --verify-only) VERIFY_ONLY=1 ;;
    -h|--help)
      cat <<'EOF'
Usage: ./scripts/ensure_crawl4ai.sh [--verify-only]

  (default)       pip install trade-stack[external-predictions] + crawl4ai-setup
  --verify-only   Check import only — no install
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
  shift
done

stack_verify_crawl4ai() {
  PYTHONPATH="$ROOT/integrations" "$PY" - <<'PY'
import sys
try:
    import crawl4ai  # noqa: F401
except ImportError:
    print("crawl4ai import failed", file=sys.stderr)
    sys.exit(1)
from trade_integrations.dataflows.crawl4ai_client import crawl4ai_is_installed
if not crawl4ai_is_installed():
    print("crawl4ai_is_installed() returned False", file=sys.stderr)
    sys.exit(1)
print("ok crawl4ai")
PY
}

if (( VERIFY_ONLY )); then
  stack_verify_crawl4ai
  exit 0
fi

echo "[crawl4ai] installing Python package into $(dirname "$PY") ..."
"$PIP" install -q --upgrade pip
"$PIP" install -q -e "$ROOT/.[external-predictions]"

if command -v crawl4ai-setup >/dev/null 2>&1; then
  echo "[crawl4ai] running crawl4ai-setup (Playwright Chromium) ..."
  crawl4ai-setup
elif "$PY" -m crawl4ai.setup 2>/dev/null; then
  echo "[crawl4ai] ran python -m crawl4ai.setup"
else
  echo "[crawl4ai] WARN: crawl4ai-setup not found — try: pip install crawl4ai && crawl4ai-setup" >&2
fi

stack_verify_crawl4ai
echo "[crawl4ai] done"
