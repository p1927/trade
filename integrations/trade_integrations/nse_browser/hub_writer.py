"""Persist raw artifacts and normalized parquet into hub _data/nse_browser/."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.nse_browser.registry import get_mission, hub_root

logger = logging.getLogger(__name__)


def _read_parquet_or_csv(path: Path) -> pd.DataFrame:
    csv_path = path.with_suffix(".csv")
    if path.is_file():
        try:
            return pd.read_parquet(path)
        except Exception:
            if csv_path.is_file():
                return pd.read_csv(csv_path)
            raise
    if csv_path.is_file():
        return pd.read_csv(csv_path)
    return pd.DataFrame()


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        frame.to_parquet(path, index=False)
    except ImportError:
        frame.to_csv(path.with_suffix(".csv"), index=False)
        return
    frame.to_csv(path.with_suffix(".csv"), index=False)


def save_raw_bytes(content: bytes, *, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return dest


def save_raw_text(content: str, *, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return dest


def upsert_daily_parquet(
    frame: pd.DataFrame,
    *,
    path: Path,
    date_col: str = "date",
) -> int:
    """Merge rows by date (last wins) into hub parquet.

    When a ``granularity`` column is present on incoming or existing rows, dedupe
    uses ``(date, granularity)`` so monthly aggregates can coexist with daily rows
    on the same calendar date (e.g. month-end).

    When ``index_slug`` is present, dedupe uses ``(date, index_slug)`` so sector
    index panels keep one row per index per day.
    """
    if frame.empty or date_col not in frame.columns:
        return 0
    incoming = frame.copy()
    incoming[date_col] = incoming[date_col].astype(str).str[:10]
    existing = _read_parquet_or_csv(path) if path.is_file() or path.with_suffix(".csv").is_file() else pd.DataFrame()

    use_granularity = "granularity" in incoming.columns or (
        not existing.empty and "granularity" in existing.columns
    )
    use_index_slug = "index_slug" in incoming.columns or (
        not existing.empty and "index_slug" in existing.columns
    )

    dedupe = [date_col]
    if use_index_slug:
        if "index_slug" not in incoming.columns:
            incoming["index_slug"] = ""
        if not existing.empty and "index_slug" not in existing.columns:
            existing = existing.copy()
            existing["index_slug"] = ""
        dedupe.append("index_slug")
    if use_granularity:
        if "granularity" not in incoming.columns:
            incoming["granularity"] = "daily"
        if not existing.empty and "granularity" not in existing.columns:
            existing = existing.copy()
            existing["granularity"] = "daily"
        if "granularity" not in dedupe:
            dedupe.append("granularity")

    if existing.empty:
        merged = incoming
    else:
        existing[date_col] = existing[date_col].astype(str).str[:10]
        incoming_keys = {tuple(str(row[c]) for c in dedupe) for _, row in incoming[dedupe].iterrows()}
        keep_mask = ~existing.apply(
            lambda row: tuple(str(row[c]) for c in dedupe) in incoming_keys, axis=1
        )
        keep = existing[keep_mask]
        merged = pd.concat([keep, incoming], ignore_index=True)
    merged = merged.sort_values(dedupe).drop_duplicates(dedupe, keep="last")
    _write_parquet(merged, path)
    return len(incoming)


def _dedupe_daily_frame(frame: pd.DataFrame, *, date_col: str = "date") -> pd.DataFrame:
    """Drop duplicate keys preserving last row per trading day (and slug/granularity when present)."""
    if frame.empty or date_col not in frame.columns:
        return frame
    out = frame.copy()
    out[date_col] = out[date_col].astype(str).str[:10]
    dedupe = [date_col]
    if "index_slug" in out.columns:
        dedupe.append("index_slug")
    if "granularity" in out.columns:
        out["granularity"] = out["granularity"].fillna("daily").astype(str)
        dedupe.append("granularity")
    return out.sort_values(dedupe).drop_duplicates(dedupe, keep="last")


def load_fii_dii_daily() -> pd.DataFrame:
    path = hub_root() / "fii_dii_daily.parquet"
    frame = _read_parquet_or_csv(path)
    return _dedupe_daily_frame(frame)


def load_fpi_daily() -> pd.DataFrame:
    path = hub_root() / "fpi_daily.parquet"
    frame = _read_parquet_or_csv(path)
    return _dedupe_daily_frame(frame)


def load_hub_parquet(name: str) -> pd.DataFrame:
    path = hub_root() / name
    frame = _read_parquet_or_csv(path)
    return _dedupe_daily_frame(frame)


def load_archive_dataset(dataset: str) -> pd.DataFrame:
    path = hub_root() / "archives" / f"{dataset}.parquet"
    frame = _read_parquet_or_csv(path)
    if frame.empty:
        return frame
    if "date" in frame.columns:
        frame["date"] = frame["date"].astype(str).str[:10]
    return frame.sort_values("date").drop_duplicates("date", keep="last") if "date" in frame.columns else frame


def load_dataset_frame(dataset_id: str) -> pd.DataFrame:
    """Load hub parquet for a canonical dataset id."""
    from trade_integrations.nse_browser.registry import get_dataset

    spec = get_dataset(dataset_id)
    if spec is None:
        return pd.DataFrame()
    if spec.id == "fii_dii":
        return load_fii_dii_daily()
    if spec.id == "fpi":
        return load_fpi_daily()
    if spec.id in ("mf_sebi", "fii_sebi", "sector_indices"):
        return load_hub_parquet(Path(spec.parquet_rel).name)
    return load_archive_dataset(spec.id)


def query_frame_by_dates(
    frame: pd.DataFrame,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    date_col: str = "date",
    limit: int = 500,
) -> pd.DataFrame:
    """Filter a daily hub frame to a date range."""
    if frame.empty:
        return frame
    out = frame.copy()
    if date_col not in out.columns:
        return out.head(limit)
    out[date_col] = out[date_col].astype(str).str[:10]
    if start_date:
        out = out[out[date_col] >= start_date[:10]]
    if end_date:
        out = out[out[date_col] <= end_date[:10]]
    return out.sort_values(date_col).tail(limit)


def frame_to_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert DataFrame to JSON-safe list of dicts."""
    if frame.empty:
        return []
    out = frame.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].astype(str).str[:10]
    records = out.to_dict(orient="records")
    safe: list[dict[str, Any]] = []
    for row in records:
        entry: dict[str, Any] = {}
        for key, val in row.items():
            if val is None or (isinstance(val, float) and pd.isna(val)):
                entry[key] = None
            elif hasattr(val, "item"):
                try:
                    entry[key] = val.item()
                except (ValueError, AttributeError):
                    entry[key] = str(val)
            else:
                entry[key] = val
        safe.append(entry)
    return safe


