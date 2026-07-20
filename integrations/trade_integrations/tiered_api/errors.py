"""Exceptions for tiered external API queue + hub cache."""

from __future__ import annotations


class TieredApiError(RuntimeError):
    """Base error for tiered API layer."""


class TieredApiNotConfiguredError(TieredApiError):
    """Raised when a tiered source has no API key/token configured."""


class TieredApiBudgetExhausted(TieredApiError):
    """Raised when daily call budget for a source is exhausted."""

    def __init__(self, source: str, *, calls: int, limit: int) -> None:
        self.source = source
        self.calls = calls
        self.limit = limit
        super().__init__(
            f"Tiered API budget exhausted for {source}: {calls}/{limit} calls today (UTC)"
        )


class TieredApiDisabledError(TieredApiError):
    """Raised when tiered APIs are blocked by fetch policy (e.g. Nifty-50 batch)."""


class TieredApiSourceUnknownError(TieredApiError):
    """Raised when source key is not registered."""
