"""Tests for light refresh pipeline activity log and save guards."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trade_integrations.dataflows.index_research.models import IndexResearchDoc
from trade_integrations.dataflows.index_research.light_refresh import (
    _concurrent_full_run_saved,
    _coerce_utc,
)


@pytest.mark.unit
def test_coerce_utc_parses_iso_strings():
    dt = _coerce_utc("2026-07-17T09:11:54.215813+00:00")
    assert dt is not None
    assert dt.tzinfo is not None


@pytest.mark.unit
def test_concurrent_full_run_saved_detects_newer_done_log():
    start = datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc)
    existing = IndexResearchDoc(
        ticker="NIFTY",
        as_of=start + timedelta(minutes=5),
        pipeline_log=[
            {"stage": "start", "message": "full run", "at": (start + timedelta(minutes=5)).isoformat()},
            {"stage": "done", "message": "complete", "at": (start + timedelta(minutes=5)).isoformat()},
        ],
    )
    assert _concurrent_full_run_saved(disk_as_of_at_start=start, existing=existing) is True


@pytest.mark.unit
def test_concurrent_full_run_saved_ignores_older_hub():
    start = datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc)
    existing = IndexResearchDoc(
        ticker="NIFTY",
        as_of=start - timedelta(minutes=5),
        pipeline_log=[{"stage": "done", "message": "complete", "at": start.isoformat()}],
    )
    assert _concurrent_full_run_saved(disk_as_of_at_start=start, existing=existing) is False


@pytest.mark.unit
def test_is_light_refresh_only_log():
    from trade_integrations.context.hub import _is_light_refresh_only_log

    assert _is_light_refresh_only_log([{"stage": "light_refresh"}, {"stage": "done"}]) is True
    assert _is_light_refresh_only_log([{"stage": "start"}, {"stage": "done"}]) is False


@pytest.mark.unit
def test_light_refresh_emits_self_contained_pipeline_log(monkeypatch, tmp_path):
    pytest.importorskip("sklearn")
    from trade_integrations.context import hub as hub_mod
    from trade_integrations.dataflows.company_research.models import StageResult
    from trade_integrations.dataflows.index_research.light_refresh import run_index_light_refresh
    from trade_integrations.dataflows.index_research.models import ConstituentSignal

    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.light_refresh.batch_constituent_research",
        lambda **_: [
            ConstituentSignal(symbol="RELIANCE", weight=0.5, sentiment_score=0.1, momentum_7d_pct=1.0),
        ],
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.light_refresh.fetch_global_macro_snapshot",
        lambda **_: StageResult(
            stage="macro_global",
            status="ok",
            vendor="test",
            fetched_at=datetime.now(timezone.utc),
            data={"factors": {"usd_inr": 83.0, "india_vix": 14.0}, "factor_rows": []},
            errors=[],
        ),
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.light_refresh._fetch_spot",
        lambda _: 24500.0,
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.light_refresh._nifty_trend_20d",
        lambda: "up",
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.light_refresh._material_news_for_index",
        lambda _: ["RELIANCE results"],
    )
    monkeypatch.setattr(
        "trade_integrations.dataflows.index_research.light_refresh.append_prediction",
        lambda *_, **__: None,
    )

    doc, reason = run_index_light_refresh("NIFTY", horizon_days=14, force=True)

    assert reason in {"material_news", "macro_drift", "forced"}
    assert doc.pipeline_log
    stages = {row["stage"] for row in doc.pipeline_log}
    assert "light_refresh" in stages
    assert "start" not in stages
    assert "done" in stages
    assert doc.horizon.get("days") == 14
    first = doc.pipeline_log[0]["message"]
    assert "14d" in first or "14" in first
    last_at = doc.pipeline_log[-1]["at"]
    assert last_at
    assert abs(
        datetime.fromisoformat(last_at).timestamp() - doc.as_of.timestamp()
    ) < 5
