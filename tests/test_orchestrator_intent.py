"""Tests for orchestrator intent parsing and auto-propose fallback."""

from __future__ import annotations

import pytest


def test_extract_nifty_intraday_budget() -> None:
    from trade_integrations.autonomous_agents.orchestrator_intent import build_auto_propose_kwargs

    kwargs = build_auto_propose_kwargs(
        user_message="Create an agent to paper trade NIFTY intraday, ₹50k budget, max loss ₹5k",
        assistant_text="I'll set that up for you.",
        orchestrator_session_id="orch1",
    )
    assert kwargs is not None
    assert kwargs["symbols"] == ["NIFTY"]
    assert kwargs["budget_inr"] == 50_000
    assert kwargs["max_daily_loss_inr"] == 5_000
    assert kwargs.get("execution_market") == "IN"
    assert "intraday" in kwargs["mandate"].lower()


def test_extract_us_symbol() -> None:
    from trade_integrations.autonomous_agents.orchestrator_intent import build_auto_propose_kwargs

    kwargs = build_auto_propose_kwargs(
        user_message="Create autonomous agent for NVDA swing, $10k budget",
        assistant_text="",
        orchestrator_session_id="orch2",
    )
    assert kwargs is not None
    assert kwargs["symbols"] == ["NVDA"]
    assert kwargs["budget_inr"] == 10_000
    assert kwargs.get("execution_market") == "US"


def test_skips_pure_clarifying_turn() -> None:
    from trade_integrations.autonomous_agents.orchestrator_intent import build_auto_propose_kwargs

    kwargs = build_auto_propose_kwargs(
        user_message="Which index do you prefer?",
        assistant_text="NIFTY or BANKNIFTY?",
        orchestrator_session_id="orch3",
    )
    assert kwargs is None


def test_auto_propose_on_hallucinated_proposal_id(agents_hub, monkeypatch) -> None:
    from trade_integrations.autonomous_agents.orchestrator_intent import maybe_auto_propose_after_orchestrator_turn

    monkeypatch.setenv("ORCHESTRATOR_AUTO_PROPOSE", "1")

    result = maybe_auto_propose_after_orchestrator_turn(
        orchestrator_session_id="orch_h",
        user_message="Create NIFTY intraday agent paper ₹20k",
        assistant_text="Proposal ID aap_deadbeef123 is ready for you.",
        tools_called=[],
    )
    assert result is not None
    assert result["status"] == "ready"
    assert result["proposal"]["symbols"] == ["NIFTY"]
    assert result["proposal"].get("auto_proposed") is True


def test_does_not_auto_propose_when_tool_called() -> None:
    from trade_integrations.autonomous_agents.orchestrator_intent import maybe_auto_propose_after_orchestrator_turn

    result = maybe_auto_propose_after_orchestrator_turn(
        orchestrator_session_id="orch4",
        user_message="Create NIFTY agent",
        assistant_text="Done.",
        tools_called=["propose_autonomous_agent"],
    )
    assert result is None


def test_does_not_auto_propose_when_mcp_tool_called() -> None:
    from trade_integrations.autonomous_agents.orchestrator_intent import maybe_auto_propose_after_orchestrator_turn

    result = maybe_auto_propose_after_orchestrator_turn(
        orchestrator_session_id="orch4b",
        user_message="Create NIFTY agent",
        assistant_text="Done.",
        tools_called=["mcp_openalgo_propose_autonomous_agent"],
    )
    assert result is None


def test_reliance_paper_trade_without_create_agent_phrase(agents_hub, monkeypatch) -> None:
    from trade_integrations.autonomous_agents.orchestrator_intent import build_auto_propose_kwargs

    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.orchestrator_intent.search_india_symbols",
        lambda query, limit=5: [{"symbol": "RELIANCE", "exchange": "NSE"}],
    )
    kwargs = build_auto_propose_kwargs(
        user_message="Reliance paper trade ₹50k intraday",
        assistant_text="",
        orchestrator_session_id="orch_rel",
    )
    assert kwargs is not None
    assert kwargs["symbols"] == ["RELIANCE"]
    assert kwargs["budget_inr"] == 50_000
    assert kwargs.get("user_text") == "Reliance paper trade ₹50k intraday"


def test_create_nifty_agent_does_not_extract_agent_ticker() -> None:
    from trade_integrations.autonomous_agents.symbol_extract import extract_orchestrator_symbols

    assert extract_orchestrator_symbols("Create NIFTY paper trade agent ₹20k intraday") == ["NIFTY"]


def test_watch_and_report_sets_observe_mode() -> None:
    from trade_integrations.autonomous_agents.orchestrator_intent import build_auto_propose_kwargs

    kwargs = build_auto_propose_kwargs(
        user_message="Watch NIFTY and report on index moves",
        assistant_text="",
        orchestrator_session_id="orch_obs",
    )
    assert kwargs is not None
    assert kwargs.get("agent_mode") == "observe"
    assert kwargs.get("allowed_instruments") == ["equity"]
    assert "NIFTY" in kwargs["symbols"]
    assert "observe" in kwargs["mandate"].lower() or "report" in kwargs["mandate"].lower()
    assert "options" not in str(kwargs.get("allowed_instruments"))


@pytest.fixture
def agents_hub(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents" / "proposals").mkdir(parents=True)
    monkeypatch.setattr(
        "trade_integrations.context.hub.get_hub_dir",
        lambda: hub,
    )
    return hub
