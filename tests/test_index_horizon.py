"""Unit tests for index horizon router, history loader, and factor matrix."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.factor_matrix import build_factor_matrix
from trade_integrations.dataflows.index_research.factor_store import save_daily_factors
from trade_integrations.dataflows.index_research.horizon import resolve_horizon


@pytest.mark.unit
def test_resolve_horizon_profiles():
    profile_a = resolve_horizon(2)
    assert profile_a.name == "A"
    assert profile_a.days == 2
    assert profile_a.feature_window == 5
    assert profile_a.poly_degree == 1

    profile_b = resolve_horizon(14)
    assert profile_b.name == "B"
    assert profile_b.feature_window == 14
    assert profile_b.poly_degree == 1

    profile_c = resolve_horizon(45)
    assert profile_c.name == "C"
    assert profile_c.feature_window == 60
    assert profile_c.poly_degree == 2


@pytest.mark.unit
def test_resolve_horizon_env_default(monkeypatch):
    monkeypatch.delenv("INDEX_RESEARCH_HORIZON_DAYS", raising=False)
    assert resolve_horizon(None).days == 14

    monkeypatch.setenv("INDEX_RESEARCH_HORIZON_DAYS", "7")
    assert resolve_horizon(None).name == "B"
    assert resolve_horizon(None).days == 7


@pytest.mark.unit
def test_load_nifty_history_mocked(monkeypatch):
    dates = pd.date_range("2026-01-01", periods=5, freq="D")
    fake_hist = pd.DataFrame(
        {
            "Close": [24000.0, 24100.0, 24200.0, 24300.0, 24400.0],
        },
        index=dates,
    )

    class _FakeTicker:
        def __init__(self, symbol: str):
            self.symbol = symbol

        def history(self, start, end):
            del start, end
            return fake_hist

    fake_yf = type("yf", (), {"Ticker": _FakeTicker})
    monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf())

    from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history

    df = load_nifty_history(days=30)
    assert len(df) == 5
    assert list(df.columns) == ["date", "close"]
    assert df.iloc[-1]["close"] == pytest.approx(24400.0)


@pytest.mark.unit
def test_load_aligned_factor_history(tmp_path, monkeypatch):
    dates = pd.date_range("2026-07-10", periods=3, freq="D")
    fake_hist = pd.DataFrame({"Close": [24000.0, 24100.0, 24200.0]}, index=dates)

    class _FakeTicker:
        def __init__(self, symbol: str):
            self.symbol = symbol

        def history(self, start, end):
            del start, end
            return fake_hist

    fake_yf = type("yf", (), {"Ticker": _FakeTicker})
    monkeypatch.setitem(__import__("sys").modules, "yfinance", fake_yf())
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    save_daily_factors("2026-07-10", [{"factor": "usd_inr", "value": 83.0}])
    save_daily_factors("2026-07-11", [{"factor": "usd_inr", "value": 83.2}])

    from trade_integrations.dataflows.index_research.sources.history_loader import (
        load_aligned_factor_history,
    )

    aligned = load_aligned_factor_history(days=30)
    assert "close" in aligned.columns
    assert "usd_inr" in aligned.columns
    assert aligned["usd_inr"].iloc[0] == pytest.approx(83.0)


@pytest.mark.unit
def test_build_factor_matrix_synthetic():
    rng = np.random.default_rng(42)
    rows = 30
    horizon = resolve_horizon(14)
    dates = pd.date_range("2026-01-01", periods=rows, freq="D")

    usd_inr = rng.normal(83.0, 0.2, rows)
    oil = rng.normal(80.0, 1.0, rows)
    close = 24000 + np.cumsum(rng.normal(0, 20, rows))
    target_shift = np.roll((np.roll(close, -horizon.days) - close) / close * 100, 0)
    usd_inr = usd_inr + target_shift * 0.05

    history = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": close,
            "usd_inr": usd_inr,
            "oil_brent": oil,
        }
    )

    X, y, names = build_factor_matrix(history, horizon)
    assert X.shape[0] == len(y)
    assert X.shape[0] > 0
    assert len(names) >= 1
    assert "usd_inr" in names or "oil_brent" in names
