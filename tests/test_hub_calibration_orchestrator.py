"""Tests for unified hub calibration orchestrator."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from trade_integrations.hub_analytics.calibration_orchestrator import (
    run_evening_hub_maintenance,
    run_morning_hub_calibration,
)
from trade_integrations.hub_analytics.manifest import build_manifest, write_hub_manifest


@pytest.fixture
def hub(tmp_path, monkeypatch):
    data = tmp_path / "_data"
    (data / "index_predictions").mkdir(parents=True)
    (data / "options_predictions").mkdir(parents=True)
    (data / "autonomous_agents").mkdir(parents=True)
    (data / "trades").mkdir(parents=True)
    (data / "index_factors" / "daily").mkdir(parents=True)
    (tmp_path / "NIFTY" / "options_research").mkdir(parents=True)
    (tmp_path / "NIFTY" / "options_research" / "latest.json").write_text("{}", encoding="utf-8")
    pd.DataFrame([{"underlying": "NIFTY", "strategy_name": "iron_condor"}]).to_parquet(
        data / "options_predictions" / "ledger.parquet",
        index=False,
    )
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    return tmp_path


def test_morning_dry_run():
    summary = run_morning_hub_calibration({"dry_run": True})
    assert summary["status"] == "dry_run"
    assert summary["phase"] == "morning"


def test_evening_dry_run(hub):
    summary = run_evening_hub_maintenance({"dry_run": True})
    assert summary["status"] == "dry_run"
    assert summary["phase"] == "evening"


def test_write_manifest_includes_calibration(hub):
    pd.DataFrame(
        [{"strategy": "iron_condor", "net_pnl_inr": 10.0, "action": "CLOSE", "intent_source": "paper"}]
    ).to_parquet(hub / "_data" / "autonomous_agents" / "outcomes.parquet", index=False)
    result = write_hub_manifest(sync_executions=False)
    assert Path(result["path"]).is_file()
    manifest = build_manifest(hub)
    assert manifest.get("calibration") is not None
    assert manifest.get("analytics", {}).get("engine") == "duckdb"
