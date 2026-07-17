"""Tests for autonomous proposal routing preflight."""

from __future__ import annotations

import pytest

from trade_integrations.autonomous_agents.proposals import propose_autonomous_agent, validate_proposal_routing


@pytest.fixture
def agents_hub(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents" / "proposals").mkdir(parents=True)
    monkeypatch.setattr(
        "trade_integrations.context.hub.get_hub_dir",
        lambda: hub,
    )
    return hub


def test_nifty_proposal_routes_india(agents_hub) -> None:
    result = propose_autonomous_agent(
        symbols=["NIFTY"],
        mandate="Paper trade NIFTY autonomously; intraday (flat by close)",
        budget_inr=20_000,
        max_daily_loss_inr=2_000,
        user_text="Create NIFTY autonomous ₹20k OpenAlgo",
        execution_market="IN",
    )
    assert result["status"] == "ready"
    prop = result["proposal"]
    assert prop["execution_market"] == "IN"
    assert prop["execution_backend"] == "openalgo"
    assert prop["symbols"] == ["NIFTY"]
    assert not prop.get("routing_errors")


def test_niftybees_proposal_routes_india(agents_hub) -> None:
    result = propose_autonomous_agent(symbols=["NIFTYBEES"], mandate="Paper trade NIFTYBEES")
    assert result["status"] == "ready"
    prop = result["proposal"]
    assert prop["execution_market"] == "IN"
    assert prop["execution_backend"] == "openalgo"
    assert prop["watch_spec"]["rules"][0]["exchange"] == "NSE"


def test_nifty_us_override_blocked(agents_hub) -> None:
    result = propose_autonomous_agent(
        symbols=["NIFTY"],
        mandate="Paper trade NIFTY",
        execution_market="US",
    )
    assert result["status"] == "incomplete"
    assert result.get("routing_errors")
    assert result["proposal"]["execution_market"] == "US"


def test_validate_catches_in_backend_mismatch() -> None:
    errors = validate_proposal_routing(
        {
            "execution_market": "IN",
            "execution_backend": "alpaca",
            "symbols": ["NIFTY"],
            "watch_spec": {"rules": [{"symbol": "NIFTY", "exchange": "NSE"}]},
        }
    )
    assert errors


def test_nvda_proposal_routes_us(agents_hub) -> None:
    result = propose_autonomous_agent(
        symbols=["NVDA"],
        mandate="Paper trade NVDA swing",
        user_text="NVDA paper $600",
        execution_market="US",
    )
    assert result["status"] == "ready"
    prop = result["proposal"]
    assert prop["execution_market"] == "US"
    assert prop["execution_backend"] == "alpaca"


def test_reliance_proposal_defaults_equity(agents_hub, monkeypatch) -> None:
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.proposals.build_stack_health",
        lambda: {"vibe_scheduler": "ok"},
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.market_resolve.is_india_listed_symbol",
        lambda sym: str(sym).upper() == "RELIANCE",
    )
    result = propose_autonomous_agent(
        symbols=["RELIANCE"],
        mandate="Paper trade Reliance intraday ₹50k",
        user_text="Reliance paper trade",
        execution_market="IN",
    )
    assert result["status"] == "ready"
    instruments = result["proposal"]["mandate_config"]["allowed_instruments"]
    assert instruments == ["equity"]
