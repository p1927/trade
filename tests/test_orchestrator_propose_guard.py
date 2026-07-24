"""Tests for orchestrator propose guard and incomplete auto-propose SSE."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "vibetrading" / "agent"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))


def test_assistant_claims_proposal_ready_detects_card_phrases() -> None:
    from trade_integrations.autonomous_agents.orchestrator_intent import assistant_claims_proposal_ready

    assert assistant_claims_proposal_ready("Please confirm the proposal card above.")
    assert assistant_claims_proposal_ready("The proposal is ready for you.")
    assert assistant_claims_proposal_ready("Proposal ID aap_deadbeef1234567890 is ready.")
    assert not assistant_claims_proposal_ready("Which index do you prefer?")


def test_nifty_paper_trade_requires_instrument_clarification(agents_hub) -> None:
    from trade_integrations.autonomous_agents.orchestrator_intent import build_auto_propose_kwargs

    kwargs = build_auto_propose_kwargs(
        user_message="Create NIFTY paper trade agent ₹20k intraday",
        assistant_text="I'll set that up.",
        orchestrator_session_id="orch_nifty",
    )
    assert kwargs is not None
    assert kwargs["symbols"] == ["NIFTY"]
    assert "allowed_instruments" not in kwargs or kwargs.get("allowed_instruments") != ["options"]
    assert "instruments" in (kwargs.get("intent_needs_clarification") or [])


def test_auto_propose_nifty_without_options_keyword_is_incomplete(agents_hub, monkeypatch) -> None:
    from trade_integrations.autonomous_agents.orchestrator_intent import (
        maybe_auto_propose_after_orchestrator_turn,
    )

    monkeypatch.setenv("ORCHESTRATOR_AUTO_PROPOSE", "1")
    monkeypatch.setenv("INTENT_EXTRACTOR_LLM", "0")
    result = maybe_auto_propose_after_orchestrator_turn(
        orchestrator_session_id="orch_ready",
        user_message="Create autonomous agent for NIFTY paper trade ₹20k",
        assistant_text="Confirm the card above when ready.",
        tools_called=[],
    )
    assert result is not None
    assert result["status"] == "incomplete"
    assert result["proposal"]["symbols"] == ["NIFTY"]
    assert "allowed_instruments" in (result["proposal"].get("missing_fields") or [])


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
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.proposals.build_stack_health",
        lambda: {"vibe_scheduler": "ok"},
    )
    monkeypatch.setattr(
        "trade_integrations.execution.profile.resolve_profile",
        lambda **kwargs: type(
            "P",
            (),
            {
                "backend": "openalgo",
                "market": "IN",
                "allowed_instruments": ("equity",),
                "mode": "paper",
                "prompt_fragment_id": "in_equity_paper",
            },
        )(),
    )
    return hub
