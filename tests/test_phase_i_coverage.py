"""Unit tests for Phase I coverage gates."""

from __future__ import annotations

import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.phase_i_coverage import (
    audit_phase_i_coverage,
    phase_i_keys_for_ridge,
)


@pytest.mark.unit
def test_phase_i_coverage_eligible_when_sufficient():
    rows = 200
    frame = pd.DataFrame(
        {
            "nifty_earnings_yield": [5.0] * rows,
            "equity_risk_premium": [0.5] * rows,
        }
    )
    audit = audit_phase_i_coverage(frame)
    assert "nifty_earnings_yield" in audit["ridge_eligible"]
    assert phase_i_keys_for_ridge(frame) == ("nifty_earnings_yield", "equity_risk_premium")


@pytest.mark.unit
def test_phase_i_coverage_rejects_sparse():
    frame = pd.DataFrame({"nifty_earnings_yield": [5.0, None, None, None, None]})
    audit = audit_phase_i_coverage(frame)
    assert audit["ridge_eligible"] == []
