#!/usr/bin/env bash
# Sync forked submodules with their upstream repositories.
#
# Usage:
#   ./scripts/sync.sh all
#   ./scripts/sync.sh tradingagents
#   ./scripts/sync.sh openalgo
#   ./scripts/sync.sh vibetrading
#   ./scripts/sync.sh ed-alpha
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

sync_vibetrading() {
  echo "==> Syncing Vibe Trading (upstream -> p1927/Vibe-Trading submodule)"
  ensure_upstream "$ROOT/vibetrading" "https://github.com/HKUDS/Vibe-Trading.git"
  cd "$ROOT/vibetrading"
  git fetch upstream
  git merge --no-edit upstream/main
  git push origin main
  cd "$ROOT"
  git add vibetrading
  echo "    Updated vibetrading submodule pointer."
  echo "    Commit and push trade when ready: git commit -m 'chore: bump vibetrading submodule'"
}

sync_ed_alpha() {
  echo "==> Syncing ED-ALPHA (upstream -> p1927/ED-ALPHA submodule)"
  ensure_upstream "$ROOT/ed-alpha" "https://github.com/E9Technologies/ED-ALPHA.git"
  cd "$ROOT/ed-alpha"
  git fetch upstream
  git merge --no-edit upstream/main
  git push origin main
  cd "$ROOT"
  git add ed-alpha
  echo "    Updated ed-alpha submodule pointer."
  echo "    Commit and push trade when ready: git commit -m 'chore: bump ed-alpha submodule'"
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
  echo
  echo "==> Vibe Trading upstream delta"
  if [[ -d "$ROOT/vibetrading/.git" ]]; then
    ensure_upstream "$ROOT/vibetrading" "https://github.com/HKUDS/Vibe-Trading.git"
    cd "$ROOT/vibetrading"
    git fetch upstream --quiet
    git log --oneline HEAD..upstream/main | head -10 || true
  else
    echo "    vibetrading/ submodule not initialized"
  fi
  echo
  echo "==> ED-ALPHA upstream delta"
  if [[ -d "$ROOT/ed-alpha/.git" ]]; then
    ensure_upstream "$ROOT/ed-alpha" "https://github.com/E9Technologies/ED-ALPHA.git"
    cd "$ROOT/ed-alpha"
    git fetch upstream --quiet
    git log --oneline HEAD..upstream/main | head -10 || true
  else
    echo "    ed-alpha/ submodule not initialized"
  fi
}

case "$TARGET" in
  tradingagents|ta|agents)
    sync_tradingagents
    ;;
  openalgo|oa)
    sync_openalgo
    ;;
  vibetrading|vibe|vt)
    sync_vibetrading
    ;;
  ed-alpha|edalpha|ea)
    sync_ed_alpha
    ;;
  all)
    sync_tradingagents
    sync_openalgo
    sync_vibetrading
    sync_ed_alpha
    ;;
  status)
    show_status
    ;;
  -h|--help)
    cat <<'EOF'
Usage: ./scripts/sync.sh [target]

Targets:
  all             Sync all submodules (default)
  tradingagents   Merge upstream TradingAgents into tradingagents/
  openalgo        Merge upstream OpenAlgo into openalgo/
  vibetrading     Merge upstream HKUDS/Vibe-Trading into vibetrading/
  ed-alpha        Merge upstream E9Technologies/ED-ALPHA into ed-alpha/
  status          Show pending upstream commits for all submodules
EOF
    ;;
  *)
    echo "Unknown target: $TARGET" >&2
    exit 1
    ;;
esac
