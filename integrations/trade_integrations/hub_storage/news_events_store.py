"""Hub SSOT for distilled news events (internal storage).

Application code must read/query via ``trade_integrations.dataflows.news_hub_bridge``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.news_dedup import publish_day_from_value
from trade_integrations.dataflows.index_research.news_tags import record_matches_filters
from trade_integrations.hub_storage.news_event_models import (
    DistilledNewsEvent,
    EventConsensus,
    NewsReference,
    TimelineEntry,
)
from trade_integrations.hub_storage.parquet_io import concat_dataframes, read_dataframe, write_dataframe

_EVENTS_REL = Path("_data") / "news_events" / "events.parquet"

_EVENT_COLUMNS = (
    "event_id",
    "ticker",
    "title",
    "content",
    "timeline_json",
    "references_json",
    "consensus_json",
    "tags_json",
    "predicted_impact_json",
    "actual_impact_json",
    "structured_summary_json",
    "sources_json",
    "verification_json",
    "verification_data_as_of",
    "tagged_factors_json",
    "maturity_date",
    "horizon_trading_days",
    "status",
    "verification_status",
    "processing_version",
    "first_seen_at",
    "updated_at",
    "publish_day",
    "published_at",
)


def events_path() -> Path:
    return get_hub_dir() / _EVENTS_REL


def ensure_events_storage() -> None:
    path = events_path()
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        write_dataframe(_coerce_events_frame(pd.DataFrame(columns=list(_EVENT_COLUMNS))), path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _infer_market_impact_status(
    *,
    market_impact_status: str = "",
    actual_impact: dict[str, Any] | None = None,
    predicted_impact: dict[str, Any] | None = None,
    structured_summary: dict[str, Any] | None = None,
) -> str:
    explicit = str(market_impact_status or "").strip()
    if explicit:
        return explicit
    event_meta = ((structured_summary or {}).get("event_meta") or {})
    if isinstance(event_meta, dict):
        meta_status = str(event_meta.get("market_impact_status") or "").strip()
        if meta_status:
            return meta_status
        if event_meta.get("distilled_by") == "rule_fallback":
            return "claimed"
    actual = actual_impact or {}
    predicted = predicted_impact or {}
    if isinstance(actual, dict) and actual.get("nifty_points") is not None:
        return "observed"
    if isinstance(predicted, dict) and predicted.get("nifty_points") is not None:
        return "predicted"
    return "unverified"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=str) if value is not None else ""


def _json_loads(raw: Any, default: Any = None) -> Any:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    text = str(raw).strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def _load_events_frame() -> pd.DataFrame:
    ensure_events_storage()
    frame = read_dataframe(events_path())
    if frame.empty:
        return pd.DataFrame(columns=list(_EVENT_COLUMNS))
    for col in _EVENT_COLUMNS:
        if col not in frame.columns:
            frame[col] = None
    return frame


def _event_to_row(event: DistilledNewsEvent, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = event.to_dict()
    publish_day = event.publish_day or publish_day_from_value(event.published_at) or ""
    extra = extra or {}
    htd_raw = extra.get("horizon_trading_days", event.__dict__.get("horizon_trading_days"))
    try:
        horizon_days = int(htd_raw) if htd_raw is not None else None
    except (TypeError, ValueError):
        horizon_days = None
    return {
        "event_id": event.event_id,
        "ticker": event.ticker.strip().upper(),
        "title": event.title[:500],
        "content": event.content[:4000],
        "timeline_json": _json_dumps(payload.get("timeline") or []),
        "references_json": _json_dumps(payload.get("references") or []),
        "consensus_json": _json_dumps(payload.get("consensus") or {}),
        "tags_json": _json_dumps(event.tags),
        "predicted_impact_json": _json_dumps(event.predicted_impact),
        "actual_impact_json": _json_dumps(event.actual_impact),
        "structured_summary_json": _json_dumps(event.structured_summary),
        "sources_json": _json_dumps(event.sources),
        "verification_json": _json_dumps(extra.get("verification")),
        "verification_data_as_of": str(extra.get("verification_data_as_of") or "")[:10],
        "tagged_factors_json": _json_dumps(extra.get("tagged_factors") or []),
        "maturity_date": str(extra.get("maturity_date") or "")[:10] or None,
        "horizon_trading_days": horizon_days,
        "status": event.status or "active",
        "verification_status": event.verification_status or "pending",
        "processing_version": int(event.processing_version or 1),
        "first_seen_at": event.first_seen_at or _now_iso(),
        "updated_at": event.updated_at or _now_iso(),
        "publish_day": publish_day,
        "published_at": event.published_at or publish_day,
    }


def _row_to_event(row: Any) -> dict[str, Any]:
    data = row.to_dict() if hasattr(row, "to_dict") else dict(row)
    timeline = [TimelineEntry.from_dict(x) for x in _json_loads(data.get("timeline_json"), [])]
    references = [NewsReference.from_dict(x) for x in _json_loads(data.get("references_json"), [])]
    verification = _json_loads(data.get("verification_json"), {})
    tagged_factors = _json_loads(data.get("tagged_factors_json"), [])
    event = DistilledNewsEvent(
        event_id=str(data.get("event_id") or ""),
        ticker=str(data.get("ticker") or "NIFTY"),
        title=str(data.get("title") or ""),
        content=str(data.get("content") or ""),
        publish_day=str(data.get("publish_day") or ""),
        timeline=timeline,
        references=references,
        consensus=EventConsensus.from_dict(_json_loads(data.get("consensus_json"), {})),
        tags=_json_loads(data.get("tags_json"), {}),
        predicted_impact=_json_loads(data.get("predicted_impact_json"), {}),
        actual_impact=_json_loads(data.get("actual_impact_json"), {}),
        structured_summary=_json_loads(data.get("structured_summary_json"), {}),
        sources=_json_loads(data.get("sources_json"), []),
        status=str(data.get("status") or "active"),
        verification_status=str(data.get("verification_status") or "pending"),
        processing_version=int(data.get("processing_version") or 1),
        first_seen_at=str(data.get("first_seen_at") or ""),
        updated_at=str(data.get("updated_at") or ""),
        published_at=str(data.get("published_at") or ""),
    )
    event.market_impact_status = _infer_market_impact_status(
        actual_impact=event.actual_impact,
        predicted_impact=event.predicted_impact,
        structured_summary=event.structured_summary,
    )
    payload = event.to_dict()
    payload["verification"] = verification
    payload["verification_data_as_of"] = str(data.get("verification_data_as_of") or "")
    payload["tagged_factors"] = tagged_factors
    payload["maturity_date"] = data.get("maturity_date")
    payload["horizon_trading_days"] = data.get("horizon_trading_days")
    return payload


def _coerce_events_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize dtypes so parquet writes stay stable after row-wise merges."""
    cols = list(_EVENT_COLUMNS)
    if frame.empty:
        return pd.DataFrame(columns=cols)
    for col in cols:
        if col not in frame.columns:
            frame[col] = None
    frame = frame[cols].copy()
    if "horizon_trading_days" in frame.columns:
        frame["horizon_trading_days"] = pd.to_numeric(
            frame["horizon_trading_days"],
            errors="coerce",
        ).astype("Int64")
    if "processing_version" in frame.columns:
        frame["processing_version"] = (
            pd.to_numeric(frame["processing_version"], errors="coerce").fillna(1).astype("int64")
        )
    return frame


