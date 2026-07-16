"""Tests for JSON-safe serialization."""

from __future__ import annotations

import json

import pytest

from trade_integrations.dataflows.json_safe import json_safe


@pytest.mark.unit
def test_json_safe_breaks_cycles():
    cyclic: dict = {}
    cyclic["self"] = cyclic
    safe = json_safe(cyclic)
    json.dumps(safe)
    assert safe["self"] == "<circular>"


@pytest.mark.unit
def test_json_safe_numpy_scalar():
    try:
        import numpy as np
    except ImportError:
        pytest.skip("numpy not installed")
    assert json_safe(np.float64(1.5)) == 1.5
