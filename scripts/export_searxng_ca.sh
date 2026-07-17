#!/usr/bin/env bash
# Export Cloudflare Gateway (WARP/Zero Trust) CA for SearXNG Docker SSL trust.
# Run once on macOS when SearXNG engines fail with SSL certificate verify errors.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/stack/searxng/certs/cloudflare-gateway-ca.crt"
mkdir -p "$(dirname "$OUT")"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This helper targets macOS Keychain export. Place your MITM CA at:" >&2
  echo "  $OUT" >&2
  exit 1
fi

if security find-certificate -a -c "Gateway CA - Cloudflare Managed G2" -p /Library/Keychains/System.keychain >"$OUT" 2>/dev/null; then
  :
elif security find-certificate -a -c "Gateway CA" -p /Library/Keychains/System.keychain >"$OUT" 2>/dev/null; then
  :
else
  echo "Cloudflare Gateway CA not found in System keychain." >&2
  echo "If you use a different HTTPS proxy, copy its root CA to:" >&2
  echo "  $OUT" >&2
  exit 1
fi

openssl x509 -in "$OUT" -noout -subject
echo "Wrote $OUT"
echo "Restart SearXNG: docker compose -f docker-compose.stack.yml restart searxng"
