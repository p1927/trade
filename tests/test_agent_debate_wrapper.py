"""Regression tests for TradingAgents debate hub-context wrapper."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
TRADINGAGENTS = ROOT / "tradingagents"
for path in (INTEGRATIONS, TRADINGAGENTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


@pytest.mark.unit
def test_run_agent_debate_hub_wrapper_accepts_asset_type_keyword():
    """propagate() calls _run_graph(..., asset_type=...) — wrapper must accept that kwarg."""
    final_state = {
        "investment_debate_state": {"bull_history": "bull", "bear_history": "bear"},
        "risk_debate_state": {},
        "trade_date": "2026-07-16",
    }
    graph_calls: list[dict] = []

    class FakePropagator:
        def create_initial_state(self, *args, **kwargs):
            return {"past_context": kwargs.get("past_context", "")}

    class FakeGraph:
        def __init__(self, *args, **kwargs):
            self.propagator = FakePropagator()
            self._run_graph = self._original_run_graph

        def _original_run_graph(self, company_name, trade_date, asset_type="stock"):
            graph_calls.append(
                {"company": company_name, "date": trade_date, "asset_type": asset_type}
            )
            return final_state, "HOLD"

        def propagate(self, company_name, trade_date, asset_type="stock"):
            return self._run_graph(company_name, trade_date, asset_type=asset_type)

    with (
        patch("tradingagents.graph.trading_graph.TradingAgentsGraph", FakeGraph),
        patch(
            "trade_integrations.bridge.hub_context.build_tradingagents_options_context",
            return_value="--- F&O trade plan ---\nstub\n",
        ),
        patch(
            "trade_integrations.bridge.hub_context.build_tradingagents_index_context",
            return_value="",
        ),
        patch("trade_integrations.bridge.agent_debate._build_graph_config", return_value={}),
        patch("trade_integrations.context.hub.save_agent_debate") as save_debate,
        patch(
            "trade_integrations.bridge.hub_context.infer_debate_asset_type",
            return_value="options",
        ),
    ):
        from trade_integrations.bridge.agent_debate import run_agent_debate

        payload = run_agent_debate("NIFTY", asset_type="options")

    assert graph_calls == [
        {"company": "^NSEI", "date": payload["trade_date"], "asset_type": "options"}
    ]
    assert payload["ticker"] == "NIFTY"
    assert payload["asset_type"] == "options"
    save_debate.assert_called_once()
