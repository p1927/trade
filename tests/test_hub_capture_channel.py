"""Tests for hub channel read-first + write-through."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()

    def _hub_dir() -> Path:
        return hub

    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    monkeypatch.setenv("HUB_NO_LEARN", "0")
    monkeypatch.setenv("OPENALGO_BROKER", "")
    monkeypatch.setenv("STOCK_SIMULATOR_MODE", "")
    monkeypatch.delenv("REDIRECT_URL", raising=False)
    monkeypatch.setattr("trade_integrations.context.hub.get_hub_dir", _hub_dir)
    import trade_integrations.hub_capture.registry as capture_registry
    import trade_integrations.hub_capture.channel as channel_mod

    monkeypatch.setattr(capture_registry, "get_hub_dir", _hub_dir)
    monkeypatch.setattr(channel_mod, "get_hub_dir", _hub_dir)
    monkeypatch.setattr("trade_integrations.stock_simulator.integration.hub_no_learn", lambda: False)
    monkeypatch.setattr("trade_integrations.stock_simulator.integration.is_simulator_active", lambda: False)

    with channel_mod._l1_cache._lock:
        channel_mod._l1_cache._entries.clear()
    stats_path = hub / "_data" / "capture" / "channel_stats.json"
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text("{}", encoding="utf-8")
    return hub


def _fake_chain(underlying, exchange, *, expiry_date=None, strike_count=None):
    return {
        "underlying": underlying.upper(),
        "underlying_ltp": 24500.0,
        "expiry_date": "16JUL26",
        "chain": [
            {
                "strike": 24400,
                "ce": {"ltp": 180.0, "oi": 400},
                "pe": {"ltp": 80.0, "oi": 500},
            },
            {
                "strike": 24500,
                "ce": {"ltp": 100.0, "oi": 500},
                "pe": {"ltp": 95.0, "oi": 600},
            },
            {
                "strike": 24600,
                "ce": {"ltp": 60.0, "oi": 450},
                "pe": {"ltp": 130.0, "oi": 550},
            },
        ],
        "total_call_oi": 1350,
        "total_put_oi": 1650,
        "pcr": round(1650 / 1350, 4),
        "source": "mock_vendor",
    }


def test_channel_vendor_fetch_and_write_through(hub_tmp):
    from trade_integrations.hub_capture.channel import channel_stats_today, get_chain
    from trade_integrations.hub_capture.registry import save_registry, update_entity

    save_registry({"entities": []})
    update_entity("NIFTY", {"capture_enabled": True, "factor_groups": ["derivatives"]})

    chain = get_chain("NIFTY", "NFO", _fake_chain, strike_count=5)
    assert chain["underlying"] == "NIFTY"
    assert len(chain["chain"]) == 3

    stats = channel_stats_today()
    assert stats.get("vendor_fetches", 0) >= 1

    capture_dir = hub_tmp / "_data" / "capture" / "nifty" / "derivatives_chain"
    assert capture_dir.is_dir()
    assert any(capture_dir.glob("*.parquet"))


def test_channel_hub_hit_on_fresh_latest(hub_tmp, monkeypatch):
    from trade_integrations.hub_capture.channel import get_chain
    from trade_integrations.hub_capture.registry import save_registry, update_entity

    save_registry({"entities": []})
    update_entity("NIFTY", {"capture_enabled": True, "factor_groups": ["derivatives"]})

    latest = hub_tmp / "NIFTY" / "options_research" / "latest.json"
    latest.parent.mkdir(parents=True)
    latest.write_text(
        json.dumps(
            {
                "underlying": "NIFTY",
                "as_of": datetime.now(timezone.utc).isoformat(),
                "chain_snapshot": _fake_chain("NIFTY", "NFO"),
            }
        ),
        encoding="utf-8",
    )

    calls = {"n": 0}

    def counting_fetch(*args, **kwargs):
        calls["n"] += 1
        return _fake_chain(*args, **kwargs)

    monkeypatch.setenv("TRADINGAGENTS_OPTIONS_CACHE_MINUTES", "30")
    chain = get_chain("NIFTY", "NFO", counting_fetch, strike_count=5)
    assert chain.get("channel") == "hub_latest"
    assert calls["n"] == 0


def test_capture_disabled_skips_write_through(hub_tmp):
    from trade_integrations.hub_capture.channel import get_chain
    from trade_integrations.hub_capture.registry import save_registry, update_entity

    save_registry({"entities": []})
    update_entity("NIFTY", {"capture_enabled": False, "factor_groups": ["derivatives"]})

    get_chain("NIFTY", "NFO", _fake_chain, strike_count=5)
    capture_dir = hub_tmp / "_data" / "capture" / "nifty" / "derivatives_chain"
    assert not capture_dir.exists() or not any(capture_dir.glob("*.parquet"))


def test_watch_policy_uses_short_ttl(monkeypatch):
    from trade_integrations.openalgo.freshness import FreshnessPolicy, ttl_seconds

    monkeypatch.setenv("OPENALGO_WATCH_QUOTE_TTL_SECONDS", "5")
    assert ttl_seconds(FreshnessPolicy.WATCH) == 5
    assert ttl_seconds(FreshnessPolicy.LIVE) == 0


def test_l1_dedupe_within_ttl():
    from trade_integrations.openalgo.freshness import L1Cache

    cache = L1Cache()
    cache.set("NIFTY:quotes", {"ltp": 1}, ttl_seconds=5)
    assert cache.get("NIFTY:quotes")["ltp"] == 1


def test_read_captured_pcr(hub_tmp):
    from trade_integrations.hub_capture.registry import save_registry, update_entity
    from trade_integrations.hub_capture.writers import record_chain_snapshot
    from trade_integrations.hub_capture.channel import read_captured_pcr

    save_registry({"entities": []})
    update_entity("NIFTY", {"capture_enabled": True, "factor_groups": ["derivatives"]})
    record_chain_snapshot("NIFTY", _fake_chain("NIFTY", "NFO"))
    pcr = read_captured_pcr("NIFTY")
    assert pcr is not None
    assert pcr > 0


def test_read_captured_pcr_skips_nan(hub_tmp):
    from trade_integrations.hub_capture.registry import save_registry, update_entity
    from trade_integrations.hub_capture.writers import _append_rows
    from trade_integrations.hub_capture.channel import read_captured_pcr

    save_registry({"entities": []})
    update_entity("NIFTY", {"capture_enabled": True, "factor_groups": ["derivatives"]})

    _append_rows(
        "NIFTY",
        "derivatives_chain",
        [
            {
                "entity_id": "NIFTY",
                "captured_at": "2026-07-17T10:00:00+00:00",
                "series": "pcr_summary",
                "nifty_pcr": 1.25,
                "source": "openalgo",
                "vendor": "openalgo",
            },
            {
                "entity_id": "NIFTY",
                "captured_at": "2026-07-17T11:00:00+00:00",
                "series": "pcr_summary",
                "nifty_pcr": float("nan"),
                "source": "openalgo",
                "vendor": "openalgo",
            },
        ],
        dedupe_keys=("captured_at", "series", "source"),
    )

    assert read_captured_pcr("NIFTY", day="2026-07-17") == 1.25


def test_watch_stale_latest_skips_capture_fallback(hub_tmp, monkeypatch):
    from trade_integrations.hub_capture.channel import get_chain
    from trade_integrations.hub_capture.registry import save_registry, update_entity
    from trade_integrations.hub_capture.writers import record_chain_snapshot
    from trade_integrations.openalgo.freshness import FreshnessPolicy

    save_registry({"entities": []})
    update_entity("NIFTY", {"capture_enabled": True, "factor_groups": ["derivatives"]})
    record_chain_snapshot("NIFTY", _fake_chain("NIFTY", "NFO"))

    latest = hub_tmp / "NIFTY" / "options_research" / "latest.json"
    latest.parent.mkdir(parents=True)
    stale_as_of = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    latest.write_text(
        json.dumps(
            {
                "underlying": "NIFTY",
                "as_of": stale_as_of,
                "chain_snapshot": _fake_chain("NIFTY", "NFO"),
            }
        ),
        encoding="utf-8",
    )

    calls = {"n": 0}

    def counting_fetch(*args, **kwargs):
        calls["n"] += 1
        return _fake_chain(*args, **kwargs)

    monkeypatch.setenv("OPENALGO_WATCH_QUOTE_TTL_SECONDS", "5")
    chain = get_chain(
        "NIFTY",
        "NFO",
        counting_fetch,
        strike_count=5,
        policy=FreshnessPolicy.WATCH,
    )
    assert calls["n"] == 1
    assert chain.get("source") == "mock_vendor"


def test_watch_get_chain_second_call_within_ttl_skips_fetch(hub_tmp, monkeypatch):
    from trade_integrations.hub_capture import channel as channel_mod
    from trade_integrations.hub_capture.channel import channel_stats_today, get_chain
    from trade_integrations.hub_capture.registry import save_registry, update_entity
    from trade_integrations.openalgo.freshness import FreshnessPolicy

    save_registry({"entities": []})
    update_entity("NIFTY", {"capture_enabled": True, "factor_groups": ["derivatives"]})

    with channel_mod._l1_cache._lock:
        channel_mod._l1_cache._entries.clear()

    calls = {"n": 0}

    def counting_fetch(*args, **kwargs):
        calls["n"] += 1
        return _fake_chain(*args, **kwargs)

    monkeypatch.setenv("OPENALGO_WATCH_QUOTE_TTL_SECONDS", "30")

    first = get_chain(
        "NIFTY",
        "NFO",
        counting_fetch,
        strike_count=5,
        policy=FreshnessPolicy.WATCH,
    )
    assert calls["n"] == 1
    assert first["underlying"] == "NIFTY"

    second = get_chain(
        "NIFTY",
        "NFO",
        counting_fetch,
        strike_count=5,
        policy=FreshnessPolicy.WATCH,
    )
    assert calls["n"] == 1
    assert second["underlying"] == "NIFTY"

    stats = channel_stats_today()
    assert stats.get("l1_hits", 0) >= 1
    assert stats.get("vendor_fetches", 0) == 1


def test_watch_get_multi_quotes_second_call_within_ttl_skips_fetch(hub_tmp, monkeypatch):
    from trade_integrations.hub_capture import channel as channel_mod
    from trade_integrations.hub_capture.channel import channel_stats_today, get_multi_quotes
    from trade_integrations.openalgo.freshness import FreshnessPolicy

    with channel_mod._l1_cache._lock:
        channel_mod._l1_cache._entries.clear()

    calls = {"n": 0}

    def fake_multi_quotes(requests):
        calls["n"] += 1
        return {
            "quotes": [
                {
                    "symbol": row["symbol"],
                    "exchange": row["exchange"],
                    "ltp": 24500.0,
                    "source": "mock_vendor",
                }
                for row in requests
            ]
        }

    requests = [
        {"symbol": "NIFTY", "exchange": "NSE_INDEX"},
        {"symbol": "RELIANCE", "exchange": "NSE"},
    ]

    monkeypatch.setenv("OPENALGO_WATCH_QUOTE_TTL_SECONDS", "30")

    first = get_multi_quotes(requests, fake_multi_quotes, policy=FreshnessPolicy.WATCH)
    assert calls["n"] == 1
    assert first["NIFTY@NSE_INDEX"]["ltp"] == 24500.0
    assert first["RELIANCE@NSE"]["ltp"] == 24500.0

    second = get_multi_quotes(requests, fake_multi_quotes, policy=FreshnessPolicy.WATCH)
    assert calls["n"] == 1
    assert second["NIFTY@NSE_INDEX"]["ltp"] == 24500.0

    stats = channel_stats_today()
    assert stats.get("l1_hits", 0) >= 1
    assert stats.get("vendor_fetches", 0) == 1


def test_get_history_l1_dedupe_within_ttl(monkeypatch):
    from trade_integrations.hub_capture import channel as channel_mod
    from trade_integrations.hub_capture.channel import get_history
    from trade_integrations.openalgo.freshness import FreshnessPolicy
    import pandas as pd

    with channel_mod._l1_cache._lock:
        channel_mod._l1_cache._entries.clear()

    calls = {"n": 0}

    def fake_history(symbol, start, end, *, interval="D"):
        calls["n"] += 1
        return pd.DataFrame(
            {
                "Date": pd.to_datetime([start]),
                "Open": [100.0],
                "High": [101.0],
                "Low": [99.0],
                "Close": [100.5],
                "Volume": [1000],
            }
        )

    monkeypatch.setenv("TRADINGAGENTS_OPTIONS_CACHE_MINUTES", "30")

    first = get_history(
        "NIFTY",
        "2026-01-01",
        "2026-01-31",
        "D",
        fake_history,
        policy=FreshnessPolicy.NORMAL,
    )
    assert calls["n"] == 1
    assert len(first) == 1

    second = get_history(
        "NIFTY",
        "2026-01-01",
        "2026-01-31",
        "D",
        fake_history,
        policy=FreshnessPolicy.NORMAL,
    )
    assert calls["n"] == 1
    assert len(second) == 1
