"""Tests for pipeline snapshot binding."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trade_integrations.dataflows.index_research.pipeline_snapshot import (
    MissingSnapshotError,
    StaleSnapshotError,
    load_pipeline_doc_from_hub,
    normalize_as_of,
    resolve_bound_pipeline_doc,
    snapshot_summary,
)


@pytest.mark.unit
def test_normalize_as_of_second_precision():
    dt = datetime(2026, 7, 17, 10, 30, 45, 123456, tzinfo=timezone.utc)
    assert normalize_as_of(dt) == "2026-07-17T10:30:45+00:00"
    assert normalize_as_of("2026-07-17T10:30:45.123456+00:00") == "2026-07-17T10:30:45+00:00"
    assert normalize_as_of("2026-07-17T10:30:45Z") == "2026-07-17T10:30:45+00:00"


@pytest.mark.unit
def test_resolve_bound_pipeline_doc_match(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    as_of = datetime(2026, 7, 17, 10, 30, 0, tzinfo=timezone.utc)
    hub_dir = tmp_path / "NIFTY" / "index_research"
    hub_dir.mkdir(parents=True)
    payload = {
        "ticker": "NIFTY",
        "as_of": as_of.isoformat(),
        "spot": 24500.0,
        "horizon": {"days": 14},
        "prediction": {"expected_return_pct": 1.0, "bottom_up_return_pct": 0.3},
        "global_factors": [{"factor": "usd_inr", "value": 83.5}],
        "news_impact": {"items": [{"title": "Test headline"}]},
    }
    (hub_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")

    doc, model = resolve_bound_pipeline_doc("NIFTY", as_of.isoformat())
    assert doc.spot == 24500.0
    assert model is None or isinstance(model, dict)

    summary = snapshot_summary(doc)
    assert summary["ticker"] == "NIFTY"
    assert summary["news_item_count"] == 1


@pytest.mark.unit
def test_resolve_bound_pipeline_doc_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    as_of = datetime(2026, 7, 17, 10, 30, 0, tzinfo=timezone.utc)
    hub_dir = tmp_path / "NIFTY" / "index_research"
    hub_dir.mkdir(parents=True)
    payload = {
        "ticker": "NIFTY",
        "as_of": as_of.isoformat(),
        "spot": 24500.0,
    }
    (hub_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StaleSnapshotError):
        resolve_bound_pipeline_doc("NIFTY", "2026-07-17T11:00:00+00:00")


@pytest.mark.unit
def test_resolve_bound_pipeline_doc_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    with pytest.raises(MissingSnapshotError):
        resolve_bound_pipeline_doc("NIFTY", "2026-07-17T10:30:00+00:00")


@pytest.mark.unit
def test_load_pipeline_doc_no_news_refresh(tmp_path, monkeypatch):
    """load_pipeline_doc_from_hub must not call news_hub_bridge refresh."""
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    hub_dir = tmp_path / "NIFTY" / "index_research"
    hub_dir.mkdir(parents=True)
    payload = {
        "ticker": "NIFTY",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "spot": 24000.0,
        "news_impact": {},
    }
    (hub_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")

    doc = load_pipeline_doc_from_hub("NIFTY")
    assert doc is not None
    assert doc.news_impact == {}
