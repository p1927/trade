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
_TARGET_VERBS = re.compile(
    r"\b(?:target|forecast|outlook|expects?|expected to|sees?|projects?|projected|"
    r"raised to|cut to|revised to|price target)\b",
    re.I,
)
_RESISTANCE_SUPPORT = re.compile(
    r"\b(?:resistance|support|ceiling|cap|hurdle)\b",
    re.I,
)
_RESISTANCE_PHRASE = re.compile(
    r"\b(?:face|faces|facing|test|tests|testing|may\s+see|could\s+see|might\s+see|see)\s+"
    r"(?:\w+\s+){0,4}(?:resistance|support)\b|"
    r"\b(?:resistance|support|ceiling|cap|hurdle)\s+(?:near|at|around|zone|mark|level)\b",
    re.I,
)

MIN_INDEX_LEVEL = 15_000.0
MAX_INDEX_LEVEL = 35_000.0


def _level_tokens(level: float) -> set[str]:
    whole = int(round(level))
    return {str(whole), f"{whole:,}"}


def _level_mentioned(text: str, level: float) -> bool:
    compact = text.replace(",", "")
    return any(token.replace(",", "") in compact for token in _level_tokens(level))


def _resistance_at_level(sentence: str, level: float) -> bool:
    """True when the sentence frames this numeric level as resistance/support, not a target."""
    sent = sentence.strip()
    if not sent or not _level_mentioned(sent, level):
        return False
    if not _RESISTANCE_SUPPORT.search(sent):
        return False
    if _RESISTANCE_PHRASE.search(sent):
        return True
    if re.search(
        r"\b(?:as|a)\s+(?:[\w-]+\s+){0,4}(?:resistance|support|ceiling|cap|hurdle)\b",
        sent,
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:sees?|projects?|expects?)\s+[^.\n]{0,100}\bat\s+[\d,]+\s+as\s+"
        r"(?:[\w-]+\s+){0,4}(?:resistance|support|ceiling|cap|hurdle)\b",
        sent,
        re.I,
    ):
        return True
    if _TARGET_VERBS.search(sent) and re.search(
        r"\b(?:resistance|support|ceiling|cap|hurdle)\s+(?:zone|level|mark|area)\b",
        sent,
        re.I,
    ):
        return True
    return False


def _explicit_nifty_target_at_level(text: str, level: float) -> bool:
    """True when a sentence ties NIFTY to an explicit target at this level (not resistance phrasing)."""
    for sentence in re.split(r"[.!?\n;]+", text):
        sent = sentence.strip()
        if not sent or not _level_mentioned(sent, level):
            continue
        if not _NIFTY50.search(sent):
            continue
        if _resistance_at_level(sent, level):
            continue
        if _NIFTY_TARGET_PARAGRAPH.search(sent):
            return True
        if _TARGET_VERBS.search(sent):
            return True
        if re.search(r"\b(?:weekly|monthly)\s+(?:outlook|target|forecast)\b", sent, re.I):
            return True
    return False


def reject_resistance_only_target(
    record: ExternalPredictionRecord,
    body: str,
) -> ExternalPredictionRecord:
    """Downgrade resistance/support levels that are not explicit analyst targets."""
    target = record.target or ExternalPredictionTarget()
    mid = target.mid
    if mid is None:
        return record

    prov = record.provenance or {}
    text = " ".join(
        [
            body or "",
            " ".join(record.rationale_bullets or []),
            str(prov.get("title") or ""),
            str(prov.get("summary") or ""),
        ]
    )
    if _explicit_nifty_target_at_level(text, mid):
        return record
    if not _level_mentioned(text, mid):
        return record
    if not _RESISTANCE_SUPPORT.search(text):
        return record

    record.fetch_status = "not_found"
    record.error_message = "resistance_not_target"
    return record


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
    if mid is None and target.high is not None and MIN_INDEX_LEVEL <= target.high <= MAX_INDEX_LEVEL:
        target.mid = target.high
        if target.low is None:
            target.low = target.high
        record.target = target
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

    instrument = str(record.extraction.get("instrument") or (record.provenance or {}).get("instrument") or "")
    if instrument and instrument.upper() not in {"NIFTY50", "NIFTY 50", "NIFTY"}:
        record.fetch_status = "not_found"
        record.error_message = "not_nifty50_instrument"
        return record

    horizon_match = _evaluate_horizon(record)
    record.provenance = {**(record.provenance or {}), "horizon_match": horizon_match}
    if horizon_match.get("in_window") is False:
        record.provenance["horizon_match"] = {
            **horizon_match,
            "soft_mismatch": True,
        }

    record = reject_resistance_only_target(record, text)
    if record.fetch_status != "ok" and record.error_message == "resistance_not_target":
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
