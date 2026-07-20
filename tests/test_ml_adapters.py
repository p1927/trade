"""Tests for ml_adapters — macro lags and stationary transforms."""

from __future__ import annotations

import pandas as pd

from trade_integrations.dataflows.index_research.ml_adapters.macro_lag_features import (
    enrich_macro_lag_columns,
)
from trade_integrations.dataflows.index_research.ml_adapters.stationary_frame import to_stationary_pct_change


def test_macro_lags_are_backward_only():
    frame = pd.DataFrame(
        {
            "date": [f"2026-01-{i:02d}" for i in range(1, 11)],
            "repo_rate": [6.0] * 5 + [6.25] * 5,
        }
    )
    out = enrich_macro_lag_columns(frame)
    assert "repo_rate_lag_1w" in out.columns
    assert pd.isna(out["repo_rate_lag_1w"].iloc[0])
    assert out["repo_rate_lag_1w"].iloc[9] == 6.0


def test_stationary_pct_change_no_future_leak():
    frame = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "close": [100.0, 101.0, 102.0],
            "repo_rate": [6.0, 6.0, 6.25],
        }
    )
    out = to_stationary_pct_change(frame, periods=1)
    assert "close_pct_5d" in out.columns
    assert pd.isna(out["close_pct_5d"].iloc[0])
