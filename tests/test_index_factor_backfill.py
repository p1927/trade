"""Unit tests for factor history backfill."""

from __future__ import annotations

import os
import tempfile

import numpy as np
import pandas as pd
import pytest


@pytest.mark.unit
def test_backfill_writes_technical_and_calendar_factors(monkeypatch):
    dates = pd.date_range("2026-01-01", periods=35, freq="B")
    nifty = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": 24000 + np.arange(len(dates)) * 5,
        }
    )

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("TRADE_STACK_HUB_DIR", tmp)
        monkeypatch.setattr(
            "trade_integrations.dataflows.index_research.factor_backfill.load_nifty_history",
            lambda days=365, start=None: nifty,
        )
        monkeypatch.setattr(
            "trade_integrations.dataflows.index_research.factor_backfill._fetch_yfinance_close_series",
            lambda sym, start, end: pd.Series(dtype=float),
        )
        monkeypatch.setattr(
            "trade_integrations.dataflows.index_research.factor_backfill._fetch_fred_dgs10_series",
            lambda start, end: pd.Series(dtype=float),
        )
        monkeypatch.setattr(
            "trade_integrations.dataflows.index_research.factor_backfill.repo_rate_on",
            lambda day: 6.5,
        )
        monkeypatch.setattr(
            "trade_integrations.dataflows.index_research.factor_backfill_enrichment.enrich_factor_history",
            lambda days=365: {"days_enriched": 0},
        )

        from trade_integrations.dataflows.index_research.factor_backfill import (
            backfill_factor_history,
        )
        from trade_integrations.dataflows.index_research.factor_store import (
            load_factor_history,
        )

        summary = backfill_factor_history(days=60)
        assert summary["days_written"] == len(nifty)

        long_df = load_factor_history(nifty["date"].iloc[0], nifty["date"].iloc[-1])
        factors = set(long_df["factor"].unique())
        assert "nifty_return_7d" in factors
        assert "nifty_rsi_14" in factors
        assert "days_to_monthly_expiry" in factors
        assert "repo_rate" in factors
