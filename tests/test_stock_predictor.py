"""Tests for stock quantitative predictor."""

from __future__ import annotations

import pandas as pd
import pytest

from trade_integrations.dataflows.stock_research.predictor import predict_stock


def _synthetic_history(n: int = 60, start: float = 1000.0) -> pd.DataFrame:
    import numpy as np

    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    noise = np.random.default_rng(42).normal(0, 5, n)
    close = start + np.cumsum(noise)
    return pd.DataFrame({"date": dates, "close": close})


@pytest.mark.unit
class TestStockPredictor:
    def test_predict_with_history_returns_range(self):
        hist = _synthetic_history()
        spot = float(hist["close"].iloc[-1])
        out = predict_stock("RELIANCE", spot, horizon_days=1, history=hist)
        assert out["range"]["low"] is not None
        assert out["range"]["high"] is not None
        assert out["range"]["low"] < spot < out["range"]["high"]
        assert out["model_confidence"] > 0
        assert out["view"] in ("bullish", "bearish", "neutral")

    def test_zero_spot_neutral(self):
        out = predict_stock("RELIANCE", 0, horizon_days=14)
        assert out["model_confidence"] == 0.0

    def test_fallback_when_no_history(self):
        out = predict_stock("RELIANCE", 1296.0, horizon_days=1, history=pd.DataFrame())
        assert out["source"] == "fallback_band"
        assert out["range"]["low"] < 1296.0 < out["range"]["high"]
