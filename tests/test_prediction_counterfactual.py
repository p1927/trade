"""Tests for prediction counterfactual decomposition."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.index_research.prediction_counterfactual import (
    classify_counterfactual_row,
    decompose_macro_prediction,
)
from trade_integrations.dataflows.index_research.predictor import ModelArtifact
from trade_integrations.dataflows.index_research.horizon import resolve_horizon


@pytest.mark.unit
def test_classify_mapping_error_t0():
    tag = classify_counterfactual_row(
        predicted_t0=2.0,
        actual=-3.0,
        explained_by_drift=0.5,
        residual=-5.0,
        macro_raw=2.0,
        macro_capped=2.0,
        missing_t0=[],
    )
    assert tag == "mapping_error_T0"


@pytest.mark.unit
def test_classify_drift_dominant():
    tag = classify_counterfactual_row(
        predicted_t0=2.0,
        actual=-1.0,
        explained_by_drift=-2.5,
        residual=-3.0,
        macro_raw=2.0,
        macro_capped=2.0,
        missing_t0=[],
    )
    assert tag == "drift_dominant"


@pytest.mark.unit
def test_classify_data_gap_t0():
    tag = classify_counterfactual_row(
        predicted_t0=1.0,
        actual=-2.0,
        explained_by_drift=0.0,
        residual=-3.0,
        macro_raw=1.0,
        macro_capped=1.0,
        missing_t0=["fii_net_5d", "dii_net_5d", "oil_brent"],
    )
    assert tag == "data_gap_T0"


@pytest.mark.unit
def test_decompose_macro_prediction_returns_contributors():
    artifact = ModelArtifact(
        coefficients={"fii_net_5d": 0.1},
        intercept=0.5,
        feature_names=["fii_net_5d"],
        poly_degree=1,
        mae=1.5,
        feature_means=[0.0],
        feature_stds=[1.0],
    )
    horizon = resolve_horizon(14)
    raw, capped, contribs = decompose_macro_prediction(
        {"fii_net_5d": 2.0},
        artifact,
        horizon_profile=horizon,
    )
    assert isinstance(raw, float)
    assert isinstance(capped, float)
    assert isinstance(contribs, list)
