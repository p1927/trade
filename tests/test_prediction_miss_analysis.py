"""Unit tests for prediction miss root-cause analysis."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.prediction_miss_analysis import (
    categorize_miss,
    compute_factor_delta_horizon,
    enrich_eval_row_horizon,
    resolve_maturity_date,
    run_miss_analysis,
)


def _synthetic_frame(rows: int = 40) -> pd.DataFrame:
    dates = pd.date_range("2025-10-01", periods=rows, freq="B")
    t = np.linspace(0, 4 * np.pi, rows)
    close = 23000 + 500 * np.sin(t)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": close,
            "oil_brent": 80 + np.arange(rows) * 0.1,
            "india_vix": 14 + np.sin(t),
            "nifty_return_7d": np.linspace(-2, 3, rows),
            "constituent_momentum_7d": np.linspace(-1, 2, rows),
            "fii_net_5d": np.linspace(-5000, 2000, rows),
        }
    )


@pytest.mark.unit
def test_resolve_maturity_date_finds_trading_day():
    trading = ["2025-10-01", "2025-10-02", "2025-10-03", "2025-10-06"]
    maturity = resolve_maturity_date("2025-10-01", 14, trading)
    assert maturity is not None
    assert maturity <= (date.fromisoformat("2025-10-01") + timedelta(days=14)).isoformat()


@pytest.mark.unit
def test_compute_factor_delta_horizon_orders_by_abs_delta():
    t0 = {"oil_brent": 80.0, "india_vix": 14.0}
    t1 = {"oil_brent": 85.0, "india_vix": 14.1}
    deltas = compute_factor_delta_horizon(t0, t1, limit=2)
    assert len(deltas) == 2
    assert deltas[0]["factor"] == "oil_brent"
    assert deltas[0]["delta"] == pytest.approx(5.0)


@pytest.mark.unit
def test_categorize_miss_neutral_boundary():
    cat = categorize_miss(
        predicted_return_pct=0.05,
        actual_return_pct=-0.2,
        factor_delta_horizon=[],
        headlines_at_maturity=[],
        missing_factors_t0=[],
        missing_factors_t1=[],
    )
    assert cat == "neutral_boundary"


@pytest.mark.unit
def test_categorize_miss_cap_saturation():
    cat = categorize_miss(
        predicted_return_pct=-5.0,
        actual_return_pct=4.0,
        macro_raw_pct=-8.5,
        macro_delta_pct=-5.0,
        factor_delta_horizon=[
            {"factor": "nifty_return_7d", "delta": 3.0},
            {"factor": "constituent_momentum_7d", "delta": 2.0},
        ],
        headlines_at_maturity=[],
        missing_factors_t0=[],
        missing_factors_t1=[],
    )
    assert cat == "cap_saturation"


@pytest.mark.unit
def test_enrich_eval_row_horizon_adds_maturity_and_miss_category(monkeypatch):
    frame = _synthetic_frame(30)
    trading = frame["date"].astype(str).tolist()
    eval_row = {
        "date": trading[10],
        "predicted_return_pct": -2.0,
        "actual_forward_return_pct": 3.0,
        "direction_correct": False,
        "macro_delta_pct": -2.0,
        "macro_raw_pct": -2.0,
    }
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_miss_analysis._fetch_index_headlines",
        lambda day, limit=5: [],
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_miss_analysis.collect_constituent_headlines_for_day",
        lambda day, limit=4: [],
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_miss_analysis._constituent_movers_horizon",
        lambda *a, **k: [],
    )

    enriched = enrich_eval_row_horizon(
        eval_row,
        frame,
        ["oil_brent", "india_vix", "nifty_return_7d", "constituent_momentum_7d", "fii_net_5d"],
        horizon_days=14,
        trading_dates=trading,
    )
    assert enriched.get("maturity_date")
    assert enriched.get("factor_delta_horizon")
    assert enriched.get("miss_category")
    assert enriched.get("learning_note")


@pytest.mark.unit
def test_run_miss_analysis_from_synthetic_backtest(monkeypatch):
    pytest.importorskip("sklearn")
    frame = _synthetic_frame(70)
    backtest = {
        "status": "ok",
        "horizon_days": 14,
        "metrics": {"direction_hit_rate": 0.5, "mae_pct": 2.0},
        "daily_evaluations": [
            {
                "date": str(frame["date"].iloc[50])[:10],
                "predicted_return_pct": -1.0,
                "actual_forward_return_pct": 2.0,
                "direction_correct": False,
                "macro_delta_pct": -1.0,
                "macro_raw_pct": -1.0,
            }
        ],
    }
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_miss_analysis.load_aligned_factor_history",
        lambda days=365: frame,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_miss_analysis._fetch_index_headlines",
        lambda day, limit=5: [],
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_miss_analysis.collect_constituent_headlines_for_day",
        lambda day, limit=4: [],
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.prediction_miss_analysis._constituent_movers_horizon",
        lambda *a, **k: [],
    )

    report = run_miss_analysis(days=180, backtest_report=backtest)
    assert report["status"] == "ok"
    assert report["summary"]["miss_count"] == 1
    assert report["misses"][0]["miss_category"]
