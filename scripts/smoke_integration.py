#!/usr/bin/env python3
"""Live integration smoke checks for the trade stack."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Load trade .env
env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))

import trade_integrations  # noqa: F401
import tradingagents.default_config as default_config
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.interface import VENDOR_LIST, route_to_vendor
import copy

set_config(copy.deepcopy(default_config.DEFAULT_CONFIG))

print("=== Integration registration ===")
assert "openalgo" in VENDOR_LIST
assert "searxng" in VENDOR_LIST
assert "aggregated" in VENDOR_LIST
print("vendors:", ", ".join(VENDOR_LIST))

print("\n=== OpenAlgo live quote (RELIANCE.NS) ===")
try:
    result = route_to_vendor("get_stock_data", "RELIANCE.NS", "2026-07-01", "2026-07-15")
    preview = result[:400].replace("\n", " ")
    print("OK:", preview, "...")
except Exception as exc:
    print("FAIL:", exc)
    sys.exit(1)

print("\n=== Aggregated news (NSEI) ===")
try:
    news = route_to_vendor("get_news", "NSEI", "2026-07-08", "2026-07-15")
    preview = news[:400].replace("\n", " ")
    print("OK:", preview, "...")
except Exception as exc:
    print("FAIL:", exc)
    sys.exit(1)

print("\n=== RSS feeds (sentiment integration) ===")
from trade_integrations.dataflows.rss_feeds import fetch_rss_feeds

rss = fetch_rss_feeds("RELIANCE")
preview = rss[:300].replace("\n", " ")
print("OK:", preview, "...")

print("\nAll integration smoke checks passed.")
