"""Tests for OpenAlgo WebSocket watch feed (mocked — no live WS)."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.ws_feed import (  # noqa: E402
    OpenAlgoWsWatchFeed,
    _ws_url,
    ticks_to_quote_snapshots,
)


class _FakeWebSocketApp:
    instances: list["_FakeWebSocketApp"] = []

    def __init__(self, url, on_open=None, on_message=None, on_error=None, on_close=None):
        self.url = url
        self.on_open = on_open
        self.on_message = on_message
        self.on_error = on_error
        self.on_close = on_close
        self.sent: list[str] = []
        self._closed = False
        _FakeWebSocketApp.instances.append(self)

    def send(self, payload: str) -> None:
        self.sent.append(payload)

    def close(self) -> None:
        self._closed = True
        if self.on_close:
            self.on_close(self)

    def run_forever(self, *, ping_interval=None, ping_timeout=None) -> None:
        if self.on_open:
            self.on_open(self)
        while not self._closed:
            time.sleep(0.05)


@pytest.fixture(autouse=True)
def _reset_fake_ws():
    _FakeWebSocketApp.instances.clear()
    yield
    _FakeWebSocketApp.instances.clear()


@pytest.mark.unit
def test_ws_url_defaults(monkeypatch) -> None:
    monkeypatch.delenv("OPENALGO_WS_URL", raising=False)
    monkeypatch.setenv("OPENALGO_WS_HOST", "127.0.0.1")
    monkeypatch.delenv("OPENALGO_WS_PORT", raising=False)
    assert _ws_url() == "ws://127.0.0.1:8765"

    monkeypatch.setenv("OPENALGO_WS_URL", "ws://custom:9999")
    assert _ws_url() == "ws://custom:9999"


@pytest.mark.unit
def test_openalgo_ws_watch_feed_polls_normalized_ticks(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "websocket", MagicMock(WebSocketApp=_FakeWebSocketApp))

    feed = OpenAlgoWsWatchFeed(
        context_generation="2026-07-23T09:15:00+05:30",
        ws_url="ws://127.0.0.1:8765",
        api_key="test-key",
    )
    feed.subscribe(["NIFTY"])

    deadline = time.time() + 2.0
    while time.time() < deadline:
        if _FakeWebSocketApp.instances:
            break
        time.sleep(0.02)
    assert _FakeWebSocketApp.instances, "WS thread did not start"

    ws = _FakeWebSocketApp.instances[0]
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if any(json.loads(row).get("action") == "subscribe" for row in ws.sent):
            break
        time.sleep(0.05)
    assert any(json.loads(row).get("action") == "authenticate" for row in ws.sent)
    assert any(json.loads(row).get("action") == "subscribe" for row in ws.sent)

    message = json.dumps(
        {
            "type": "market_data",
            "data": {
                "symbol": "NIFTY",
                "exchange": "NSE_INDEX",
                "ltp": 24500.5,
                "timestamp": "2026-07-23T10:00:00+00:00",
            },
        }
    )
    ws.on_message(ws, message)

    ticks = feed.poll_ticks()
    assert len(ticks) == 1
    assert ticks[0].symbol == "NIFTY"
    assert ticks[0].ltp == 24500.5
    assert ticks[0].context_generation == "2026-07-23T09:15:00+05:30"
    assert ticks[0].ts == "2026-07-23T10:00:00+00:00"

    quotes = ticks_to_quote_snapshots(ticks)
    assert "NIFTY" in quotes
    assert quotes["NIFTY"].ltp == 24500.5

    feed.close()
    assert feed.poll_ticks() == []


@pytest.mark.unit
def test_openalgo_ws_watch_feed_close_stops_thread(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "websocket", MagicMock(WebSocketApp=_FakeWebSocketApp))

    feed = OpenAlgoWsWatchFeed(ws_url="ws://127.0.0.1:8765", api_key="test-key")
    feed.subscribe(["NIFTY"])

    deadline = time.time() + 2.0
    while time.time() < deadline:
        if feed._thread is not None and feed._thread.is_alive():
            break
        time.sleep(0.02)

    feed.close()
    thread = feed._thread
    assert thread is None or not thread.is_alive()


@pytest.mark.unit
def test_poll_loop_ws_mode_uses_ws_with_rest_fallback(monkeypatch) -> None:
    from nautilus_openalgo_bridge.models import QuoteSnapshot
    from nautilus_openalgo_bridge.runtime import poll_loop

    monkeypatch.setenv("WATCH_FEED_MODE", "ws")

    ws_mock = MagicMock()
    ws_mock.poll_ticks.return_value = []
    rest_mock = MagicMock()
    rest_mock.poll.return_value = {
        "NIFTY": QuoteSnapshot(symbol="NIFTY", exchange="NSE_INDEX", ltp=24000.0),
    }

    with patch.object(poll_loop, "_get_ws_feed", return_value=ws_mock), patch(
        "nautilus_openalgo_bridge.runtime.poll_loop.OpenAlgoQuoteFeed",
        return_value=rest_mock,
    ):
        quotes = poll_loop._poll_quotes(["NIFTY"], context_generation="gen-a", rest_feed=rest_mock)

    ws_mock.subscribe.assert_called_once_with(["NIFTY"])
    rest_mock.poll.assert_called_once_with(["NIFTY"])
    assert quotes["NIFTY"].ltp == 24000.0


@pytest.mark.unit
def test_poll_loop_ws_mode_returns_ws_ticks_without_rest(monkeypatch) -> None:
    from nautilus_openalgo_bridge.runtime import poll_loop
    from trade_integrations.execution.watch_feed import WatchTick

    monkeypatch.setenv("WATCH_FEED_MODE", "ws")

    ws_mock = MagicMock()
    ws_mock.poll_ticks.return_value = [
        WatchTick(
            symbol="NIFTY",
            ltp=24500.0,
            ts="2026-07-23T10:00:00+00:00",
            context_generation="gen-a",
        )
    ]
    rest_mock = MagicMock()

    with patch.object(poll_loop, "_get_ws_feed", return_value=ws_mock):
        quotes = poll_loop._poll_quotes(["NIFTY"], context_generation="gen-a", rest_feed=rest_mock)

    rest_mock.poll.assert_not_called()
    assert quotes["NIFTY"].ltp == 24500.0