def upsert_event(event: DistilledNewsEvent) -> None:
    """Insert or merge a distilled event in hub parquet."""
    event_id = str(event.event_id or "").strip()
    if not event_id:
        return

    event.market_impact_status = _infer_market_impact_status(
        market_impact_status=event.market_impact_status,
        actual_impact=event.actual_impact,
        predicted_impact=event.predicted_impact,
        structured_summary=event.structured_summary,
    )

    frame = _load_events_frame()
    incoming = _event_to_row(event)
    now = _now_iso()

    if not frame.empty and event_id in frame["event_id"].astype(str).values:
        idx = frame.index[frame["event_id"].astype(str) == event_id][0]
        existing = frame.loc[idx].to_dict()
        incoming["first_seen_at"] = existing.get("first_seen_at") or incoming["first_seen_at"]
        incoming["processing_version"] = int(existing.get("processing_version") or 1) + 1
        incoming["updated_at"] = now
        for col, val in incoming.items():
            if col in frame.columns:
                frame.at[idx, col] = val
    else:
        incoming["first_seen_at"] = incoming.get("first_seen_at") or now
        incoming["updated_at"] = now
        new_row = pd.DataFrame([incoming])
        frame = concat_dataframes(frame, new_row)

    write_dataframe(_coerce_events_frame(frame), events_path())


