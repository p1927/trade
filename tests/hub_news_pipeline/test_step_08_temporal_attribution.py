"""Tests for hub news pipeline step 08 — temporal attribution."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.hub_news_pipeline.step_08_temporal_attribution import (
    enrich_item_for_prediction,
    future_events_in_horizon,
    has_cause_indicators,
    strip_article_opinions,
)
from trade_integrations.dataflows.index_research.news_prediction_visibility import (
    visible_for_prediction_attribution,
)


def test_prepare_items_for_prediction_attribution_strips_and_enriches():
    from trade_integrations.dataflows.index_research.hub_news_pipeline.step_08_temporal_attribution import (
        prepare_items_for_prediction_attribution,
    )

    items = [
        {
            "provenance": "staging",
            "sources": [{}, {}],
            "article_enrichment": {
                "cause_indicators": [{"factor": "fii_net_5d"}],
                "future_events": [{"event": "RBI", "expected_date": "2026-03-20"}],
                "article_opinions": [{"text": "25000"}],
                "prediction_value_score": 0.8,
            },
        }
    ]
    out = prepare_items_for_prediction_attribution(
        items,
        prediction_date="2026-03-15",
        horizon_days=14,
    )
    assert len(out) == 1
    assert out[0]["prediction_attribution"]["cause_indicators"]
    assert "article_opinions" not in (out[0].get("article_enrichment") or {})


def test_strip_article_opinions():
    item = {
        "article_enrichment": {
            "cause_indicators": [{"factor": "fii_net_5d"}],
            "article_opinions": [{"text": "NIFTY 25000"}],
        }
    }
    out = strip_article_opinions(item)
    assert "article_opinions" not in out["article_enrichment"]
    assert out["article_enrichment"]["cause_indicators"]


def test_strip_article_opinions_from_references():
    item = {
        "references": [
            {
                "structured_enrichment": {
                    "cause_indicators": [{"factor": "fii_net_5d"}],
                    "article_opinions": [{"text": "target 25000"}],
                }
            }
        ]
    }
    out = strip_article_opinions(item)
    se = out["references"][0]["structured_enrichment"]
    assert "article_opinions" not in se


def test_future_events_in_horizon_excludes_undated_and_far():
    events = [
        {"event": "RBI MPC", "expected_date": "2026-03-20"},
        {"event": "Far future", "expected_date": "2026-06-01"},
        {"event": "Undated RBI", "timeline_phrase": "soon"},
    ]
    kept = future_events_in_horizon(events, prediction_date="2026-03-15", horizon_days=14)
    assert len(kept) == 1
    assert kept[0]["event"] == "RBI MPC"


def test_future_events_invalid_prediction_date_returns_empty():
    assert future_events_in_horizon([{"event": "x", "expected_date": "2026-03-20"}], prediction_date="bad") == []


def test_enrich_item_for_prediction():
    item = {
        "title": "NIFTY falls",
        "published_at": "2026-03-15",
        "article_enrichment": {
            "cause_indicators": [{"factor": "fii_net_5d", "mechanism": "outflows"}],
            "future_events": [{"event": "RBI MPC", "expected_date": "2026-03-20"}],
            "facts": [{"text": "FII sold", "as_of": "2026-03-15"}],
            "article_opinions": [{"text": "target 25000"}],
            "prediction_value_score": 0.8,
        },
    }
    out = enrich_item_for_prediction(item, prediction_date="2026-03-15", horizon_days=14)
    pa = out["prediction_attribution"]
    assert pa["cause_indicators"]
    assert pa["future_events"]
    assert pa["facts"][0]["as_of"] == "2026-03-15"
    assert pa["market_context_as_of"]["as_of"] == "2026-03-15"
    assert "article_opinions" not in (out.get("article_enrichment") or {})


def test_has_cause_indicators_event_meta():
    record = {"event_meta": {"cause_indicators": [{"factor": "fii_net_5d"}]}}
    assert has_cause_indicators(record) is True


def test_visible_single_ref_enriched_causes():
    record = {
        "provenance": "ssot",
        "references": [{}],
        "article_enrichment": {
            "prediction_value_score": 0.85,
            "cause_indicators": [{"factor": "fii_net_5d"}],
        },
    }
    assert visible_for_prediction_attribution(record) is True


def test_visible_single_ref_score_without_causes_false():
    record = {
        "provenance": "ssot",
        "references": [{}],
        "article_enrichment": {"prediction_value_score": 0.85},
    }
    assert visible_for_prediction_attribution(record) is False


def test_distill_event_preserves_structured_enrichment(monkeypatch):
    from trade_integrations.dataflows.index_research import news_distillation as dist_mod

    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_staging_store.minimax_configured",
        lambda: False,
    )
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_staging_store.rule_fallback_distillation_enabled",
        lambda: True,
    )

    refs = [
        {
            "title": "NIFTY falls on FII selling",
            "summary": "Foreign investors sold",
            "url": "https://example.com/a",
            "published_at": "2026-03-15",
            "structured_enrichment": {
                "cause_indicators": [{"factor": "fii_net_5d", "mechanism": "outflows"}],
                "future_events": [{"event": "RBI MPC", "expected_date": "2026-03-22"}],
            },
            "pipeline_distill_hints": "cause (fii_net_5d): outflows",
        }
    ]

    distilled = dist_mod.distill_event(refs=refs)
    em = (distilled.get("structured_summary") or {}).get("event_meta") or {}
    assert em.get("cause_indicators")
    assert em.get("future_events")
