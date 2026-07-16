"""Tests for trade widget presentability gates."""

from __future__ import annotations

from trade_integrations.trade_widgets.presentability import (
    is_widget_presentable,
    presentation_mode_for,
)


def test_options_not_presentable_without_legs() -> None:
    w = {
        "asset_type": "options",
        "plan_status": "partial",
        "ranked_strategies": [],
        "charges": {},
    }
    assert not is_widget_presentable(w, "options_strategy")


def test_options_presentable_when_ready() -> None:
    w = {
        "asset_type": "options",
        "plan_status": "ready",
        "ranked_strategies": [{"name": "IC"}],
        "strategy_variants": {"iron_condor": {"legs": [{"symbol": "X"}]}},
        "payoff": {"samples": [{"spot": 100, "pnl": 0}]},
        "charges": {"net_debit_credit": 500},
    }
    assert is_widget_presentable(w, "options_strategy")
    assert presentation_mode_for(w, "options_strategy") == "options_strategy"


def test_index_presentable_with_factors() -> None:
    w = {
        "asset_type": "index",
        "plan_status": "ready",
        "factor_explanation": {"contributors": [{}]},
    }
    assert is_widget_presentable(w, "index_outlook")
    assert presentation_mode_for(w, "index_outlook") == "index_outlook"


def test_none_intent_never_presentable() -> None:
    w = {"asset_type": "options", "plan_status": "ready", "ranked_strategies": [{"name": "IC"}]}
    assert not is_widget_presentable(w, "none")