def get_event(event_id: str) -> dict[str, Any] | None:
    frame = _load_events_frame()
    if frame.empty:
        return None
    matches = frame[frame["event_id"].astype(str) == event_id.strip()]
    if matches.empty:
        return None
    return _row_to_event(matches.iloc[-1])


def count_events(*, ticker: str | None = None) -> int:
    frame = _load_events_frame()
    if frame.empty:
        return 0
    if ticker and "ticker" in frame.columns:
        frame = frame[frame["ticker"].astype(str).str.upper() == ticker.strip().upper()]
    return int(len(frame))


def existing_event_ids(*, ticker: str | None = None) -> set[str]:
    """Event ids already present in events parquet (for incremental migration)."""
    frame = _load_events_frame()
    if frame.empty:
        return set()
    if ticker and "ticker" in frame.columns:
        frame = frame[frame["ticker"].astype(str).str.upper() == ticker.strip().upper()]
    return {str(v).strip() for v in frame["event_id"].astype(str) if str(v).strip()}


_INACTIVE_EVENT_STATUSES = frozenset({"discarded", "rolled_up", "archived"})


def list_events(
    *,
    ticker: str = "NIFTY",
    since: str | None = None,
    until: str | None = None,
    publish_day: str | None = None,
    status: str | list[str] | None = None,
    limit: int = 50,
    include_rejected: bool = False,
) -> list[dict[str, Any]]:
    frame = _load_events_frame()
    if frame.empty:
        return []

    sym = ticker.strip().upper()
    if "ticker" in frame.columns:
        frame = frame[frame["ticker"].astype(str).str.upper() == sym]

    if "status" in frame.columns:
        frame = frame[~frame["status"].astype(str).isin(_INACTIVE_EVENT_STATUSES)]

    statuses: set[str] | None = None
    if status is None and not include_rejected:
        statuses = {"approved", "partial", "pending"}
    elif isinstance(status, str):
        statuses = {status}
    elif isinstance(status, list):
        statuses = set(status)

    if statuses is not None and "verification_status" in frame.columns:
        frame = frame[frame["verification_status"].astype(str).isin(statuses)]

    if publish_day and "publish_day" in frame.columns:
        frame = frame[frame["publish_day"].astype(str).str[:10] == publish_day[:10]]

    records = [_row_to_event(row) for _, row in frame.iterrows()]
    filtered: list[dict[str, Any]] = []
    for rec in records:
        if record_matches_filters(
            _event_as_record(rec),
            since=since,
            until=until,
            publish_day=publish_day,
            topics=None,
            factors=None,
            themes=None,
            tags=None,
        ):
            filtered.append(rec)

    filtered.sort(
        key=lambda r: str(r.get("published_at") or r.get("publish_day") or ""),
        reverse=True,
    )
    return filtered[:limit]


def query_events(
    *,
    ticker: str = "NIFTY",
    topics: list[str] | None = None,
    factors: list[str] | None = None,
    themes: list[str] | None = None,
    tags: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    publish_day: str | None = None,
    limit: int = 50,
    include_rejected: bool = False,
) -> list[dict[str, Any]]:
    pool = list_events(
        ticker=ticker,
        since=since,
        until=until,
        publish_day=publish_day,
        limit=max(limit * 4, 80),
        include_rejected=include_rejected,
    )
    out: list[dict[str, Any]] = []
    for rec in pool:
        if record_matches_filters(
            _event_as_record(rec),
            since=since,
            until=until,
            publish_day=publish_day,
            topics=topics,
            factors=factors,
            themes=themes,
            tags=tags,
        ):
            out.append(rec)
        if len(out) >= limit:
            break
    return out


def _event_as_record(event: dict[str, Any]) -> dict[str, Any]:
    """Shape event dict for tag/date filters shared with verified records."""
    return {
        "tags": event.get("tags") or {},
        "published_at": event.get("published_at") or event.get("publish_day") or "",
        "title": event.get("title") or "",
        "content_summary": event.get("content") or "",
    }


