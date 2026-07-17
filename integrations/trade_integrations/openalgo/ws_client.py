"""OpenAlgo WebSocket proxy client — seeds hub channel L1 cache for WATCH subscribers."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_ws_feed: "OpenAlgoWsFeed | None" = None
_ws_lock = threading.Lock()


def _ws_enabled() -> bool:
    return os.getenv("OPENALGO_WS_ENABLED", "1").strip().lower() in ("1", "true", "yes")


def _ws_url() -> str:
    host = os.getenv("OPENALGO_WS_HOST") or os.getenv("OPENALGO_HOST", "http://127.0.0.1:5001")
    host = host.replace("https://", "").replace("http://", "").split("/")[0]
    if ":" not in host:
        port = os.getenv("OPENALGO_WS_PORT") or os.getenv("WEBSOCKET_PORT", "8765")
        host = f"{host}:{port}"
    scheme = "wss" if os.getenv("OPENALGO_WS_USE_SSL", "").strip().lower() in ("1", "true") else "ws"
    return os.getenv("OPENALGO_WS_URL") or f"{scheme}://{host}"


class OpenAlgoWsFeed:
    """Background thread subscribing to OpenAlgo WS LTP (mode 1) and seeding L1 cache."""

    def __init__(self, subscriptions: list[dict[str, str]] | None = None) -> None:
        self._subscriptions = subscriptions or []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ws_app: Any = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="openalgo-ws-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._ws_app is not None:
            try:
                self._ws_app.close()
            except Exception:
                pass

    def update_subscriptions(self, subscriptions: list[dict[str, str]]) -> None:
        self._subscriptions = subscriptions

    def _run(self) -> None:
        try:
            import websocket  # websocket-client
        except ImportError:
            logger.warning("websocket-client not installed; OpenAlgo WS feed disabled")
            return

        from trade_integrations.openalgo.rest_client import openalgo_settings

        try:
            _, api_key = openalgo_settings()
        except Exception as exc:
            logger.warning("OpenAlgo WS feed skipped: %s", exc)
            return

        url = _ws_url()

        def on_message(_ws: Any, message: str) -> None:
            if self._stop.is_set():
                return
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                return
            if payload.get("type") != "market_data":
                return
            data = payload.get("data") or {}
            symbol = str(data.get("symbol") or "").upper()
            exchange = str(data.get("exchange") or "NSE").upper()
            ltp = data.get("ltp")
            if not symbol or ltp is None:
                return
            quote = {
                "ltp": ltp,
                "symbol": symbol,
                "exchange": exchange,
                "volume": data.get("volume"),
                "change_pct": data.get("change_percent") or data.get("change_pct"),
                "source": "openalgo_ws",
            }
            try:
                from trade_integrations.hub_capture.channel import seed_quote_l1

                seed_quote_l1(symbol, exchange, quote)
            except Exception as exc:
                logger.debug("WS L1 seed failed for %s@%s: %s", symbol, exchange, exc)

        def on_open(ws: Any) -> None:
            ws.send(json.dumps({"action": "authenticate", "api_key": api_key}))
            time.sleep(0.2)
            for sub in self._subscriptions:
                symbol = sub.get("symbol", "").upper()
                exchange = sub.get("exchange", "NSE").upper()
                if not symbol:
                    continue
                ws.send(
                    json.dumps(
                        {
                            "action": "subscribe",
                            "symbol": symbol,
                            "exchange": exchange,
                            "mode": 1,
                        }
                    )
                )

        def on_error(_ws: Any, error: Any) -> None:
            if not self._stop.is_set():
                logger.debug("OpenAlgo WS error: %s", error)

        def on_close(_ws: Any, *_args: Any) -> None:
            logger.debug("OpenAlgo WS closed")

        while not self._stop.is_set():
            try:
                self._ws_app = websocket.WebSocketApp(
                    url,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                self._ws_app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                logger.debug("OpenAlgo WS reconnect after error: %s", exc)
            if not self._stop.is_set():
                time.sleep(2.0)


def get_ws_feed() -> OpenAlgoWsFeed | None:
    global _ws_feed
    if not _ws_enabled():
        return None
    with _ws_lock:
        if _ws_feed is None:
            _ws_feed = OpenAlgoWsFeed()
        return _ws_feed


def ensure_ws_feed(subscriptions: list[dict[str, str]]) -> OpenAlgoWsFeed | None:
    """Start or refresh the shared WS feed for the given symbol/exchange pairs."""
    feed = get_ws_feed()
    if feed is None:
        return None
    feed.update_subscriptions(subscriptions)
    feed.start()
    return feed
