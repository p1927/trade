"""US SEC filings — edgartools when SEC_EDGAR_IDENTITY is set."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from ..market import NormalizedTicker
from ..models import StageResult

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def fetch_filings_us(normalized: NormalizedTicker) -> StageResult:
    identity = os.getenv("SEC_EDGAR_IDENTITY", "").strip()
    if not identity:
        return StageResult(
            stage="filings",
            status="skipped",
            vendor="edgartools",
            fetched_at=_stage_now(),
            data={
                "reason": "SEC_EDGAR_IDENTITY not set",
                "remediation": "Set SEC_EDGAR_IDENTITY='Your Name your@email.com' in .env",
            },
            errors=["SEC_EDGAR_IDENTITY missing"],
        )

    try:
        from edgar import Company, set_identity
    except ImportError:
        return StageResult(
            stage="filings",
            status="skipped",
            vendor="edgartools",
            fetched_at=_stage_now(),
            data={"reason": "edgartools not installed"},
            errors=["pip install edgartools"],
        )

    filings: list[dict[str, Any]] = []
    try:
        set_identity(identity)
        company = Company(normalized.base_symbol)
        for filing in company.get_filings(form=["10-Q", "8-K"]).head(8):
            filings.append(
                {
                    "form": getattr(filing, "form", "") or "",
                    "filing_date": str(getattr(filing, "filing_date", "") or ""),
                    "description": str(getattr(filing, "description", "") or "")[:200],
                    "source": "edgartools",
                }
            )
    except Exception as exc:
        logger.info("edgartools filings failed for %s: %s", normalized.base_symbol, exc)
        return StageResult(
            stage="filings",
            status="error",
            vendor="edgartools",
            fetched_at=_stage_now(),
            data={"symbol": normalized.base_symbol},
            errors=[str(exc)],
        )

    return StageResult(
        stage="filings",
        status="ok" if filings else "partial",
        vendor="edgartools",
        fetched_at=_stage_now(),
        data={"symbol": normalized.base_symbol, "filings": filings},
    )
