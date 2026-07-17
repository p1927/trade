"""Tests for hub news event matching and dedup thresholds."""

from __future__ import annotations


def test_match_threshold_defaults_to_0_72(monkeypatch):
    monkeypatch.delenv("HUB_NEWS_MATCH_THRESHOLD", raising=False)
    monkeypatch.delenv("HUB_NEWS_DEDUP_SUMMARY_THRESHOLD", raising=False)

    from trade_integrations.dataflows.index_research import news_event_matching as mod

    assert mod.match_threshold() == 0.72


def test_bucket_match_does_not_merge_below_threshold(monkeypatch):
    monkeypatch.setenv("HUB_NEWS_DEDUP_SUMMARY_THRESHOLD", "0.72")
    monkeypatch.delenv("HUB_NEWS_MATCH_THRESHOLD", raising=False)

    from trade_integrations.dataflows.index_research.news_event_matching import find_matching_event

    ref = {
        "title": "RBI holds rates steady in surprise decision",
        "summary": "The central bank kept the repo rate unchanged citing inflation risks.",
        "published_at": "2026-02-17T10:00:00+00:00",
        "tags": {
            "topics": ["rbi"],
            "themes": ["flat"],
            "factors": ["policy_rate"],
            "symbols": ["NIFTY"],
            "publish_day": "2026-02-17",
        },
    }
    event = {
        "title": "Nifty edges higher on global cues",
        "content_summary": "Benchmark indices rose tracking overnight gains on Wall Street.",
        "published_at": "2026-02-17T11:00:00+00:00",
        "tags": {
            "topics": ["rbi"],
            "themes": ["flat"],
            "factors": ["policy_rate"],
            "symbols": ["NIFTY"],
            "publish_day": "2026-02-17",
        },
    }
    assert find_matching_event(ref, [event], ticker="NIFTY") is None


def test_find_matching_event_merges_when_summary_similarity_high(monkeypatch):
    monkeypatch.setenv("HUB_NEWS_DEDUP_SUMMARY_THRESHOLD", "0.72")

    from trade_integrations.dataflows.index_research.news_event_matching import find_matching_event

    body = (
        "Foreign investors sold Rs 2,500 crore in the cash segment on Thursday, "
        "dragging the Nifty lower by 120 points amid global risk-off sentiment."
    )
    ref = {
        "title": "FII selling drags Nifty lower",
        "summary": body,
        "published_at": "2026-02-17T10:00:00+00:00",
        "tags": {
            "topics": ["fii"],
            "themes": ["selloff"],
            "factors": ["fii_net_5d"],
            "symbols": ["NIFTY"],
            "publish_day": "2026-02-17",
        },
    }
    event = {
        "title": "FII selling drags Nifty lower by 120 points",
        "content_summary": body,
        "published_at": "2026-02-17T11:00:00+00:00",
        "tags": {
            "topics": ["fii"],
            "themes": ["selloff"],
            "factors": ["fii_net_5d"],
            "symbols": ["NIFTY"],
            "publish_day": "2026-02-17",
        },
    }
    matched = find_matching_event(ref, [event], ticker="NIFTY")
    assert matched is not None
