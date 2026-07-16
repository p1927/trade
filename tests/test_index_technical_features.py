"""Unit tests for Nifty technical feature engineering."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.technical_features import (
    compute_return_pct,
    compute_rsi,
    enrich_nifty_technical_columns,
    latest_technical_factor_dict,
)


@pytest.mark.unit
def test_compute_rsi_bounds():
    close = pd.Series([100, 101, 102, 101, 100, 99, 98, 99, 100, 101] * 3, dtype=float)
    rsi = compute_rsi(close, period=14)
    assert rsi.min() >= 0.0
    assert rsi.max() <= 100.0


@pytest.mark.unit
def test_compute_return_pct_seven_day():
    close = pd.Series([100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 107.0])
    ret = compute_return_pct(close, 7)
    assert ret.iloc[-1] == pytest.approx(7.0)


@pytest.mark.unit
def test_enrich_nifty_technical_columns_adds_expected_keys():
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=30, freq="D").strftime("%Y-%m-%d"),
            "close": 24000 + np.arange(30) * 10,
            "high": 24010 + np.arange(30) * 10,
            "low": 23990 + np.arange(30) * 10,
        }
    )
    enriched = enrich_nifty_technical_columns(frame)
    for key in (
        "nifty_return_7d",
        "nifty_return_14d",
        "nifty_rsi_14",
        "nifty_realized_vol_20d",
        "nifty_ma20_distance_pct",
        "nifty_macd_line",
        "nifty_stoch_k",
        "nifty_bb_width_pct",
    ):
        assert key in enriched.columns


@pytest.mark.unit
def test_latest_technical_factor_dict_returns_floats():
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=30, freq="D").strftime("%Y-%m-%d"),
            "close": 24000 + np.arange(30) * 5,
        }
    )
    latest = latest_technical_factor_dict(frame)
    assert latest
    assert all(isinstance(value, float) for value in latest.values())
