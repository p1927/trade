"""Tests for maintainer fact adjudication backfill."""

from __future__ import annotations

import pytest

from trade_integrations.hub_storage import news_events_store as events_store
from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent, EventConsensus


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    monkeypatch.setenv("MINIMAX_API_KEY", "")
    return hub


def test_fact_backfill_skips_when_no_candidates(hub_tmp, monkeypatch):
    monkeypatch.setenv("HUB_NEWS_LLM_ADJUDICATION_ENABLED", "0")
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_staging_store.pipeline_pause_status",
        lambda **_: {"pipeline_paused": False, "pause_reason": ""},
    )
    from trade_integrations.dataflows.index_research.news_maintainer_facts import (
        run_fact_adjudication_backfill,
    )

    events_store.upsert_event(
        DistilledNewsEvent(
            event_id="evt:ok",
            ticker="NIFTY",
            title="Oil rises on supply cut",
            content="Brent crude moved higher.",
            publish_day="2026-04-28",
            structured_summary={
                "event_meta": {
                    "references": [
                        {
                            "ref_id": "ref:a",
                            "extracted_claims": [{"type": "oil_price", "value": "up"}],
                        }
                    ]
                }
            },
            consensus=EventConsensus(direction="bullish", ref_count=1),
            verification_status="approved",
        )
    )

    result = run_fact_adjudication_backfill(ticker="NIFTY", lookback_days=30, limit=10)
    assert result.get("skipped") is True
    assert result.get("reason") == "no_refs_needing_facts"
