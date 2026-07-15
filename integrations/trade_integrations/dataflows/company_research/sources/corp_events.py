"""Corporate event forecast — ED-ALPHA HTTP sidecar (US-only, optional)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from ..market import Market, NormalizedTicker
from ..models import StageResult

logger = logging.getLogger(__name__)


def fetch_corp_events(normalized: NormalizedTicker, *, market: Market) -> StageResult:
    if market != Market.US:
        return StageResult(
            stage="corp_events",
            status="skipped",
            vendor="ed_alpha",
            fetched_at=datetime.now(timezone.utc),
            data={"reason": "ED-ALPHA is US SEC 8-K focused"},
        )

    base = os.getenv("ED_ALPHA_BASE_URL", "").strip().rstrip("/")
    if not base:
        return StageResult(
            stage="corp_events",
            status="skipped",
            vendor="ed_alpha",
            fetched_at=datetime.now(timezone.utc),
            data={
                "reason": "ED_ALPHA_BASE_URL not configured",
                "remediation": "Start ED-ALPHA Docker sidecar and set ED_ALPHA_BASE_URL=http://localhost:8000",
            },
        )

    try:
        import requests

        response = requests.get(
            f"{base}/predictions/{normalized.base_symbol}",
            timeout=20,
        )
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status_code}")
        payload = response.json()
    except Exception as exc:
        logger.info("ED-ALPHA request failed: %s", exc)
        return StageResult(
            stage="corp_events",
            status="skipped",
            vendor="ed_alpha",
            fetched_at=datetime.now(timezone.utc),
            data={"reason": str(exc)},
            errors=[str(exc)],
        )

    return StageResult(
        stage="corp_events",
        status="ok",
        vendor="ed_alpha",
        fetched_at=datetime.now(timezone.utc),
        data=payload if isinstance(payload, dict) else {"raw": payload},
    )
