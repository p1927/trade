"""Append-only capture writers for proprietary hub time series."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.hub_capture.gate import should_capture
from trade_integrations.hub_capture.registry import capture_base_dir, get_entity
from trade_integrations.hub_storage.parquet_io import read_dataframe, write_dataframe

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _series_dir(entity_id: str, series: str) -> Path:
    return capture_base_dir(entity_id) / series


def _daily_path(entity_id: str, series: str, day: str) -> Path:
    return _series_dir(entity_id, series) / f"{day[:10]}.parquet"


def _dedupe_key(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    parts = [str(row.get(k) or "") for k in keys]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _append_rows(
    entity_id: str,
    series: str,
    rows: list[dict[str, Any]],
    *,
    dedupe_keys: tuple[str, ...],
) -> dict[str, Any]:
    if not rows:
        return {"status": "empty", "appended": 0}
    day = str(rows[0].get("captured_at") or _now_iso())[:10]
    path = _daily_path(entity_id, series, day)
    existing = read_dataframe(path)
    if existing.empty:
        frame = pd.DataFrame(rows)
    else:
        frame = pd.concat([existing, pd.DataFrame(rows)], ignore_index=True)
    if dedupe_keys:
        frame["_dedupe"] = frame.apply(lambda r: _dedupe_key(r.to_dict(), dedupe_keys), axis=1)
        before = len(frame)
        frame = frame.drop_duplicates(subset=["_dedupe"], keep="last")
        frame = frame.drop(columns=["_dedupe"])
        appended = len(frame) - len(existing) if not existing.empty else len(frame)
    else:
        appended = len(rows)
    write_dataframe(frame, path)
    return {"status": "ok", "path": str(path), "appended": appended, "total_rows": len(frame)}


def _flatten_chain_rows(
    entity_id: str,
    chain_data: dict[str, Any],
    *,
    source: str,
    vendor: str,
    captured_at: str | None = None,
) -> list[dict[str, Any]]:
    ts = captured_at or _now_iso()
    underlying = str(chain_data.get("underlying") or entity_id).upper()
    expiry = chain_data.get("expiry_date") or chain_data.get("expiry")
    spot = chain_data.get("underlying_ltp") or chain_data.get("spot")
    rows: list[dict[str, Any]] = []
    for leg in chain_data.get("chain") or []:
        if not isinstance(leg, dict):
            continue
        strike = leg.get("strike") or leg.get("strike_price")
        if strike is None:
            continue
        for opt_type in ("CE", "PE"):
            nested = leg.get(opt_type.lower()) or leg.get(opt_type)
            if isinstance(nested, dict):
                ltp = nested.get("ltp") or nested.get("price")
                oi = nested.get("oi") or nested.get("open_interest")
                iv = nested.get("iv") or nested.get("implied_volatility")
                volume = nested.get("volume")
                opt_symbol = nested.get("symbol")
            else:
                prefix = opt_type.lower()
                ltp = leg.get(f"{prefix}_ltp")
                oi = leg.get(f"{prefix}_oi")
                iv = leg.get(f"{prefix}_iv")
                volume = leg.get(f"{prefix}_volume")
                opt_symbol = leg.get(f"{prefix}_symbol")
            if ltp is None and oi is None:
                continue
            rows.append(
                {
                    "entity_id": underlying,
                    "captured_at": ts,
                    "underlying": underlying,
                    "expiry": expiry,
                    "spot": spot,
                    "strike": strike,
                    "option_type": opt_type,
                    "ltp": ltp,
                    "oi": oi,
                    "iv": iv,
                    "volume": volume,
                    "symbol": opt_symbol,
                    "source": source,
                    "vendor": vendor,
                    "series": "derivatives_chain",
                }
            )
    return rows


def _aggregate_pcr(rows: list[dict[str, Any]]) -> float | None:
    ce_oi = pe_oi = 0.0
    for row in rows:
        oi = row.get("oi")
        if oi is None:
            continue
        try:
            val = float(oi)
        except (TypeError, ValueError):
            continue
        if row.get("option_type") == "CE":
            ce_oi += val
        elif row.get("option_type") == "PE":
            pe_oi += val
    if ce_oi <= 0:
        return None
    return round(pe_oi / ce_oi, 4)


def record_chain_snapshot(
    entity_id: str,
    chain_data: dict[str, Any],
    *,
    source: str = "openalgo",
    vendor: str = "openalgo",
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Persist option chain snapshot when capture gate allows."""
    if not should_capture(entity_id, "derivatives_chain"):
        return {"status": "skipped", "reason": "capture_disabled"}
    rows = _flatten_chain_rows(
        entity_id, chain_data, source=source, vendor=vendor, captured_at=captured_at
    )
    if not rows:
        return {"status": "empty", "reason": "no_chain_rows"}
    pcr = chain_data.get("pcr")
    if pcr is None:
        pcr = _aggregate_pcr(rows)
    summary_row = {
        "entity_id": entity_id.upper(),
        "captured_at": captured_at or _now_iso(),
        "underlying": str(chain_data.get("underlying") or entity_id).upper(),
        "spot": chain_data.get("underlying_ltp") or chain_data.get("spot"),
        "expiry": chain_data.get("expiry_date"),
        "nifty_pcr": pcr,
        "source": source,
        "vendor": vendor,
        "series": "pcr_summary",
        "leg_count": len(rows),
    }
    chain_result = _append_rows(
        entity_id,
        "derivatives_chain",
        rows,
        dedupe_keys=("captured_at", "strike", "option_type", "source"),
    )
    pcr_result: dict[str, Any] = {"status": "skipped", "reason": "pcr_unavailable"}
    if pcr is not None and not (isinstance(pcr, float) and pd.isna(pcr)):
        pcr_result = _append_rows(
            entity_id,
            "derivatives_chain",
            [summary_row],
            dedupe_keys=("captured_at", "series", "source"),
        )
    return {
        "status": "ok",
        "chain": chain_result,
        "pcr_summary": pcr_result,
        "nifty_pcr": pcr,
    }


