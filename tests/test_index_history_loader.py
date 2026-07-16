"""Unit tests for aligned history enrichment."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.sources.history_loader import (
    enrich_history_features,
)


@pytest.mark.unit
def test_enrich_history_features_adds_technical_and_calendar():
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=25, freq="D").strftime("%Y-%m-%d"),
            "close": 24000 + np.arange(25) * 8,
        }
    )
    enriched = enrich_history_features(frame)
    assert "nifty_return_7d" in enriched.columns
    assert "days_to_monthly_expiry" in enriched.columns
    assert enriched["is_budget_week"].iloc[-1] in {0.0, 1.0}
