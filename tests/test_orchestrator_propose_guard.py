"""Tests for orchestrator propose guard and incomplete auto-propose SSE."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_assistant_claims_proposal_ready_detects_card_phrases() -> None:
    from trade_integrations.autonomous_agents.orchestrator_intent import assistant_claims_proposal_ready

    assert assistant_claims_proposal_ready("Please confirm the proposal card above.")
    assert assistant_claims_proposal_ready("The proposal is ready for you.")
    assert assistant_claims_proposal_ready("Proposal ID aap_deadbeef1234567890 is ready.")
    assert not assistant_claims_proposal_ready("Which index do you prefer?")


def test_nifty_paper_trade_defaults_to_options(agents_hub) -> None:
    from trade_integrations.autonomous_agents.orchestrator_intent import build_auto_propose_kwargs

    kwargs = build_auto_propose_kwargs(
        user_message="Create NIFTY paper trade agent ₹20k intraday",
        assistant_text="I'll set that up.",
        orchestrator_session_id="orch_nifty",
    )
    assert kwargs is not None
    assert kwargs["symbols"] == ["NIFTY"]
    assert kwargs.get("allowed_instruments") == ["options"]


def test_auto_propose_nifty_without_options_keyword_is_ready(agents_hub, monkeypatch) -> None:
    from trade_integrations.autonomous_agents.orchestrator_intent import (
        maybe_auto_propose_after_orchestrator_turn,
    )

    monkeypatch.setenv("ORCHESTRATOR_AUTO_PROPOSE", "1")
    result = maybe_auto_propose_after_orchestrator_turn(
        orchestrator_session_id="orch_ready",
        user_message="Create autonomous agent for NIFTY paper trade ₹20k",
        assistant_text="Confirm the card above when ready.",
        tools_called=[],
    )
    assert result is not None
    assert result["status"] == "ready"
    assert result["proposal"]["symbols"] == ["NIFTY"]
    assert result["proposal"]["mandate_config"]["allowed_instruments"] == ["options"]


def test_emit_autonomous_proposal_emits_incomplete() -> None:
    from src.trade.orchestrator_propose_guard import emit_autonomous_proposal

    bus = MagicMock()
    proposal = {
        "proposal_id": "aap_test123456789012345678901234567890",
        "status": "incomplete",
        "missing_fields": ["allowed_instruments"],
    }
    assert emit_autonomous_proposal(bus, "sess1", {"proposal": proposal}) is True
    bus.emit.assert_called_once_with("sess1", "autonomous_agent.proposal", proposal)


@pytest.mark.asyncio
async def test_propose_guard_auto_proposes_on_prose_only(agents_hub, monkeypatch) -> None:
    from src.trade.orchestrator_propose_guard import maybe_enforce_orchestrator_propose

    monkeypatch.setenv("ORCHESTRATOR_AUTO_PROPOSE", "1")
    monkeypatch.setenv("ORCHESTRATOR_PROPOSE_GUARD_ENABLED", "1")

    svc = MagicMock()
    svc.event_bus = MagicMock()
    svc.send_message = AsyncMock()

    session_config = {"session_kind": "autonomous_orchestrator", "orchestrator": True}
    handled = await maybe_enforce_orchestrator_propose(
        svc,
        "orch_guard",
        user_message="Create NIFTY intraday options paper agent, ₹20k",
        assistant_text="Proposal is ready — confirm the card.",
        tools_called=[],
        session_config=session_config,
    )
    assert handled is True
    svc.event_bus.emit.assert_called()
    svc.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_propose_guard_retries_when_auto_propose_blocked(monkeypatch) -> None:
    from src.trade.orchestrator_propose_guard import maybe_enforce_orchestrator_propose

    monkeypatch.setenv("ORCHESTRATOR_AUTO_PROPOSE", "0")
    monkeypatch.setenv("ORCHESTRATOR_PROPOSE_GUARD_ENABLED", "1")

    svc = MagicMock()
    svc.event_bus = MagicMock()
    svc.send_message = AsyncMock()

    session_config = {"session_kind": "autonomous_orchestrator", "orchestrator": True}
    handled = await maybe_enforce_orchestrator_propose(
        svc,
        "orch_retry",
        user_message="Create NIFTY intraday options paper agent, ₹20k",
        assistant_text="Proposal is ready — confirm the card.",
        tools_called=[],
        session_config=session_config,
    )
    assert handled is True
    svc.send_message.assert_called_once()


@pytest.fixture
def agents_hub(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents" / "proposals").mkdir(parents=True)
    monkeypatch.setattr(
        "trade_integrations.context.hub.get_hub_dir",
        lambda: hub,
    )
    return hub
