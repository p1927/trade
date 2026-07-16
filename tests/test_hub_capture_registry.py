"""Tests for hub capture registry and gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def test_default_registry_seeds_nifty(hub_tmp):
    from trade_integrations.hub_capture.registry import default_registry, load_registry, registry_path

    reg = load_registry(create=True)
    assert registry_path().is_file()
    assert reg["entities"][0]["id"] == "NIFTY"
    assert reg["entities"][0]["capture_enabled"] is True
    assert "derivatives" in reg["entities"][0]["factor_groups"]


def test_factor_tier_classification():
    from trade_integrations.hub_capture.registry import factor_tier

    assert factor_tier("nifty_pcr") == "capture"
    assert factor_tier("oil_brent") == "scalar"
    assert factor_tier("index_spot_tick") == "ephemeral"


def test_build_factor_tree():
    from trade_integrations.hub_capture.registry import build_factor_tree

    tree = build_factor_tree()
    assert len(tree) >= 1
    all_factors = [f for group in tree for f in group["factors"]]
    assert any(f["key"] == "nifty_pcr" and f["tier"] == "capture" for f in all_factors)


def test_should_capture_respects_registry(hub_tmp):
    from trade_integrations.hub_capture.gate import should_capture
    from trade_integrations.hub_capture.registry import save_registry, update_entity

    save_registry({"entities": []})
    update_entity("NIFTY", {"capture_enabled": True, "factor_groups": ["derivatives"]})
    assert should_capture("NIFTY", "derivatives_chain") is True
    update_entity("NIFTY", {"capture_enabled": False})
    assert should_capture("NIFTY", "derivatives_chain") is False


def test_record_chain_snapshot_writes_parquet(hub_tmp):
    from trade_integrations.hub_capture.registry import save_registry, update_entity
    from trade_integrations.hub_capture.writers import record_chain_snapshot

    save_registry({"entities": []})
    update_entity("NIFTY", {"capture_enabled": True, "factor_groups": ["derivatives"]})
    chain = {
        "underlying": "NIFTY",
        "underlying_ltp": 24500.0,
        "expiry_date": "16-JUL-2026",
        "chain": [
            {
                "strike": 24500,
                "ce": {"ltp": 120.5, "oi": 1000},
                "pe": {"ltp": 115.0, "oi": 1200},
            }
        ],
        "source": "openalgo",
    }
    result = record_chain_snapshot("NIFTY", chain, source="openalgo", vendor="openalgo")
    assert result["status"] == "ok"
    capture_dir = hub_tmp / "_data" / "capture" / "nifty" / "derivatives_chain"
    assert capture_dir.is_dir()
    assert any(capture_dir.glob("*.parquet"))


def test_update_entity_persists(hub_tmp):
    from trade_integrations.hub_capture.registry import load_registry, registry_path, update_entity

    update_entity("NIFTY", {"capture_enabled": False, "retention_days": {"derivatives": 180}})
    reg = load_registry(create=False)
    entity = reg["entities"][0]
    assert entity["capture_enabled"] is False
    assert entity["retention_days"]["derivatives"] == 180
    payload = json.loads(registry_path().read_text(encoding="utf-8"))
    assert payload["version"] == 1
