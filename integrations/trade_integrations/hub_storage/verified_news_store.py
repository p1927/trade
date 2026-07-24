"""Hub SSOT for verified, deduplicated news records (internal storage).

Application code must read/query via ``trade_integrations.dataflows.news_hub_bridge``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.hub_storage.parquet_io import concat_dataframes, read_dataframe, write_dataframe

_RECORDS_REL = Path("_data") / "news_verified" / "records.parquet"
_IMPACT_LEDGER_REL = Path("_data") / "news_impact" / "ledger.parquet"

_RECORD_COLUMNS = (
    "canonical_story_id",
    "ticker",
    "title",
    "content_summary",
    "structured_summary_json",
    "sources_json",
    "published_at",
    "verification_status",
    "verification_json",
    "verification_data_as_of",
    "predicted_impact_json",
    "actual_impact_json",
    "maturity_date",
    "horizon_trading_days",
    "tagged_factors_json",
    "tags_json",
    "first_seen_at",
    "updated_at",
)


def verified_records_path() -> Path:
    return get_hub_dir() / _RECORDS_REL


def impact_ledger_path() -> Path:
    return get_hub_dir() / _IMPACT_LEDGER_REL


def ensure_hub_storage() -> None:
    """Create empty parquet ledgers when missing so DuckDB views and verify can register."""
    records = verified_records_path()
    if not records.is_file():
        records.parent.mkdir(parents=True, exist_ok=True)
        write_dataframe(pd.DataFrame(columns=list(_RECORD_COLUMNS)), records)
    ledger = impact_ledger_path()
    if not ledger.is_file():
        ledger.parent.mkdir(parents=True, exist_ok=True)
        write_dataframe(pd.DataFrame(columns=["canonical_story_id", "updated_at"]), ledger)
    try:
        from trade_integrations.hub_storage.news_migrations import ensure_hub_news_migrations

        ensure_hub_news_migrations()
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("hub news migration skipped: %s", exc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _merge_sources(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _normalize_sources((existing or []) + (incoming or []))


def _record_to_row(record: dict[str, Any]) -> dict[str, Any]:
    story_id = str(record.get("canonical_story_id") or "").strip()
    now = _now_iso()
    sources = _normalize_sources(record.get("sources"))
    htd_raw = record.get("horizon_trading_days")
    try:
        if htd_raw is None or pd.isna(htd_raw):
            horizon_days = None
        else:
            horizon_days = int(htd_raw) or None
    except (TypeError, ValueError):
        horizon_days = None
    return {
        "canonical_story_id": story_id,
        "ticker": str(record.get("ticker") or "NIFTY").upper(),
        "title": str(record.get("title") or "")[:500],
        "content_summary": str(record.get("content_summary") or "")[:2000],
        "structured_summary_json": _json_dumps(record.get("structured_summary")),
        "sources_json": _json_dumps(sources),
        "published_at": str(record.get("published_at") or "")[:32],
        "verification_status": str(record.get("verification_status") or "pending"),
        "verification_json": _json_dumps(record.get("verification")),
        "verification_data_as_of": str(record.get("verification_data_as_of") or "")[:10],
        "predicted_impact_json": _json_dumps(record.get("predicted_impact")),
        "actual_impact_json": _json_dumps(record.get("actual_impact")),
        "maturity_date": str(record.get("maturity_date") or "")[:10] or None,
        "horizon_trading_days": horizon_days,
        "tagged_factors_json": _json_dumps(record.get("tagged_factors")),
        "tags_json": _json_dumps(record.get("tags")),
        "first_seen_at": str(record.get("first_seen_at") or now),
        "updated_at": now,
    }


def _row_to_record(row: dict[str, Any] | pd.Series) -> dict[str, Any]:
    data = dict(row)
    verification = _json_loads(data.get("verification_json"), {})
    return {
        "canonical_story_id": data.get("canonical_story_id"),
        "id": data.get("canonical_story_id"),
        "ticker": data.get("ticker"),
        "title": data.get("title"),
        "content_summary": data.get("content_summary"),
        "structured_summary": _json_loads(data.get("structured_summary_json"), {}),
        "sources": _json_loads(data.get("sources_json"), []),
        "published_at": data.get("published_at"),
        "verification_status": data.get("verification_status"),
        "verification": verification,
        "verification_data_as_of": data.get("verification_data_as_of"),
        "predicted_impact": _json_loads(data.get("predicted_impact_json")),
        "predicted": _json_loads(data.get("predicted_impact_json")),
        "actual_impact": _json_loads(data.get("actual_impact_json")),
        "actual": _json_loads(data.get("actual_impact_json")),
        "maturity_date": data.get("maturity_date"),
        "horizon_trading_days": data.get("horizon_trading_days"),
        "tagged_factors": _json_loads(data.get("tagged_factors_json"), []),
        "tags": _json_loads(data.get("tags_json"), {}),
        "first_seen_at": data.get("first_seen_at"),
        "updated_at": data.get("updated_at"),
        "status": "reconciled" if data.get("actual_impact_json") else "live",
        "raw_headline": data.get("title"),
        "url": (_json_loads(data.get("sources_json"), []) or [{}])[0].get("url", ""),
        "source": (_json_loads(data.get("sources_json"), []) or [{}])[0].get("vendor", ""),
        "confidence_note": "Model-attributed estimate; verified against factor data where possible.",
    }


def _load_records_frame() -> pd.DataFrame:
    frame = read_dataframe(verified_records_path())
    if frame.empty:
        return pd.DataFrame(columns=list(_RECORD_COLUMNS))
    for col in _RECORD_COLUMNS:
        if col not in frame.columns:
            frame[col] = None
    return frame


def _coerce_records_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize dtypes so parquet writes stay stable after row-wise merges."""
    if frame.empty:
        return frame
    if "horizon_trading_days" in frame.columns:
        frame["horizon_trading_days"] = pd.to_numeric(
            frame["horizon_trading_days"],
            errors="coerce",
        ).astype("Int64")
    return frame


