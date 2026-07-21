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

    assert result["method"] in {"marginal", "linear_shap", "grouped_marginal", "correlation_dependent_shap"}
    assert result["contributors"]
    total_contrib = sum(row["contribution_pct"] for row in result["contributors"])
    assert total_contrib == pytest.approx(result["macro_delta_pct"], abs=0.01)
    for row in result["contributors"]:
        assert "share_of_macro" in row
        assert "contribution_index_pts" in row


@pytest.mark.unit
def test_factor_sensitivity_reconciled_around_headline():
    """Shocked points must be continuous with reconciled headline at 0%."""
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
    headline = bottom_up + 0.9
    curves = build_factor_sensitivity(
        macro,
        horizon=horizon,
        spot=24500.0,
        bottom_up_return_pct=bottom_up,
        headline_return_pct=headline,
        artifact=artifact,
        sweep_pct=(-5, 5, 5),
        max_factors=2,
    )
    curve = curves[0]
    zero = next(p for p in curve["points"] if p["factor_delta_pct"] == 0)
    minus = next(p for p in curve["points"] if p["factor_delta_pct"] == -5)
    plus = next(p for p in curve["points"] if p["factor_delta_pct"] == 5)
    assert zero["return_pct"] == pytest.approx(headline, abs=0.01)
    assert abs(minus["return_pct"] - zero["return_pct"]) < 2.5
    assert abs(plus["return_pct"] - zero["return_pct"]) < 2.5


@pytest.mark.unit
def test_factor_sensitivity_includes_pinned_flow_factors():
    horizon = resolve_horizon(14)
    macro = {
        "usd_inr": 83.2,
        "oil_brent": 82.0,
        "fii_net_5d": 1200.0,
        "dii_net_5d": -800.0,
        "india_vix": 14.0,
    }
    artifact = ModelArtifact(
        coefficients={"usd_inr": 0.05, "fii_net_5d": -0.02, "dii_net_5d": 0.01},
        intercept=0.1,
        mae=1.2,
        feature_names=["usd_inr", "fii_net_5d", "dii_net_5d"],
        poly_degree=1,
        horizon_name="B",
    )
    curves = build_factor_sensitivity(
        macro,
        horizon=horizon,
        spot=24500.0,
        bottom_up_return_pct=0.5,
        artifact=artifact,
        sweep_pct=(-5, 5, 5),
        max_factors=2,
    )
    factors = {c["factor"] for c in curves}
    assert "fii_net_5d" in factors
    assert "dii_net_5d" in factors


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
def test_explain_uses_grouped_marginal_when_multicollinearity_warning(monkeypatch):
    horizon = resolve_horizon(14)
    artifact = ModelArtifact(
        coefficients={"usd_inr": 0.05, "oil_brent": -0.03, "sp500": 0.02},
        intercept=0.1,
        mae=1.2,
        feature_names=["usd_inr", "oil_brent", "sp500"],
        poly_degree=1,
        horizon_name="B",
        multicollinearity_warning=True,
        correlated_pairs=[
            {"factor_a": "oil_brent", "factor_b": "sp500", "correlation": 0.82},
        ],
    )

    def _no_panel(_artifact, *, days=365):
        return None

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.explain._load_panel_background_matrix",
        _no_panel,
    )

    result = explain_macro_factors(
        {"usd_inr": 83.2, "oil_brent": 82.0, "sp500": 5200.0},
        horizon=horizon,
        spot=24500.0,
        bottom_up_return_pct=0.5,
        artifact=artifact,
    )
    assert result["method"] == "grouped_marginal"
    assert result["multicollinearity_warning"] is True
    assert result.get("correlated_pairs")
    total_contrib = sum(row["contribution_pct"] for row in result["contributors"])
    assert total_contrib == pytest.approx(result["macro_delta_pct"], abs=0.05)
    assert result.get("channel_attribution")


@pytest.mark.unit
def test_explain_falls_back_to_grouped_marginal_when_tier2_unavailable(monkeypatch):
    """Tier 2 unavailable (no panel) must not block Tier 1 grouped marginal."""
    horizon = resolve_horizon(14)
    artifact = ModelArtifact(
        coefficients={"usd_inr": 0.05, "sp500": 0.02},
        intercept=0.1,
        mae=1.2,
        feature_names=["usd_inr", "sp500"],
        poly_degree=1,
        horizon_name="B",
        multicollinearity_warning=True,
        correlated_pairs=[
            {"factor_a": "usd_inr", "factor_b": "sp500", "correlation": 0.85},
        ],
    )

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.explain._load_panel_background_matrix",
        lambda _artifact, *, days=365: None,
    )

    result = explain_macro_factors(
        {"usd_inr": 83.2, "sp500": 5200.0},
        horizon=horizon,
        spot=24500.0,
        bottom_up_return_pct=0.5,
        artifact=artifact,
    )
    assert result["method"] == "grouped_marginal"
    assert result["contributors"]

