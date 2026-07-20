"""Unit tests for Phase I coverage gates."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.phase_i_coverage import (
    PHASE_I_ABLATION_BLOCKS,
    audit_phase_i_coverage,
    phase_i_keys_for_ridge,
    summarize_phase_i_ablation,
)


@pytest.mark.unit
def test_phase_i_coverage_eligible_when_sufficient():
    rows = 200
    frame = pd.DataFrame(
        {
            "nifty_earnings_yield": [5.0] * rows,
            "equity_risk_premium": [0.5] * rows,
        }
    )
    audit = audit_phase_i_coverage(frame)
    assert "nifty_earnings_yield" in audit["ridge_eligible"]
    assert phase_i_keys_for_ridge(frame) == ("nifty_earnings_yield", "equity_risk_premium")
    assert "ablation" in audit
    assert audit["ablation"]["accept_gate_pp"] == 3.0


@pytest.mark.unit
def test_phase_i_coverage_rejects_sparse():
    frame = pd.DataFrame({"nifty_earnings_yield": [5.0, None, None, None, None]})
    audit = audit_phase_i_coverage(frame)
    assert audit["ridge_eligible"] == []


@pytest.mark.unit
def test_phase_i_ablation_reads_diagnostics(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    hub.mkdir()
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    diag_dir = hub / "NIFTY" / "index_research"
    diag_dir.mkdir(parents=True)
    payload = {
        "as_of": "2026-07-20T00:00:00+00:00",
        "baseline_direction_hit_rate": 0.48,
        "block_ablation": [
            {
                "block": "phase_i_valuation",
                "delta_pp": 3.5,
                "baseline_hit_rate": 0.48,
                "direction_hit_rate_without_block": 0.515,
            }
        ],
    }
    (diag_dir / "equation_diagnostics_latest.json").write_text(json.dumps(payload), encoding="utf-8")

    summary = summarize_phase_i_ablation(ticker="NIFTY")
    assert summary["diagnostics_available"] is True
    valuation = next(g for g in summary["groups"] if g["block"] == "phase_i_valuation")
    assert valuation["promotion_ready"] is True
    assert len(summary["groups"]) == len(PHASE_I_ABLATION_BLOCKS)
