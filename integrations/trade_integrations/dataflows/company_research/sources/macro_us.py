"""US macro — FRED + Polymarket via TradingAgents vendors."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..models import StageResult
from .resilience import SourceAttempt, remediation_for, stage_status_from_attempts

logger = logging.getLogger(__name__)


def _stage_now() -> datetime:
    return datetime.now(timezone.utc)


def fetch_macro_us() -> StageResult:
    attempts: list[SourceAttempt] = []
    merged: dict = {"market": "US"}

    try:
        from tradingagents.dataflows.interface import get_fred_macro_data

        fred = get_fred_macro_data()
        if fred and "unavailable" not in fred.lower()[:100]:
            attempts.append(SourceAttempt(name="fred", status="ok", data={"excerpt": fred[:2000]}))
            merged["fred_excerpt"] = fred[:2000]
        else:
            attempts.append(
                SourceAttempt(
                    name="fred",
                    status="skipped",
                    error="FRED_API_KEY missing or no data",
                    remediation="Set FRED_API_KEY in .env",
                )
            )
    except Exception as exc:
        attempts.append(SourceAttempt(name="fred", status="error", error=str(exc)))

    try:
        from tradingagents.dataflows.interface import get_polymarket_prediction_markets

        poly = get_polymarket_prediction_markets("Fed rate recession S&P 2026", limit=5)
        if poly:
            attempts.append(SourceAttempt(name="polymarket", status="ok", data={"excerpt": poly[:2000]}))
            merged["polymarket_excerpt"] = poly[:2000]
        else:
            attempts.append(
                SourceAttempt(name="polymarket", status="error", error="no data", remediation=remediation_for("no_data"))
            )
    except Exception as exc:
        attempts.append(SourceAttempt(name="polymarket", status="error", error=str(exc)))

    ok = [a.name for a in attempts if a.status == "ok"]
    return StageResult(
        stage="macro",
        status=stage_status_from_attempts(attempts, has_output=bool(ok)),
        vendor="+".join(ok) if ok else "macro_us",
        fetched_at=_stage_now(),
        data={**merged, "source_attempts": [a.to_dict() for a in attempts]},
        errors=[f"{a.name}: {a.error}" for a in attempts if a.status != "ok" and a.error],
    )
