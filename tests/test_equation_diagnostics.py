"""Tests for equation diagnostics."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.index_research.equation_diagnostics import (
    FACTOR_BLOCKS,
    LOGIC_CONFLICTS,
    _sign_conflicts,
)


@pytest.mark.unit
def test_factor_blocks_include_joint_flows():
    assert "joint_flows" in FACTOR_BLOCKS
    assert "institutional_net_5d" in FACTOR_BLOCKS["joint_flows"]
    assert "delta" not in FACTOR_BLOCKS


@pytest.mark.unit
def test_logic_conflict_register_populated():
    assert len(LOGIC_CONFLICTS) >= 3


@pytest.mark.unit
def test_sign_conflicts_detects_literature_mismatch():
    conflicts = _sign_conflicts(
        {"fii_net_5d": -0.2},
        [{"factor": "fii_net_5d", "corr_forward_return": 0.3}],
    )
    assert any(c.get("conflict") == "coef_vs_literature" for c in conflicts)
