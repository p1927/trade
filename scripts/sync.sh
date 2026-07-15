#!/usr/bin/env bash
# Sync forked dependencies with their upstream repositories.
#
# Usage:
#   ./scripts/sync.sh all
#   ./scripts/sync.sh tradingagents
#   ./scripts/sync.sh openalgo
#   ./scripts/sync.sh status

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-all}"

sync_tradingagents() {
  echo "==> Syncing TradingAgents (upstream -> trade)"
  cd "$ROOT"
  git fetch upstream
  git merge --no-edit upstream/main
  echo "    Merged TauricResearch/TradingAgents into trade."
  echo "    Push when ready: git push origin main"
}

sync_openalgo() {
  echo "==> Syncing OpenAlgo (upstream -> p1927/openalgo submodule)"
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
  echo "==> Trade remotes"
  cd "$ROOT"
  git remote -v
  echo
  echo "==> TradingAgents upstream delta"
  git fetch upstream --quiet
  git log --oneline HEAD..upstream/main | head -10 || true
  echo
  echo "==> OpenAlgo upstream delta"
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
  all             Sync TradingAgents and OpenAlgo (default)
  tradingagents   Merge upstream TradingAgents into trade
  openalgo        Merge upstream OpenAlgo into the openalgo submodule
  status          Show pending upstream commits for both repos
EOF
    ;;
  *)
    echo "Unknown target: $TARGET" >&2
    exit 1
    ;;
esac
