"""Tests for unified mandate resolution and execution profiles."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trade_integrations.auto_paper.mandate_config import MandateConfig, resolve_mandate_config
from trade_integrations.auto_paper.mandate_enforcer import (
    MandateViolation,
    assert_widget_allowed,
    widget_instrument_class,
)
from trade_integrations.execution.profile import (
    profile_for_symbol,
    resolve_profile,
    resolve_profile_from_context,
)
from trade_integrations.openalgo.market_context import MarketContext


def _in_market_context(*, venue: str, analyze_mode: bool) -> MarketContext:
    return MarketContext(
        context_generation="2026-07-23T09:15:00+05:30",
        data_broker="zerodha",
        execution_venue=venue,
        analyze_mode=analyze_mode,
        market_region="IN",
        positions_authority="sandbox.db" if venue == "sandbox" else "broker",
        quotes_source="broker_plugin",
        simulator={"active": False},
        capabilities=("options", "equity"),
    )


def test_resolve_profile_from_context_in_sandbox_paper() -> None:
    agent = {
        "symbols": ["NIFTY"],
        "execution_market": "IN",
        "constraints": {"mode": "live"},
        "mandate": "Paper trade NIFTY options",
    }
    profile = resolve_profile_from_context(
        agent=agent,
        market_context=_in_market_context(venue="sandbox", analyze_mode=True),
    )
    assert profile.market == "IN"
    assert profile.mode == "paper"
    assert profile.uses_openalgo_auto_paper
    assert profile.prompt_fragment_id == "in_options_paper"


def test_resolve_profile_from_context_in_broker_live() -> None:
    agent = {
        "symbols": ["NIFTY"],
        "execution_market": "IN",
        "constraints": {"mode": "paper"},
        "mandate": "Trade NIFTY options",
    }
    profile = resolve_profile_from_context(
        agent=agent,
        market_context=_in_market_context(venue="broker", analyze_mode=False),
    )
    assert profile.market == "IN"
    assert profile.mode == "live"
    assert not profile.uses_openalgo_auto_paper
    assert profile.prompt_fragment_id == "in_options_live"


def test_resolve_profile_from_context_alpaca_synthetic_paper() -> None:
    agent = {
        "symbols": ["SPY"],
        "execution_market": "US",
        "constraints": {"mode": "live"},
        "mandate": "Paper trade US SPY via Alpaca shares.",
        "mandate_config": {"allowed_instruments": ["equity"]},
    }
    ctx = MarketContext(
        context_generation="alpaca-alpaca-paper-sdk-synthetic",
        data_broker="alpaca",
        execution_venue="paper-api.alpaca.markets",
        analyze_mode=True,
        market_region="US",
        positions_authority="alpaca",
        quotes_source="alpaca",
        simulator={"active": False},
        capabilities=("equity",),
    )
    profile = resolve_profile_from_context(agent=agent, market_context=ctx)
    assert profile.market == "US"
    assert profile.mode == "paper"
    assert profile.backend == "openalgo"
    assert profile.prompt_fragment_id == "us_equity_paper"


@pytest.mark.unit
def test_resolve_profile_from_context_prefers_market_region() -> None:
    agent = {
        "symbols": ["SPY"],
        "execution_market": "US",
        "constraints": {"mode": "paper"},
        "mandate": "US equity",
    }
    ctx = _in_market_context(venue="sandbox", analyze_mode=True)
    profile = resolve_profile_from_context(agent=agent, market_context=ctx)
    assert profile.market == "IN"
    assert profile.backend == "openalgo"


@pytest.mark.unit
def test_resolve_profile_uses_market_context_when_adapter_succeeds(monkeypatch) -> None:
    agent = {
        "symbols": ["NIFTY"],
        "execution_market": "IN",
        "constraints": {"mode": "live"},
        "mandate": "Paper trade NIFTY options",
    }
    mock_adapter = MagicMock()
    mock_adapter.market_context.return_value = _in_market_context(
        venue="sandbox",
        analyze_mode=True,
    )
    monkeypatch.setattr(
        "trade_integrations.execution.trading_port.adapter_for_agent",
        lambda agent: mock_adapter,
    )

    profile = resolve_profile(agent=agent)

    assert profile.mode == "paper"
    mock_adapter.market_context.assert_called_once_with()


@pytest.mark.unit
def test_resolve_profile_falls_back_on_context_fetch_failure(monkeypatch) -> None:
    agent = {
        "symbols": ["NIFTY"],
        "execution_market": "IN",
        "constraints": {"mode": "paper"},
        "mandate": "Paper trade NIFTY options",
    }

    def _raise(_agent: dict) -> MagicMock:
        raise RuntimeError("OpenAlgo unreachable")

    monkeypatch.setattr(
        "trade_integrations.execution.trading_port.adapter_for_agent",
        _raise,
    )

    profile = resolve_profile(agent=agent)

    assert profile.mode == "paper"
    assert profile.uses_openalgo_auto_paper


def test_resolve_mandate_config_spy_equity() -> None:
    cfg = resolve_mandate_config(symbols=["SPY"], mandate_text="Paper trade US SPY via Alpaca shares.")
    assert cfg.allowed_instruments == ["equity"]
    assert cfg.watch_spec["rules"][0]["exchange"] == "US"


def test_resolve_mandate_config_nifty_options() -> None:
    cfg = resolve_mandate_config(
        symbols=["NIFTY"],
        mandate_text="Paper trade NIFTY options autonomously.",
    )
    assert "options" in cfg.allowed_instruments
    assert cfg.watch_spec["rules"][0]["symbol"] == "NIFTY"


def test_proposal_and_agent_mandate_parity() -> None:
    draft_cfg = resolve_mandate_config(
        symbols=["SPY"],
        mandate_text="Alpaca paper",
        execution_market="US",
        confidence_threshold=60,
    )
    agent_cfg = resolve_mandate_config(
        symbols=["SPY"],
        stored=draft_cfg.to_dict(),
        execution_market="US",
    )
    assert agent_cfg.allowed_instruments == draft_cfg.allowed_instruments


def test_resolve_profile_us_paper(monkeypatch) -> None:
    agent = {
        "symbols": ["SPY"],
        "execution_market": "US",
        "constraints": {"mode": "paper"},
        "mandate": "Paper trade US SPY via OpenAlgo Alpaca plugin.",
        "mandate_config": {"allowed_instruments": ["equity"]},
    }
    mock_adapter = MagicMock()
    mock_adapter.market_context.return_value = MarketContext(
        context_generation="alpaca-alpaca-paper-sdk-synthetic",
        data_broker="alpaca",
        execution_venue="paper-api.alpaca.markets",
        analyze_mode=True,
        market_region="US",
        positions_authority="alpaca.paper",
        quotes_source="broker_plugin",
        simulator={"active": False},
        capabilities=("equity",),
    )
    monkeypatch.setattr(
        "trade_integrations.execution.trading_port.adapter_for_agent",
        lambda _agent: mock_adapter,
    )
    profile = resolve_profile(agent=agent)
    assert profile.market == "US"
    assert profile.backend == "openalgo"
    assert profile.uses_openalgo_auto_paper
    assert profile.uses_nautilus_watch
    assert profile.uses_nautilus_handoff
    assert profile.watch_backend == "nautilus_openalgo"
    assert profile.prompt_fragment_id == "us_equity_paper"


@pytest.mark.unit
def test_resolve_profile_us_paper_from_market_context() -> None:
    agent = {
        "symbols": ["SPY"],
        "execution_market": "US",
        "constraints": {"mode": "paper"},
        "mandate": "Paper trade US SPY via OpenAlgo Alpaca plugin.",
        "mandate_config": {"allowed_instruments": ["equity"]},
    }
    ctx = MarketContext(
        context_generation="2026-07-23T09:15:00+05:30",
        data_broker="alpaca",
        execution_venue="paper-api.alpaca.markets",
        analyze_mode=True,
        market_region="US",
        positions_authority="alpaca.paper",
        quotes_source="broker_plugin",
        simulator={"active": False},
        capabilities=("equity", "basket"),
    )
    profile = resolve_profile_from_context(agent=agent, market_context=ctx)
    assert profile.market == "US"
    assert profile.backend == "openalgo"
    assert profile.uses_openalgo_auto_paper
    assert profile.uses_nautilus_handoff
    assert profile.watch_backend == "nautilus_openalgo"
    assert profile.prompt_fragment_id == "us_equity_paper"


def test_resolve_profile_in_options() -> None:
    agent = {
        "symbols": ["NIFTY"],
        "execution_market": "IN",
        "constraints": {"mode": "paper"},
        "mandate": "Paper trade NIFTY options",
    }
    profile = resolve_profile(agent=agent)
    assert profile.market == "IN"
    assert profile.uses_openalgo_auto_paper
    assert profile.uses_nautilus_handoff


def test_widget_instrument_enforcement() -> None:
    mandate = MandateConfig(allowed_instruments=["options"])
    options_widget = {"widget_id": "tp_abc", "implementation_steps": []}
    assert_widget_allowed(options_widget, mandate)
    equity_widget = {"widget_id": "ts_xyz", "implementation_steps": []}
    try:
        assert_widget_allowed(equity_widget, mandate)
        raised = False
    except MandateViolation:
        raised = True
    assert raised
    assert widget_instrument_class(equity_widget) == "equity"


def test_us_options_profile_fragment() -> None:
    cfg = resolve_mandate_config(
        symbols=["SPY"],
        execution_market="US",
        stored={"allowed_instruments": ["options"], "confidence_threshold": 60},
    )
    agent = {
        "symbols": ["SPY"],
        "execution_market": "US",
        "constraints": {"mode": "paper"},
        "mandate_config": cfg.to_dict(),
    }
    profile = resolve_profile(agent=agent)
    assert profile.prompt_fragment_id == "us_options_paper"
