"""Backfill FII participant OI / PCR from NSE via nselib participant_wise_open_interest."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.context.hub import get_hub_dir
from trade_integrations.dataflows.index_research.factor_store import load_day_factor_keys, upsert_daily_factors
from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history

logger = logging.getLogger(__name__)

_CACHE_DIR_NAME = "_data/participant_oi"


def _cache_dir() -> Path:
    path = get_hub_dir() / _CACHE_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_path(day: str) -> Path:
    return _cache_dir() / f"{day[:10]}.json"


def _parse_trade_date(day: str) -> str:
    """DD-MM-YYYY for nselib."""
    d = datetime.strptime(day[:10], "%Y-%m-%d")
    return d.strftime("%d-%m-%Y")


def _extract_fii_row(frame: pd.DataFrame) -> dict[str, float] | None:
    if frame is None or frame.empty:
        return None
    col = "Client Type" if "Client Type" in frame.columns else frame.columns[0]
    fii = frame[frame[col].astype(str).str.upper() == "FII"]
    if fii.empty:
        return None
    row = fii.iloc[0]

    def _num(name: str) -> float | None:
        if name not in row.index:
            return None
        try:
            val = float(row[name])
            return val if pd.notna(val) else None
        except (TypeError, ValueError):
            return None

    fut_long = _num("Future Index Long")
    fut_short = _num("Future Index Short")
    call_short = _num("Option Index Call Short")
    put_short = _num("Option Index Put Short")
    pcr = None
    if call_short and call_short > 0 and put_short is not None:
        pcr = put_short / call_short
    fut_ratio = None
    if fut_short and fut_short > 0 and fut_long is not None:
        fut_ratio = fut_long / fut_short
    return {
        "fii_idx_fut_long": fut_long,
        "fii_idx_fut_short": fut_short,
        "fii_fut_long_short_ratio": fut_ratio,
        "nifty_pcr": pcr,
        "fii_idx_put_oi": put_short,
        "fii_idx_call_oi": call_short,
    }


def fetch_participant_oi_day(day: str) -> dict[str, Any] | None:
    """Fetch one day's FII participant OI; use disk cache when present."""
    cache = _cache_path(day)
    if cache.is_file():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    from trade_integrations.dataflows import source_availability

    capability = "participant_oi"
    if not source_availability.should_attempt("nselib", capability):
        return None

    try:
        from nselib import derivatives

        frame = derivatives.participant_wise_open_interest(trade_date=_parse_trade_date(day))
    except ImportError as exc:
        source_availability.record_failure("nselib", capability, exc)
        logger.debug("participant OI failed %s: %s", day, exc)
        return None
    except Exception as exc:
        source_availability.record_failure("nselib", capability, exc)
        logger.debug("participant OI failed %s: %s", day, exc)
        return None

    metrics = _extract_fii_row(frame)
    if not metrics:
        source_availability.record_failure("nselib", capability, "empty participant OI metrics")
        return None
    payload = {"date": day[:10], **metrics, "source": "nselib_participant_oi"}
    try:
        cache.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass
    source_availability.record_success("nselib", capability)
    return payload


_PARTICIPANT_OI_FACTORS = frozenset(
    {
        "fii_idx_fut_long",
        "fii_idx_fut_short",
        "fii_fut_long_short_ratio",
        "nifty_pcr",
        "fii_idx_put_oi",
        "fii_idx_call_oi",
    }
)


def backfill_participant_oi(
    *,
    days: int = 365,
    sleep_seconds: float = 0.4,
    max_days: int | None = 120,
    skip_if_complete: bool = False,
) -> dict[str, int | str]:
    """Loop trading days and cache participant OI; upsert derivatives factors."""
    nifty = load_nifty_history(days=days)
    if nifty.empty:
        return {"status": "error", "reason": "no_nifty_history"}

    trading_days = nifty["date"].astype(str).tolist()
    if max_days is not None:
        trading_days = trading_days[-max_days:]

    written = 0
    skipped = 0
    errors = 0
    for day in trading_days:
        try:
            if skip_if_complete and _PARTICIPANT_OI_FACTORS.issubset(load_day_factor_keys(day)):
                skipped += 1
                continue
            payload = fetch_participant_oi_day(day)
            if not payload:
                skipped += 1
                time.sleep(sleep_seconds)
                continue
            rows = []
            for factor, value in payload.items():
                if factor in {"date", "source"} or value is None:
                    continue
                rows.append(
                    {
                        "factor": factor,
                        "value": float(value),
                        "source": "backfill_participant_oi",
                    }
                )
            if rows:
                upsert_daily_factors(day, rows)
                written += 1
            else:
                skipped += 1
        except Exception as exc:
            logger.debug("participant backfill %s: %s", day, exc)
            errors += 1
        time.sleep(sleep_seconds)

    return {
        "status": "ok",
        "trading_days": len(trading_days),
        "written": written,
        "skipped": skipped,
        "errors": errors,
        "start": trading_days[0] if trading_days else None,
        "end": trading_days[-1] if trading_days else None,
    }
