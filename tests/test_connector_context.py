"""Tests for connector-driven execution market resolution."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

AGENT_SRC = Path(__file__).resolve().parents[1] / "vibetrading" / "agent"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))

from trade_integrations.execution.connector_context import (
    connector_execution_backend,
    connector_execution_market,
    connector_execution_path,
    connector_from_profile_id,
    load_active_connector_context,
    load_selected_profile_id,
    symbol_allowed_for_connector_market,
)


@pytest.mark.unit
def test_connector_from_profile_id() -> None:
    assert connector_from_profile_id("openalgo-paper-sdk") == "openalgo"
    assert connector_from_profile_id("alpaca-paper-sdk") == "alpaca"


@pytest.mark.unit
def test_connector_execution_market_mapping() -> None:
    assert connector_execution_market("openalgo") == "IN"
    assert connector_execution_market("alpaca") == "US"
    assert connector_execution_market("ibkr") == "US"


@pytest.mark.unit
def test_connector_execution_path_mapping() -> None:
    assert connector_execution_path("openalgo") == "openalgo"
    assert connector_execution_path("dhan") == "openalgo"
    assert connector_execution_path("shoonya") == "openalgo"
    assert connector_execution_path("alpaca") == "openalgo"
    assert connector_execution_path("ibkr") == "connector_sdk"
    assert connector_execution_path("tiger") == "connector_sdk"
    assert connector_execution_path("robinhood") == "connector_sdk"


@pytest.mark.unit
def test_connector_execution_backend_honest_for_us_connectors() -> None:
    assert connector_execution_backend("alpaca") == "openalgo"
    assert connector_execution_backend("ibkr") == "connector_sdk"
    assert connector_execution_backend("tiger") == "connector_sdk"
    assert connector_execution_backend("openalgo") == "openalgo"


@pytest.mark.unit
def test_connector_execution_path_alpaca_routes_openalgo() -> None:
    assert connector_execution_path("alpaca") == "openalgo"
    assert connector_execution_backend("alpaca") == "openalgo"


@pytest.mark.unit
def test_load_selected_profile_id_from_runtime(tmp_path, monkeypatch) -> None:
    runtime = tmp_path / "vibe-trading"
    runtime.mkdir()
    (runtime / "trading-connections.json").write_text(
        json.dumps({"selected_profile": "alpaca-paper-sdk"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBE_TRADING_RUNTIME_ROOT", str(runtime))
    assert load_selected_profile_id() == "alpaca-paper-sdk"
    ctx = load_active_connector_context()
    assert ctx is not None
    assert ctx.market == "US"
    assert ctx.backend == "openalgo"
    assert ctx.execution_path == "openalgo"


@pytest.mark.unit
def test_load_active_connector_context_ibkr_uses_connector_sdk(tmp_path, monkeypatch) -> None:
    runtime = tmp_path / "vibe-trading"
    runtime.mkdir()
    (runtime / "trading-connections.json").write_text(
        json.dumps({"selected_profile": "ibkr-paper-local"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBE_TRADING_RUNTIME_ROOT", str(runtime))
    ctx = load_active_connector_context()
    assert ctx is not None
    assert ctx.connector == "ibkr"
    assert ctx.market == "US"
    assert ctx.backend == "connector_sdk"
    assert ctx.execution_path == "connector_sdk"
    assert ctx.backend != "alpaca"


@pytest.mark.unit
def test_default_profile_parity_openalgo_paper(monkeypatch) -> None:
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    monkeypatch.setenv("OPENALGO_API_KEY", "test-key")
    monkeypatch.setenv("OPENALGO_HOST", "http://127.0.0.1:5001")
    monkeypatch.setenv("OPENALGO_PAPER_MODE", "true")
    from trade_integrations.execution.default_profile import infer_default_profile_id as trade_infer
    from src.trading.profiles import infer_default_profile_id as vibe_infer

    assert trade_infer() == vibe_infer() == "openalgo-paper-sdk"


@pytest.mark.unit
def test_default_profile_parity_openalgo_live(monkeypatch) -> None:
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    monkeypatch.setenv("OPENALGO_API_KEY", "test-key")
    monkeypatch.setenv("OPENALGO_HOST", "http://127.0.0.1:5001")
    monkeypatch.setenv("OPENALGO_PAPER_MODE", "off")
    from trade_integrations.execution.default_profile import infer_default_profile_id as trade_infer
    from src.trading.profiles import infer_default_profile_id as vibe_infer

    assert trade_infer() == vibe_infer() == "openalgo-live-sdk-readonly"


@pytest.mark.unit
def test_symbol_allowed_for_connector_market() -> None:
    ok, err = symbol_allowed_for_connector_market("NIFTY", "IN")
    assert ok is True
    assert err is None
    ok, err = symbol_allowed_for_connector_market("NIFTY", "US")
    assert ok is False
    assert err is not None


@pytest.mark.unit
def test_autonomous_agent_rejects_alpaca_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VIBE_TRADING_RUNTIME_ROOT", str(tmp_path))
    cfg = tmp_path / "trading-connections.json"
    cfg.write_text(json.dumps({"selected_profile": "alpaca-paper-sdk"}), encoding="utf-8")
    agent = {"id": "aa_test", "type": "autonomous_agent.instance"}
    with pytest.raises(ValueError, match="cannot use Alpaca SDK"):
        load_active_connector_context(agent=agent)


@pytest.mark.unit
def test_non_autonomous_agent_allows_alpaca_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VIBE_TRADING_RUNTIME_ROOT", str(tmp_path))
    cfg = tmp_path / "trading-connections.json"
    cfg.write_text(json.dumps({"selected_profile": "alpaca-paper-sdk"}), encoding="utf-8")
    ctx = load_active_connector_context(agent={"id": "session_xyz"})
    assert ctx is not None
    assert ctx.profile_id == "alpaca-paper-sdk"
