#!/bin/sh
# Install optional custom CA(s) before SearXNG starts.
# Required when the host routes HTTPS through Cloudflare Gateway / WARP (or similar MITM):
# without the gateway CA, every search engine fails with SSL CERTIFICATE_VERIFY_FAILED.
set -eu

CERT_DIR="/etc/searxng/certs"
SYSTEM_CA="/usr/local/share/ca-certificates"

if [ -d "$CERT_DIR" ]; then
  mkdir -p "$SYSTEM_CA"
  for cert in "$CERT_DIR"/*.crt "$CERT_DIR"/*.pem; do
    [ -f "$cert" ] || continue
    base="$(basename "$cert")"
    cp "$cert" "$SYSTEM_CA/$base"
  done
fi

exec /usr/local/searxng/entrypoint.sh "$@"
