"""Parse and normalize step 04 LLM enrichment output."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    match = _JSON_OBJECT.search(text)
    if match:
        try:
            payload = json.loads(match.group(0))
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(text[start : end + 1])
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    return {}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_day(raw: str) -> date | None:
    text = (raw or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def resolve_expected_date(
    *,
    publish_day: str,
    timeline_phrase: str = "",
    expected_date: str = "",
) -> tuple[str, str]:
    """Return (expected_date ISO day, date_confidence)."""
    parsed = _parse_day(expected_date)
    if parsed is not None:
        return parsed.isoformat(), "high"

    base = _parse_day(publish_day)
    if base is None:
        return "", "low"

    phrase = (timeline_phrase or "").lower()
    if "tomorrow" in phrase:
        return (base + timedelta(days=1)).isoformat(), "medium"
    if "next week" in phrase or "coming week" in phrase:
        return (base + timedelta(days=7)).isoformat(), "medium"
    if "next month" in phrase:
        return (base + timedelta(days=30)).isoformat(), "low"
    return "", "low"


def normalize_article_enrichment(
    raw: dict[str, Any],
    *,
    enrichment_mode: str,
    publish_day: str,
    published_at: str,
) -> dict[str, Any]:
    """Normalize LLM payload into hub ref enrichment schema."""
    cause_indicators: list[dict[str, Any]] = []
    for row in raw.get("cause_indicators") or []:
        if not isinstance(row, dict):
            continue
        cause_indicators.append(
            {
                "factor": str(row.get("factor") or "")[:64],
                "mechanism": str(row.get("mechanism") or row.get("text") or "")[:500],
                "direction_hint": str(row.get("direction_hint") or "unclear")[:16],
                "confidence": _coerce_float(row.get("confidence"), 0.5),
                "evidence_quote": str(row.get("evidence_quote") or "")[:300],
            }
        )

    future_events: list[dict[str, Any]] = []
    for row in raw.get("future_events") or []:
        if not isinstance(row, dict):
            continue
        timeline_phrase = str(row.get("timeline_phrase") or row.get("horizon_phrase") or "")
        expected, conf = resolve_expected_date(
            publish_day=publish_day,
            timeline_phrase=timeline_phrase,
            expected_date=str(row.get("expected_date") or ""),
        )
        date_confidence = str(row.get("date_confidence") or conf)[:16]
        future_events.append(
            {
                "event": str(row.get("event") or "")[:300],
                "timeline_phrase": timeline_phrase[:120],
                "expected_date": expected,
                "date_confidence": date_confidence,
                "index_impact_mechanism": str(
                    row.get("index_impact_mechanism") or row.get("impact_mechanism") or ""
                )[:400],
            }
        )

    article_opinions: list[dict[str, Any]] = []
    for row in raw.get("article_opinions") or []:
        if not isinstance(row, dict):
            continue
        article_opinions.append(
            {
                "kind": str(row.get("kind") or "price_prediction")[:32],
                "text": str(row.get("text") or "")[:400],
                "use_for_prediction": False,
                "reason_discarded": str(
                    row.get("reason_discarded") or "article opinion not hub signal"
                )[:120],
            }
        )

    facts: list[dict[str, Any]] = []
    for row in raw.get("facts") or []:
        if isinstance(row, dict):
            facts.append(
                {
                    "text": str(row.get("text") or "")[:400],
                    "as_of": str(row.get("as_of") or published_at)[:40],
                }
            )
        elif row:
            facts.append({"text": str(row)[:400], "as_of": published_at})

    has_signal = bool(cause_indicators or future_events or facts or raw.get("distilled_summary") or raw.get("summary"))
    if "relevant" in raw:
        relevant = _coerce_bool(raw.get("relevant"), False)
    else:
        relevant = has_signal

    return {
        "relevant": relevant,
        "enrichment_mode": enrichment_mode,
        "publish_day": publish_day,
        "published_at": published_at,
        "cause_indicators": cause_indicators[:12],
        "future_events": future_events[:12],
        "article_opinions": article_opinions[:8],
        "facts": facts[:16],
        "distilled_summary": str(raw.get("distilled_summary") or raw.get("summary") or "")[:600],
        "prediction_value_score": max(
            0.0, min(1.0, _coerce_float(raw.get("prediction_value_score"), 0.0))
        ),
    }


def parse_enrichment_response(
    text: str,
    *,
    enrichment_mode: str,
    publish_day: str,
    published_at: str,
) -> dict[str, Any]:
    payload = _parse_json_object(text)
    return normalize_article_enrichment(
        payload,
        enrichment_mode=enrichment_mode,
        publish_day=publish_day,
        published_at=published_at,
    )
