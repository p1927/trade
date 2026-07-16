"""Tests for hub channel read-first + write-through."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def _fake_chain(underlying, exchange, *, expiry_date=None, strike_count=None):
    return {
        "underlying": underlying.upper(),
        "underlying_ltp": 24500.0,
        "expiry_date": "16JUL26",
        "chain": [
            {
                "strike": 24500,
                "ce": {"ltp": 100.0, "oi": 500},
                "pe": {"ltp": 95.0, "oi": 600},
            }
        ],
        "source": "mock_vendor",
    }


def test_channel_vendor_fetch_and_write_through(hub_tmp):
    from trade_integrations.hub_capture.channel import channel_stats_today, get_chain
    from trade_integrations.hub_capture.registry import save_registry, update_entity

    save_registry({"entities": []})
    update_entity("NIFTY", {"capture_enabled": True, "factor_groups": ["derivatives"]})

    chain = get_chain("NIFTY", "NFO", _fake_chain, strike_count=5)
    assert chain["underlying"] == "NIFTY"
    assert len(chain["chain"]) == 1

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
