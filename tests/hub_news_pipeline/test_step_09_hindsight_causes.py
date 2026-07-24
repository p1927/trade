"""Tests for hub news pipeline step 09 — hindsight causes."""

from __future__ import annotations

import pandas as pd

from trade_integrations.dataflows.index_research.hub_news_pipeline.step_09_hindsight_causes import (
    annotate_cause_indicator,
    annotate_future_event,
    build_hindsight_causes_for_ref,
    compare_directions,
    direction_from_return_pct,
    normalize_direction_hint,
    ref_needs_hindsight,
)


def _sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2026-03-10", "2026-03-11", "2026-03-12", "2026-03-13", "2026-03-14", "2026-03-17"],
            "close": [100.0, 101.0, 102.5, 101.5, 103.0, 104.0],
            "fii_net_5d": [10.0, 12.0, 15.0, 14.0, 18.0, 20.0],
            "oil_brent": [80.0, 81.0, 82.0, 79.0, 78.0, 77.0],
        }
    )


def test_normalize_direction_hint():
    assert normalize_direction_hint("bullish") == "bullish"
    assert normalize_direction_hint("BEARISH") == "bearish"
    assert normalize_direction_hint("unclear") == "unclear"


def test_normalize_direction_hint_avoids_substring_false_positives():
    assert normalize_direction_hint("upgrade cycle") == "unclear"
    assert normalize_direction_hint("support levels") == "unclear"
    assert normalize_direction_hint("breakdown of talks") == "unclear"


def test_compare_directions():
    assert compare_directions("bullish", "bullish") == "aligned"
    assert compare_directions("bullish", "bearish") == "contradicted"
    assert compare_directions("unclear", "bullish") == "unverifiable"


def test_direction_from_return_pct():
    assert direction_from_return_pct(1.5) == "bullish"
    assert direction_from_return_pct(-2.0) == "bearish"
    assert direction_from_return_pct(0.05) == "neutral"


def test_annotate_cause_indicator_nifty_aligned():
    frame = _sample_frame()
    row = annotate_cause_indicator(
        {"factor": "nifty", "mechanism": "risk-on", "direction_hint": "bullish"},
        cause_index=0,
        publish_day="2026-03-10",
        frame=frame,
        as_of="2026-03-17",
    )
    assert row is not None
    assert row["alignment"] == "aligned"
    assert row["actual_nifty_return_pct"] == 4.0


def test_annotate_cause_indicator_factor_contradicted():
    frame = _sample_frame()
    row = annotate_cause_indicator(
        {"factor": "oil_brent", "mechanism": "crude spike", "direction_hint": "bearish"},
        cause_index=0,
        publish_day="2026-03-10",
        frame=frame,
        as_of="2026-03-17",
    )
    assert row is not None
    assert row["alignment"] == "contradicted"


def test_annotate_cause_indicator_resolves_mixed_case_factor():
    frame = _sample_frame().rename(columns={"fii_net_5d": "FII_NET_5D"})
    row = annotate_cause_indicator(
        {"factor": "FII_NET_5D", "mechanism": "inflows", "direction_hint": "bullish"},
        cause_index=0,
        publish_day="2026-03-10",
        frame=frame,
        as_of="2026-03-17",
    )
    assert row is not None
    assert row["actual_factor_delta"] == 10.0


def test_annotate_future_event_after_expected_date():
    frame = _sample_frame()
    row = annotate_future_event(
        {
            "event": "RBI MPC",
            "expected_date": "2026-03-12",
            "index_impact_mechanism": "bullish if rate cut",
        },
        frame=frame,
        as_of="2026-03-17",
    )
    assert row is not None
    assert row["window_elapsed"] is True
    assert row["kind"] == "future_event"


def test_annotate_future_event_snaps_weekend_expected_date():
    frame = _sample_frame()
    row = annotate_future_event(
        {
            "event": "Weekend policy",
            "expected_date": "2026-03-15",
            "index_impact_mechanism": "neutral",
        },
        frame=frame,
        as_of="2026-03-17",
    )
    assert row is not None
    assert row["actual_nifty_return_pct"] is not None


def test_annotate_future_event_skips_future_dates():
    frame = _sample_frame()
    row = annotate_future_event(
        {"event": "Budget", "expected_date": "2026-04-01"},
        frame=frame,
        as_of="2026-03-17",
    )
    assert row is None


def test_build_hindsight_causes_skips_article_opinions():
    frame = _sample_frame()
    ref = {
        "structured_enrichment": {
            "cause_indicators": [{"factor": "nifty", "direction_hint": "bullish"}],
            "future_events": [{"event": "RBI", "expected_date": "2026-03-12"}],
            "article_opinions": [{"text": "NIFTY 25000"}],
        }
    }
    rows = build_hindsight_causes_for_ref(
        ref,
        publish_day="2026-03-10",
        frame=frame,
        as_of="2026-03-17",
    )
    assert len(rows) == 2
    assert all(row.get("kind") != "article_opinion" for row in rows)


def test_ref_needs_hindsight():
    ref = {
        "structured_enrichment": {
            "cause_indicators": [{"factor": "fii_net_5d", "direction_hint": "bullish"}],
        }
    }
    assert ref_needs_hindsight(ref, as_of="2026-03-17") is True
    ref["hindsight_causes"] = [
        {
            "kind": "cause_indicator",
            "cause_index": 0,
            "factor": "fii_net_5d",
            "mechanism": "",
            "window_start": "2026-03-10",
        }
    ]
    assert ref_needs_hindsight(ref, as_of="2026-03-17") is False


def test_annotate_cause_indicator_snaps_weekend_publish_day():
    frame = _sample_frame()
    row = annotate_cause_indicator(
        {"factor": "oil_brent", "mechanism": "crude", "direction_hint": "bearish"},
        cause_index=0,
        publish_day="2026-03-15",
        frame=frame,
        as_of="2026-03-17",
    )
    assert row is not None
    assert row["window_start"] == "2026-03-14"
    assert row["actual_factor_delta"] is not None


def test_annotate_cause_indicator_caps_as_of_to_last_session():
    frame = _sample_frame()
    row = annotate_cause_indicator(
        {"factor": "oil_brent", "mechanism": "crude", "direction_hint": "bearish"},
        cause_index=0,
        publish_day="2026-03-10",
        frame=frame,
        as_of="2026-03-16",
    )
    assert row is not None
    assert row["window_end"] == "2026-03-14"
    assert row["actual_factor_delta"] is not None


def test_ref_needs_hindsight_with_duplicate_factors():
    ref = {
        "structured_enrichment": {
            "cause_indicators": [
                {"factor": "fii_net_5d", "mechanism": "selling pressure", "direction_hint": "bearish"},
                {"factor": "fii_net_5d", "mechanism": "buyback support", "direction_hint": "bullish"},
            ],
        }
    }
    assert ref_needs_hindsight(ref, as_of="2026-03-17") is True
    ref["hindsight_causes"] = [
        {
            "kind": "cause_indicator",
            "cause_index": 0,
            "factor": "fii_net_5d",
            "mechanism": "selling pressure",
        }
    ]
    assert ref_needs_hindsight(ref, as_of="2026-03-17") is True
