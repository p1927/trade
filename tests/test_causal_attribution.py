"""Tests for causal day-move attribution."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.index_research.causal_attribution import build_causal_hypotheses


@pytest.mark.unit
def test_build_causal_hypotheses_oil_and_fii():
    drivers = [
        {
            "factor": "oil_brent",
            "label": "Brent crude",
            "prev": 80.0,
            "current": 88.0,
            "change_pct": 10.0,
        },
        {
            "factor": "fii_net_5d",
            "label": "FII net (5d)",
            "prev": -500.0,
            "current": -1200.0,
            "change_pct": -140.0,
        },
    ]
    causes = build_causal_hypotheses(
        factor_drivers=drivers,
        realized_1d_pct=-1.5,
        calendar_events=[{"event": "monthly_expiry", "description": "Monthly F&O expiry in 1 day(s)"}],
        index_headlines=[{"title": "FII sell-off hits Indian markets", "source": "news"}],
    )
    assert causes
    assert any("oil" in (c.get("explanation") or "").lower() or "crude" in (c.get("explanation") or "").lower() for c in causes)
    assert any(c.get("category") == "flows" for c in causes)
    assert causes[0].get("confidence", 0) >= causes[-1].get("confidence", 0)
