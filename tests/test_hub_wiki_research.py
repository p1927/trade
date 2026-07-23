"""Tests for LLM Wiki Deep Research gap detection and export."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import patch_hub_wiki_dirs


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    patch_hub_wiki_dirs(monkeypatch, hub)
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub


def _event(**overrides):
    base = {
        "event_id": "evt:conflict1",
        "ticker": "NIFTY",
        "title": "Oil spike hits markets",
        "content": "Brent rose on supply fears.",
        "status": "active",
        "structured_summary": {
            "event_meta": {
                "event_id": "evt:conflict1",
                "references": [{"publisher": "A", "raw_title": "Oil up"}],
                "consensus": {
                    "conflicts": ["Source A says +3%, Source B says +1%"],
                    "factors": ["oil_brent"],
                },
            }
        },
    }
    base.update(overrides)
    return base


def test_detect_research_gaps_conflicts():
    from trade_integrations.dataflows.hub_wiki.research_gaps import detect_research_gaps, pick_primary_gap

    gaps = detect_research_gaps(_event())
    assert any(g["gap_kind"] == "conflicts" for g in gaps)
    assert pick_primary_gap(gaps)["gap_kind"] == "conflicts"


def test_export_research_writes_raw_source(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.hub_wiki.research import export_research_for_event

    monkeypatch.setenv("HUB_NEWS_WIKI_DEEP_RESEARCH", "1")
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.research.chat_wiki",
        lambda *_a, **_k: {
            "ok": True,
            "message": "Verified impact: modest NIFTY drag from oil.",
            "references": [{"title": "Reuters", "url": "https://example.com"}],
        },
    )

    event = _event()
    gap = {"gap_kind": "conflicts", "detail": "conflicting levels"}
    result = export_research_for_event(event, gap=gap, rescan=False)
    assert result["ok"] is True
    md_path = Path(result["source_md_path"])
    assert md_path.is_file()
    text = md_path.read_text(encoding="utf-8")
    assert "type: research" in text
    assert "evt:conflict1" in text
    assert "Verified impact" in text


def test_research_dedupes_same_day(hub_tmp, monkeypatch):
    from trade_integrations.dataflows.hub_wiki.research import export_research_for_event

    monkeypatch.setenv("HUB_NEWS_WIKI_DEEP_RESEARCH", "1")
    monkeypatch.setattr(
        "trade_integrations.dataflows.hub_wiki.research.chat_wiki",
        lambda *_a, **_k: {"ok": True, "message": "Answer one."},
    )
    event = _event()
    gap = {"gap_kind": "conflicts", "detail": "conflicting levels"}
    first = export_research_for_event(event, gap=gap, rescan=False)
    assert first["ok"] is True
    second = export_research_for_event(event, gap=gap, rescan=False)
    assert second.get("skipped") is True
    assert second.get("reason") == "already_run_today"
