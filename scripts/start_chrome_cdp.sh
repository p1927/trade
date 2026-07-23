#!/usr/bin/env bash
# Launch Google Chrome with remote debugging for Crawl4AI CDP mode (macOS).
set -euo pipefail

PORT="${1:-9222}"
PROFILE_DIR="${TMPDIR:-/tmp}/chrome-cdp-${PORT}"

echo "Starting Chrome with remote debugging on port ${PORT}"
echo "Profile dir: ${PROFILE_DIR}"
echo "Set CRAWL4AI_CDP_URL=http://127.0.0.1:${PORT} in .env"

if [[ "$(uname -s)" == "Darwin" ]]; then
  exec open -na "Google Chrome" --args \
    "--remote-debugging-port=${PORT}" \
    "--user-data-dir=${PROFILE_DIR}" \
    "--no-first-run" \
    "--no-default-browser-check"
fi

echo "Non-macOS: run Chrome manually with --remote-debugging-port=${PORT}" >&2
exit 1
