"""Unit tests for index research aggregator."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from trade_integrations.dataflows.company_research.models import StageResult
from trade_integrations.dataflows.index_research.models import ConstituentSignal, IndexResearchDoc


def _mock_signals() -> list[ConstituentSignal]:
    return [
        ConstituentSignal(
            symbol="RELIANCE",
            weight=0.5,
            sector="Energy",
            sentiment_score=0.2,
            events=[{"type": "results", "date": "2026-07-20"}],
        ),
        ConstituentSignal(
            symbol="TCS",
            weight=0.3,
            sector="Information Technology",
            sentiment_score=-0.1,
        ),
    ]


def _mock_macro_stage() -> StageResult:
    now = datetime.now(timezone.utc)
    return StageResult(
        stage="macro_global",
        status="ok",
        vendor="macro_global",
        fetched_at=now,
        data={
            "factors": {
                "usd_inr": 83.2,
                "oil_brent": 82.0,
                "india_vix": 14.5,
            },
            "factor_rows": [
                {"factor": "usd_inr", "value": 83.2, "source": "yfinance"},
            ],
        },
    )


@pytest.mark.unit
def test_run_index_research_orchestration(monkeypatch):
    append_mock = MagicMock()
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.append_prediction",
        append_mock,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.data_completeness.ensure_factor_data_complete",
        lambda **kwargs: {"passes_gate": True, "after": {"min_pct": 95.0}},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.batch_constituent_research",
        lambda **kwargs: _mock_signals(),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.fetch_global_macro_snapshot",
        lambda **kwargs: _mock_macro_stage(),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator._fetch_spot",
        lambda ticker: 24500.0,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator._nifty_trend_20d",
        lambda: "up",
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.compute_accuracy_metrics",
        lambda **kwargs: {"sample_count": 3, "mae_14d_pct": 1.2},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.attach_constituent_momentum",
        lambda signals, **kwargs: signals,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.refresh_news_impact",
        lambda **kwargs: {"items": [], "summary": {"approved_count": 0}},
    )
    from trade_integrations.dataflows.index_research.predictor import ModelArtifact

    artifact = ModelArtifact(
        coefficients={"usd_inr": 0.04, "oil_brent": -0.02, "india_vix": -0.01},
        intercept=0.05,
        feature_names=["usd_inr", "oil_brent", "india_vix"],
        poly_degree=1,
        mae=1.2,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.explain.load_stored_model_artifact",
        lambda: artifact,
    )

    from trade_integrations.dataflows.index_research.aggregator import run_index_research

    doc = run_index_research("NIFTY", horizon_days=14, refresh_constituents=True)

    assert isinstance(doc, IndexResearchDoc)
    assert doc.ticker == "NIFTY"
    assert doc.spot == pytest.approx(24500.0)
    assert doc.horizon["days"] == 14
    assert doc.prediction.get("view") in {"bullish", "bearish", "neutral"}
    assert doc.prediction.get("range")
    assert doc.prediction.get("top_drivers")
    assert len(doc.constituent_signals) == 2
    assert doc.scenarios
    assert doc.regime.get("label")
    assert doc.accuracy["sample_count"] == 3
    assert doc.factor_explanation.get("contributors")
    assert doc.factor_sensitivity
    append_mock.assert_called_once()


@pytest.mark.unit
def test_run_index_research_horizon_a(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.data_completeness.ensure_factor_data_complete",
        lambda **kwargs: {"passes_gate": True, "after": {"min_pct": 95.0}},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.append_prediction",
        MagicMock(),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.batch_constituent_research",
        lambda **kwargs: _mock_signals(),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.fetch_global_macro_snapshot",
        lambda **kwargs: _mock_macro_stage(),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator._fetch_spot",
        lambda ticker: 24500.0,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator._nifty_trend_20d",
        lambda: "sideways",
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.compute_accuracy_metrics",
        lambda **kwargs: {"sample_count": 0},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.attach_constituent_momentum",
        lambda signals, **kwargs: signals,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.refresh_news_impact",
        lambda **kwargs: {"items": [], "summary": {"approved_count": 0}},
    )

    from trade_integrations.dataflows.index_research.aggregator import run_index_research

    doc = run_index_research("NIFTY", horizon_days=2, refresh_constituents=True)

    assert doc.horizon["name"] == "A"
    assert doc.horizon["days"] == 2


@pytest.mark.unit
def test_run_index_research_passes_scenario_anchor_to_predict(monkeypatch):
    captured: dict = {}

    def _capture_predict(*args, **kwargs):
        captured.update(kwargs)
        from trade_integrations.dataflows.index_research.predictor import predict_nifty

        return predict_nifty(*args, **kwargs)

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.data_completeness.ensure_factor_data_complete",
        lambda **kwargs: {"passes_gate": True, "after": {"min_pct": 95.0}},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.append_prediction",
        MagicMock(),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.batch_constituent_research",
        lambda **kwargs: _mock_signals(),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.fetch_global_macro_snapshot",
        lambda **kwargs: _mock_macro_stage(),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator._fetch_spot",
        lambda ticker: 24500.0,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator._nifty_trend_20d",
        lambda: "up",
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.compute_accuracy_metrics",
        lambda **kwargs: {"sample_count": 0},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.attach_constituent_momentum",
        lambda signals, **kwargs: signals,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.refresh_news_impact",
        lambda **kwargs: {"items": [], "summary": {"approved_count": 0}},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.predict_nifty",
        _capture_predict,
    )
    from trade_integrations.dataflows.index_research.predictor import ModelArtifact

    artifact = ModelArtifact(
        coefficients={"usd_inr": 0.04},
        intercept=0.05,
        feature_names=["usd_inr"],
        poly_degree=1,
        mae=1.2,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.explain.load_stored_model_artifact",
        lambda: artifact,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.load_stored_model_artifact",
        lambda: artifact,
    )

    from trade_integrations.dataflows.index_research.aggregator import run_index_research

    run_index_research("NIFTY", horizon_days=14, refresh_constituents=True)

    assert captured.get("scenario_anchor_return_pct") is not None


@pytest.mark.unit
def test_run_index_research_sets_data_quality_warning_when_gate_fails(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.data_completeness.ensure_factor_data_complete",
        lambda **kwargs: {
            "passes_gate": False,
            "after": {"min_pct": 55.0, "gate_threshold_pct": 90.0},
        },
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.append_prediction",
        MagicMock(),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.batch_constituent_research",
        lambda **kwargs: _mock_signals(),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.fetch_global_macro_snapshot",
        lambda **kwargs: _mock_macro_stage(),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator._fetch_spot",
        lambda ticker: 24500.0,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator._nifty_trend_20d",
        lambda: "up",
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.compute_accuracy_metrics",
        lambda **kwargs: {"sample_count": 0},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.attach_constituent_momentum",
        lambda signals, **kwargs: signals,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.refresh_news_impact",
        lambda **kwargs: {"items": [], "summary": {"approved_count": 0}},
    )
    from trade_integrations.dataflows.index_research.predictor import ModelArtifact

    artifact = ModelArtifact(
        coefficients={"usd_inr": 0.04},
        intercept=0.05,
        feature_names=["usd_inr"],
        poly_degree=1,
        mae=1.2,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.explain.load_stored_model_artifact",
        lambda: artifact,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.load_stored_model_artifact",
        lambda: artifact,
    )

    from trade_integrations.dataflows.index_research.aggregator import run_index_research

    doc = run_index_research("NIFTY", horizon_days=14, refresh_constituents=True)

    warning = doc.prediction.get("data_quality_warning") or {}
    assert warning.get("gate") == "flow_coverage"
    assert warning.get("min_pct") == 55.0


@pytest.mark.unit
def test_run_index_research_sign_conflict_when_anchor_opposes_macro(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.data_completeness.ensure_factor_data_complete",
        lambda **kwargs: {"passes_gate": True, "after": {"min_pct": 95.0}},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.append_prediction",
        MagicMock(),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.batch_constituent_research",
        lambda **kwargs: _mock_signals(),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.fetch_global_macro_snapshot",
        lambda **kwargs: _mock_macro_stage(),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator._fetch_spot",
        lambda ticker: 24500.0,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator._nifty_trend_20d",
        lambda: "up",
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.compute_accuracy_metrics",
        lambda **kwargs: {"sample_count": 0},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.attach_constituent_momentum",
        lambda signals, **kwargs: signals,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.refresh_news_impact",
        lambda **kwargs: {"items": [], "summary": {"approved_count": 0}},
    )
    monkeypatch.setattr(
        "trade_integrations.context.hub.load_agent_debate_json",
        lambda sym: None,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.scenario_weighted_return_pct",
        lambda *args, **kwargs: -1.5,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.scenarios.scenario_weighted_return_pct",
        lambda *args, **kwargs: -1.5,
    )

    def _predict_sign_conflict(**kwargs):
        spot = float(kwargs["spot"])
        return {
            "view": "bullish",
            "expected_return_pct": 1.2,
            "bottom_up_return_pct": 0.2,
            "macro_delta_pct": 1.0,
            "raw_macro_delta_pct": 4.0,
            "direction_view": "bullish",
            "direction_confidence": 0.58,
            "range": {"low": spot * 0.99, "high": spot * 1.01, "confidence": 0.5},
        }

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.predict_nifty",
        _predict_sign_conflict,
    )
    from trade_integrations.dataflows.index_research.predictor import ModelArtifact

    artifact = ModelArtifact(
        coefficients={"usd_inr": 0.04},
        intercept=0.05,
        feature_names=["usd_inr"],
        poly_degree=1,
        mae=1.2,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.explain.load_stored_model_artifact",
        lambda: artifact,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.load_stored_model_artifact",
        lambda: artifact,
    )

    from trade_integrations.dataflows.index_research.aggregator import run_index_research

    doc = run_index_research("NIFTY", horizon_days=14, refresh_constituents=True)

    assert doc.prediction.get("sign_conflict") is True
    assert doc.prediction.get("direction_view") == "neutral"
    assert doc.prediction.get("ridge_raw_macro_delta_pct") == pytest.approx(4.0)
