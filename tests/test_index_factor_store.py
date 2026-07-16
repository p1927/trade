"""Unit tests for index research factor store."""

from __future__ import annotations

import pytest


@pytest.mark.unit
def test_save_and_load_daily_factors(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    from trade_integrations.dataflows.index_research.factor_store import (
        load_factor_history,
        save_daily_factors,
    )

    rows = [{"factor": "usd_inr", "value": 83.2, "z_score": 0.1}]
    save_daily_factors("2026-07-16", rows)
    df = load_factor_history("2026-07-16", "2026-07-16")
    assert len(df) == 1
    assert df.iloc[0]["factor"] == "usd_inr"
