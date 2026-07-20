"""Tests for bottom-up coefficient calibration."""

from __future__ import annotations

import numpy as np
import pytest

from trade_integrations.dataflows.index_research.calibrate_bottom_up import (
    BottomUpCoeffs,
    apply_bottom_up_coeffs,
    calibrate_bottom_up_coeffs,
)


@pytest.mark.unit
def test_calibrate_bottom_up_returns_defaults_when_sparse():
    coeffs = calibrate_bottom_up_coeffs(window_days=5)
    assert coeffs.calibrated is False
    assert coeffs.sentiment_beta > 0


@pytest.mark.unit
def test_apply_bottom_up_coeffs_respects_cap():
    coeffs = BottomUpCoeffs(sentiment_beta=50.0, momentum_scale=2.0, calibrated=True)
    value = apply_bottom_up_coeffs(5.0, 10.0, coeffs)
    assert abs(value) <= 3.0 + 0.01


@pytest.mark.unit
def test_calibrate_bottom_up_fits_when_archives_available(monkeypatch):
    from datetime import date, timedelta

    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(20, 0, -1)]

    def fake_bottom_up(day: str, *, horizon_days: int) -> float | None:
        key = day[:10]
        if key not in days:
            return None
        idx = days.index(key)
        return float(idx) * 0.05

    class _Sig:
        def __init__(self, i: int) -> None:
            self.sentiment_score = 0.1 * i
            self.momentum_7d_pct = 0.2 * i
            self.weight = 1.0

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.calibrate_bottom_up.bottom_up_return_from_archives",
        fake_bottom_up,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.calibrate_bottom_up.load_constituent_signals_for_day",
        lambda day: [_Sig(days.index(day[:10]))] if day[:10] in days else [],
    )
    import pandas as pd

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.sources.history_loader.load_nifty_history",
        lambda **kw: pd.DataFrame({"date": days, "close": np.linspace(100, 120, len(days))}),
    )

    coeffs = calibrate_bottom_up_coeffs(window_days=20)
    assert coeffs.calibrated is True
    assert coeffs.sample_count >= 12
