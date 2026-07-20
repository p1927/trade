"""Phase 3 claim extraction and parent threading tests."""

from __future__ import annotations


def test_extract_claims_percent_and_level():
    from trade_integrations.dataflows.index_research.news_claim_extraction import extract_claims

    claims = extract_claims("Nifty rises 1.2% to 24500 on FII inflows", "Nifty closed at 24500")
    kinds = {c["kind"] for c in claims}
    assert "percent_move" in kinds
    assert "index_level" in kinds


def test_infer_parent_event_id_for_geopolitical():
    from trade_integrations.dataflows.index_research.news_parent_events import infer_parent_event_id

    ref = {"title": "Iran conflict day 11", "published_at": "2026-07-20", "tags": {"topics": ["geopolitical"]}}
    pid = infer_parent_event_id(ref)
    assert pid and pid.startswith("parent:geopolitical:")


def test_rule_fallback_distill_without_minimax(monkeypatch):
    from trade_integrations.dataflows.index_research.news_distillation import distill_event

    monkeypatch.setenv("MINIMAX_API_KEY", "")
    monkeypatch.setenv("HUB_NEWS_RULE_FALLBACK_DISTILL", "1")
    from trade_integrations.hub_storage import news_staging_store as staging

    monkeypatch.setattr(staging, "minimax_configured", lambda: False)

    out = distill_event(
        refs=[{"title": "RBI holds rates", "summary": "Repo unchanged at 6.5%", "url": "https://x/a"}],
        previous=None,
    )
    meta = out["structured_summary"]["event_meta"]
    assert meta["distillation_mode"] == "rule_fallback"
    assert out["title"]
