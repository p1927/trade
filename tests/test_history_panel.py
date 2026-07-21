"""Regression tests for history panel assembly and data integrity invariants."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.panel_invariants import (
    ANNUAL_JOIN_BLOCKLIST,
    PanelInvariantError,
    assert_panel_invariants,
    audit_panel_invariants,
)


def _daily_usd_panel(n: int = 120) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    usd = 83.0 + pd.Series(range(n), dtype=float) * 0.01
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "close": 24000.0 + pd.Series(range(n), dtype=float),
            "usd_inr": usd,
        }
    )


def _annual_macro() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "year": [2024, 2025],
            "usd_inr": [83.9, 84.0],
            "gdp_growth_pct": [7.0, 6.8],
            "granularity": ["annual", "annual"],
        }
    )


@pytest.mark.unit
def test_annual_join_preserves_daily_usd_inr_variance():
    from trade_integrations.dataflows.index_research.history_panel import _join_annual_macro_by_year

    panel = _daily_usd_panel()
    with patch(
        "trade_integrations.dataflows.index_research.history_panel.load_history_dataset",
        return_value=_annual_macro(),
    ):
        out = _join_annual_macro_by_year(panel)

    std = float(pd.to_numeric(out["usd_inr"], errors="coerce").std())
    assert std > 0.05
    assert out["usd_inr"].nunique(dropna=True) > 10
    assert "gdp_growth_pct" in out.columns


@pytest.mark.unit
def test_annual_join_blocklist_excludes_usd_inr():
    assert "usd_inr" in ANNUAL_JOIN_BLOCKLIST


@pytest.mark.unit
def test_annual_join_drop_logic_would_fail_invariants():
    """Simulate the old bug: replacing daily FX with flat annual values."""
    panel = _daily_usd_panel()
    corrupted = panel.copy()
    corrupted["usd_inr"] = 83.9
    corrupted["usd_inr_momentum_5d"] = 0.0

    report = audit_panel_invariants(corrupted, window_days=100, skip_regression=True)
    assert report["ok"] is False
    assert any("pinned_flat:usd_inr" in v or "derived_flat" in v for v in report["violations"])


@pytest.mark.unit
def test_healthy_panel_passes_pinned_invariants():
    panel = _daily_usd_panel()
    panel["oil_brent"] = 80.0 + pd.Series(range(len(panel)), dtype=float) * 0.02
    panel["sp500"] = 5000.0 + pd.Series(range(len(panel)), dtype=float) * 0.5
    panel["us_10y"] = 4.0 + pd.Series(range(len(panel)), dtype=float) * 0.001
    panel["india_vix"] = 14.0 + pd.Series(range(len(panel)), dtype=float) * 0.05
    panel["fii_net_5d"] = 1000.0 + pd.Series(range(len(panel)), dtype=float) * 10
    panel["dii_net_5d"] = 500.0 + pd.Series(range(len(panel)), dtype=float) * 5
    panel["nifty_pcr"] = 1.0 + pd.Series(range(len(panel)), dtype=float) * 0.01
    panel["repo_rate"] = 6.5
    panel["nifty_pe"] = 22.0 + pd.Series(range(len(panel)), dtype=float) * 0.01
    panel["usd_inr_momentum_5d"] = panel["usd_inr"].astype(float).diff(5).fillna(0.05)
    panel["india_vix_velocity_3d"] = panel["india_vix"].astype(float).diff(3).fillna(0.1)
    panel["us_10y_velocity_3d"] = panel["us_10y"].astype(float).diff(3).fillna(0.02)

    with patch(
        "trade_integrations.dataflows.index_research.history_store.load_history_dataset",
        return_value=pd.DataFrame(
            {
                "date": panel["date"],
                "usd_inr": panel["usd_inr"],
                "oil_brent": panel["oil_brent"],
            }
        ),
    ):
        report = assert_panel_invariants(panel, window_days=100, skip_regression=True)
    assert report["ok"] is True


@pytest.mark.unit
def test_save_panel_rejects_corrupted_panel(tmp_path, monkeypatch):
    from trade_integrations.dataflows.index_research import history_store

    monkeypatch.setattr(history_store, "get_panel_dir", lambda: tmp_path)
    monkeypatch.setattr(history_store, "get_hub_dir", lambda: tmp_path)

    bad = _daily_usd_panel(80)
    bad["usd_inr"] = 83.9
    bad["oil_brent"] = 80.0
    bad["sp500"] = 5000.0
    bad["us_10y"] = 4.0
    bad["india_vix"] = 14.0
    bad["fii_net_5d"] = 1000.0
    bad["dii_net_5d"] = 500.0
    bad["nifty_pcr"] = 1.0
    bad["repo_rate"] = 6.5
    bad["nifty_pe"] = 22.0

    with pytest.raises(PanelInvariantError):
        history_store.save_panel(bad, name="test_panel", skip_invariants=False)


@pytest.mark.unit
def test_ffill_lagged_macro_skips_india_vix():
    from trade_integrations.dataflows.index_research.history_panel import _ffill_lagged_macro_columns

    frame = pd.DataFrame(
        {
            "date": ["2026-07-20", "2026-07-21"],
            "close": [24238.5, 24150.8],
            "usd_inr": [96.28, float("nan")],
            "india_vix": [13.0, float("nan")],
        }
    )
    out = _ffill_lagged_macro_columns(frame)
    assert float(out.loc[1, "usd_inr"]) == pytest.approx(96.28)
    assert pd.isna(out.loc[1, "india_vix"])
