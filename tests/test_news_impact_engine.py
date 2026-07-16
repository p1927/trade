"""Tests for news enrichment, verification, and impact pipeline."""

from __future__ import annotations

import json

import pytest

from trade_integrations.dataflows.index_research.news_enrichment import (
    build_content_summary,
    build_structured_summary,
    de_clickbait_title,
    enrich_headline,
)
from trade_integrations.dataflows.index_research.news_verification import (
    VerifiedClaim,
    _approval_from_claims,
    is_approved_status,
    verify_enriched_news,
)


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.hub_storage import verified_news_store as store

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(store, "get_hub_dir", lambda: hub)
    return hub


def test_de_clickbait_strips_prefix():
    assert de_clickbait_title("BREAKING: Nifty falls on FII selling").startswith("Nifty")


def test_content_summary_prefers_body_over_headline():
    summary = build_content_summary(
        "Nifty to crash 20% tomorrow!!!",
        "FII sold Rs 3,200 crore over five sessions; Brent rose 2.1% on supply fears.",
    )
    assert "FII sold" in summary
    assert "crash 20%" not in summary or summary.index("FII sold") < summary.find("crash")


def test_structured_summary_extracts_facts_and_factors():
    structured = build_structured_summary(
        "Oil surge hits markets",
        "Brent crude jumped after Middle East tensions; FIIs sold heavily.",
    )
    assert structured.facts
    assert "oil_brent" in structured.implied_factors or "fii_net_5d" in structured.implied_factors


def test_approval_rejects_contradicted_claims():
    claims = [
        VerifiedClaim("FII selling", "fii_net_5d", "contradicted", "delta +5000"),
        VerifiedClaim("Oil up", "oil_brent", "unverifiable"),
    ]
    result = _approval_from_claims(claims)
    assert result.status == "rejected"
    assert not is_approved_status(result.status)


def test_approval_accepts_supported_claims():
    claims = [
        VerifiedClaim("FII selling", "fii_net_5d", "supported", "delta -8000"),
    ]
    result = _approval_from_claims(claims)
    assert result.status == "approved"
    assert is_approved_status(result.status)


def test_verify_enriched_news_returns_status(monkeypatch):
    import pandas as pd

    frame = pd.DataFrame(
        {
            "date": ["2026-02-17", "2026-02-18", "2026-02-19"],
            "close": [24000.0, 23900.0, 23800.0],
            "fii_net_5d": [-1000.0, -5000.0, -9000.0],
            "oil_brent": [80.0, 82.0, 85.0],
        }
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_verification.load_aligned_factor_history",
        lambda **_: frame,
    )
    item = enrich_headline(
        headline_id="abc",
        title="Foreign investors continue selling Indian equities",
        summary="FII outflows accelerated over the past week according to depository data.",
        published_at="2026-02-17T10:00:00+00:00",
    )
    verification = verify_enriched_news(item, publish_day="2026-02-17")
    assert verification.status in {"approved", "partial", "rejected", "pending"}


def test_publish_day_from_rfc_date():
    from trade_integrations.dataflows.index_research.news_dedup import (
        normalize_published_at,
        publish_day_from_value,
    )

    raw = "Tue, 10 Feb 2026 08:00:00 GMT"
    assert publish_day_from_value(raw) == "2026-02-10"
    assert normalize_published_at(raw).startswith("2026-02-10")


def test_merge_raw_headlines_dedupes_sources():
    from trade_integrations.dataflows.index_research.news_dedup import merge_raw_headlines

    merged = merge_raw_headlines(
        [
            {
                "title": "FII selling weighs on Nifty",
                "summary": "Short headline only.",
                "url": "https://news.example.com/1",
                "source": "rss",
                "published_at": "2026-02-17",
            },
            {
                "title": "FII selling weighs on Nifty",
                "summary": "Foreign investors sold Rs 3,000 crore over the week according to NSDL data.",
                "url": "https://other.example.com/2",
                "source": "aggregator",
                "published_at": "2026-02-17",
            },
        ]
    )
    assert len(merged) == 1
    assert len(merged[0]["sources"]) == 2
    assert "3,000 crore" in merged[0]["summary"]


