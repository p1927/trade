"""Tests for maintainer safety sweep."""

from __future__ import annotations

import pytest


@pytest.fixture
def hub_tmp(tmp_path, monkeypatch):
    from trade_integrations.context import hub as hub_mod

    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setattr(hub_mod, "get_hub_dir", lambda: hub)
    return hub


def test_safety_sweep_respects_disabled_flag(hub_tmp, monkeypatch):
    monkeypatch.setenv("HUB_NEWS_POST_UPSERT_SAFETY_SCAN", "0")
    from trade_integrations.dataflows.index_research.news_maintainer_safety_sweep import (
        run_maintenance_safety_sweep,
    )

    result = run_maintenance_safety_sweep(ticker="NIFTY")
    assert result.get("skipped") is True
    assert result.get("reason") == "disabled"
