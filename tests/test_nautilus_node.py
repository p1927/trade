"""Tests for Nautilus TradingNode bootstrap (requires .venv-nautilus)."""

from __future__ import annotations

import pytest

pytest.importorskip("nautilus_trader")

from nautilus_openalgo_bridge.node import build_trading_node_config  # noqa: E402


def test_build_trading_node_config_has_openalgo_client_and_actors():
    cfg = build_trading_node_config(agent_id="aa_test")
    assert "OPENALGO" in cfg.data_clients
    assert len(cfg.actors) == 3
    assert str(cfg.trader_id).startswith("TRADE-WATCH-")


def test_build_trading_node_config_redis_flush_on_start_false(monkeypatch):
    monkeypatch.setenv("NAUTILUS_REDIS_URL", "redis://127.0.0.1:6379/0")
    cfg = build_trading_node_config(agent_id="aa_test")
    assert cfg.cache.database is not None
    assert cfg.cache.flush_on_start is False
