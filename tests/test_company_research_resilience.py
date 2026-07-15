"""Unit tests for multi-source resilience helpers."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.company_research.sources.resilience import (
    SourceAttempt,
    classify_error,
    merge_identity_fields,
    resolve_bse_scrip_code,
    run_sources,
)


@pytest.mark.unit
class TestResilience:
    def test_classify_nse_403(self):
        assert classify_error("HTTP 403: Access Denied") == "nse_403"

    def test_run_sources_collects_all(self):
        attempts = run_sources(
            [
                ("a", lambda: {"name": "Alpha"}),
                ("b", lambda: None),
                ("c", lambda: {"sector": "Energy"}),
            ]
        )
        assert len(attempts) == 3
        assert sum(1 for a in attempts if a.status == "ok") == 2

    def test_merge_identity_fills_gaps(self):
        attempts = [
            SourceAttempt(name="yfinance", status="ok", data={"name": "Reliance", "sector": "Energy"}),
            SourceAttempt(name="dalal_bse", status="ok", data={"pe_ratio": "39.98", "sector": ""}),
        ]
        merged = merge_identity_fields(attempts)
        assert merged["name"] == "Reliance"
        assert merged["sector"] == "Energy"
        assert merged["pe_ratio"] == "39.98"
        assert "yfinance" in merged["sources"]

    def test_bse_code_defaults(self):
        assert resolve_bse_scrip_code("RELIANCE") == "500325"
        assert resolve_bse_scrip_code("UNKNOWNXYZ") is None
