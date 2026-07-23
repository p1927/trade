"""Unit tests for mandate_config and mandate_enforcer."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from trade_integrations.autonomous_agents.mandate_config import (  # noqa: E402
    MandateConfig,
    parse_mandate_from_text,
    scheduled_actions_for,
    to_watch_spec,
)
from trade_integrations.autonomous_agents.mandate_enforcer import (  # noqa: E402
    MandateViolation,
    assert_can_execute,
    validate_decision,
)


def test_parse_intraday_mandate():
    cfg = parse_mandate_from_text(
        "Intraday NIFTY options, flatten by close",
        symbols=["NIFTY"],
    )
    assert cfg.holding_period == "intraday"
    assert cfg.flatten_policy == "session_close"
    assert cfg.product_type == "MIS"
    assert cfg.needs_session_close_flatten()


def test_parse_swing_mandate():
    cfg = parse_mandate_from_text(
        "Hold RELIANCE swing until earnings",
        symbols=["RELIANCE"],
    )
    assert cfg.holding_period == "multi_day"
    assert cfg.product_type == "NRML"
    assert not cfg.needs_session_close_flatten()


def test_parse_vix_alert():
    cfg = parse_mandate_from_text(
        "Watch BANKNIFTY, don't trade until VIX > 14",
        symbols=["BANKNIFTY"],
    )
    assert cfg.alert_rules.vix_above == 14.0
    assert cfg.revision_policy == "user_guidance_only"


def test_to_watch_spec_builds_rules():
    cfg = MandateConfig()
    cfg.alert_rules.spot_move_pct = 0.5
    cfg.alert_rules.vix_above = 14.0
    spec = to_watch_spec(cfg, symbols=["NIFTY"])
    assert len(spec["rules"]) == 2
    assert spec["rules"][0]["symbol"] == "NIFTY"
    assert spec["rules"][1]["symbol"] == "INDIAVIX"


def test_scheduled_actions_intraday():
    cfg = parse_mandate_from_text("Intraday flatten by close", symbols=["NIFTY"])
    actions = scheduled_actions_for(cfg)
    assert "session_close_flatten" in actions


def test_scheduled_actions_swing():
    cfg = parse_mandate_from_text("Swing trade RELIANCE", symbols=["RELIANCE"])
    actions = scheduled_actions_for(cfg)
    assert "session_close_flatten" not in actions


def test_assert_can_execute_halted():
    session = {"enabled": True, "halted": True, "halt_reason": "daily_loss"}
    with pytest.raises(MandateViolation) as exc:
        assert_can_execute(session, mandate=MandateConfig())
    assert exc.value.code == "session_halted"


def test_validate_decision_hold_after_close_intraday(monkeypatch):
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.mandate_enforcer.is_trading_session_open",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        "trade_integrations.autonomous_agents.mandate_enforcer.list_open_entries",
        lambda: [{"widget_id": "tp_test"}],
    )
    session = {
        "enabled": True,
        "mandate_config": MandateConfig(
            holding_period="intraday",
            flatten_policy="session_close",
            market_hours_only=True,
        ).to_dict(),
    }
    decision, warnings = validate_decision("HOLD", session)
    assert decision == "EXIT"
    assert warnings
