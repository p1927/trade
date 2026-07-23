"""Detect knowledge gaps in distilled events that warrant LLM Wiki Deep Research."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any


def _event_meta(event: dict[str, Any]) -> dict[str, Any]:
    structured = event.get("structured_summary") if isinstance(event.get("structured_summary"), dict) else {}
    meta = structured.get("event_meta") if isinstance(structured.get("event_meta"), dict) else {}
    return meta


def _consensus(event: dict[str, Any]) -> dict[str, Any]:
    meta = _event_meta(event)
    consensus = meta.get("consensus") if isinstance(meta.get("consensus"), dict) else {}
    if consensus:
        return consensus
    raw = event.get("consensus")
    return raw if isinstance(raw, dict) else {}


def _reference_count(event: dict[str, Any]) -> int:
    meta = _event_meta(event)
    refs = meta.get("references") or event.get("references") or []
    if isinstance(refs, list) and refs:
        return len(refs)
    return int(meta.get("ref_count") or meta.get("source_count") or 1)


def _market_impact_status(event: dict[str, Any]) -> str:
    meta = _event_meta(event)
    return str(
        meta.get("market_impact_status")
        or event.get("market_impact_status")
        or "unverified"
    ).strip().lower()


def _parse_day(value: str | None) -> date | None:
    raw = str(value or "").strip()[:10]
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def detect_research_gaps(event: dict[str, Any], *, today: date | None = None) -> list[dict[str, Any]]:
    """Return gap descriptors for an event (may be empty)."""
    gaps: list[dict[str, Any]] = []
    status = str(event.get("status") or "active").strip().lower()
    if status == "superseded":
        return gaps

    consensus = _consensus(event)
    conflicts = consensus.get("conflicts") or []
    if conflicts:
        gaps.append(
            {
                "gap_kind": "conflicts",
                "detail": f"{len(conflicts)} source conflict(s)",
                "priority": 1,
            }
        )

    ref_count = _reference_count(event)
    if ref_count <= 1 and status == "active":
        gaps.append(
            {
                "gap_kind": "single_source",
                "detail": "only one reference attached",
                "priority": 2,
            }
        )

    impact_status = _market_impact_status(event)
    maturity = _parse_day(str(event.get("maturity_date") or ""))
    ref_day = today or datetime.now(timezone.utc).date()
    if impact_status == "unverified" and maturity and maturity <= ref_day:
        gaps.append(
            {
                "gap_kind": "unverified_impact",
                "detail": f"impact still unverified after maturity {maturity.isoformat()}",
                "priority": 3,
            }
        )

    factors = list(consensus.get("factors") or consensus.get("primary_factors") or [])
    tags = event.get("tags") if isinstance(event.get("tags"), dict) else {}
    tagged = list(tags.get("factors") or [])[:8]
    linked = set(str(f) for f in factors + tagged if f)
    if linked and ref_count <= 2 and status == "active":
        gaps.append(
            {
                "gap_kind": "thin_coverage",
                "detail": f"tagged factors {sorted(linked)[:5]} with only {ref_count} ref(s)",
                "priority": 4,
            }
        )

    return sorted(gaps, key=lambda row: int(row.get("priority") or 99))


def pick_primary_gap(gaps: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not gaps:
        return None
    return gaps[0]
