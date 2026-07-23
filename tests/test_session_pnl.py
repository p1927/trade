"""Tests for autonomous-agent session P&L baseline."""

from __future__ import annotations

from unittest.mock import patch

from trade_integrations.autonomous_agents.market_feedback import _session_pnl_block


def test_us_symbol_skips_openalgo_pnl() -> None:
    block = _session_pnl_block({"budget_inr": 20_000}, focus_ticker="SPY")
    assert block.get("pnl_basis") == "alpaca"
    assert "may not apply" in block.get("note", "")


def test_budget_baseline_not_sandbox_cash() -> None:
    session = {"budget_inr": 20_000}
    with patch("trade_integrations.execution.openalgo_client.OpenAlgoClient") as mock_cls:
        mock_cls.return_value.get_funds.return_value = {"availablecash": 9_998_800}
        block = _session_pnl_block(session, focus_ticker="NIFTY")
    assert block["starting_inr"] == 20_000
    assert block["current_inr"] == 9_998_800
    assert block.get("baseline_warning")
