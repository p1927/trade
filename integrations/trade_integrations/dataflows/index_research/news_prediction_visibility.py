"""Prediction attribution visibility — soft-create events hidden until corroborated."""

from __future__ import annotations

from typing import Any


def event_ref_count(record: dict[str, Any]) -> int:
    """Reference count for an event/headline dict."""
    em = ((record.get("structured_summary") or {}).get("event_meta") or {})
    if em.get("ref_count") is not None:
        try:
            return max(0, int(em.get("ref_count")))
        except (TypeError, ValueError):
            pass
    refs = record.get("references") or []
    if isinstance(refs, list) and refs:
        return len(refs)
    sources = record.get("sources") or []
    if isinstance(sources, list) and sources:
        return len(sources)
    return 1


def visible_for_prediction_attribution(record: dict[str, Any]) -> bool:
    """Soft-create policy: hide single-ref SSOT events from Prediction until a 2nd ref."""
    if str(record.get("provenance") or "") == "staging":
        return True
    return event_ref_count(record) >= 2


def filter_prediction_attribution_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in items if visible_for_prediction_attribution(row)]