def distilled_event_to_headline_dict(event: dict[str, Any]) -> dict[str, Any]:
    """Backward-compatible headline dict for bridge consumers."""
    event_id = str(event.get("event_id") or "")
    structured = event.get("structured_summary") or {}
    if not structured.get("event_meta"):
        event_meta = {
            "event_id": event_id,
            "distilled": True,
            "references": event.get("references") or [],
            "timeline": event.get("timeline") or [],
            "consensus": event.get("consensus") or {},
        }
        structured = {**structured, "event_meta": event_meta}

    sources = event.get("sources") or []
    if not sources:
        for ref in event.get("references") or []:
            if not isinstance(ref, dict):
                continue
            sources.append(
                {
                    "vendor": ref.get("vendor") or "unknown",
                    "publisher": ref.get("publisher") or ref.get("vendor") or "unknown",
                    "url": ref.get("url") or "",
                }
            )

    first_url = ""
    if sources and isinstance(sources[0], dict):
        first_url = str(sources[0].get("url") or "")

    verification = event.get("verification") or {}
    predicted = event.get("predicted_impact") or {}
    actual = event.get("actual_impact") or {}
    has_actual = bool((actual or {}).get("nifty_points") is not None)
    market_impact_status = _infer_market_impact_status(
        market_impact_status=str(event.get("market_impact_status") or ""),
        actual_impact=actual if isinstance(actual, dict) else {},
        predicted_impact=predicted if isinstance(predicted, dict) else {},
        structured_summary=structured if isinstance(structured, dict) else {},
    )

    return {
        "canonical_story_id": event_id,
        "id": event_id,
        "event_id": event_id,
        "ticker": event.get("ticker") or "NIFTY",
        "title": event.get("title") or "",
        "content_summary": event.get("content") or "",
        "summary": event.get("content") or "",
        "content": event.get("content") or "",
        "structured_summary": structured,
        "sources": sources,
        "published_at": event.get("published_at") or event.get("publish_day") or "",
        "publish_day": event.get("publish_day") or "",
        "tags": event.get("tags") or {},
        "verification_status": event.get("verification_status") or "pending",
        "verification": verification,
        "verification_data_as_of": event.get("verification_data_as_of") or "",
        "predicted_impact": predicted,
        "predicted": predicted,
        "actual_impact": actual,
        "actual": actual,
        "tagged_factors": event.get("tagged_factors") or [],
        "maturity_date": event.get("maturity_date"),
        "horizon_trading_days": event.get("horizon_trading_days"),
        "provenance": "distilled_event",
        "url": first_url,
        "source": (sources[0].get("vendor") if sources and isinstance(sources[0], dict) else "") or "",
        "status": "reconciled" if has_actual else (event.get("status") or "live"),
        "market_impact_status": market_impact_status,
        "event_kind": event.get("event_kind") or "",
        "parent_event_id": event.get("parent_event_id"),
        "timeline": event.get("timeline") or [],
        "references": event.get("references") or [],
        "consensus": event.get("consensus") or {},
        "first_seen_at": event.get("first_seen_at") or "",
        "updated_at": event.get("updated_at") or "",
        "raw_headline": event.get("title") or "",
        "confidence_note": "Model-attributed estimate; verified against factor data where possible.",
    }


