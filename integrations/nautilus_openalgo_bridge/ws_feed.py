"""OpenAlgo WebSocket watch feed — implements WatchFeedHandle for the bridge."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from trade_integrations.execution.watch_feed import WatchFeedHandle, WatchTick

logger = logging.getLogger(__name__)

_BACKOFF_INITIAL_SEC = 1.0
_BACKOFF_MAX_SEC = 60.0


def _ws_url() -> str:
    explicit = os.getenv("OPENALGO_WS_URL", "").strip()
    if explicit:
        return explicit
    host = os.getenv("OPENALGO_WS_HOST") or os.getenv("OPENALGO_HOST", "http://127.0.0.1:5001")
    host = host.replace("https://", "").replace("http://", "").split("/")[0]
    if ":" not in host:
        port = os.getenv("OPENALGO_WS_PORT") or os.getenv("WEBSOCKET_PORT", "8765")
        host = f"{host}:{port}"
    scheme = "wss" if os.getenv("OPENALGO_WS_USE_SSL", "").strip().lower() in ("1", "true") else "ws"
    return f"{scheme}://{host}"


def _watch_keys_for_openalgo_symbol(oa_symbol: str) -> list[str]:
    from nautilus_openalgo_bridge.instruments import WATCH_SYMBOL_MAP

    keys = [oa_symbol.upper()]
    for watch_key, (mapped_symbol, _) in WATCH_SYMBOL_MAP.items():
        if mapped_symbol == oa_symbol.upper() and watch_key not in keys:
            keys.append(watch_key)
    return keys


class OpenAlgoWsWatchFeed:
    """Background WS client that accumulates :class:`WatchTick` rows for poll_ticks."""

    def __init__(
        self,
        *,
        context_generation: str = "",
        ws_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._context_generation = str(context_generation or "").strip()
        self._ws_url = ws_url or _ws_url()
        self._api_key = api_key
        self._subscriptions: list[dict[str, str]] = []
        self._watch_symbols: list[str] = []
        self._ticks: deque[WatchTick] = deque()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws_app: Any = None
        self._ws_lock = threading.Lock()
        self._backoff_sec = _BACKOFF_INITIAL_SEC

    def subscribe(self, symbols: list[str]) -> None:
        from nautilus_openalgo_bridge.instruments import multiquote_requests

        watch_symbols = [s.strip().upper() for s in symbols if s and s.strip()]
        subscriptions = multiquote_requests(watch_symbols)
        with self._lock:
            self._watch_symbols = watch_symbols
            self._subscriptions = subscriptions
        self._send_subscriptions()
        self._ensure_thread()

    def poll_ticks(self) -> list[WatchTick]:
        with self._lock:
            out = list(self._ticks)
            self._ticks.clear()
            return out

    def close(self) -> None:
        self._stop.set()
        with self._ws_lock:
            ws = self._ws_app
            self._ws_app = None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        self._thread = None
        with self._lock:
            self._ticks.clear()

    def _ensure_thread(self) -> None:
        if self._stop.is_set():
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="openalgo-ws-watch-feed", daemon=True)
        self._thread.start()

    def _resolve_api_key(self) -> str | None:
        if self._api_key:
            return self._api_key
        try:
            from trade_integrations.openalgo.rest_client import openalgo_settings

            _, api_key = openalgo_settings()
            return api_key
        except Exception as exc:
            logger.warning("OpenAlgo WS watch feed skipped: %s", exc)
            return None

    def _send_subscriptions(self) -> None:
        with self._ws_lock:
            ws = self._ws_app
        if ws is None:
            return
        with self._lock:
            subs = list(self._subscriptions)
        for sub in subs:
            symbol = sub.get("symbol", "").upper()
            exchange = sub.get("exchange", "NSE").upper()
            if not symbol:
                continue
            try:
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
            except Exception as exc:
                logger.debug("WS subscribe send failed for %s@%s: %s", symbol, exchange, exc)

    def _enqueue_tick(self, oa_symbol: str, ltp: float, *, ts: str | None = None) -> None:
        tick_ts = ts or datetime.now(timezone.utc).isoformat()
        generation = self._context_generation
        with self._lock:
            watch_symbols = set(self._watch_symbols)
        keys = _watch_keys_for_openalgo_symbol(oa_symbol)
        if watch_symbols:
            keys = [key for key in keys if key in watch_symbols]
            if not keys:
                return
        for key in keys:
            tick = WatchTick(
                symbol=key,
                ltp=float(ltp),
                ts=tick_ts,
                context_generation=generation,
            )
            with self._lock:
                self._ticks.append(tick)

    def _run(self) -> None:
        try:
            import websocket  # websocket-client
        except ImportError:
            logger.warning("websocket-client not installed; OpenAlgo WS watch feed disabled")
            return

        api_key = self._resolve_api_key()
        if not api_key:
            return

        while not self._stop.is_set():
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
                ltp = data.get("ltp")
                if not symbol or ltp is None:
                    return
                ts_raw = data.get("timestamp") or data.get("ts")
                ts = str(ts_raw) if ts_raw else None
                try:
                    self._enqueue_tick(symbol, float(ltp), ts=ts)
                except (TypeError, ValueError):
                    return

            def on_open(ws: Any) -> None:
                with self._ws_lock:
                    self._ws_app = ws
                self._backoff_sec = _BACKOFF_INITIAL_SEC
                try:
                    ws.send(json.dumps({"action": "authenticate", "api_key": api_key}))
                except Exception as exc:
                    logger.debug("WS authenticate failed: %s", exc)
                    return
                time.sleep(0.2)
                self._send_subscriptions()

            def on_error(_ws: Any, error: Any) -> None:
                if not self._stop.is_set():
                    logger.debug("OpenAlgo WS watch error: %s", error)

            def on_close(_ws: Any, *_args: Any) -> None:
                with self._ws_lock:
                    if self._ws_app is _ws:
                        self._ws_app = None
                logger.debug("OpenAlgo WS watch closed")

            try:
                app = websocket.WebSocketApp(
                    self._ws_url,
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                )
                with self._ws_lock:
                    self._ws_app = app
                app.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as exc:
                if not self._stop.is_set():
                    logger.debug("OpenAlgo WS watch reconnect after error: %s", exc)
            finally:
                with self._ws_lock:
                    if self._ws_app is not None:
                        try:
                            self._ws_app.close()
                        except Exception:
                            pass
                        self._ws_app = None

            if self._stop.is_set():
                break
            delay = self._backoff_sec
            self._backoff_sec = min(self._backoff_sec * 2.0, _BACKOFF_MAX_SEC)
            time.sleep(delay)


def ticks_to_quote_snapshots(ticks: list[WatchTick]) -> dict[str, "QuoteSnapshot"]:
    """Convert polled ticks to watch-symbol QuoteSnapshot dict (latest tick wins)."""
    from nautilus_openalgo_bridge.instruments import normalize_watch_symbol
    from nautilus_openalgo_bridge.models import QuoteSnapshot

    out: dict[str, QuoteSnapshot] = {}
    for tick in ticks:
        key = normalize_watch_symbol(tick.symbol)
        out[key] = QuoteSnapshot(
            symbol=key,
            exchange="NSE",
            ltp=tick.ltp,
            fetched_at=tick.ts,
        )
    return out
