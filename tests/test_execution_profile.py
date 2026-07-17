"""Tests for unified mandate resolution and execution profiles."""

from __future__ import annotations

from trade_integrations.auto_paper.mandate_config import MandateConfig, resolve_mandate_config
from trade_integrations.auto_paper.mandate_enforcer import (
    MandateViolation,
    assert_widget_allowed,
    widget_instrument_class,
)
from trade_integrations.execution.profile import profile_for_symbol, resolve_profile


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


def test_resolve_profile_us_paper() -> None:
    profile = profile_for_symbol("SPY", mode="paper")
    assert profile.market == "US"
    assert profile.backend == "alpaca"
    assert not profile.uses_openalgo_auto_paper
    assert profile.uses_nautilus_watch
    assert profile.watch_backend == "nautilus_alpaca"
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