def event_from_verified_record(record: dict[str, Any]) -> DistilledNewsEvent:
    """Convert a legacy verified record row into a distilled event."""
    story_id = str(record.get("canonical_story_id") or record.get("event_id") or "")
    structured = record.get("structured_summary") or {}
    event_meta = (structured.get("event_meta") if isinstance(structured, dict) else {}) or {}
    event_id = story_id or str(event_meta.get("event_id") or "")
    refs_raw = event_meta.get("references") or []
    references = [NewsReference.from_dict(r) for r in refs_raw if isinstance(r, dict)]
    if not references:
        for src in record.get("sources") or []:
            if not isinstance(src, dict):
                continue
            references.append(
                NewsReference(
                    url=str(src.get("url") or ""),
                    publisher=str(src.get("publisher") or src.get("vendor") or ""),
                    vendor=str(src.get("vendor") or "unknown"),
                    raw_title=str(record.get("title") or ""),
                    raw_summary=str(record.get("content_summary") or "")[:600],
                    published_at=str(record.get("published_at") or ""),
                )
            )

    timeline_raw = event_meta.get("timeline") or []
    timeline = [TimelineEntry.from_dict(x) for x in timeline_raw if isinstance(x, dict)]
    if not timeline:
        timeline = [
            TimelineEntry(
                at=str(record.get("first_seen_at") or record.get("updated_at") or _now_iso()),
                kind="created",
                summary=str(record.get("content_summary") or "")[:300],
            )
        ]

    tags = record.get("tags") or {}
    publish_day = publish_day_from_value(str(record.get("published_at") or "")) or str(
        tags.get("publish_day") or ""
    )

    return DistilledNewsEvent(
        event_id=event_id,
        ticker=str(record.get("ticker") or "NIFTY"),
        title=str(record.get("title") or ""),
        content=str(record.get("content_summary") or ""),
        publish_day=publish_day,
        timeline=timeline,
        references=references,
        consensus=EventConsensus.from_dict(event_meta.get("consensus") or {}),
        tags=dict(tags),
        predicted_impact=dict(record.get("predicted_impact") or {}),
        actual_impact=dict(record.get("actual_impact") or record.get("actual") or {}),
        status="active",
        structured_summary=dict(structured) if isinstance(structured, dict) else {},
        verification_status=str(record.get("verification_status") or "pending"),
        sources=list(record.get("sources") or []),
        published_at=str(record.get("published_at") or publish_day),
        first_seen_at=str(record.get("first_seen_at") or _now_iso()),
        updated_at=str(record.get("updated_at") or _now_iso()),
    )


def build_event_from_distilled_row(
    *,
    event_id: str,
    ticker: str,
    row: dict[str, Any],
    distilled: dict[str, Any],
    publish_day: str,
    verification_status: str = "pending",
) -> DistilledNewsEvent:
    """Build a DistilledNewsEvent from worker row + distill output."""
    structured = distilled.get("structured_summary") or {}
    event_meta = (structured.get("event_meta") if isinstance(structured, dict) else {}) or {}
    refs_raw = event_meta.get("references") or []
    references = [NewsReference.from_dict(r) for r in refs_raw if isinstance(r, dict)]
    timeline_raw = event_meta.get("timeline") or []
    timeline = [TimelineEntry.from_dict(x) for x in timeline_raw if isinstance(x, dict)]
    tags = row.get("tags") if isinstance(row.get("tags"), dict) else {}
    if not tags.get("publish_day") and publish_day:
        tags = {**tags, "publish_day": publish_day}

    market_impact_status = _infer_market_impact_status(
        market_impact_status=str(event_meta.get("market_impact_status") or ""),
        structured_summary=dict(structured) if isinstance(structured, dict) else {},
    )

    return DistilledNewsEvent(
        event_id=event_id,
        ticker=ticker,
        title=str(distilled.get("title") or row.get("title") or ""),
        content=str(distilled.get("content") or row.get("summary") or ""),
        publish_day=publish_day,
        timeline=timeline,
        references=references,
        consensus=EventConsensus.from_dict(event_meta.get("consensus") or {}),
        tags=tags,
        structured_summary=dict(structured) if isinstance(structured, dict) else {},
        verification_status=verification_status,
        sources=list(row.get("sources") or []),
        published_at=str(row.get("published_at") or publish_day),
        market_impact_status=market_impact_status,
        event_kind=str(event_meta.get("event_kind") or "macro"),
        parent_event_id=str(event_meta.get("parent_event_id") or "") or None,
    )


def _normalize_sources(sources: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for src in sources or []:
        if not isinstance(src, dict):
            continue
        url = str(src.get("url") or "").strip()
        vendor = str(src.get("vendor") or src.get("source") or "").strip()
        key = f"{vendor}|{url}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "vendor": vendor or "unknown",
                "publisher": str(src.get("publisher") or vendor or "unknown"),
                "url": url,
                "fetched_at": str(src.get("fetched_at") or _now_iso()),
            }
        )
    return out


def _should_replace_content(existing: str, incoming: str, *, incoming_structured: dict[str, Any] | None = None) -> bool:
    if not incoming:
        return False
    if not existing:
        return True
    event_meta = ((incoming_structured or {}).get("event_meta") or {})
    if event_meta.get("distilled_by") == "minimax":
        return True
    return len(incoming) > len(existing)


