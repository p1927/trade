"""Parity tests for shared macro forecast path."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trade_integrations.dataflows.index_research.horizon import resolve_horizon
from trade_integrations.dataflows.index_research.macro_forecast import compute_macro_only_return
from trade_integrations.dataflows.index_research.prediction_algorithms.context_builder import build_track_context
from trade_integrations.dataflows.index_research.prediction_algorithms.tracks.macro_only import run_macro_only
from trade_integrations.dataflows.index_research.predictor import ModelArtifact


def _artifact() -> ModelArtifact:
    return ModelArtifact(
        coefficients={"usd_inr": -0.15, "india_vix": -0.08, "oil_brent": -0.05},
        intercept=0.1,
        mae=1.5,
        feature_names=["usd_inr", "india_vix", "oil_brent"],
        trained_at=datetime.now(timezone.utc).isoformat(),
    )


def _factors() -> dict:
    return {
        "usd_inr": 83.0,
        "india_vix": 15.0,
        "oil_brent": 80.0,
        "news_material_7d": 0.0,
    }


@pytest.mark.unit
def test_macro_only_track_matches_shared_function():
    pytest.importorskip("sklearn")
    artifact = _artifact()
    horizon = resolve_horizon(14)
    macro, prov = compute_macro_only_return(
        _factors(),
        horizon,
        artifact,
        scenario_anchor=0.2,
        as_of_day="2026-07-17",
    )
    ctx = build_track_context(
        ticker="NIFTY",
        spot=24500.0,
        horizon_days=14,
        macro_factors=_factors(),
        scenario_anchor=0.2,
        as_of_day="2026-07-17",
    )
    ctx.model_artifact = artifact
    track = run_macro_only(ctx)
    assert track.available
    assert track.expected_return_pct == pytest.approx(macro)
    assert track.provenance.get("raw_macro_delta_pct") == prov.get("raw_macro_delta_pct")


@pytest.mark.unit
def test_parity_macro_only_matches_backtest_row(monkeypatch):
    pytest.importorskip("sklearn")
    import pandas as pd

    from trade_integrations.dataflows.index_research.backtest_runner import run_walk_forward_backtest

    history = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-02", periods=90, freq="B").strftime("%Y-%m-%d"),
            "close": [23000 + i * 10 for i in range(90)],
            "usd_inr": [83.0] * 90,
            "india_vix": [14.0 + (i % 5) * 0.2 for i in range(90)],
            "oil_brent": [80.0] * 90,
        }
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.backtest_runner.load_aligned_factor_history",
        lambda days=180: history,
    )

    report = run_walk_forward_backtest(days=180, horizon_days=14, min_train_rows=30, eval_step=5)
    assert report["status"] == "ok"
    rows = report.get("daily_evaluations") or []
    assert rows
    first = rows[0]
    assert "macro_delta_pct" in first
    assert first["macro_delta_pct"] is not None
