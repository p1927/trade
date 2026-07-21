"""Unit tests for walk-forward index backtest runner."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.backtest_runner import (
    audit_factor_coverage,
    run_walk_forward_backtest,
)


def _synthetic_aligned(rows: int = 80) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    dates = pd.date_range("2025-10-01", periods=rows, freq="B")
    t = np.linspace(0, 6 * np.pi, rows)
    close = 23000 + 800 * np.sin(t) + np.cumsum(rng.normal(0, 30, rows))
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": close,
            "oil_brent": 80 + rng.normal(0, 2, rows),
            "india_vix": 14 + rng.normal(0, 0.8, rows),
            "sp500": 5500 + np.cumsum(rng.normal(0, 15, rows)),
            "us_10y": 4.2 + rng.normal(0, 0.05, rows),
            "nifty_return_14d": rng.normal(0, 1, rows),
            "is_results_season": [1.0 if d.month in {1, 4, 7, 10} else 0.0 for d in dates],
        }
    )


@pytest.mark.unit
def test_audit_factor_coverage_reports_columns():
    frame = _synthetic_aligned(30)
    audit = audit_factor_coverage(frame)
    factors = {row["factor"] for row in audit}
    assert "oil_brent" in factors
    assert all(row["coverage_pct"] == 100.0 for row in audit if row["factor"] == "oil_brent")


@pytest.mark.unit
def test_walk_forward_backtest_produces_eval_rows(monkeypatch):
    pytest.importorskip("sklearn")
    history = _synthetic_aligned(100)
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.backtest_runner.load_aligned_factor_history",
        lambda days=180: history,
    )

    report = run_walk_forward_backtest(
        days=180,
        horizon_days=14,
        min_train_rows=30,
        eval_step=3,
        eval_protocol="purged_expanding",
    )

    assert report["status"] == "ok"
    assert report["eval_protocol"] == "purged_expanding"
    assert report["eval_count"] >= 3
    assert report["metrics"]["mae_pct"] is not None
    assert report["daily_evaluations"]
    row = report["daily_evaluations"][0]
    assert "date" in row
    assert "factor_drivers" in row
    assert "calendar_events" in row