def test_ingest_cache_hit_skips_reverify(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.index_research import news_impact_engine as engine
    from trade_integrations.hub_storage import verified_news_store as store

    monkeypatch.setattr(store, "get_hub_dir", lambda: hub_tmp)
    calls = {"verify": 0}
    original = engine.verify_enriched_news

    def counting_verify(*args, **kwargs):
        calls["verify"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, "verify_enriched_news", counting_verify)
    monkeypatch.setattr(
        engine,
        "collect_headlines_for_day",
        lambda *a, **k: [
            {
                "canonical_story_id": "title:cached story",
                "id": "title:cached story",
                "title": "Cached story",
                "summary": "FII sold heavily.",
                "sources": [{"vendor": "rss", "url": "", "publisher": "rss"}],
                "published_at": "2026-07-16",
            }
        ],
    )
    monkeypatch.setattr(
        engine,
        "load_aligned_factor_history",
        lambda **_: __import__("pandas").DataFrame(
            {
                "date": ["2026-07-16"],
                "close": [25000.0],
                "fii_net_5d": [-1000.0],
            }
        ),
    )

    store.upsert_verified_record(
        {
            "canonical_story_id": "title:cached story",
            "title": "Cached story",
            "content_summary": "FII sold heavily.",
            "sources": [{"vendor": "rss", "url": "", "publisher": "rss"}],
            "published_at": "2026-07-16",
            "verification_status": "partial",
            "verification": {"status": "partial"},
            "verification_data_as_of": "2026-07-16",
            "tags": {
                "topics": ["fii"],
                "factors": ["fii_net_5d"],
                "flat": ["topic:fii", "factor:fii_net_5d"],
            },
        }
    )

    stats1 = engine.ingest_headlines_for_day(day="2026-07-16", headline_limit=5)
    assert stats1["cache_hits"] == 1
    assert stats1["verified"] == 0
    assert calls["verify"] == 0

    stats2 = engine.ingest_headlines_for_day(day="2026-07-16", headline_limit=5)
    assert stats2["cache_hits"] == 1
    assert calls["verify"] == 0


def test_needs_reverify_when_cached_tagless():
    from trade_integrations.dataflows.index_research import news_impact_engine as engine

    cached = {"verification_data_as_of": "2026-07-16", "verification_status": "partial", "tags": {}}
    row = {
        "tags": {
            "topics": ["fii"],
            "factors": ["fii_net_5d"],
            "flat": ["topic:fii", "factor:fii_net_5d"],
        }
    }
    assert engine.needs_reverify(cached, row, publish_day="2026-07-16") is True


def test_cache_hit_merges_tags_when_cached_sparse(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.index_research import news_impact_engine as engine
    from trade_integrations.hub_storage import verified_news_store as store

    monkeypatch.setattr(store, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(
        engine,
        "collect_headlines_for_day",
        lambda *a, **k: [
            {
                "canonical_story_id": "title:sparse story",
                "title": "Sparse story",
                "summary": "FII sold heavily; oil prices rose.",
                "sources": [{"vendor": "rss", "url": "", "publisher": "rss"}],
                "published_at": "2026-07-16",
                "tags": {
                    "topics": ["fii", "oil"],
                    "factors": ["fii_net_5d", "oil_brent"],
                    "flat": ["topic:fii", "factor:fii_net_5d"],
                },
            }
        ],
    )
    monkeypatch.setattr(
        engine,
        "load_aligned_factor_history",
        lambda **_: __import__("pandas").DataFrame(
            {"date": ["2026-07-16"], "close": [25000.0], "fii_net_5d": [-1000.0]}
        ),
    )
    monkeypatch.setattr(engine, "verify_enriched_news", lambda *a, **k: __import__(
        "trade_integrations.dataflows.index_research.news_verification",
        fromlist=["VerifiedClaim", "_approval_from_claims"],
    )._approval_from_claims([]))

    store.upsert_verified_record(
        {
            "canonical_story_id": "title:sparse story",
            "title": "Sparse story",
            "content_summary": "FII sold heavily.",
            "sources": [{"vendor": "rss", "url": "", "publisher": "rss"}],
            "published_at": "2026-07-16",
            "verification_status": "partial",
            "verification": {"status": "partial"},
            "verification_data_as_of": "2026-07-16",
            "tags": {
                "topics": ["oil"],
                "factors": ["oil_brent"],
                "flat": ["topic:oil", "factor:oil_brent"],
            },
        }
    )

    stats = engine.ingest_headlines_for_day(day="2026-07-16", headline_limit=5)
    assert stats["cache_hits"] == 1
    assert stats.get("tags_merged", 0) == 1
    assert stats["verified"] == 0
    rec = store.get_verified_record("title:sparse story")
    assert "fii" in (rec.get("tags") or {}).get("topics", [])


def test_resolve_news_impact_prefers_snapshot_and_hydrates_tags(hub_tmp, monkeypatch):
    from trade_integrations.context import hub as hub_mod
    from trade_integrations.dataflows.index_research import news_impact_engine as engine
    from trade_integrations.hub_storage import verified_news_store as store

    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(store, "get_hub_dir", lambda: hub_tmp)
    monkeypatch.setattr(engine, "get_hub_dir", lambda: hub_tmp)
    store.upsert_verified_record(
        {
            "canonical_story_id": "title:hydrate me",
            "title": "Hydrate me",
            "content_summary": "US markets dragged Nifty lower.",
            "sources": [{"vendor": "rss", "url": "", "publisher": "rss"}],
            "published_at": "2026-07-16",
            "verification_status": "approved",
            "verification": {"status": "approved"},
            "verification_data_as_of": "2026-07-16",
            "tags": {
                "topics": ["us_markets"],
                "factors": ["sp500", "index_sentiment"],
                "flat": ["topic:us_markets", "factor:sp500"],
            },
        }
    )
    snap_path = hub_tmp / "NIFTY" / "index_research" / "news_impact_latest.json"
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_path.write_text(
        json.dumps(
            {
                "status": "ok",
                "items": [
                    {
                        "canonical_story_id": "title:hydrate me",
                        "title": "Hydrate me",
                        "tags": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = engine.resolve_news_impact(ticker="NIFTY", doc=None, limit=8)
    assert len(report.get("items") or []) == 1
    tags = (report["items"][0].get("tags") or {})
    assert "us_markets" in tags.get("topics", [])
    assert "sp500" in tags.get("factors", [])
