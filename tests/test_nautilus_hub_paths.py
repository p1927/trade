"""Tests for Nautilus bridge hub path helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, get_type_hints

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))


def test_hub_paths_resolves_type_hints():
    import nautilus_openalgo_bridge.hub_paths as hub_paths

    hints = get_type_hints(hub_paths.load_agent_json)
    assert hints["return"] == dict[str, Any]


def test_load_agent_json_missing_returns_empty(tmp_path, monkeypatch):
    import nautilus_openalgo_bridge.hub_paths as hub_paths

    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    assert hub_paths.load_agent_json("aa_missing") == {}