def is_mission_fresh(mission_id: str, *, freshness_hours: int | None = None) -> tuple[bool, str | None]:
    """
    Return (is_fresh, fetched_at_iso).

    Uses mission status fetched_at and MissionSpec.freshness_hours when not overridden.
    """
    status = load_mission_status(mission_id)
    fetched_at = status.get("fetched_at") or status.get("updated_at")
    if not fetched_at:
        return False, None
    spec = get_mission(mission_id)
    hours = freshness_hours if freshness_hours is not None else (spec.freshness_hours if spec else 24)
    try:
        ts = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - ts
        return age <= timedelta(hours=hours), str(fetched_at)
    except (TypeError, ValueError):
        return False, str(fetched_at)


def dataset_parquet_path(dataset_id: str) -> Path | None:
    from trade_integrations.nse_browser.registry import get_dataset

    spec = get_dataset(dataset_id)
    if spec is None:
        return None
    return hub_root() / spec.parquet_rel


def save_mission_status(mission_id: str, payload: dict[str, Any]) -> Path:
    dest = hub_root() / "status" / f"{mission_id}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = {
        **payload,
        "mission": mission_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    dest.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return dest


def load_mission_status(mission_id: str) -> dict[str, Any]:
    dest = hub_root() / "status" / f"{mission_id}.json"
    if not dest.is_file():
        return {}
    try:
        return json.loads(dest.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("mission status read failed %s: %s", mission_id, exc)
        return {}


def mission_result(
    *,
    mission: str,
    status: str,
    vendor: str,
    rows: int = 0,
    date_range: dict[str, str | None] | None = None,
    artifacts: list[str] | None = None,
    data: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    return {
        "mission": mission,
        "status": status,
        "vendor": vendor,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
        "date_range": date_range or {},
        "artifacts": artifacts or [],
        "data": data or {},
        "error": error,
    }
