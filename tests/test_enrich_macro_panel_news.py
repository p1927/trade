"""Panel-first news merge in enrich_macro_with_news_features."""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_enrich_macro_preserves_panel_news_columns(monkeypatch):
    from trade_integrations.dataflows.index_research.event_overlay import enrich_macro_with_news_features

    def _hub_zeros(_day, *, ticker="NIFTY"):
        return {key: 0.0 for key in (
            "news_material_7d",
            "news_war_7d",
            "news_surprise_7d",
        )}

    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.news_event_features.compute_news_features_for_day",
        _hub_zeros,
    )
    panel_row = {
        "usd_inr": 83.0,
        "news_material_7d": 4.5,
        "news_war_7d": 1.2,
    }
    out = enrich_macro_with_news_features(panel_row, as_of_day="2020-03-15", ticker="NIFTY")
    assert out["news_material_7d"] == pytest.approx(4.5)
    assert out["news_war_7d"] == pytest.approx(1.2)
    assert out["news_surprise_7d"] == pytest.approx(0.0)
