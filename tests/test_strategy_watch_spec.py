"""Tests for strategy-derived watch specs."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from trade_integrations.auto_paper.mandate_config import MandateConfig  # noqa: E402
from trade_integrations.autonomous_agents.strategy_watch_spec import (  # noqa: E402
    build_watch_spec_for_strategy,
    format_watch_spec_summary,
)


def test_hold_cash_uses_entry_rules_not_full_mandate_dump():
    mc = MandateConfig()
    mc.alert_rules.vix_above = 18.0
    spec = build_watch_spec_for_strategy(
        strategy="hold_cash",
        mandate=mc,
        symbols=["RELIANCE"],
        target=1200.0,
    )
    metrics = {r["metric"] for r in spec["rules"]}
    assert "spot_move_pct" in metrics
    assert "level_below" in metrics
    assert not any(r.get("symbol") == "INDIAVIX" for r in spec["rules"])
    assert spec["strategy"] == "hold_cash"
    assert "thesis_break" not in spec["review_triggers"]


def test_momentum_breakout_direction_up():
    mc = MandateConfig()
    spec = build_watch_spec_for_strategy(
        strategy="momentum_breakout",
        mandate=mc,
        symbols=["RELIANCE"],
        stop=1280.0,
    )
    move = next(r for r in spec["rules"] if r["metric"] == "spot_move_pct")
    assert move["direction"] == "up"


def test_format_watch_spec_summary_includes_strategy():
    spec = {"strategy": "hold_cash", "rules": [{"label": "RELIANCE entry setup", "metric": "spot_move_pct", "threshold": 1.0, "direction": "either"}]}
    text = format_watch_spec_summary(spec)
    assert "hold_cash" in text
    assert "RELIANCE" in text
