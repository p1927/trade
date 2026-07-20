"""Recent gap backfill via nselib (participant OI / FII derivatives)."""

from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd

from trade_integrations.dataflows.index_research.participant_oi_backfill import fetch_participant_oi_day
from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
    upsert_flow_cash_cache,
)
from trade_integrations.dataflows.index_research.sources.nselib_fetch import iso_to_nselib

logger = logging.getLogger(__name__)


def fetch_fii_derivatives_day(day: str) -> dict[str, Any] | None:
    """One day of FII Nifty futures OI from nselib fii_derivatives_statistics xls."""
    try:
        from nselib import derivatives

        frame = derivatives.fii_derivatives_statistics(trade_date=iso_to_nselib(day))
    except Exception as exc:
        logger.debug("nselib fii_derivatives_statistics failed %s: %s", day, exc)
        return None
    if frame is None or frame.empty:
        return None

    col = "fii_derivatives" if "fii_derivatives" in frame.columns else frame.columns[0]
    nifty = frame[frame[col].astype(str).str.upper().str.strip() == "NIFTY FUTURES"]
    if nifty.empty:
        return None
    row = nifty.iloc[0]

    def _num(name: str) -> float | None:
        if name not in row.index:
            return None
        try:
            val = float(row[name])
            return val if pd.notna(val) else None
        except (TypeError, ValueError):
            return None

    open_oi = _num("open_contracts")
    if open_oi is None:
        return None

    payload: dict[str, Any] = {
        "date": day[:10],
        "source": "nselib_fii_derivatives",
        "fii_nifty_fut_open_oi": open_oi,
    }
    buy_val = _num("buy_value_in_Cr")
    sell_val = _num("sell_value_in_Cr")
    if buy_val is not None:
        payload["fii_nifty_fut_buy_cr"] = buy_val
    if sell_val is not None:
        payload["fii_nifty_fut_sell_cr"] = sell_val
    return payload


def backfill_nselib_flow_gaps(
    trading_days: list[str],
    *,
    sleep_seconds: float = 0.5,
) -> dict[str, int | str]:
    """Backfill participant OI + FII deriv stats for explicit trading days."""
    rows: list[dict[str, Any]] = []
    participant_hits = 0
    deriv_hits = 0
    skipped = 0

    for day in trading_days:
        merged: dict[str, Any] = {"date": day[:10]}
        poi = fetch_participant_oi_day(day)
        if poi:
            merged.update({k: v for k, v in poi.items() if k not in {"date", "source"} and v is not None})
            participant_hits += 1
        deriv = fetch_fii_derivatives_day(day)
        if deriv:
            merged.update({k: v for k, v in deriv.items() if k not in {"date", "source"} and v is not None})
            deriv_hits += 1
        if len(merged) <= 1:
            skipped += 1
            time.sleep(sleep_seconds)
            continue
        merged["source"] = "nselib_recent_backfill"
        rows.append(merged)
        time.sleep(sleep_seconds)

    written = upsert_flow_cash_cache(rows) if rows else 0
    return {
        "status": "ok",
        "days_requested": len(trading_days),
        "participant_hits": participant_hits,
        "deriv_hits": deriv_hits,
        "skipped": skipped,
        "cache_rows_written": written,
        "start": trading_days[0] if trading_days else None,
        "end": trading_days[-1] if trading_days else None,
    }
