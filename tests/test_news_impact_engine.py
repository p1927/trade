"""Tests for news enrichment, verification, and impact pipeline."""

from __future__ import annotations

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
