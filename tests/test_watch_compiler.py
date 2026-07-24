"""Tests for watch condition compiler."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from trade_integrations.autonomous_agents.intent_schema import AgentIntent, WatchCondition  # noqa: E402
from trade_integrations.autonomous_agents.watch_compiler import compile_watch_from_intent  # noqa: E402


def test_schedule_only_no_default_spot_rule() -> None:
    intent = AgentIntent(
        engagement="observe",
        instruments=["index"],
        symbols=["NIFTY"],
        watch_conditions=[
            WatchCondition(kind="schedule", symbol="NIFTY", params={"every_min": 3}, label="poll"),
        ],
        clarified={"schedules": True, "watch_conditions": True},
    )
    schedules, spec = compile_watch_from_intent(intent)
    assert schedules["watch_ms"] == 180_000
    assert spec["rules"] == []


def test_price_level_rules() -> None:
    intent = AgentIntent(
        symbols=["NIFTY"],
        watch_conditions=[
            WatchCondition(
                kind="price_level",
                symbol="NIFTY",
                params={"above": 24500, "below": 24000},
            ),
        ],
        clarified={"watch_conditions": True},
    )
    _, spec = compile_watch_from_intent(intent)
    metrics = {row["metric"] for row in spec["rules"]}
    assert metrics == {"level_above", "level_below"}


def test_price_move_points_without_spot_skips_rule() -> None:
    intent = AgentIntent(
        symbols=["NIFTY"],
        watch_conditions=[
            WatchCondition(kind="price_move", symbol="NIFTY", params={"points": 50}),
        ],
        clarified={"watch_conditions": True},
    )
    _, spec = compile_watch_from_intent(intent)
    assert spec["rules"] == []


def test_price_move_points_with_spot() -> None:
    intent = AgentIntent(
        symbols=["NIFTY"],
        watch_conditions=[
            WatchCondition(kind="price_move", symbol="NIFTY", params={"points": 50}),
        ],
        clarified={"watch_conditions": True},
    )
    _, spec = compile_watch_from_intent(intent, spot=24500.0)
    assert len(spec["rules"]) == 1
    assert spec["rules"][0]["metric"] == "spot_move_pct"
    assert spec["rules"][0]["threshold"] > 0