def upsert_hub_record(record: dict[str, Any]) -> None:
    """Upsert a hub headline record into events SSOT (legacy record shape)."""
    story_id = str(record.get("canonical_story_id") or record.get("event_id") or "").strip()
    if not story_id:
        return

    event = event_from_verified_record(record)
    extra = {
        "verification": record.get("verification"),
        "verification_data_as_of": record.get("verification_data_as_of"),
        "tagged_factors": record.get("tagged_factors") or [],
        "maturity_date": record.get("maturity_date"),
        "horizon_trading_days": record.get("horizon_trading_days"),
    }
    incoming = _event_to_row(event, extra=extra)

    frame = _load_events_frame()
    event_id = str(event.event_id or story_id)

    if not frame.empty and event_id in frame["event_id"].astype(str).values:
        idx = frame.index[frame["event_id"].astype(str) == event_id][0]
        existing = frame.loc[idx].to_dict()
        merged_sources = _normalize_sources(
            _json_loads(existing.get("sources_json"), []) + _json_loads(incoming.get("sources_json"), [])
        )
        existing_content = str(existing.get("content") or "")
        incoming_content = str(incoming.get("content") or "")
        incoming_structured = _json_loads(incoming.get("structured_summary_json"), {})
        if _should_replace_content(
            existing_content,
            incoming_content,
            incoming_structured=incoming_structured,
        ):
            existing["content"] = incoming_content
        if incoming.get("structured_summary_json"):
            existing["structured_summary_json"] = incoming["structured_summary_json"]
        if incoming.get("verification_json"):
            existing["verification_json"] = incoming["verification_json"]
            existing["verification_status"] = incoming["verification_status"]
            existing["verification_data_as_of"] = incoming["verification_data_as_of"]
        if incoming.get("predicted_impact_json"):
            existing["predicted_impact_json"] = incoming["predicted_impact_json"]
        if incoming.get("actual_impact_json"):
            existing["actual_impact_json"] = incoming["actual_impact_json"]
        if incoming.get("tagged_factors_json"):
            existing["tagged_factors_json"] = incoming["tagged_factors_json"]
        if incoming.get("tags_json"):
            from trade_integrations.dataflows.index_research.news_tags import merge_article_tags, tags_from_dict

            merged_tags = merge_article_tags(
                tags_from_dict(_json_loads(existing.get("tags_json"), {})),
                tags_from_dict(_json_loads(incoming.get("tags_json"), {})),
            )
            existing["tags_json"] = _json_dumps(merged_tags.to_dict())
        if incoming.get("maturity_date"):
            existing["maturity_date"] = incoming["maturity_date"]
        if incoming.get("horizon_trading_days"):
            existing["horizon_trading_days"] = incoming["horizon_trading_days"]
        existing["sources_json"] = _json_dumps(merged_sources)
        existing["title"] = incoming.get("title") or existing.get("title")
        existing["published_at"] = incoming.get("published_at") or existing.get("published_at")
        existing["publish_day"] = incoming.get("publish_day") or existing.get("publish_day")
        existing["updated_at"] = _now_iso()
        existing["processing_version"] = int(existing.get("processing_version") or 1) + 1
        for col, val in existing.items():
            if col in frame.columns:
                frame.at[idx, col] = val
    else:
        incoming["first_seen_at"] = incoming.get("first_seen_at") or _now_iso()
        incoming["updated_at"] = _now_iso()
        frame = concat_dataframes(frame, pd.DataFrame([incoming]))

    write_dataframe(_coerce_events_frame(frame), events_path())


def patch_event_meta(
    updates: list[tuple[str, dict[str, Any]]],
    *,
    min_rows: int | None = None,
) -> int:
    """Batch-update structured_summary.event_meta on distilled events."""
    if not updates:
        return 0
    frame = _load_events_frame()
    if frame.empty:
        return 0
    before = len(frame)
    if min_rows is not None and before < min_rows:
        raise RuntimeError(f"refusing patch: row count {before} below guard {min_rows}")
    patched = 0
    for event_id, event_meta in updates:
        eid = str(event_id or "").strip()
        if not eid:
            continue
        mask = frame["event_id"].astype(str) == eid
        if not mask.any():
            continue
        idx = frame.index[mask][0]
        structured = _json_loads(frame.at[idx, "structured_summary_json"], {})
        if not isinstance(structured, dict):
            structured = {}
        structured["event_meta"] = event_meta
        frame.at[idx, "structured_summary_json"] = _json_dumps(structured)
        frame.at[idx, "updated_at"] = _now_iso()
        patched += 1
    if patched:
        after = len(frame)
        if after != before:
            raise RuntimeError(f"refusing patch: row count changed {before} -> {after}")
        write_dataframe(_coerce_events_frame(frame), events_path())
    return patched


