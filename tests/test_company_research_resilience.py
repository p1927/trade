"""Unit tests for multi-source resilience helpers."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.company_research.sources.resilience import (
    SourceAttempt,
    classify_error,
    merge_identity_fields,
    resolve_bse_scrip_code,
    run_sources,
    stage_errors,
    stage_status_from_attempts,
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
        assert sum(1 for a in attempts if a.status == "error") == 1

    def test_run_sources_optional_skips_silently(self):
        attempts = run_sources(
            [
                ("core", lambda: {"name": "Reliance"}),
                ("fragile", lambda: None),
            ],
            optional=frozenset({"fragile"}),
        )
        assert attempts[0].status == "ok"
        assert attempts[1].status == "skipped"
        assert attempts[1].error == "no data"

    def test_stage_status_ignores_optional_failures(self):
        attempts = [
            SourceAttempt(name="bse_india", status="ok", data={"events": []}),
            SourceAttempt(name="yfinance", status="ok", data={"events": []}),
        ]
        assert stage_status_from_attempts(attempts, has_output=True, stage="calendar") == "ok"
        assert stage_errors(attempts, stage="calendar") == []

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
