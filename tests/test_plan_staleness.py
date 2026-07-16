"""Unit tests for options plan staleness monitor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trade_integrations.monitor.config import get_monitor_config
from trade_integrations.monitor.plan_staleness import evaluate_plan_staleness
from trade_integrations.monitor.service import MonitorService


class _FakeDoc:
    """Duck-typed hub research doc for staleness tests."""

    def __init__(
        self,
        *,
        underlying: str = "NIFTY",
        spot: float = 24500.0,
        as_of: datetime | None = None,
        prediction: dict | None = None,
        expiry: str = "30JUL25",
    ):
        self.underlying = underlying
        self.spot = spot
        self.as_of = as_of or datetime.now(timezone.utc)
        self.prediction = prediction or {"view": "neutral"}
        self.expiry = expiry


@pytest.mark.unit
def test_spot_drift_marks_stale(monkeypatch):
    monkeypatch.setenv("OPTIONS_REALTIME_MONITOR_ENABLED", "true")
    cfg = get_monitor_config()
    now = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
    doc = _FakeDoc(spot=24500.0, as_of=now - timedelta(minutes=5))
    live_spot = 24500.0 * (1 + (cfg.spot_drift_pct + 0.5) / 100)

    report = evaluate_plan_staleness(doc, live_spot=live_spot, now=now)

    assert report.status == "stale"
    assert report.spot_drift_pct > cfg.spot_drift_pct
    assert "spot_drift" in report.reasons
    assert report.suggested_action == "re_recommend"


@pytest.mark.unit
def test_fresh_within_threshold(monkeypatch):
    monkeypatch.setenv("OPTIONS_REALTIME_MONITOR_ENABLED", "true")
    cfg = get_monitor_config()
    now = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
    doc = _FakeDoc(spot=24500.0, as_of=now - timedelta(minutes=5))
    drift = cfg.spot_drift_pct * 0.5
    live_spot = 24500.0 * (1 + drift / 100)

    report = evaluate_plan_staleness(doc, live_spot=live_spot, now=now)

    assert report.status == "fresh"
    assert report.suggested_action == "none"
    assert report.age_minutes <= cfg.max_age_minutes


@pytest.mark.unit
def test_monitor_disabled_returns_none_from_service(monkeypatch):
    monkeypatch.setenv("OPTIONS_REALTIME_MONITOR_ENABLED", "false")

    service = MonitorService()
    assert service.is_enabled() is False
    assert service.evaluate_ticker("NIFTY") is None

    doc = _FakeDoc()
    report = service.evaluate_doc(doc)
    assert report.status == "fresh"
    assert "monitor_disabled" in report.reasons
