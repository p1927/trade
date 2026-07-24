"""Tests for hub news pipeline step 10 — enrichment backfill maintainer."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_context import (
    RefPipelineContext,
)
from trade_integrations.dataflows.index_research.hub_news_pipeline.step_10_backfill_maintainer import (
    merge_pipeline_ref,
    pipeline_payload_from_event_ref,
    ref_has_enrichment_signal,
    ref_needs_enrichment_backfill,
    ref_needs_force_reenrich,
    run_enrichment_backfill,
    sync_structured_enrichment,
)


def test_ref_needs_enrichment_backfill_missing():
    assert ref_needs_enrichment_backfill({"url": "https://x", "raw_title": "NIFTY"}) is True


def test_ref_needs_enrichment_backfill_skips_with_causes():
    ref = {
        "url": "https://x",
        "article_enrichment": {
            "cause_indicators": [{"factor": "fii_net_5d"}],
        },
    }
    assert ref_needs_enrichment_backfill(ref) is False


def test_ref_needs_force_reenrich_for_thin_legacy():
    ref = {
        "article_enrichment": {
            "relevant": True,
            "distilled_summary": "summary only",
            "cause_indicators": [],
        }
    }
    assert ref_needs_force_reenrich(ref) is True


def test_pipeline_payload_sets_force_reenrich():
    ref = {
        "raw_title": "Title",
        "raw_summary": "Snippet",
        "url": "https://example.com/a",
        "article_enrichment": {"relevant": True, "distilled_summary": "x"},
    }
    payload = pipeline_payload_from_event_ref(ref, event={"published_at": "2026-03-10"})
    assert payload["_relevance_prefiltered"] is True
    assert payload["_force_re_enrich"] is True
    assert payload["title"] == "Title"


def test_sync_structured_enrichment():
    ref = {
        "article_enrichment": {
            "cause_indicators": [{"factor": "oil_brent"}],
            "future_events": [{"event": "RBI"}],
            "article_opinions": [{"text": "25000"}],
        }
    }
    out = sync_structured_enrichment(ref)
    assert out["structured_enrichment"]["cause_indicators"]
    assert out["pipeline_distill_hints"]


def test_merge_pipeline_ref_preserves_existing_fields():
    existing = {"url": "https://x", "publisher": "mint", "raw_title": "Old"}
    enriched = {
        "article_enrichment": {"cause_indicators": [{"factor": "fii_net_5d"}]},
        "summary": "Fetched body",
    }
    merged = merge_pipeline_ref(existing, enriched)
    assert merged["publisher"] == "mint"
    assert merged["structured_enrichment"]["cause_indicators"]


def test_run_enrichment_backfill(monkeypatch):
    monkeypatch.setenv("HUB_NEWS_PIPELINE_ENABLED", "1")

    event = {
        "event_id": "evt-1",
        "published_at": "2026-03-10",
        "structured_summary": {
            "event_meta": {
                "references": [
                    {
                        "url": "https://example.com/a",
                        "raw_title": "FII selling",
                        "raw_summary": "Markets weak",
                    }
                ]
            }
        },
    }

    def _list_events(**kwargs):
        return [event]

    def _get_event(event_id):
        return event if event_id == "evt-1" else None

    patches: list[tuple[str, dict]] = []

    def _patch(updates, **kwargs):
        patches.extend(updates)
        return len(updates)

    def _pause(**kwargs):
        return {"pipeline_paused": False}

    def _run_pipeline(ref, **kwargs):
        ctx = RefPipelineContext(ref=dict(ref), ticker="NIFTY")
        ctx.article_enrichment = {
            "relevant": True,
            "cause_indicators": [{"factor": "fii_net_5d", "direction_hint": "bearish"}],
            "future_events": [],
            "article_opinions": [],
            "facts": [],
            "prediction_value_score": 0.7,
            "enrichment_mode": "snippet_fallback",
        }
        ctx.ref["article_enrichment"] = ctx.article_enrichment
        return ctx

    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_events_store.list_events",
        _list_events,
    )
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_events_store.get_event",
        _get_event,
    )
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_events_store.patch_event_meta",
        _patch,
    )
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_staging_store.pipeline_pause_status",
        _pause,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.hub_news_pipeline.pipeline_runner.run_ref_pipeline",
        _run_pipeline,
    )

    result = run_enrichment_backfill(ticker="NIFTY", lookback_days=90, limit=50)
    assert result["refs_enriched"] == 1
    assert result["events_updated"] == 1
    assert len(patches) == 1
    refs = patches[0][1]["references"]
    assert refs[0]["structured_enrichment"]["cause_indicators"]


def test_ref_needs_enrichment_backfill_skips_after_attempt():
    ref = {
        "url": "https://x",
        "article_enrichment": {"distilled_summary": "thin"},
        "enrichment_backfill_at": "2026-03-10T00:00:00Z",
    }
    assert ref_needs_enrichment_backfill(ref) is False


def test_run_enrichment_backfill_idempotent_skip(monkeypatch):
    monkeypatch.setenv("HUB_NEWS_PIPELINE_ENABLED", "1")

    event = {
        "event_id": "evt-2",
        "structured_summary": {
            "event_meta": {
                "references": [
                    {
                        "url": "https://example.com/b",
                        "raw_title": "Already enriched",
                        "article_enrichment": {
                            "cause_indicators": [{"factor": "repo_rate"}],
                        },
                    }
                ]
            }
        },
    }

    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_events_store.list_events",
        lambda **kwargs: [event],
    )
    monkeypatch.setattr(
        "trade_integrations.hub_storage.news_staging_store.pipeline_pause_status",
        lambda **kwargs: {"pipeline_paused": False},
    )

    result = run_enrichment_backfill(ticker="NIFTY")
    assert result["skipped"] is True
    assert result["reason"] == "no_refs_needing_enrichment"


def test_ref_has_enrichment_signal_from_structured():
    ref = {"structured_enrichment": {"future_events": [{"event": "Budget"}]}}
    assert ref_has_enrichment_signal(ref) is True
