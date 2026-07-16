"""Tests for index trade widget payload."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trade_integrations.dataflows.index_research.models import IndexResearchDoc
from trade_integrations.dataflows.index_research.widget_payload import (
    build_index_trade_widget_from_doc,
)


@pytest.mark.unit
def test_index_widget_includes_factor_charts():
    doc = IndexResearchDoc(
        ticker="NIFTY",
        as_of=datetime.now(timezone.utc),
        spot=24500.0,
        horizon={"name": "B", "days": 14},
        prediction={"view": "bullish", "expected_return_pct": 1.0, "range": {"low": 24200, "high": 24800}},
        factor_explanation={
            "method": "marginal",
            "contributors": [{"factor": "usd_inr", "label": "USD/INR", "contribution_pct": 0.3}],
        },
        factor_sensitivity=[
            {
                "factor": "usd_inr",
                "label": "USD/INR",
                "points": [{"factor_delta_pct": -5, "index_level": 24400}, {"factor_delta_pct": 5, "index_level": 24600}],
            }
        ],
        event_impact_curves=[
            {"event": "rbi_policy", "outcome": "dovish_hold", "index_level": 24700, "curve": []},
        ],
    )

    widget = build_index_trade_widget_from_doc(doc)

    assert widget["asset_type"] == "index"
    assert widget["type"] == "trade_plan.widget"
    assert widget["factor_explanation"]["contributors"]
    assert widget["factor_sensitivity"]
    assert widget["event_impact_curves"]
