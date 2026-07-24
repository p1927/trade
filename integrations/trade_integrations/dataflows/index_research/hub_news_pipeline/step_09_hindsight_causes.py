"""Step 09 — hindsight cause alignment after maturity or future-event dates."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd

from trade_integrations.dataflows.index_research.prediction_miss_analysis import (
    factor_snapshot_at,
)

logger = logging.getLogger(__name__)

STEP_ID = "step_09_hindsight_causes"

_TOKEN_RE = re.compile(r"[a-z]+")
_BULLISH_HINTS = frozenset({"bullish", "up", "positive", "rise", "rally", "higher"})
_BEARISH_HINTS = frozenset({"bearish", "down", "negative", "fall", "drop", "lower"})
_NEUTRAL_HINTS = frozenset({"neutral", "flat", "sideways", "range"})
_INDEX_FACTORS = frozenset({"nifty", "index", "nifty50", "nse", ""})
_FACTOR_INDEX_SIGN: dict[str, int] = {
    "fii_net_5d": 1,
    "dii_net_5d": 1,
    "oil_brent": -1,
    "india_vix": -1,
    "repo_rate": -1,
}
_FLAT_PCT_THRESHOLD = 0.15


def _parse_day(raw: str) -> date | None:
    text = (raw or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def normalize_direction_hint(hint: str) -> str:
    text = (hint or "").strip().lower()
    if not text or text == "unclear":
        return "unclear"
    tokens = set(_TOKEN_RE.findall(text))
    if tokens & _BULLISH_HINTS:
        return "bullish"
    if tokens & _BEARISH_HINTS:
        return "bearish"
    if tokens & _NEUTRAL_HINTS:
        return "neutral"
    return "unclear"


def direction_from_return_pct(return_pct: float | None) -> str:
    if return_pct is None:
        return "unverifiable"
    if return_pct > _FLAT_PCT_THRESHOLD:
        return "bullish"
    if return_pct < -_FLAT_PCT_THRESHOLD:
        return "bearish"
    return "neutral"


def compare_directions(hint: str, actual: str) -> str:
    expected = normalize_direction_hint(hint)
    if expected == "unclear" or actual in {"unverifiable", "unclear"}:
        return "unverifiable"
    if expected == "neutral" or actual == "neutral":
        return "neutral"
    if expected == actual:
        return "aligned"
    return "contradicted"


def _enrichment_from_ref(ref: dict[str, Any]) -> dict[str, Any]:
    structured = ref.get("structured_enrichment")
    if isinstance(structured, dict) and (
        structured.get("cause_indicators") or structured.get("future_events")
    ):
        return dict(structured)
    enrichment = ref.get("article_enrichment")
    if isinstance(enrichment, dict) and (
        enrichment.get("cause_indicators") or enrichment.get("future_events")
    ):
        return dict(enrichment)
    return {}


def _trading_dates(frame: pd.DataFrame) -> list[str]:
    if frame.empty or "date" not in frame.columns:
        return []
    return frame["date"].astype(str).str[:10].tolist()


def _session_index(
    dates: list[str],
    day: str,
    *,
    prefer: str = "on_or_before",
) -> int | None:
    target = day[:10]
    if target in dates:
        return dates.index(target)
    if prefer == "on_or_before":
        eligible = [i for i, d in enumerate(dates) if d <= target]
        return eligible[-1] if eligible else None
    eligible = [i for i, d in enumerate(dates) if d >= target]
    return eligible[0] if eligible else None


def _session_window_end(
    frame: pd.DataFrame,
    start_day: str,
    *,
    sessions: int,
) -> str | None:
    dates = _trading_dates(frame)
    if not dates:
        return None
    prefer = "on_or_before" if sessions <= 0 else "on_or_after"
    idx = _session_index(dates, start_day, prefer=prefer)
    if idx is None:
        return None
    if sessions < 0:
        end_idx = max(0, idx + sessions)
    else:
        end_idx = min(len(dates) - 1, idx + sessions)
    return dates[end_idx]


def _resolve_factor_column(factor: str, frame: pd.DataFrame) -> str:
    key = (factor or "").strip().lower()
    if key in _INDEX_FACTORS:
        return ""
    columns = {str(col).lower(): str(col) for col in frame.columns}
    return columns.get(key, "")


def _snapped_session_day(
    frame: pd.DataFrame,
    day: str,
    *,
    prefer: str = "on_or_before",
) -> str | None:
    dates = _trading_dates(frame)
    idx = _session_index(dates, day, prefer=prefer)
    if idx is None:
        return None
    return dates[idx]


def _cap_session_day(frame: pd.DataFrame, day: str, cap_day: str) -> str | None:
    dates = _trading_dates(frame)
    idx = _session_index(dates, day, prefer="on_or_before")
    cap_idx = _session_index(dates, cap_day, prefer="on_or_before")
    if idx is None or cap_idx is None:
        return None
    return dates[min(idx, cap_idx)]


def _nifty_return_pct(
    frame: pd.DataFrame,
    start_day: str,
    end_day: str,
) -> float | None:
    if frame.empty or "close" not in frame.columns:
        return None
    dates = _trading_dates(frame)
    start_idx = _session_index(dates, start_day, prefer="on_or_before")
    end_idx = _session_index(dates, end_day, prefer="on_or_before")
    if start_idx is None or end_idx is None or end_idx < start_idx:
        return None
    spot0 = float(frame.iloc[start_idx]["close"])
    spot1 = float(frame.iloc[end_idx]["close"])
    if spot0 <= 0:
        return None
    return (spot1 - spot0) / spot0 * 100.0


def _factor_delta(
    frame: pd.DataFrame,
    factor: str,
    start_day: str,
    end_day: str,
) -> tuple[float | None, str]:
    column = _resolve_factor_column(factor, frame)
    if not column:
        return None, ""
    t0 = factor_snapshot_at(start_day, frame, [column], keys=[column]).get(column)
    t1 = factor_snapshot_at(end_day, frame, [column], keys=[column]).get(column)
    if t0 is None or t1 is None:
        return None, column
    return float(t1) - float(t0), column


def _factor_implied_index_direction(factor: str, delta: float | None) -> str:
    if delta is None:
        return "unverifiable"
    key = (factor or "").strip().lower()
    sign = _FACTOR_INDEX_SIGN.get(key)
    if sign is None:
        return "unverifiable"
    adjusted = delta * sign
    if adjusted > 0:
        return "bullish"
    if adjusted < 0:
        return "bearish"
    return "neutral"


def _cause_hindsight_key(cause: dict[str, Any], *, index: int) -> tuple[str, int, str, str]:
    return (
        "cause_indicator",
        index,
        str(cause.get("factor") or "")[:64],
        str(cause.get("mechanism") or "")[:80],
    )


def _future_hindsight_key(event: dict[str, Any]) -> tuple[str, str, str]:
    return (
        "future_event",
        str(event.get("event") or "")[:300],
        str(event.get("expected_date") or "")[:10],
    )


def _existing_hindsight_keys(existing: list[Any]) -> set[tuple[Any, ...]]:
    keys: set[tuple[Any, ...]] = set()
    for row in existing:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "")
        if kind == "cause_indicator":
            keys.add(
                (
                    kind,
                    int(row.get("cause_index", -1)),
                    str(row.get("factor") or "")[:64],
                    str(row.get("mechanism") or "")[:80],
                )
            )
        elif kind == "future_event":
            keys.add(
                (
                    kind,
                    str(row.get("event") or "")[:300],
                    str(row.get("expected_date") or "")[:10],
                )
            )
    return keys


def annotate_cause_indicator(
    cause: dict[str, Any],
    *,
    cause_index: int,
    publish_day: str,
    frame: pd.DataFrame,
    as_of: str,
    sessions: int = 5,
) -> dict[str, Any] | None:
    if not isinstance(cause, dict):
        return None
    factor = str(cause.get("factor") or "").strip()
    factor_column = _resolve_factor_column(factor, frame)
    hint = str(cause.get("direction_hint") or "unclear")
    pub = _parse_day(publish_day)
    today = _parse_day(as_of)
    if pub is None or today is None or pub > today:
        return None

    window_start = _snapped_session_day(frame, publish_day, prefer="on_or_before")
    if window_start is None:
        return None
    dates = _trading_dates(frame)
    start_idx = dates.index(window_start)
    end_idx = min(len(dates) - 1, start_idx + max(sessions, 0))
    window_end = _cap_session_day(frame, dates[end_idx], as_of)
    if window_end is None:
        return None
    if _parse_day(window_end) is None or _parse_day(window_end) < _parse_day(window_start):
        return None

    nifty_return = _nifty_return_pct(frame, window_start, window_end)
    nifty_direction = direction_from_return_pct(nifty_return)
    factor_delta, resolved_factor = _factor_delta(frame, factor, window_start, window_end)
    factor_direction = _factor_implied_index_direction(resolved_factor or factor, factor_delta)

    if factor_column:
        alignment = compare_directions(hint, factor_direction)
        actual_direction = factor_direction
    else:
        alignment = compare_directions(hint, nifty_direction)
        actual_direction = nifty_direction

    evidence_parts = []
    if nifty_return is not None:
        evidence_parts.append(f"NIFTY {nifty_return:+.2f}% ({window_start}→{window_end})")
    if factor_delta is not None and resolved_factor:
        evidence_parts.append(f"{resolved_factor} Δ {factor_delta:+.4g}")

    return {
        "kind": "cause_indicator",
        "cause_index": cause_index,
        "factor": factor[:64],
        "mechanism": str(cause.get("mechanism") or "")[:300],
        "direction_hint": hint[:16],
        "actual_direction": actual_direction,
        "actual_nifty_return_pct": round(nifty_return, 4) if nifty_return is not None else None,
        "actual_factor_delta": round(factor_delta, 4) if factor_delta is not None else None,
        "alignment": alignment,
        "evidence": "; ".join(evidence_parts)[:400],
        "window_start": window_start[:10],
        "window_end": window_end[:10],
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


def annotate_future_event(
    event: dict[str, Any],
    *,
    frame: pd.DataFrame,
    as_of: str,
    post_sessions: int = 3,
) -> dict[str, Any] | None:
    if not isinstance(event, dict):
        return None
    expected = str(event.get("expected_date") or "").strip()[:10]
    if not expected:
        return None
    expected_day = _parse_day(expected)
    today = _parse_day(as_of)
    if expected_day is None or today is None or expected_day > today:
        return None

    window_start = _session_window_end(frame, expected, sessions=-1) or expected
    raw_window_end = _session_window_end(frame, expected, sessions=post_sessions) or expected
    end_day = _cap_session_day(frame, raw_window_end, as_of)
    if end_day is None:
        return None

    nifty_return = _nifty_return_pct(frame, window_start, end_day)
    nifty_direction = direction_from_return_pct(nifty_return)
    mechanism = str(event.get("index_impact_mechanism") or "")
    hint = mechanism if mechanism else "unclear"
    alignment = compare_directions(hint, nifty_direction)

    return {
        "kind": "future_event",
        "event": str(event.get("event") or "")[:300],
        "expected_date": expected,
        "direction_hint": normalize_direction_hint(hint),
        "actual_direction": nifty_direction,
        "actual_nifty_return_pct": round(nifty_return, 4) if nifty_return is not None else None,
        "alignment": alignment,
        "window_elapsed": True,
        "evidence": (
            f"NIFTY {nifty_return:+.2f}% around {expected} ({window_start}→{end_day})"
            if nifty_return is not None
            else f"Window elapsed for {expected}; no NIFTY history"
        )[:400],
        "window_start": window_start[:10],
        "window_end": end_day[:10],
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


def build_hindsight_causes_for_ref(
    ref: dict[str, Any],
    *,
    publish_day: str,
    frame: pd.DataFrame,
    as_of: str,
    sessions: int = 5,
) -> list[dict[str, Any]]:
    enrichment = _enrichment_from_ref(ref)
    if not enrichment:
        return []

    rows: list[dict[str, Any]] = []
    for index, cause in enumerate(enrichment.get("cause_indicators") or []):
        if not isinstance(cause, dict):
            continue
        row = annotate_cause_indicator(
            cause,
            cause_index=index,
            publish_day=publish_day,
            frame=frame,
            as_of=as_of,
            sessions=sessions,
        )
        if row:
            rows.append(row)

    for event in enrichment.get("future_events") or []:
        if not isinstance(event, dict):
            continue
        row = annotate_future_event(
            event,
            frame=frame,
            as_of=as_of,
        )
        if row:
            rows.append(row)

    return rows


def _required_hindsight_keys(
    ref: dict[str, Any],
    *,
    as_of: str,
) -> set[tuple[Any, ...]]:
    enrichment = _enrichment_from_ref(ref)
    keys: set[tuple[Any, ...]] = set()
    for index, cause in enumerate(enrichment.get("cause_indicators") or []):
        if isinstance(cause, dict):
            keys.add(_cause_hindsight_key(cause, index=index))
    for event in enrichment.get("future_events") or []:
        if not isinstance(event, dict):
            continue
        expected_day = str(event.get("expected_date") or "")[:10]
        if expected_day and expected_day <= as_of[:10]:
            keys.add(_future_hindsight_key(event))
    return keys


def ref_needs_hindsight(ref: dict[str, Any], *, as_of: str) -> bool:
    required = _required_hindsight_keys(ref, as_of=as_of)
    if not required:
        return False

    existing = ref.get("hindsight_causes") or []
    if not isinstance(existing, list) or not existing:
        return True

    covered = _existing_hindsight_keys(existing)
    return not required.issubset(covered)


def run_hindsight_causes_backfill(
    *,
    ticker: str = "NIFTY",
    lookback_days: int = 90,
    limit: int = 50,
    sessions: int = 5,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Annotate mature refs with hindsight cause alignment."""
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso
    from trade_integrations.hub_storage.news_events_store import (
        get_event,
        list_events,
        patch_event_meta,
    )
    from trade_integrations.hub_storage.news_staging_store import pipeline_pause_status

    sym = ticker.strip().upper()
    pause = pipeline_pause_status(ticker=sym)
    if pause.get("pipeline_paused"):
        return {
            "ticker": sym,
            "skipped": True,
            "pipeline_paused": True,
            "pause_reason": str(pause.get("pause_reason") or ""),
        }

    today = (as_of or india_trading_date_iso())[:10]
    end = date.fromisoformat(today)
    since = (end - timedelta(days=max(lookback_days, 1))).isoformat()
    raw_events = list_events(ticker=sym, since=since, limit=5000, include_rejected=False)

    candidates: list[tuple[str, list[dict[str, Any]], str]] = []
    for event in raw_events:
        eid = str(event.get("event_id") or "").strip()
        if not eid:
            continue
        structured = event.get("structured_summary") if isinstance(event.get("structured_summary"), dict) else {}
        em = structured.get("event_meta") if isinstance(structured.get("event_meta"), dict) else {}
        refs = [dict(r) for r in (em.get("references") or []) if isinstance(r, dict)]
        if not refs:
            continue
        publish_day = str(event.get("publish_day") or event.get("published_at") or "")[:10]
        needing = [r for r in refs if ref_needs_hindsight(r, as_of=today)]
        if needing:
            candidates.append((eid, refs, publish_day))
        if len(candidates) >= limit:
            break

    if not candidates:
        return {
            "ticker": sym,
            "events_scanned": len(raw_events),
            "events_updated": 0,
            "refs_annotated": 0,
            "skipped": True,
            "reason": "no_refs_needing_hindsight",
        }

    frame = pd.DataFrame()
    try:
        from trade_integrations.dataflows.index_research.sources.history_loader import (
            load_aligned_factor_history,
        )

        frame = load_aligned_factor_history(days=max(lookback_days + 60, 120))
    except Exception as exc:
        logger.debug("hindsight factor frame unavailable: %s", exc)

    if frame.empty:
        return {
            "ticker": sym,
            "events_scanned": len(raw_events),
            "events_candidates": len(candidates),
            "events_updated": 0,
            "refs_annotated": 0,
            "skipped": True,
            "reason": "factor_frame_unavailable",
            "as_of": today,
        }

    patches: list[tuple[str, dict[str, Any]]] = []
    refs_annotated = 0
    errors = 0

    for event_id, refs, publish_day in candidates:
        try:
            stored = get_event(event_id)
            if not stored:
                continue
            structured = dict(stored.get("structured_summary") or {})
            em = dict(structured.get("event_meta") or {})
            stored_refs = [dict(r) for r in (em.get("references") or []) if isinstance(r, dict)]
            if not stored_refs:
                stored_refs = refs

            updated_refs: list[dict[str, Any]] = []
            event_changed = False
            for ref in stored_refs:
                ref_out = dict(ref)
                pub = str(ref_out.get("published_at") or publish_day or "")[:10]
                if ref_needs_hindsight(ref_out, as_of=today):
                    prior = ref_out.get("hindsight_causes")
                    hindsight = build_hindsight_causes_for_ref(
                        ref_out,
                        publish_day=pub,
                        frame=frame,
                        as_of=today,
                        sessions=sessions,
                    )
                    if hindsight:
                        merged = list(prior or []) if isinstance(prior, list) else []
                        merged_by_key = _existing_hindsight_keys(merged)
                        for row in hindsight:
                            key = (
                                (
                                    "cause_indicator",
                                    int(row.get("cause_index", -1)),
                                    str(row.get("factor") or "")[:64],
                                    str(row.get("mechanism") or "")[:80],
                                )
                                if row.get("kind") == "cause_indicator"
                                else (
                                    "future_event",
                                    str(row.get("event") or "")[:300],
                                    str(row.get("expected_date") or "")[:10],
                                )
                            )
                            if key not in merged_by_key:
                                merged.append(row)
                                merged_by_key.add(key)
                        if merged != prior:
                            ref_out["hindsight_causes"] = merged
                            event_changed = True
                            refs_annotated += 1
                updated_refs.append(ref_out)

            if event_changed:
                em["references"] = updated_refs
                patches.append((event_id, em))
        except Exception as exc:
            errors += 1
            logger.warning("hindsight backfill failed for %s: %s", event_id, exc)

    patched = patch_event_meta(patches) if patches else 0

    return {
        "ticker": sym,
        "events_scanned": len(raw_events),
        "events_candidates": len(candidates),
        "events_updated": patched,
        "refs_annotated": refs_annotated,
        "meta_patched": patched,
        "errors": errors,
        "as_of": today,
    }
