"""Tests for Alpaca quote feed used by Nautilus US watch."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.alpaca_quote_feed import AlpacaQuoteFeed  # noqa: E402


def test_alpaca_feed_poll_maps_symbol():
    feed = AlpacaQuoteFeed()
    with patch(
        "trade_integrations.dataflows.alpaca.fetch_alpaca_trade_snapshot",
        return_value={"ltp": 450.25, "volume": 1000},
    ):
        quotes = feed.poll(["SPY"])
    assert "SPY" in quotes
    assert quotes["SPY"].ltp == 450.25
    assert quotes["SPY"].exchange == "US"
