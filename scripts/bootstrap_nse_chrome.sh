#!/usr/bin/env bash
# Bootstrap Google Chrome + Python deps for NSE browser fetch module.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Installing Python nse-browser extras"
pip install -e ".[nse-browser]" -q

echo "==> Ensuring Google Chrome"
python3 - <<'PY'
from trade_integrations.nse_browser.chrome_bootstrap import ensure_chrome
path = ensure_chrome(auto_install=True)
print(f"Chrome: {path}")
PY

echo "==> Done. Run: python scripts/run_nse_browser_fetch.py --mission fii_dii_history --refresh-cookies"
