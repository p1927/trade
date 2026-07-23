"""Tests for autonomous proposal routing preflight."""

from __future__ import annotations

import json

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
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.proposals.build_stack_health",
        lambda: {"vibe_scheduler": "ok"},
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


def test_observe_mandate_proposal_sets_agent_mode(agents_hub) -> None:
    result = propose_autonomous_agent(
        symbols=["NIFTY"],
        mandate="Watch NIFTY and report on index moves",
        user_text="Watch NIFTY and report",
        execution_market="IN",
    )
    assert result["status"] == "ready"
    mc = result["proposal"].get("mandate_config") or {}
    assert mc.get("agent_mode") == "observe"
    assert mc.get("allowed_instruments") == ["equity"]


def test_niftybees_proposal_routes_india(agents_hub, monkeypatch) -> None:
    monkeypatch.setattr(
        "trade_integrations.dataflows.symbol_registry.openalgo_registry.is_symbol_known_for_proposal",
        lambda sym: str(sym).upper() in {"NIFTYBEES", "NIFTY"},
    )
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
    prop = result["proposal"]
    assert prop["execution_market"] == "US"
    assert prop.get("routing_errors")


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


def test_nvda_proposal_routes_us(agents_hub, tmp_path, monkeypatch) -> None:
    runtime = tmp_path / "vibe-trading"
    runtime.mkdir()
    (runtime / "trading-connections.json").write_text(
        json.dumps({"selected_profile": "alpaca-paper-sdk"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBE_TRADING_RUNTIME_ROOT", str(runtime))
    result = propose_autonomous_agent(
        symbols=["NVDA"],
        mandate="Paper trade NVDA swing",
        user_text="NVDA paper $600",
        execution_market="US",
    )
    assert result["status"] == "ready"
    prop = result["proposal"]
    assert prop["execution_market"] == "US"
    assert prop["execution_backend"] == "openalgo"


def test_reliance_proposal_defaults_equity(agents_hub, monkeypatch) -> None:
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.market_resolve.is_india_listed_symbol",
        lambda sym: str(sym).upper() == "RELIANCE",
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.symbol_registry.openalgo_registry.is_symbol_known_for_proposal",
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


def test_nifty_with_us_connector_blocked(agents_hub, tmp_path, monkeypatch) -> None:
    runtime = tmp_path / "vibe-trading"
    runtime.mkdir()
    (runtime / "trading-connections.json").write_text(
        json.dumps({"selected_profile": "alpaca-paper-sdk"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBE_TRADING_RUNTIME_ROOT", str(runtime))
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.proposals.build_stack_health",
        lambda: {"vibe_scheduler": "ok"},
    )
    result = propose_autonomous_agent(
        symbols=["NIFTY"],
        mandate="Paper trade NIFTY",
        budget_inr=20_000,
        max_daily_loss_inr=2_000,
    )
    assert result["status"] == "incomplete"
    prop = result["proposal"]
    assert prop.get("routing_errors")
    assert any(
        "disagrees with connector" in err or "NIFTY" in err for err in prop["routing_errors"]
    )
    assert prop["connector_profile_id"] == "alpaca-paper-sdk"


def test_commit_us_openalgo_connector_allowed(agents_hub, monkeypatch) -> None:
    from trade_integrations.autonomous_agents import proposals
    from trade_integrations.autonomous_agents.store import save_proposal

    proposal_id = "prop_us_openalgo"
    save_proposal(
        {
            "proposal_id": proposal_id,
            "status": "ready",
            "missing_fields": [],
            "routing_errors": [],
            "symbols": ["SPY"],
            "execution_market": "US",
            "execution_backend": "openalgo",
            "connector_profile_id": "openalgo-paper-sdk",
            "name": "SPY bot",
            "mandate": "paper trade US equity",
            "constraints": {
                "mode": "paper",
                "budget_inr": 20000,
                "max_daily_loss_inr": 2000,
                "confidence_threshold": 75,
            },
            "mandate_config": {"allowed_instruments": ["equity"]},
            "watch_spec": {},
            "schedules": {"watch_ms": 420000, "research_ms": 5400000},
            "alert_rules": {},
            "expires_at_ms": 9999999999999,
        }
    )

    class FakeSvc:
        def create_session(self, title="", config=None):
            from types import SimpleNamespace

            return SimpleNamespace(session_id="sess_spy", title=title)

        class Bus:
            def emit(self, *a, **k):
                pass

        event_bus = Bus()

    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.infra_startup.start_required_infra",
        lambda **k: ([], []),
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.proposals.validate_proposal_routing",
        lambda _p: [],
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.proposals.validate_proposal_symbols",
        lambda _s, execution_market="IN": [],
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.proposals._debate_eligibility_for_symbol",
        lambda _sym: (True, None),
    )

    result = proposals.commit_autonomous_agent(
        proposal_id=proposal_id,
        consent_ack=True,
        session_service=FakeSvc(),
    )
    assert result["agent"]["execution_market"] == "US"
    assert result["agent"]["connector_profile_id"] == "openalgo-paper-sdk"
