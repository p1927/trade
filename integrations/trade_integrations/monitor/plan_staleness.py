"""Evaluate whether a cached options research plan is still actionable."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from trade_integrations.monitor.config import get_monitor_config, is_monitor_enabled

StalenessStatus = Literal["fresh", "stale", "broken"]
SuggestedAction = Literal["none", "refresh", "re_recommend"]


@dataclass
class StalenessReport:
    ticker: str
    status: StalenessStatus
    as_of: datetime | None
    live_spot: float | None
    plan_spot: float | None
    spot_drift_pct: float | None
    age_minutes: float | None
    reasons: list[str]
    suggested_action: SuggestedAction


def _get_attr(doc: Any, name: str, default: Any = None) -> Any:
    if isinstance(doc, dict):
        return doc.get(name, default)
    return getattr(doc, name, default)


def _parse_as_of(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def _spot_drift_pct(plan_spot: float, live_spot: float) -> float:
    if plan_spot == 0:
        return 0.0
    return abs(live_spot - plan_spot) / plan_spot * 100.0


def evaluate_plan_staleness(
    doc: Any,
    *,
    live_spot: float | None = None,
    now: datetime | None = None,
) -> StalenessReport:
    """Score a hub research doc against live spot and age thresholds."""
    ticker = str(_get_attr(doc, "underlying", "") or "").upper()
    plan_spot = _get_attr(doc, "spot")
    as_of = _parse_as_of(_get_attr(doc, "as_of"))
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    if not is_monitor_enabled():
        return StalenessReport(
            ticker=ticker,
            status="fresh",
            as_of=as_of,
            live_spot=live_spot,
            plan_spot=plan_spot,
            spot_drift_pct=None,
            age_minutes=None,
            reasons=["monitor_disabled"],
            suggested_action="none",
        )

    cfg = get_monitor_config()
    reasons: list[str] = []

    if not ticker:
        return StalenessReport(
            ticker="",
            status="broken",
            as_of=as_of,
            live_spot=live_spot,
            plan_spot=plan_spot,
            spot_drift_pct=None,
            age_minutes=None,
            reasons=["missing_ticker"],
            suggested_action="refresh",
        )

    if plan_spot is None or as_of is None:
        missing = []
        if plan_spot is None:
            missing.append("missing_plan_spot")
        if as_of is None:
            missing.append("missing_as_of")
        return StalenessReport(
            ticker=ticker,
            status="broken",
            as_of=as_of,
            live_spot=live_spot,
            plan_spot=plan_spot,
            spot_drift_pct=None,
            age_minutes=None,
            reasons=missing,
            suggested_action="refresh",
        )

    age_minutes = (current - as_of).total_seconds() / 60.0
    drift_pct: float | None = None
    spot_stale = False
    age_stale = age_minutes > cfg.max_age_minutes

    if live_spot is not None:
        drift_pct = _spot_drift_pct(float(plan_spot), float(live_spot))
        if drift_pct > cfg.spot_drift_pct:
            spot_stale = True
            reasons.append("spot_drift")
    else:
        reasons.append("live_spot_unavailable")

    if age_stale:
        reasons.append("age_exceeded")

    if spot_stale or age_stale:
        if spot_stale:
            action: SuggestedAction = "re_recommend"
        else:
            action = "refresh"
        return StalenessReport(
            ticker=ticker,
            status="stale",
            as_of=as_of,
            live_spot=live_spot,
            plan_spot=float(plan_spot),
            spot_drift_pct=drift_pct,
            age_minutes=age_minutes,
            reasons=reasons,
            suggested_action=action,
        )

    return StalenessReport(
        ticker=ticker,
        status="fresh",
        as_of=as_of,
        live_spot=live_spot,
        plan_spot=float(plan_spot),
        spot_drift_pct=drift_pct,
        age_minutes=age_minutes,
        reasons=reasons or ["within_thresholds"],
        suggested_action="none",
    )
