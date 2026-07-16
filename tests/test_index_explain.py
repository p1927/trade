"""Unit tests for index factor explainability (SHAP / marginal + sensitivity)."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.index_research.explain import (
    build_event_impact_curves,
    build_factor_explanation_bundle,
    build_factor_sensitivity,
    explain_macro_factors,
)
from trade_integrations.dataflows.index_research.horizon import resolve_horizon
from trade_integrations.dataflows.index_research.predictor import ModelArtifact


def _artifact() -> ModelArtifact:
    return ModelArtifact(
        coefficients={"usd_inr": 0.05, "oil_brent": -0.03, "india_vix": -0.02},
        intercept=0.1,
        mae=1.2,
        feature_names=["usd_inr", "oil_brent", "india_vix"],
        poly_degree=1,
        horizon_name="B",
    )


def _macro() -> dict:
    return {"usd_inr": 83.2, "oil_brent": 82.0, "india_vix": 14.5}


@pytest.mark.unit
def test_explain_macro_factors_marginal_contributions():
    horizon = resolve_horizon(14)
    result = explain_macro_factors(
        _macro(),
        horizon=horizon,
        spot=24500.0,
        bottom_up_return_pct=0.5,
        artifact=_artifact(),
    )

    assert result["method"] in {"marginal", "shap"}
    assert result["contributors"]
    total_contrib = sum(row["contribution_pct"] for row in result["contributors"])
    assert total_contrib == pytest.approx(result["macro_delta_pct"], abs=0.01)
    for row in result["contributors"]:
        assert "share_of_macro" in row
        assert "contribution_index_pts" in row


@pytest.mark.unit
def test_factor_sensitivity_curve_points():
    horizon = resolve_horizon(14)
    curves = build_factor_sensitivity(
        _macro(),
        horizon=horizon,
        spot=24500.0,
        bottom_up_return_pct=0.5,
        artifact=_artifact(),
        sweep_pct=(-5, 5, 5),
        max_factors=2,
    )

    assert len(curves) >= 1
    curve = curves[0]
    assert curve["points"]
    assert len(curve["points"]) == 3  # -5, 0, 5
    levels = [p["index_level"] for p in curve["points"]]
    assert max(levels) >= min(levels)


@pytest.mark.unit
def test_event_impact_curves_match_scenarios():
    horizon = resolve_horizon(14)
    scenarios = [
        {"event": "rbi_policy", "outcome": "dovish_hold", "probability": 0.4},
        {"event": "earnings_cluster", "outcome": "positive_surprises", "probability": 0.35},
    ]
    curves = build_event_impact_curves(
        _macro(),
        scenarios,
        horizon=horizon,
        spot=24500.0,
        bottom_up_return_pct=0.5,
        artifact=_artifact(),
    )

    assert curves
    for curve in curves:
        assert curve.get("index_level")
        assert curve.get("curve")
        assert len(curve["curve"]) >= 2


@pytest.mark.unit
def test_explanation_bundle_rescales_after_reconciled_headline():
    """Contributors must sum to reconciled macro delta, not raw Ridge cap."""
    horizon = resolve_horizon(14)
    artifact = ModelArtifact(
        coefficients={"usd_inr": 2.0, "oil_brent": -1.0},
        intercept=4.5,
        mae=1.2,
        feature_names=["usd_inr", "oil_brent"],
        poly_degree=1,
        horizon_name="B",
    )
    macro = {"usd_inr": 83.2, "oil_brent": 82.0}
    bottom_up = 0.5
    ridge_only = explain_macro_factors(
        macro,
        horizon=horizon,
        spot=24500.0,
        bottom_up_return_pct=bottom_up,
        artifact=artifact,
    )
    ridge_macro = ridge_only["macro_delta_pct"]
    assert abs(ridge_macro) > 0.5

    reconciled_headline = bottom_up + 0.9
    bundle = build_factor_explanation_bundle(
        macro,
        [],
        horizon=horizon,
        spot=24500.0,
        bottom_up_return_pct=bottom_up,
        headline_return_pct=reconciled_headline,
        artifact=artifact,
    )
    explanation = bundle["factor_explanation"]
    assert explanation["macro_delta_pct"] == pytest.approx(0.9, abs=0.01)
    assert explanation.get("ridge_macro_delta_pct") == pytest.approx(ridge_macro, abs=0.05)
    assert explanation.get("attribution_rescaled") is True
    total_contrib = sum(row["contribution_pct"] for row in explanation["contributors"])
    assert total_contrib == pytest.approx(explanation["macro_delta_pct"], abs=0.02)


@pytest.mark.unit
def test_explanation_bundle_structure():
    horizon = resolve_horizon(14)
    bundle = build_factor_explanation_bundle(
        _macro(),
        [{"event": "rbi_policy", "outcome": "dovish_hold", "probability": 0.4}],
        horizon=horizon,
        spot=24500.0,
        bottom_up_return_pct=0.5,
        artifact=_artifact(),
    )

    assert bundle["factor_explanation"]["contributors"]
    assert bundle["factor_sensitivity"]
    assert bundle["event_impact_curves"]
