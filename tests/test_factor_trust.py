"""Tests for factor trust enrichment."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from trade_integrations.knowledge.factor_trust import (
    enrich_factor_notes_with_trust,
    load_factor_trust_map,
)


@pytest.mark.unit
def test_load_factor_trust_map_from_diagnostics():
    fake_report = {
        "baseline_direction_hit_rate": 0.44,
        "factor_correlations": [
            {"factor": "fii_net_5d", "correlation": 0.12},
            {"factor": "india_vix", "correlation": -0.09},
        ],
        "block_ablation": [
            {
                "block": "flows",
                "factors": ["fii_net_5d"],
                "delta_pp": 1.2,
            }
        ],
    }
    with patch(
        "trade_integrations.dataflows.index_research.equation_diagnostics.load_diagnostics_report",
        return_value=fake_report,
    ):
        trust = load_factor_trust_map("NIFTY")

    assert "fii_net_5d" in trust
    assert trust["fii_net_5d"].get("trust_snippet")
    assert "correlation" in trust["fii_net_5d"]["trust_snippet"]


@pytest.mark.unit
def test_enrich_factor_notes_with_trust():
    factors = {"fii_net_5d": 1000.0, "india_vix": 15.0}
    with patch(
        "trade_integrations.knowledge.factor_trust.load_factor_trust_map",
        return_value={
            "fii_net_5d": {"trust_snippet": "moderate positive correlation (+0.10)"},
        },
    ):
        notes = enrich_factor_notes_with_trust(factors, ticker="NIFTY", limit=4)

    assert "fii_net_5d" in notes
    assert "correlation" in notes["fii_net_5d"]
