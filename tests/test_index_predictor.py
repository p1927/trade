"""Unit tests for hybrid Nifty predictor."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.horizon import resolve_horizon
from trade_integrations.dataflows.index_research.models import ConstituentSignal
from trade_integrations.dataflows.index_research.predictor import (
    ModelArtifact,
    load_stored_model_artifact,
    predict_nifty,
    store_model_artifact,
    train_macro_ridge,
)


def _synthetic_history(rows: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    horizon = resolve_horizon(14)
    dates = pd.date_range("2026-01-01", periods=rows, freq="D")
    close = 24000 + np.cumsum(rng.normal(0, 25, rows))
    usd_inr = 83 + rng.normal(0, 0.15, rows)
    oil = 80 + rng.normal(0, 0.8, rows)
    fii = rng.normal(500, 120, rows)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": close,
            "usd_inr": usd_inr,
            "oil_brent": oil,
            "fii_net_5d": fii,
        }
    ), horizon


@pytest.mark.unit
def test_train_macro_ridge_synthetic():
    pytest.importorskip("sklearn")
    history, horizon = _synthetic_history()
    artifact = train_macro_ridge(history, horizon)

    assert artifact.feature_names
    assert artifact.mae >= 0
    assert artifact.horizon_name == "B"
    assert isinstance(artifact.coefficients, dict)


@pytest.mark.unit
def test_predict_nifty_hybrid(monkeypatch):
    pytest.importorskip("sklearn")
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.attribution._today",
        lambda: date(2026, 7, 16),
    )

    history, horizon = _synthetic_history()
    artifact = train_macro_ridge(history, horizon)

    signals = [
        ConstituentSignal(symbol="RELIANCE", weight=0.4, sentiment_score=0.2),
        ConstituentSignal(symbol="TCS", weight=0.3, sentiment_score=-0.1),
    ]
    macro = {"usd_inr": 83.2, "oil_brent": 82.0, "fii_net_5d": 600.0}

    result = predict_nifty(
        spot=24500.0,
        signals=signals,
        macro_factors=macro,
        horizon=horizon,
        model_artifact=artifact,
    )

    assert result["view"] in {"bullish", "bearish", "neutral"}
    assert "expected_return_pct" in result
    assert "range" in result
    assert result["range"]["low"] < result["range"]["high"]
    assert "coefficients" in result["equation"]
    assert result["bottom_up_return_pct"] != 0.0


@pytest.mark.unit
def test_store_and_load_model_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))

    artifact = ModelArtifact(
        coefficients={"usd_inr": 0.42, "oil_brent": -0.18},
        intercept=0.05,
        mae=1.1,
        r2_walk_forward=0.38,
        poly_degree=2,
        feature_names=["usd_inr", "oil_brent"],
        trained_at="2026-07-16T12:00:00Z",
        horizon_name="B",
    )
    store_model_artifact(artifact)

    loaded = load_stored_model_artifact()
    assert loaded is not None
    assert loaded.coefficients["usd_inr"] == pytest.approx(0.42)
    assert loaded.mae == pytest.approx(1.1)
    assert loaded.feature_names == ["usd_inr", "oil_brent"]

    model_path = tmp_path / "_data" / "index_factors" / "model" / "latest.json"
    assert model_path.is_file()
