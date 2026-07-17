"""Poll Alpaca quotes for US Nautilus watch bridge."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from nautilus_openalgo_bridge.models import QuoteSnapshot

logger = logging.getLogger(__name__)


class AlpacaQuoteFeed:
    """Fetch latest LTP snapshots for US watch symbols via Alpaca REST."""

    def poll(self, symbols: list[str] | None = None) -> dict[str, QuoteSnapshot]:
        from trade_integrations.dataflows.alpaca import fetch_alpaca_quote, fetch_alpaca_trade_snapshot

        out: dict[str, QuoteSnapshot] = {}
        for raw in symbols or []:
            symbol = str(raw or "").strip().upper()
            if not symbol:
                continue
            row = fetch_alpaca_trade_snapshot(symbol) or fetch_alpaca_quote(symbol)
            if not row or row.get("ltp") is None:
                continue
            try:
                ltp = float(row["ltp"])
            except (TypeError, ValueError):
                continue
            vol_raw = row.get("volume")
            volume = float(vol_raw) if vol_raw is not None else None
            out[symbol] = QuoteSnapshot(
                symbol=symbol,
                exchange="US",
                ltp=ltp,
                volume=volume,
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )
        return out
