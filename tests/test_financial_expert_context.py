"""Tests for financial expert context store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trade_integrations.dataflows.index_research.external_predictions.financial_expert_context import (
    build_and_save_expert_context,
    build_expert_context,
    expert_context_path,
    load_expert_context,
)


@pytest.fixture
def hub_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def test_build_expert_context_has_core_sections(hub_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.financial_expert_context._load_factor_snapshot",
        lambda: {"india_vix": 14.2, "fii_net_5d": -1200.0},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.financial_expert_context._load_internal_forecast",
        lambda *_a, **_k: {"direction": "bullish", "note": "test"},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.financial_expert_context._india_trading_date",
        lambda: "2026-07-23",
    )

    ctx = build_expert_context(symbol="NIFTY", horizon_days=14, spot=24000.0)
    assert ctx["symbol"] == "NIFTY"
    assert ctx["horizon_days"] == 14
    assert ctx["as_of"] == "2026-07-23"
    assert ctx["spot"] == 24000.0
    assert "NIFTY 50" in ctx["expert_brief"]
    assert ctx["extraction_rules"]
    assert len(ctx["top_factor_movers"]) >= 1


def test_save_and_load_expert_context(hub_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.financial_expert_context._load_factor_snapshot",
        lambda: {},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.external_predictions.financial_expert_context._load_internal_forecast",
        lambda *_a, **_k: None,
    )

    build_and_save_expert_context(symbol="NIFTY", horizon_days=14, spot=24100.0)
    path = expert_context_path("NIFTY")
    assert path.is_file()
    loaded = load_expert_context(symbol="NIFTY")
    assert loaded is not None
    assert loaded.get("spot") == 24100.0
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["horizon_days"] == 14
