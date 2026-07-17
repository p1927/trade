"""Registry + multi-agent node bootstrap tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.registry_paths import read_registry_agent_ids  # noqa: E402


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    hub = tmp_path / "hub"
    hub.mkdir()
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    monkeypatch.setenv("TRADE_STACK_ROOT", str(tmp_path))
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return tmp_path


def test_read_registry_agent_ids_from_file(hub_tmp: Path):
    registry = {
        "agents": [
            {"agent_id": "aa_in", "market": "IN", "symbols": ["NIFTY"]},
            {"agent_id": "aa_us", "market": "US", "symbols": ["SPY"]},
        ]
    }
    (hub_tmp / "log" / "nautilus-watch.agents.json").write_text(json.dumps(registry), encoding="utf-8")
    assert read_registry_agent_ids() == ["aa_in", "aa_us"]


def test_build_trading_node_config_multi_agent(hub_tmp: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("nautilus_trader.cache.config")
    from nautilus_openalgo_bridge.node import build_trading_node_config

    hub = hub_tmp / "hub"
    agents_dir = hub / "_data" / "autonomous_agents"
    agents_dir.mkdir(parents=True)
    for aid, sym, market in (
        ("aa_in", "NIFTY", "IN"),
        ("aa_us", "SPY", "US"),
    ):
        (agents_dir / f"{aid}.json").write_text(
            json.dumps(
                {
                    "id": aid,
                    "symbols": [sym],
                    "execution_market": market,
                    "constraints": {"max_daily_loss_inr": 1500},
                    "watch_spec": {
                        "rules": [
                            {"symbol": sym, "metric": "spot_move_pct", "threshold": 0.5, "direction": "either"},
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )

    cfg = build_trading_node_config(agent_ids=["aa_in", "aa_us"])
    assert "OPENALGO" in cfg.data_clients
    assert "ALPACA" in cfg.data_clients
    # 2 × (Watch + BridgeSignal) + 2 × RiskActor
    assert len(cfg.actors) == 6
