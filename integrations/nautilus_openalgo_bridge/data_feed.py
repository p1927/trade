"""Poll OpenAlgo quotes for the Nautilus watch bridge."""

from __future__ import annotations

import logging
import threading
from typing import Any

from trade_integrations.openalgo.freshness import FreshnessPolicy
from trade_integrations.openalgo.market_data import fetch_multi_quotes_raw

from nautilus_openalgo_bridge.config import BridgeConfig, get_bridge_config
from nautilus_openalgo_bridge.instruments import multiquote_requests, normalize_watch_symbol, resolve_openalgo_symbol
from nautilus_openalgo_bridge.models import QuoteSnapshot
from nautilus_openalgo_bridge.openalgo_client import BridgeOpenAlgoClient, get_openalgo_client

logger = logging.getLogger(__name__)


def _extract_quote_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not payload:
        return []
    if isinstance(payload.get("quotes"), list):
        return [row for row in payload["quotes"] if isinstance(row, dict)]
    if isinstance(payload.get("data"), list):
        return [row for row in payload["data"] if isinstance(row, dict)]
    if isinstance(payload.get("results"), list):
        return [row for row in payload["results"] if isinstance(row, dict)]
    return []


def parse_multiquote_response(
    payload: dict[str, Any],
    requested: list[dict[str, str]],
) -> dict[str, QuoteSnapshot]:
    """Map OpenAlgo multiquotes payload to watch-symbol QuoteSnapshot dict."""
    rows = _extract_quote_rows(payload)
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or row.get("tradingsymbol") or "").upper()
        exchange = str(row.get("exchange") or "NSE").upper()
        if symbol:
            by_key[(symbol, exchange)] = row

    out: dict[str, QuoteSnapshot] = {}
    for req in requested:
        oa_symbol = req["symbol"].upper()
        exchange = req["exchange"].upper()
        row = by_key.get((oa_symbol, exchange))
        if row is None:
            # Some responses nest by symbol only
            row = next(
                (candidate for (sym, _), candidate in by_key.items() if sym == oa_symbol),
                None,
            )
        if row is None:
            continue
        snap = QuoteSnapshot.from_openalgo_row(oa_symbol, exchange, row)
        if snap is None:
            continue
        for watch_symbol in _watch_keys_for_openalgo_symbol(oa_symbol):
            out[watch_symbol] = snap
    return out


def _watch_keys_for_openalgo_symbol(oa_symbol: str) -> list[str]:
    from nautilus_openalgo_bridge.instruments import WATCH_SYMBOL_MAP

    keys = [oa_symbol]
    for watch_key, (mapped_symbol, _) in WATCH_SYMBOL_MAP.items():
        if mapped_symbol == oa_symbol and watch_key not in keys:
            keys.append(watch_key)
    return keys


class OpenAlgoQuoteFeed:
    """Fetch latest LTP snapshots for configured watch symbols."""

    def __init__(
        self,
        client: BridgeOpenAlgoClient | None = None,
        config: BridgeConfig | None = None,
    ) -> None:
        self.config = config or get_bridge_config()
        self.client = client or get_openalgo_client(self.config)

    def poll(self, symbols: list[str] | None = None) -> dict[str, QuoteSnapshot]:
        watch_symbols = symbols or list(self.config.watch_symbols)
        requests = multiquote_requests(watch_symbols)
        if not requests:
            return {}

        try:
            from trade_integrations.openalgo.ws_client import ensure_ws_feed

            ensure_ws_feed(requests)
        except Exception:
            logger.debug("OpenAlgo WS feed unavailable", exc_info=True)

        try:
            payload = fetch_multi_quotes_raw(requests)
            if not isinstance(payload, dict):
                payload = {}
        except RuntimeError as exc:
            logger.warning("OpenAlgo multiquotes failed: %s", exc)
            return self._poll_fallback(watch_symbols)

        quotes = parse_multiquote_response(payload, requests)
        if quotes:
            threading.Thread(
                target=self._async_record_ticks,
                args=(quotes,),
                daemon=True,
                name="openalgo-tick-record",
            ).start()
            return quotes
        fallback = self._poll_fallback(watch_symbols)
        if fallback:
            threading.Thread(
                target=self._async_record_ticks,
                args=(fallback,),
                daemon=True,
                name="openalgo-tick-record",
            ).start()
        return fallback

    def _async_record_ticks(self, quotes: dict[str, QuoteSnapshot]) -> None:
        try:
            from trade_integrations.hub_capture.gate import should_capture
            from trade_integrations.hub_storage.timescale_ticks import record_quote_snapshots

            if should_capture("NIFTY", "ticks"):
                record_quote_snapshots(quotes, source="openalgo_watch")
        except Exception:
            logger.debug("timescale tick record skipped", exc_info=True)

    def _poll_fallback(self, watch_symbols: list[str]) -> dict[str, QuoteSnapshot]:
        out: dict[str, QuoteSnapshot] = {}
        for symbol in watch_symbols:
            oa_symbol, exchange = resolve_openalgo_symbol(symbol)
            try:
                row = self.client.get_quote(oa_symbol, exchange=exchange)
            except RuntimeError as exc:
                logger.debug("quote fallback failed for %s: %s", symbol, exc)
                continue
            snap = QuoteSnapshot.from_openalgo_row(oa_symbol, exchange, row)
            if snap is None:
                continue
            out[normalize_watch_symbol(symbol)] = snap
        return out
