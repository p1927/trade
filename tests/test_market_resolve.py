"""Tests for market_resolve layer."""

from __future__ import annotations

import json

from trade_integrations.autonomous_agents.market_resolve import (
    canonicalize_autonomous_symbol,
    resolve_execution_market,
    resolve_proposal_symbols,
)
from trade_integrations.autonomous_agents.proposals import validate_proposal_routing


def test_niftybees_routes_india() -> None:
    result = resolve_execution_market("NIFTYBEES")
    assert result.market == "IN"


def test_inr_hint_overrides_unknown_symbol() -> None:
    result = resolve_execution_market("FOO", user_text="paper trade ₹20k OpenAlgo NIFTY style")
    assert result.market == "IN"


def test_nifty_not_replaced_by_niftybees_when_user_said_nifty() -> None:
    canon = canonicalize_autonomous_symbol("NIFTYBEES", user_text="Create NIFTY autonomous")
    assert canon.canonical_symbol == "NIFTY"
    assert canon.warnings


def test_proposal_symbols_canonicalize_niftybees_to_nifty() -> None:
    symbols, resolution, warnings = resolve_proposal_symbols(
        ["NIFTYBEES"],
        user_text="Paper trade NIFTY intraday ₹20k",
    )
    assert symbols == ["NIFTY"]
    assert resolution.market == "IN"
    assert warnings


def test_spy_routes_us(monkeypatch) -> None:
    monkeypatch.setattr(
        "trade_integrations.execution.connector_context.load_active_connector_context",
        lambda **k: None,
    )
    result = resolve_execution_market("SPY")
    assert result.market == "US"


def test_explicit_in_invalid_for_spy_warns() -> None:
    result = resolve_execution_market("SPY", market_hint="IN")
    assert result.market == "IN"
    assert any("invalid" in w.lower() for w in result.warnings)


def test_resolve_execution_market_uses_runtime_connector(tmp_path, monkeypatch) -> None:
    runtime = tmp_path / "vibe-trading"
    runtime.mkdir()
    (runtime / "trading-connections.json").write_text(
        json.dumps({"selected_profile": "alpaca-paper-sdk"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBE_TRADING_RUNTIME_ROOT", str(runtime))
    result = resolve_execution_market("SPY")
    assert result.market == "US"
    assert result.confidence == "connector"


def test_validate_catches_execution_market_connector_mismatch() -> None:
    errors = validate_proposal_routing(
        {
            "execution_market": "IN",
            "execution_backend": "openalgo",
            "connector_profile_id": "alpaca-paper-sdk",
            "symbols": ["NIFTY"],
            "watch_spec": {"rules": [{"symbol": "NIFTY", "exchange": "NSE"}]},
        }
    )
    assert any("disagrees with connector" in err for err in errors)