def _is_distillation_leak(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return (
        "<think" in lowered
        or "redacted_thinking" in lowered
        or lowered.startswith("the user wants me to")
    )


def _should_replace_summary(
    existing: str,
    incoming: str,
    *,
    incoming_structured: dict[str, Any] | None = None,
) -> bool:
    if not incoming:
        return False
    if not existing or _is_distillation_leak(existing):
        return True
    event_meta = ((incoming_structured or {}).get("event_meta") or {})
    if event_meta.get("distilled_by") == "minimax":
        return True
    return len(incoming) > len(existing)


def iter_legacy_verified_records(
    *,
    ticker: str | None = None,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """Read legacy records.parquet directly (migration only)."""
    frame = _load_records_frame()
    if frame.empty:
        return []
    if ticker and "ticker" in frame.columns:
        frame = frame[frame["ticker"].astype(str).str.upper() == ticker.strip().upper()]
    records = [_row_to_record(row) for _, row in frame.sort_values("published_at", ascending=False).iterrows()]
    return records[:limit]


def seed_legacy_record(record: dict[str, Any]) -> None:
    """Write to legacy records.parquet only (migration tests / import source)."""
    story_id = str(record.get("canonical_story_id") or "").strip()
    if not story_id:
        return
    frame = _load_records_frame()
    incoming = _record_to_row(record)
    if not frame.empty and story_id in frame["canonical_story_id"].astype(str).values:
        idx = frame.index[frame["canonical_story_id"].astype(str) == story_id][0]
        for col, val in incoming.items():
            if col in frame.columns:
                frame.at[idx, col] = val
    else:
        frame = concat_dataframes(frame, pd.DataFrame([incoming]))
    write_dataframe(_coerce_records_frame(frame), verified_records_path())


def patch_verified_event_meta(
    updates: list[tuple[str, dict[str, Any]]],
    *,
    min_rows: int | None = None,
) -> int:
    from trade_integrations.hub_storage.news_events_store import patch_event_meta

    return patch_event_meta(updates, min_rows=min_rows)


def upsert_verified_record(record: dict[str, Any]) -> None:
    """Insert or merge a canonical story record in hub events SSOT."""
    from trade_integrations.hub_storage.news_events_store import upsert_hub_record

    upsert_hub_record(record)


def remove_verified_records(story_ids: set[str] | list[str]) -> int:
    from trade_integrations.hub_storage.news_events_store import remove_events

    return remove_events(story_ids)


def count_verified_records(*, ticker: str | None = None) -> int:
    from trade_integrations.hub_storage.news_events_store import count_events

    return count_events(ticker=ticker)


def list_verified_tickers() -> list[str]:
    from trade_integrations.hub_storage.news_events_store import list_event_tickers

    tickers = list_event_tickers()
    if tickers:
        return tickers
    try:
        from trade_integrations.hub_storage.news_migrations import events_ssot_finalized, needs_news_migration

        if events_ssot_finalized() or not needs_news_migration():
            return []
    except Exception:
        pass
    frame = _load_records_frame()
    if frame.empty or "ticker" not in frame.columns:
        return []
    return sorted({str(v).strip().upper() for v in frame["ticker"].astype(str) if str(v).strip()})


def get_verified_record(story_id: str) -> dict[str, Any] | None:
    from trade_integrations.hub_storage.news_events_store import distilled_event_to_headline_dict, get_event

    event = get_event(story_id.strip())
    if event:
        return distilled_event_to_headline_dict(event)
    return None


def list_verified_records(
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
    from trade_integrations.hub_storage.news_events_store import list_verified_records_from_events

    return list_verified_records_from_events(
        status=status,
        since=since,
        until=until,
        publish_day=publish_day,
        symbols=symbols,
        topics=topics,
        factors=factors,
        themes=themes,
        tags=tags,
        limit=limit,
        ticker=ticker,
        include_rejected=include_rejected,
    )


def list_tag_inventory(*, ticker: str = "NIFTY") -> dict[str, Any]:
    """Summarize tag values present in hub for filter UIs."""
    from trade_integrations.dataflows.index_research.news_tags import list_available_tag_vocab, tags_from_dict

    records = list_verified_records(limit=5000, ticker=ticker, include_rejected=True)
    used: dict[str, set[str]] = {
        "topics": set(),
        "themes": set(),
        "factors": set(),
        "symbols": set(),
        "days": set(),
    }
    for rec in records:
        tags = tags_from_dict(rec.get("tags"))
        used["topics"].update(tags.topics)
        used["themes"].update(tags.themes)
        used["factors"].update(tags.factors)
        used["symbols"].update(tags.symbols)
        if tags.publish_day:
            used["days"].add(tags.publish_day)
    vocab = list_available_tag_vocab()
    return {
        "ticker": ticker,
        "record_count": len(records),
        "vocab": vocab,
        "used": {k: sorted(v) for k, v in used.items()},
    }


def list_pending_maturity(as_of: str) -> list[dict[str, Any]]:
    from trade_integrations.hub_storage.news_events_store import list_pending_maturity_events

    return list_pending_maturity_events(as_of)


def count_by_status(*, ticker: str = "NIFTY") -> dict[str, int]:
    from trade_integrations.hub_storage.news_events_store import count_events_by_status

    return count_events_by_status(ticker=ticker)


def _coerce_impact_ledger_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    for col in ("predicted_return_pct", "predicted_nifty_points"):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def append_impact_ledger_row(row: dict[str, Any]) -> None:
    path = impact_ledger_path()
    new_frame = pd.DataFrame([row])
    existing = read_dataframe(path)
    combined = concat_dataframes(existing, new_frame) if not existing.empty else new_frame
    key_cols = [c for c in ("canonical_story_id", "maturity_date", "reconciled_at") if c in combined.columns]
    if key_cols:
        combined = combined.drop_duplicates(subset=key_cols, keep="last")
    write_dataframe(_coerce_impact_ledger_frame(combined), path)


def build_snapshot_from_hub(
    *,
    ticker: str = "NIFTY",
    horizon_days: int = 14,
    spot: float | None = None,
    include_rejected: bool = False,
    limit: int = 20,
    prediction_date: str | None = None,
) -> dict[str, Any]:
    """Materialize UI snapshot from hub events SSOT (no re-verification)."""
    from trade_integrations.dataflows.company_research.market import india_trading_date_iso
    from trade_integrations.dataflows.index_research.hub_news_pipeline.step_08_temporal_attribution import (
        prepare_items_for_prediction_attribution,
    )
    from trade_integrations.hub_storage.news_events_store import (
        count_events_by_status,
        distilled_event_to_headline_dict,
        list_events,
    )

    sym = ticker.strip().upper()
    pred_day = (prediction_date or india_trading_date_iso())[:10]
    pool = [
        distilled_event_to_headline_dict(event)
        for event in list_events(
            ticker=sym,
            status=["approved", "partial"],
            limit=max(limit * 4, 80),
            include_rejected=False,
        )
    ]
    reconciled_items = [
        r for r in pool if (r.get("actual_impact") or r.get("actual") or {}).get("nifty_points") is not None
    ]
    live_items = [r for r in pool if r not in reconciled_items]
    mix_limit = max(4, limit // 3)
    items = (reconciled_items[:mix_limit] + live_items)[:limit]

    try:
        from trade_integrations.dataflows.index_research.news_entity_worker import union_headlines_with_staging
        from trade_integrations.hub_storage.news_staging_store import staging_queue_stats

        items = union_headlines_with_staging(items, ticker=ticker, limit=limit)
        staging_pending = int(staging_queue_stats(ticker=ticker).get("queued") or 0)
    except Exception:
        staging_pending = 0

    items = prepare_items_for_prediction_attribution(
        items,
        prediction_date=pred_day,
        horizon_days=horizon_days,
        ticker=sym,
    )

    rejected_count = count_by_status(ticker=sym).get("rejected", 0)
    if include_rejected:
        rejected_items = [
            distilled_event_to_headline_dict(event)
            for event in list_events(
                ticker=sym,
                status="rejected",
                limit=limit,
                include_rejected=True,
            )
        ]
        items = items + rejected_items

    reconciled = sum(
        1 for i in items if (i.get("actual_impact") or i.get("actual") or {}).get("nifty_points") is not None
    )
    total_reconciled = len(reconciled_items)
    staging_live = sum(1 for i in items if i.get("provenance") == "staging")
    return {
        "status": "ok",
        "as_of": _now_iso(),
        "ticker": ticker,
        "horizon_days": horizon_days,
        "prediction_date": pred_day,
        "spot": spot,
        "items": items,
        "summary": {
            "live_count": sum(
                1
                for i in items
                if (i.get("actual_impact") or i.get("actual") or {}).get("nifty_points") is None
            ),
            "pending_count": staging_pending,
            "staging_live_count": staging_live,
            "reconciled_count": reconciled,
            "reconciled_total": total_reconciled,
            "approved_count": sum(1 for i in items if i.get("verification_status") == "approved"),
            "partial_count": sum(1 for i in items if i.get("verification_status") == "partial"),
            "rejected_count": rejected_count,
            "rejected_skipped": rejected_count if not include_rejected else 0,
            "source": "hub_events",
        },
    }
