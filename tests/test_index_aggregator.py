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

    from trade_integrations.dataflows.index_research.aggregator import run_index_research

    doc = run_index_research("NIFTY", horizon_days=14)

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
    append_mock.assert_called_once()


@pytest.mark.unit
def test_run_index_research_horizon_a(monkeypatch):
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

    from trade_integrations.dataflows.index_research.aggregator import run_index_research

    doc = run_index_research("NIFTY", horizon_days=2)

    assert doc.horizon["name"] == "A"
    assert doc.horizon["days"] == 2
