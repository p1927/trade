#!/usr/bin/env bash
# Sync forked submodules with their upstream repositories.
#
# Usage:
#   ./scripts/sync.sh all
#   ./scripts/sync.sh tradingagents
#   ./scripts/sync.sh openalgo
#   ./scripts/sync.sh status

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-all}"

ensure_upstream() {
  local dir="$1" url="$2"
  cd "$dir"
  if ! git remote | grep -qx upstream; then
    git remote add upstream "$url"
  fi
}

sync_tradingagents() {
  echo "==> Syncing TradingAgents (upstream -> p1927/TradingAgents submodule)"
  ensure_upstream "$ROOT/tradingagents" "https://github.com/TauricResearch/TradingAgents.git"
  cd "$ROOT/tradingagents"
  git fetch upstream
  git merge --no-edit upstream/main
  git push origin main
  cd "$ROOT"
  git add tradingagents
  echo "    Updated tradingagents submodule pointer."
  echo "    Commit and push trade when ready: git commit -m 'chore: bump tradingagents submodule'"
}

sync_openalgo() {
  echo "==> Syncing OpenAlgo (upstream -> p1927/openalgo submodule)"
  ensure_upstream "$ROOT/openalgo" "https://github.com/marketcalls/openalgo.git"
  cd "$ROOT/openalgo"
  git fetch upstream
  git merge --no-edit upstream/main
  git push origin main
  cd "$ROOT"
  git add openalgo
  echo "    Updated openalgo submodule pointer."
  echo "    Commit and push trade when ready: git commit -m 'chore: bump openalgo submodule'"
}

show_status() {
  echo "==> Trade repository"
  cd "$ROOT"
  git remote -v
  echo
  echo "==> TradingAgents upstream delta"
  ensure_upstream "$ROOT/tradingagents" "https://github.com/TauricResearch/TradingAgents.git"
  cd "$ROOT/tradingagents"
  git fetch upstream --quiet
  git log --oneline HEAD..upstream/main | head -10 || true
  echo
  echo "==> OpenAlgo upstream delta"
  ensure_upstream "$ROOT/openalgo" "https://github.com/marketcalls/openalgo.git"
  cd "$ROOT/openalgo"
  git fetch upstream --quiet
  git log --oneline HEAD..upstream/main | head -10 || true
}

case "$TARGET" in
  tradingagents|ta|agents)
    sync_tradingagents
    ;;
  openalgo|oa)
    sync_openalgo
    ;;
  all)
    sync_tradingagents
    sync_openalgo
    ;;
  status)
    show_status
    ;;
  -h|--help)
    cat <<'EOF'
Usage: ./scripts/sync.sh [target]

Targets:
  all             Sync TradingAgents and OpenAlgo submodules (default)
  tradingagents   Merge upstream TradingAgents into tradingagents/
  openalgo        Merge upstream OpenAlgo into openalgo/
  status          Show pending upstream commits for both submodules
EOF
    ;;
  *)
    echo "Unknown target: $TARGET" >&2
    exit 1
    ;;
esac
