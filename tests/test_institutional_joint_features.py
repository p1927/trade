"""Tests for institutional joint flow features."""

from __future__ import annotations

import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.sources.history_loader import (
    _append_institutional_joint_columns,
)


@pytest.mark.unit
def test_institutional_joint_columns_from_flows():
    frame = pd.DataFrame(
        {
            "date": ["2026-01-01", "2026-01-02"],
            "fii_net_5d": [-1000.0, -500.0],
            "dii_net_5d": [1500.0, 800.0],
        }
    )
    out = _append_institutional_joint_columns(frame)
    assert out["institutional_net_5d"].iloc[0] == 500.0
    assert out["dii_absorption_ratio"].iloc[0] == pytest.approx(1.5, rel=0.01)
