"""Corporate event forecast — ED-ALPHA HTTP sidecar (US-only, optional)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from ..market import Market, NormalizedTicker
from ..models import StageResult

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def _map_ed_alpha_status(payload: dict[str, Any]) -> str:
    status = str(payload.get("status") or "ok").lower()
    if status == "ok":
        return "ok"
    if status in {"no_data", "no_score", "not_found"}:
        return "partial"
    return "partial"


def fetch_corp_events(normalized: NormalizedTicker, *, market: Market) -> StageResult:
    if market != Market.US:
        return StageResult(
            stage="corp_events",
            status="skipped",
            vendor="ed_alpha",
            fetched_at=_stage_now(),
            data={"reason": "ED-ALPHA is US SEC 8-K focused"},
        )

    base = os.getenv("ED_ALPHA_BASE_URL", "").strip().rstrip("/")
    if not base:
        return StageResult(
            stage="corp_events",
            status="skipped",
            vendor="ed_alpha",
            fetched_at=_stage_now(),
            data={
                "reason": "ED_ALPHA_BASE_URL not configured",
                "remediation": (
                    "Run ./scripts/setup_ed_alpha.sh then set ED_ALPHA_BASE_URL=http://localhost:8000"
                ),
            },
        )

    try:
        from trade_integrations.http import get

        health = get(f"{base}/health", timeout=10)
        if not health.ok:
            raise RuntimeError(f"health check HTTP {health.status_code}")

        response = get(
            f"{base}/predictions/{normalized.base_symbol}",
            timeout=30,
        )
        if response.status_code == 404:
            raise RuntimeError("predictions endpoint not found; rebuild ed-alpha backend")
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            payload = {"raw": payload}
    except Exception as exc:
        logger.info("ED-ALPHA request failed: %s", exc)
        return StageResult(
            stage="corp_events",
            status="skipped",
            vendor="ed_alpha",
            fetched_at=_stage_now(),
            data={
                "reason": str(exc),
                "remediation": "./scripts/start_ed_alpha.sh",
            },
            errors=[str(exc)],
        )

    stage_status = _map_ed_alpha_status(payload)
    return StageResult(
        stage="corp_events",
        status=stage_status,
        vendor="ed_alpha",
        fetched_at=_stage_now(),
        data=payload,
    )