def list_event_tickers() -> list[str]:
    frame = _load_events_frame()
    if frame.empty or "ticker" not in frame.columns:
        return []
    return sorted({str(v).strip().upper() for v in frame["ticker"].astype(str) if str(v).strip()})


def count_events_by_status(*, ticker: str = "NIFTY") -> dict[str, int]:
    frame = _load_events_frame()
    if frame.empty:
        return {}
    sym = ticker.strip().upper()
    if "ticker" in frame.columns:
        frame = frame[frame["ticker"].astype(str).str.upper() == sym]
    counts: dict[str, int] = {}
    for status, group in frame.groupby(frame["verification_status"].astype(str)):
        counts[str(status)] = int(len(group))
    return counts


def list_pending_maturity_events(as_of: str, *, ticker: str | None = None) -> list[dict[str, Any]]:
    frame = _load_events_frame()
    if frame.empty:
        return []
    day = as_of[:10]
    if ticker and "ticker" in frame.columns:
        frame = frame[frame["ticker"].astype(str).str.upper() == ticker.strip().upper()]
    pending = frame[
        frame["maturity_date"].astype(str).str[:10].le(day)
        & frame["maturity_date"].astype(str).str.len().gt(0)
        & (frame["actual_impact_json"].isna() | (frame["actual_impact_json"].astype(str).str.len() == 0))
    ]
    return [distilled_event_to_headline_dict(_row_to_event(row)) for _, row in pending.iterrows()]


def list_verified_records_from_events(
    *,
    status: str | list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    publish_day: str | None = None,
    symbols: list[str] | None = None,
    topics: list[str] | None = None,
    factors: list[str] | None = None,
    themes: list[str] | None = None,
    tags: list[str] | None = None,
    limit: int = 50,
    ticker: str = "NIFTY",
    include_rejected: bool = False,
) -> list[dict[str, Any]]:
    """Query events SSOT and return legacy hub record dicts."""
    from trade_integrations.dataflows.index_research.news_tags import record_matches_filters

    if topics or factors or themes or tags:
        events = query_events(
            ticker=ticker,
            since=since,
            until=until,
            publish_day=publish_day,
            topics=topics,
            factors=factors,
            themes=themes,
            tags=tags,
            limit=limit,
            include_rejected=include_rejected,
        )
    else:
        events = list_events(
            ticker=ticker,
            since=since,
            until=until,
            publish_day=publish_day,
            status=status,
            limit=limit,
            include_rejected=include_rejected,
        )
        if status is not None and isinstance(status, str):
            statuses = {status}
            events = [e for e in events if str(e.get("verification_status") or "") in statuses]
        elif isinstance(status, list):
            statuses = set(status)
            events = [e for e in events if str(e.get("verification_status") or "") in statuses]

    out: list[dict[str, Any]] = []
    for event in events:
        rec = distilled_event_to_headline_dict(event)
        if record_matches_filters(
            rec,
            since=since,
            until=until,
            publish_day=publish_day,
            symbols=symbols,
            topics=topics,
            factors=factors,
            themes=themes,
            tags=tags,
        ):
            out.append(rec)
        if len(out) >= limit:
            break
    return out


_DISTILLATION_LOG_REL = Path("_data") / "news_events" / "distillation_log.jsonl"


def distillation_log_path() -> Path:
    return get_hub_dir() / _DISTILLATION_LOG_REL


def append_distillation_log(entry: dict[str, Any]) -> None:
    """Append one compaction/distillation audit row."""
    path = distillation_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**entry, "logged_at": _now_iso()}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")


def remove_events(event_ids: set[str] | list[str]) -> int:
    """Drop events by event_id; returns removed count."""
    ids = {str(event_id).strip() for event_id in event_ids if str(event_id).strip()}
    if not ids:
        return 0
    frame = _load_events_frame()
    if frame.empty:
        return 0
    before = len(frame)
    frame = frame[~frame["event_id"].astype(str).isin(ids)]
    removed = before - len(frame)
    if removed:
        write_dataframe(_coerce_events_frame(frame), events_path())
    return removed
