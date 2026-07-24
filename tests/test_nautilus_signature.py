"""Tests for Nautilus registry runtime signature (restart detection)."""

from __future__ import annotations

import pytest

from trade_integrations.watch_registry.nautilus_signature import (
    canonical_watch_spec,
    nautilus_registry_signature,
)


@pytest.mark.unit
def test_canonical_watch_spec_sorts_rules_stably() -> None:
    spec = {
        "rules": [
            {"symbol": "BANKNIFTY", "metric": "spot_move_pct", "threshold": 1.0},
            {"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 0.5},
        ],
        "cooldown_sec": 300,
    }
    normalized = canonical_watch_spec(spec)
    assert [row["symbol"] for row in normalized["rules"]] == ["BANKNIFTY", "NIFTY"]


@pytest.mark.unit
def test_nautilus_registry_signature_detects_rule_only_change() -> None:
    base = [
        {
            "agent_id": "aa_rule",
            "market": "IN",
            "symbols": ["NIFTY"],
            "watch_spec": {
                "rules": [{"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 0.5}],
                "cooldown_sec": 300,
            },
        }
    ]
    changed = [
        {
            "agent_id": "aa_rule",
            "market": "IN",
            "symbols": ["NIFTY"],
            "watch_spec": {
                "rules": [{"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 1.0}],
                "cooldown_sec": 300,
            },
        }
    ]
    assert nautilus_registry_signature(base) != nautilus_registry_signature(changed)


@pytest.mark.unit
def test_nautilus_registry_signature_ignores_symbol_order() -> None:
    left = [
        {
            "agent_id": "aa_sym",
            "market": "IN",
            "symbols": ["NIFTY", "BANKNIFTY"],
            "watch_spec": {"rules": [], "cooldown_sec": 300},
        }
    ]
    right = [
        {
            "agent_id": "aa_sym",
            "market": "IN",
            "symbols": ["BANKNIFTY", "NIFTY"],
            "watch_spec": {"rules": [], "cooldown_sec": 300},
        }
    ]
    assert nautilus_registry_signature(left) == nautilus_registry_signature(right)


@pytest.mark.unit
def test_nautilus_registry_signature_detects_gate_only_change() -> None:
    base = [
        {
            "agent_id": "aa_gate",
            "market": "IN",
            "symbols": ["NIFTY"],
            "watch_spec": {
                "rules": [{"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 0.5}],
                "cooldown_sec": 300,
                "gate": {"skip_if_unchanged_minutes": 5},
            },
        }
    ]
    changed = [
        {
            "agent_id": "aa_gate",
            "market": "IN",
            "symbols": ["NIFTY"],
            "watch_spec": {
                "rules": [{"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 0.5}],
                "cooldown_sec": 300,
                "gate": {"skip_if_unchanged_minutes": 15},
            },
        }
    ]
    assert nautilus_registry_signature(base) != nautilus_registry_signature(changed)
