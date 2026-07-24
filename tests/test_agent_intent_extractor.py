"""Tests for unified agent intent extraction and merge."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from trade_integrations.autonomous_agents.intent_extractor import (  # noqa: E402
    extract_agent_intent,
    fast_path_extract_intent_delta,
)
from trade_integrations.autonomous_agents.intent_merge import (  # noqa: E402
    derive_capabilities,
    merge_agent_intent,
)
from trade_integrations.autonomous_agents.intent_schema import (  # noqa: E402
    AgentIntent,
    IntentDelta,
    default_agent_intent,
)


def test_observe_index_watch_fast_path() -> None:
    delta = fast_path_extract_intent_delta("I want to watch NIFTY 50 index")
    assert delta is not None
    assert delta.engagement == "observe"
    assert "index" in (delta.instruments or [])
    assert "NIFTY" in (delta.symbols or [])


def test_watch_every_three_minutes_sets_schedule() -> None:
    delta = fast_path_extract_intent_delta("Watch NIFTY every 3 minutes")
    assert delta is not None
    assert delta.schedules is not None
    assert delta.schedules.get("watch_ms") == 180_000
    cond_kinds = {row.kind for row in (delta.watch_conditions or [])}
    assert "schedule" in cond_kinds


def test_merge_latest_message_overrides_instruments() -> None:
    prior = default_agent_intent(symbols=["NIFTY"])
    prior.engagement = "observe"
    prior.instruments = ["index"]
    prior.capabilities = derive_capabilities(prior)

    delta = IntentDelta(
        engagement="trade",
        instruments=["options"],
        explicit_fields=["engagement", "instruments"],
    )
    merged = merge_agent_intent(prior, delta)
    assert merged.engagement == "trade"
    assert merged.instruments == ["options"]
    assert merged.capabilities.get("payoff") is True


def test_derive_capabilities_observe_has_no_widgets() -> None:
    intent = AgentIntent(engagement="observe", instruments=["index"], symbols=["NIFTY"])
    caps = derive_capabilities(intent)
    assert caps["widgets"] is False
    assert caps["execution"] is False


def test_derive_capabilities_options_trade() -> None:
    intent = AgentIntent(engagement="trade", instruments=["options"], symbols=["NIFTY"])
    caps = derive_capabilities(intent)
    assert caps["payoff"] is True
    assert caps["execution"] is True


def test_futures_instrument_fast_path() -> None:
    delta = fast_path_extract_intent_delta("Create agent for NIFTY futures swing paper trade")
    assert delta is not None
    assert delta.engagement == "trade"
    assert "futures" in (delta.instruments or [])


def test_extract_without_llm_uses_fast_path(monkeypatch) -> None:
    monkeypatch.setenv("INTENT_EXTRACTOR_LLM", "0")
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.intent_extractor._extract_symbols",
        lambda text: ["NIFTY"],
    )
    result = extract_agent_intent(
        "Watch NIFTY and report on index moves",
        prior=default_agent_intent(),
        use_llm=False,
    )
    assert result.source == "fast_path"
    assert result.intent.engagement == "observe"


def test_llm_delta_merge_mocked() -> None:
    prior = default_agent_intent(symbols=["NIFTY"])
    prior.instruments = ["index"]
    prior.engagement = "observe"

    def fake_llm(prompt: str, max_tokens: int) -> str:
        return """
        {
          "explicit_fields": ["engagement", "instruments"],
          "needs_clarification": [],
          "engagement": "trade",
          "instruments": ["options"],
          "symbols": ["NIFTY"]
        }
        """

    result = extract_agent_intent(
        "Actually I want options trading on NIFTY",
        prior=prior,
        use_llm=True,
        llm_caller=fake_llm,
    )
    assert result.source == "llm"
    assert result.intent.engagement == "trade"
    assert result.intent.instruments == ["options"]


def test_auto_propose_uses_intent_observe(agents_hub, monkeypatch) -> None:
    monkeypatch.setenv("INTENT_EXTRACTOR_LLM", "0")
    from trade_integrations.autonomous_agents.orchestrator_intent import build_auto_propose_kwargs

    kwargs = build_auto_propose_kwargs(
        user_message="Create agent to watch NIFTY 50 index every 3 minutes",
        assistant_text="",
        orchestrator_session_id="orch_intent",
    )
    assert kwargs is not None
    assert kwargs.get("agent_mode") == "observe"
    assert kwargs.get("watch_interval_min") == 3
    assert isinstance(kwargs.get("intent"), dict)
    assert kwargs["intent"].get("engagement") == "observe"


@pytest.fixture
def agents_hub(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    (hub / "_data" / "autonomous_agents" / "proposals").mkdir(parents=True)
    monkeypatch.setattr(
        "trade_integrations.context.hub.get_hub_dir",
        lambda: hub,
    )
    return hub
