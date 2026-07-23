"""Blocking index for hub news events — fast T1 lookup without scanning events.parquet."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.hub_storage.news_event_models import DistilledNewsEvent
from trade_integrations.hub_storage.parquet_io import concat_dataframes, read_dataframe, write_dataframe

logger = logging.getLogger(__name__)

_INDEX_REL = Path("_data") / "news_events" / "event_index.parquet"
_INDEX_COLUMNS = (
    "event_id",
    "ticker",
    "publish_day",
    "parent_event_id",
    "title",
    "summary_snippet",
    "title_norm",
    "summary_norm",
    "bucket_key",
    "topics_json",
    "status",
    "verification_status",
    "published_at",
    "updated_at",
)
_INACTIVE_STATUSES = frozenset({"discarded", "rolled_up", "archived"})


def event_index_path():
    return get_hub_dir() / _INDEX_REL


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _load_index_frame() -> pd.DataFrame:
    frame = read_dataframe(event_index_path())
    if frame.empty:
        return pd.DataFrame(columns=list(_INDEX_COLUMNS))
    for col in _INDEX_COLUMNS:
        if col not in frame.columns:
            frame[col] = None
    return frame


def _bucket_key_from_event(event: dict[str, Any], *, ticker: str) -> str:
    from trade_integrations.dataflows.index_research.news_dedup import semantic_cluster_key

    row = {
        "title": event.get("title") or "",
        "summary": event.get("content") or event.get("content_summary") or "",
        "published_at": event.get("published_at") or event.get("publish_day") or "",
        "tags": event.get("tags") or {},
    }
    return semantic_cluster_key(row, ticker=ticker)


def _topics_from_event(event: dict[str, Any]) -> list[str]:
    tags = event.get("tags") or {}
    if isinstance(tags, dict):
        topics = tags.get("topics") or []
        if topics:
            return [str(t) for t in topics if str(t).strip()]
    consensus = event.get("consensus") or {}
    if isinstance(consensus, dict):
        return [str(t) for t in (consensus.get("topics") or []) if str(t).strip()]
    return []


def _event_to_index_row(event: DistilledNewsEvent | dict[str, Any]) -> dict[str, Any]:
    if isinstance(event, DistilledNewsEvent):
        data = event.to_dict()
    else:
        data = dict(event)
    event_id = str(data.get("event_id") or data.get("canonical_story_id") or "").strip()
    ticker = str(data.get("ticker") or "NIFTY").upper()
    title = str(data.get("title") or "")[:500]
    summary = str(data.get("content") or data.get("content_summary") or "")[:500]
    publish_day = str(data.get("publish_day") or "")[:10]
    published_at = str(data.get("published_at") or publish_day or "")[:32]
    parent = data.get("parent_event_id")
    if not parent:
        em = ((data.get("structured_summary") or {}).get("event_meta") or {})
        parent = em.get("parent_event_id")
    parent_id = str(parent or "").strip() or None
    topics = _topics_from_event(data)
    return {
        "event_id": event_id,
        "ticker": ticker,
        "publish_day": publish_day,
        "parent_event_id": parent_id,
        "title": title,
        "summary_snippet": summary,
        "title_norm": _normalize_text(title),
        "summary_norm": _normalize_text(summary),
        "bucket_key": _bucket_key_from_event(data, ticker=ticker),
        "topics_json": json.dumps(topics),
        "status": str(data.get("status") or "active"),
        "verification_status": str(data.get("verification_status") or "pending"),
        "published_at": published_at,
        "updated_at": str(data.get("updated_at") or _now_iso()),
    }


def upsert_index_from_event(event: DistilledNewsEvent | dict[str, Any]) -> None:
    row = _event_to_index_row(event)
    event_id = str(row.get("event_id") or "").strip()
    if not event_id:
        return
    if str(row.get("status") or "") in _INACTIVE_STATUSES:
        remove_index_rows({event_id})
        return

    frame = _load_index_frame()
    if not frame.empty and event_id in frame["event_id"].astype(str).values:
        idx = frame.index[frame["event_id"].astype(str) == event_id][0]
        for col, val in row.items():
            if col in frame.columns:
                frame.at[idx, col] = val
    else:
        frame = concat_dataframes(frame, pd.DataFrame([row]))
    path = event_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_dataframe(frame, path)


def remove_index_rows(event_ids: set[str] | list[str]) -> int:
    ids = {str(event_id).strip() for event_id in event_ids if str(event_id).strip()}
    if not ids:
        return 0
    frame = _load_index_frame()
    if frame.empty:
        return 0
    before = len(frame)
    frame = frame[~frame["event_id"].astype(str).isin(ids)]
    removed = before - len(frame)
    if removed:
        write_dataframe(frame, event_index_path())
    return removed


def index_row_to_headline_dict(row: dict[str, Any]) -> dict[str, Any]:
    topics = json.loads(str(row.get("topics_json") or "[]"))
    parent = str(row.get("parent_event_id") or "").strip() or None
    structured: dict[str, Any] = {}
    if parent:
        structured = {"event_meta": {"parent_event_id": parent}}
    publish_day = str(row.get("publish_day") or "")
    return {
        "canonical_story_id": row.get("event_id"),
        "event_id": row.get("event_id"),
        "title": row.get("title") or "",
        "content_summary": row.get("summary_snippet") or "",
        "published_at": row.get("published_at") or publish_day,
        "tags": {"topics": topics, "publish_day": publish_day},
        "structured_summary": structured,
    }


def query_index_candidates(
    *,
    ticker: str,
    publish_day: str | None = None,
    parent_event_id: str | None = None,
    limit: int = 120,
) -> list[dict[str, Any]]:
    """Return lightweight headline dicts for rule-based event matching."""
    sym = ticker.strip().upper()
    frame = _load_index_frame()
    if frame.empty:
        return []

    if "ticker" in frame.columns:
        frame = frame[frame["ticker"].astype(str).str.upper() == sym]
    if "status" in frame.columns:
        frame = frame[~frame["status"].astype(str).isin(_INACTIVE_STATUSES)]
    if "verification_status" in frame.columns:
        frame = frame[frame["verification_status"].astype(str).str.lower() != "rejected"]

    parent = str(parent_event_id or "").strip() or None
    day = str(publish_day or "")[:10] or None

    if parent and "parent_event_id" in frame.columns:
        frame = frame[
            frame["parent_event_id"].astype(str).eq(parent)
            | frame["parent_event_id"].astype(str).eq("")
            | frame["parent_event_id"].isna()
        ]
    elif day and "publish_day" in frame.columns:
        frame = frame[frame["publish_day"].astype(str) == day]

    if frame.empty:
        return []

    rows = [
        index_row_to_headline_dict(dict(row))
        for _, row in frame.sort_values("updated_at", ascending=False).head(limit).iterrows()
    ]
    return rows


def rebuild_event_index(*, ticker: str | None = None) -> dict[str, Any]:
    from trade_integrations.hub_storage.news_events_store import list_events

    sym_filter = ticker.strip().upper() if ticker else None
    tickers = [sym_filter] if sym_filter else None
    if not tickers:
        from trade_integrations.hub_storage.news_events_store import list_event_tickers

        tickers = list_event_tickers() or ["NIFTY"]

    rows: list[dict[str, Any]] = []
    for sym in tickers:
        for event in list_events(ticker=sym, limit=10_000, include_rejected=True):
            rows.append(_event_to_index_row(event))

    frame = _load_index_frame()
    if sym_filter and not frame.empty and "ticker" in frame.columns:
        frame = frame[frame["ticker"].astype(str).str.upper() != sym_filter]
    elif not sym_filter:
        frame = pd.DataFrame(columns=list(_INDEX_COLUMNS))

    path = event_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        frame = concat_dataframes(frame, pd.DataFrame(rows))
    if frame.empty:
        write_dataframe(pd.DataFrame(columns=list(_INDEX_COLUMNS)), path)
    else:
        write_dataframe(frame, path)

    return {"indexed": len(rows), "path": str(path)}


def ensure_event_index(*, ticker: str | None = None) -> None:
    """Rebuild index when events exist but index is empty or missing ticker rows."""
    sym = ticker.strip().upper() if ticker else None
    frame = _load_index_frame()
    if sym and not frame.empty and "ticker" in frame.columns:
        if (frame["ticker"].astype(str).str.upper() == sym).any():
            return
    elif not frame.empty and not sym:
        return
    from trade_integrations.hub_storage.news_events_store import count_events

    if count_events(ticker=ticker) <= 0:
        return
    rebuild_event_index(ticker=ticker)