@pytest.mark.unit
def test_explain_uses_correlation_dependent_shap_with_panel(monkeypatch):
    """Tier 2: panel-backed covariance SHAP when shap installed."""
    pytest.importorskip("shap")
    import numpy as np

    rng = np.random.default_rng(42)
    n = 120
    usd = rng.normal(83.0, 0.4, size=n)
    sp = usd * 15.0 + rng.normal(5200.0, 80.0, size=n)

    artifact = ModelArtifact(
        coefficients={"usd_inr": 0.04, "sp500": 0.03},
        intercept=0.05,
        mae=1.0,
        feature_names=["usd_inr", "sp500"],
        poly_degree=1,
        horizon_name="B",
        multicollinearity_warning=True,
        correlated_pairs=[
            {"factor_a": "usd_inr", "factor_b": "sp500", "correlation": 0.88},
        ],
        feature_means=[float(usd.mean()), float(sp.mean())],
        feature_stds=[float(usd.std()), float(sp.std())],
    )

    def _fake_panel(_artifact, *, days=365):
        return np.column_stack([usd, sp])

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.explain._load_panel_background_matrix",
        _fake_panel,
    )

    horizon = resolve_horizon(14)
    result = explain_macro_factors(
        {"usd_inr": 83.2, "sp500": 5200.0},
        horizon=horizon,
        spot=24500.0,
        bottom_up_return_pct=0.5,
        artifact=artifact,
    )
    assert result["method"] == "correlation_dependent_shap"
    assert result["contributors"]
    total_contrib = sum(row["contribution_pct"] for row in result["contributors"])
    assert total_contrib == pytest.approx(result["macro_delta_pct"], abs=0.05)


@pytest.mark.unit
def test_load_panel_background_matrix_requires_min_rows(monkeypatch):
    import numpy as np
    import pandas as pd

    from trade_integrations.dataflows.index_research.explain import _load_panel_background_matrix

    artifact = ModelArtifact(
        coefficients={"usd_inr": 0.05},
        intercept=0.0,
        mae=1.0,
        feature_names=["usd_inr", "oil_brent"],
        poly_degree=1,
        horizon_name="B",
    )

    def _tiny_panel(*, days=365, start=None, panel_name="NIFTY_2006_present"):
        return pd.DataFrame({"date": ["2026-01-01"], "usd_inr": [83.0], "oil_brent": [82.0]})

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.history_panel.load_aligned_panel_history",
        _tiny_panel,
    )
    assert _load_panel_background_matrix(artifact) is None

    rows = 40
    def _enough_panel(*, days=365, start=None, panel_name="NIFTY_2006_present"):
        return pd.DataFrame(
            {
                "date": [f"2026-01-{i:02d}" for i in range(1, rows + 1)],
                "usd_inr": np.linspace(82.0, 84.0, rows),
                "oil_brent": np.linspace(80.0, 85.0, rows),
            }
        )

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.history_panel.load_aligned_panel_history",
        _enough_panel,
    )
    matrix = _load_panel_background_matrix(artifact)
    assert matrix is not None
    assert matrix.shape == (rows, 2)

@pytest.mark.unit
def test_build_perturbation_groups_merges_correlated_pairs():
    from trade_integrations.dataflows.index_research.explain import _build_perturbation_groups

    artifact = ModelArtifact(
        coefficients={},
        intercept=0.0,
        mae=1.0,
        feature_names=["sp500", "equity_risk_premium", "india_vix"],
        correlated_pairs=[
            {"factor_a": "sp500", "factor_b": "equity_risk_premium", "correlation": 0.81},
            {"factor_a": "india_vix", "factor_b": "nifty_bb_width_pct", "correlation": 0.79},
        ],
    )
    groups = _build_perturbation_groups(artifact)
    flat = {member for group in groups for member in group}
    assert "sp500" in flat and "equity_risk_premium" in flat
    assert any("sp500" in group and "equity_risk_premium" in group for group in groups)


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
