"""Post-extraction validation for NIFTY 50 index street forecasts."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from trade_integrations.dataflows.index_research.external_predictions.models import (
    ExternalPredictionRecord,
    ExternalPredictionTarget,
)

_NIFTY50 = re.compile(r"nifty\s*50|nifty50", re.I)
_OPTIONS_BODY = re.compile(
    r"\b(?:option chain|call option|put option|strike price|f&o segment|"
    r"derivatives segment|mutual fund scheme)\b",
    re.I,
)
_NIFTY_TARGET_PARAGRAPH = re.compile(
    r"nifty\s*50[^.\n]{0,120}(?:target|forecast|outlook|reach|see)[^.\n]{0,80}\d{1,2}[,.]?\d{3,5}|"
    r"(?:target|forecast|outlook)[^.\n]{0,80}nifty\s*50[^.\n]{0,80}\d{1,2}[,.]?\d{3,5}",
    re.I,
)

MIN_INDEX_LEVEL = 15_000.0
MAX_INDEX_LEVEL = 35_000.0


def horizon_window_days(horizon_days: int) -> tuple[int, int]:
    hz = max(1, int(horizon_days))
    return int(hz * 0.5), int(hz * 2)


def _parse_date(value: str) -> date | None:
    raw = (value or "").strip()[:10]
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _days_ahead(from_day: date, target_day: date) -> int:
    return (target_day - from_day).days


def validate_record(
    record: ExternalPredictionRecord,
    *,
    body: str,
    used_regex_only: bool = False,
) -> ExternalPredictionRecord:
    """Apply index-only and horizon checks; may downgrade fetch_status to not_found."""
    text = body or ""
    target = record.target or ExternalPredictionTarget()
    mid = target.mid

    if mid is None or not (MIN_INDEX_LEVEL <= mid <= MAX_INDEX_LEVEL):
        record.fetch_status = "not_found"
        record.error_message = "target_out_of_range"
        return record

    if not _NIFTY50.search(text):
        record.fetch_status = "not_found"
        record.error_message = "no_nifty50_in_content"
        return record

    if _OPTIONS_BODY.search(text) and not _NIFTY_TARGET_PARAGRAPH.search(text):
        record.fetch_status = "not_found"
        record.error_message = "options_not_index_forecast"
        return record

    if used_regex_only and not _NIFTY_TARGET_PARAGRAPH.search(text):
        record.fetch_status = "not_found"
        record.error_message = "weak_regex_no_nifty50_target_context"
        return record

    instrument = str(record.extraction.get("instrument") or record.provenance.get("instrument") or "")
    if instrument and instrument.upper() not in {"NIFTY50", "NIFTY 50", "NIFTY"}:
        record.fetch_status = "not_found"
        record.error_message = "not_nifty50_instrument"
        return record

    horizon_match = _evaluate_horizon(record)
    record.provenance = {**record.provenance, "horizon_match": horizon_match}
    if horizon_match.get("in_window") is False:
        record.fetch_status = "not_found"
        record.error_message = "horizon_mismatch"
        return record

    record.fetch_status = "ok"
    record.error_message = ""
    return record


def _evaluate_horizon(record: ExternalPredictionRecord) -> dict[str, Any]:
    hz = int(record.horizon_days or 14)
    lo, hi = horizon_window_days(hz)
    as_of = _parse_date(record.as_of) or date.today()
    target_day = _parse_date(record.target_date)

    if target_day is None:
        return {
            "selected_days": hz,
            "target_days_ahead": None,
            "window_low": lo,
            "window_high": hi,
            "in_window": None,
        }

    ahead = _days_ahead(as_of, target_day)
    in_window = lo <= ahead <= hi
    return {
        "selected_days": hz,
        "target_days_ahead": ahead,
        "window_low": lo,
        "window_high": hi,
        "in_window": in_window,
    }


def record_to_live_forecast(record: ExternalPredictionRecord) -> dict[str, Any] | None:
    """Map ExternalPredictionRecord to LiveForecastInput-compatible dict for UI."""
    spot = record.spot_at_fetch
    mid = record.target.mid if record.target else None
    if spot is None or mid is None or spot <= 0:
        return None
    expected_return_pct = record.expected_return_pct
    if expected_return_pct is None:
        expected_return_pct = round((mid / spot - 1) * 100, 2)
    return {
        "asOf": record.as_of,
        "spot": spot,
        "expectedReturnPct": expected_return_pct,
        "rangeLow": record.target.low if record.target else None,
        "rangeHigh": record.target.high if record.target else None,
    }
