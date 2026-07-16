"""Tests for news article tagging and hub filter helpers."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.news_tags import (
    build_article_tags,
    merge_article_tags,
    record_matches_filters,
    tags_are_empty,
    topics_from_record,
)


def test_build_article_tags_fii_oil():
    tags = build_article_tags(
        "FII selling drags Nifty as Brent crude surges",
        "Foreign investors sold Rs 3,000 crore; oil up 2%.",
        ticker="NIFTY",
        published_at="2026-02-17T10:00:00+00:00",
    )
    assert "fii" in tags.topics
    assert "oil" in tags.topics
    assert "fii_net_5d" in tags.factors or "oil_brent" in tags.factors
    assert tags.publish_day == "2026-02-17"
    assert any(t.startswith("topic:") for t in tags.flat)


def test_merge_article_tags_unions():
    a = build_article_tags("FII selling weighs on markets", ticker="NIFTY")
    b = build_article_tags("Oil prices jump on supply fears", ticker="NIFTY")
    merged = merge_article_tags(a, b)
    assert "fii" in merged.topics or "fii_net_5d" in merged.factors
    assert "oil" in merged.topics or "oil_brent" in merged.factors
    assert len(merged.flat) >= 4


def test_record_matches_filters_by_factor():
    record = {
        "published_at": "2026-04-28T07:00:00+00:00",
        "tags": {
            "publish_day": "2026-04-28",
            "topics": ["oil", "fii"],
            "factors": ["fii_net_5d", "oil_brent"],
            "flat": ["factor:fii_net_5d", "topic:oil"],
        },
    }
    assert record_matches_filters(record, factors=["fii_net_5d"])
    assert not record_matches_filters(record, factors=["repo_rate"])


def test_topics_from_record_maps_hub_vocab():
    record = {
        "tags": {
            "topics": ["us_markets", "forex", "fii"],
            "factors": ["oil_brent"],
        }
    }
    topics = topics_from_record(record)
    assert "us" in topics
    assert "usd" in topics
    assert "fii" in topics
    assert "oil" in topics


def test_tags_are_empty():
    assert tags_are_empty({})
    assert tags_are_empty({"flat": ["day:2026-02-17"]})
    assert not tags_are_empty({"topics": ["oil"]})
