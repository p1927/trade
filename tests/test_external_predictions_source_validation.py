"""Tests for user add-source validation."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.external_predictions.source_validation import (
    validate_user_source_request,
)


def test_validate_user_source_requires_domain_and_entry_url() -> None:
    domains, urls, err = validate_user_source_request(
        display_name="My Broker",
        domains=["example.com"],
        entry_urls=["https://www.example.com/markets"],
    )
    assert err is None
    assert domains == ["example.com"]
    assert urls == ["https://www.example.com/markets"]


def test_validate_user_source_rejects_mismatched_entry_host() -> None:
    _, _, err = validate_user_source_request(
        display_name="My Broker",
        domains=["example.com"],
        entry_urls=["https://other.com/markets"],
    )
    assert err is not None
    assert "must match" in err


def test_validate_user_source_allows_update_without_entry_urls() -> None:
    domains, urls, err = validate_user_source_request(
        display_name="My Broker",
        domains=["example.com"],
        entry_urls=[],
        require_entry_urls=False,
    )
    assert err is None
    assert domains == ["example.com"]
    assert urls == []
