"""Unit tests for Phase 3 browse markdown and trade-plan views."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trade_integrations.dataflows.options_research.browse_summary import (
    build_browse_summary,
    format_browse_markdown,
)


@pytest.mark.unit
class TestFormatBrowseMarkdown:
    def test_renders_table(self):
        summary = build_browse_summary(
            {
                "underlying": "NIFTY",
                "underlying_ltp": 24000,
                "atm_strike": 24000,
                "expiry_date": "21JUL26",
                "expiries": ["21JUL26"],
                "pcr": 1.0,
                "chain": [
                    {
                        "strike": 24000,
                        "ce": {"ltp": 100, "oi": 5000, "iv": 18},
                        "pe": {"ltp": 95, "oi": 4800, "iv": 17},
                    }
                ],
            }
        )
        md = format_browse_markdown(summary)
        assert "NIFTY" in md
        assert "24000" in md
        assert "| Strike |" in md


@pytest.mark.unit
class TestTradePlanBrowseView:
    def test_hub_json_has_browse_for_api(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
        hub_file = tmp_path / "NIFTY" / "options_research" / "latest.json"
        hub_file.parent.mkdir(parents=True)
        payload = {
            "underlying": "NIFTY",
            "browse_summary": {"spot": 24000, "atm_strike": 24000, "top_strikes": []},
            "recommended": {"name": "long_straddle"},
        }
        hub_file.write_text(json.dumps(payload), encoding="utf-8")

        loaded = json.loads(hub_file.read_text())
        browse = loaded.get("browse_summary") or {}
        assert browse.get("spot") == 24000
