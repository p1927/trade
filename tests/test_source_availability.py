"""Tests for in-process source availability circuit breaker."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows import source_availability as sa


@pytest.fixture(autouse=True)
def _reset_availability(monkeypatch, tmp_path):
    monkeypatch.setattr("trade_integrations.context.hub.get_hub_dir", lambda: tmp_path)
    sa.clear_availability_cache()
    yield
    sa.clear_availability_cache()


@pytest.mark.unit
def test_not_installed_opens_circuit_immediately():
    vendor, capability = "tapetide", "identity"
    assert sa.should_attempt(vendor, capability) is True

    sa.record_failure(vendor, capability, "No module named 'tapetide'")

    assert sa.should_attempt(vendor, capability) is False
    assert sa.get_status(vendor, capability) == sa.SourceStatus.UNAVAILABLE


@pytest.mark.unit
def test_success_closes_circuit():
    vendor, capability = "yfinance", "fundamentals"
    sa.record_failure(vendor, capability, "connection refused")
    sa.record_failure(vendor, capability, "connection refused")
    sa.record_failure(vendor, capability, "connection refused")
    assert sa.should_attempt(vendor, capability) is False

    sa.record_success(vendor, capability)

    assert sa.should_attempt(vendor, capability) is True
    assert sa.get_status(vendor, capability) == sa.SourceStatus.AVAILABLE
    assert sa.list_all_statuses().get(f"{vendor}:{capability}") == sa.SourceStatus.AVAILABLE
