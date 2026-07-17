"""Tests for pipeline activity log and factor catalog."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.index_research.factor_catalog import list_factor_catalog
from trade_integrations.dataflows.index_research.factor_matrix import MACRO_FACTOR_KEYS
from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger


@pytest.mark.unit
def test_pipeline_logger_collects_and_callbacks():
    seen: list[str] = []
    log = PipelineLogger(on_entry=lambda e: seen.append(e.stage))
    log.info("macro", "test message", factor="oil_brent")
    assert len(log.entries) == 1
    assert seen == ["macro"]
    assert log.to_dicts()[0]["message"] == "test message"


@pytest.mark.unit
def test_factor_catalog_covers_matrix_keys():
    catalog = list_factor_catalog()
    catalog_keys = {row["key"] for row in catalog["macro_and_technical"]}
    assert set(MACRO_FACTOR_KEYS).issubset(catalog_keys)
    assert catalog["total_macro_keys"] >= len(MACRO_FACTOR_KEYS)
    assert len(catalog["constituent_research"]) >= 5
    assert len(catalog["news_and_sentiment"]) >= 2
    assert len(catalog["derivatives"]) >= 3
    assert len(catalog["pipeline_modules"]) >= 5
    assert any(row["key"] == "tapetide" for row in catalog["pipeline_modules"])
    assert any(row["key"] == "screener_in" for row in catalog["pipeline_modules"])
    assert "india_data_sources" in catalog
    assert len(catalog["india_data_sources"]["sources"]) >= 6
    assert catalog["india_data_sources"]["stage_source_order"]["peers"][0] == "screener_in"
    assert any(row["key"] == "ed_alpha" for row in catalog["pipeline_modules"])
    assert any(row["key"] == "news_per_constituent" for row in catalog["constituent_research"])


@pytest.mark.unit
def test_run_index_research_emits_pipeline_log(monkeypatch):
    pytest.importorskip("sklearn")
    from trade_integrations.dataflows.index_research.aggregator import run_index_research
    from trade_integrations.dataflows.index_research.models import ConstituentSignal
    from trade_integrations.dataflows.index_research.pipeline_log import PipelineLogger

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.data_completeness.ensure_factor_data_complete",
        lambda **kwargs: {"passes_gate": True, "after": {"min_pct": 95.0}},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.batch_constituent_research",
        lambda **_: [
            ConstituentSignal(symbol="RELIANCE", weight=0.5, sentiment_score=0.1, momentum_7d_pct=1.0),
        ],
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.attach_constituent_momentum",
        lambda signals, **kwargs: signals,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.fetch_global_macro_snapshot",
        lambda **_: __import__(
            "trade_integrations.dataflows.company_research.models", fromlist=["StageResult"]
        ).StageResult(
            stage="macro_global",
            status="ok",
            vendor="test",
            fetched_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            data={"factors": {"usd_inr": 83.0, "india_vix": 14.0}, "factor_rows": []},
            errors=[],
        ),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator._fetch_spot",
        lambda _: 24500.0,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator._nifty_trend_20d",
        lambda: "up",
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.append_prediction",
        lambda *_, **__: None,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.aggregator.compute_accuracy_metrics",
        lambda: {},
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.news_hub_bridge.refresh_news_impact",
        lambda **kwargs: {"items": [], "summary": {"approved_count": 0}},
    )

    plog = PipelineLogger()
    doc = run_index_research("NIFTY", horizon_days=14, pipeline=plog, refresh_constituents=True)

    assert doc.pipeline_log
    stages = {row["stage"] for row in doc.pipeline_log}
    assert "start" in stages
    assert "predict" in stages
    assert "done" in stages
