#!/usr/bin/env bash
# Sync MiniMax env into .env.skyvern and start self-hosted Skyvern.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PATH="/Applications/Docker.app/Contents/Resources/bin:/usr/local/bin:/opt/homebrew/bin:${PATH:-}"
export SKYVERN_HOST_PORT="${SKYVERN_HOST_PORT:-8010}"

CA_BUNDLE="$ROOT/.skyvern-data/host-ca-bundle.pem"
mkdir -p "$ROOT/.skyvern-data"
security find-certificate -a -p /Library/Keychains/System.keychain /System/Library/Keychains/SystemRootCertificates.keychain >"$CA_BUNDLE" 2>/dev/null || cp /etc/ssl/cert.pem "$CA_BUNDLE"

"$ROOT/scripts/sync_skyvern_env.sh"
docker compose -f docker-compose.skyvern.yml up -d --force-recreate skyvern skyvern-ui

echo ""
echo "Skyvern starting — UI http://localhost:8080  API http://localhost:${SKYVERN_HOST_PORT}"
echo "Local API key: auto-read from .skyvern-data/.skyvern/credentials.toml (no .env copy needed)"
echo "LLM brain: MiniMax from your .env (MINIMAX_API_KEY)"