def record_flow_snapshot(
    entity_id: str,
    metrics: dict[str, Any],
    *,
    source: str,
    captured_at: str | None = None,
) -> dict[str, Any]:
    if not should_capture(entity_id, "flows"):
        return {"status": "skipped", "reason": "capture_disabled"}
    ts = captured_at or _now_iso()
    row = {
        "entity_id": entity_id.upper(),
        "captured_at": ts,
        "date": str(metrics.get("date") or ts[:10]),
        "source": source,
        "series": "flows",
        **{k: metrics[k] for k in metrics if k not in {"date"}},
    }
    return _append_rows(entity_id, "flows", [row], dedupe_keys=("date", "source", "series"))


def record_vix_snapshot(
    entity_id: str,
    value: float,
    *,
    source: str,
    captured_at: str | None = None,
) -> dict[str, Any]:
    if not should_capture(entity_id, "vix"):
        return {"status": "skipped", "reason": "capture_disabled"}
    ts = captured_at or _now_iso()
    row = {
        "entity_id": entity_id.upper(),
        "captured_at": ts,
        "date": ts[:10],
        "india_vix": value,
        "source": source,
        "series": "vix",
    }
    return _append_rows(entity_id, "vix", [row], dedupe_keys=("date", "source"))


def record_quote_snapshot(
    entity_id: str,
    quote: dict[str, Any],
    *,
    source: str = "openalgo",
    captured_at: str | None = None,
) -> dict[str, Any]:
    if not should_capture(entity_id, "derivatives_chain"):
        return {"status": "skipped", "reason": "capture_disabled"}
    ts = captured_at or _now_iso()
    row = {
        "entity_id": entity_id.upper(),
        "captured_at": ts,
        "date": ts[:10],
        "ltp": quote.get("ltp"),
        "volume": quote.get("volume"),
        "change_pct": quote.get("change_pct"),
        "source": source,
        "series": "quotes",
        "channel": "hub_channel",
    }
    return _append_rows(entity_id, "quotes", [row], dedupe_keys=("captured_at", "source", "series"))


def record_news_snapshot(
    entity_id: str,
    headlines: list[dict[str, Any]],
    *,
    source: str,
    captured_at: str | None = None,
) -> dict[str, Any]:
    if not should_capture(entity_id, "flows"):
        return {"status": "skipped", "reason": "capture_disabled"}
    ts = captured_at or _now_iso()
    rows = []
    for headline in headlines:
        if not isinstance(headline, dict):
            continue
        title = str(headline.get("title") or "").strip()
        if not title:
            continue
        rows.append(
            {
                "entity_id": entity_id.upper(),
                "captured_at": ts,
                "date": ts[:10],
                "title": title[:500],
                "summary": str(headline.get("summary") or "")[:1000],
                "url": str(headline.get("url") or headline.get("link") or "")[:500],
                "source": source,
                "series": "news",
                "channel": "hub_channel",
            }
        )
    if not rows:
        return {"status": "empty"}
    return _append_rows(entity_id, "news", rows, dedupe_keys=("date", "title", "source"))


def prune_capture_series(entity_id: str) -> dict[str, Any]:
    """Delete daily capture files older than entity retention policy."""
    entity = get_entity(entity_id)
    if not entity:
        return {"status": "skipped", "reason": "unknown_entity"}
    retention = entity.get("retention_days") or {}
    summary: dict[str, Any] = {"pruned": {}}
    from datetime import date, timedelta

    today = date.today()
    mapping = {
        "derivatives_chain": int(retention.get("derivatives", 365)),
        "flows": int(retention.get("flows", 365)),
        "vix": int(retention.get("vix", 365)),
        "quotes": int(retention.get("derivatives", 365)),
        "news": int(retention.get("flows", 365)),
    }
    for series, keep_days in mapping.items():
        directory = _series_dir(entity_id, series)
        removed = 0
        if directory.is_dir():
            cutoff = today - timedelta(days=keep_days)
            for path in directory.glob("*.parquet"):
                try:
                    file_day = date.fromisoformat(path.stem[:10])
                except ValueError:
                    continue
                if file_day < cutoff:
                    path.unlink(missing_ok=True)
                    csv = path.with_suffix(".csv")
                    if csv.is_file():
                        csv.unlink(missing_ok=True)
                    removed += 1
        summary["pruned"][series] = removed
    summary["status"] = "ok"
    return summary
