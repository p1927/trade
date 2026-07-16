"""Explain Nifty daily moves from aligned factor history."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from trade_integrations.dataflows.index_research.backtest_runner import (
    _calendar_events_for_date,
    _factor_drivers,
    _FACTOR_LABELS,
)
from trade_integrations.dataflows.index_research.causal_attribution import (
    build_causal_hypotheses,
    collect_constituent_headlines_for_day,
    _fetch_index_headlines,
)
from trade_integrations.dataflows.index_research.sources.history_loader import (
    load_aligned_factor_history,
)


def build_nifty_price_series(*, days: int = 365) -> list[dict[str, Any]]:
    """Daily Nifty close + 1d return for historical charts."""
    frame = load_aligned_factor_history(days=days)
    if frame.empty or "close" not in frame.columns:
        return []
    frame = frame.sort_values("date").reset_index(drop=True)
    closes = frame["close"].astype(float)
    frame["realized_1d_pct"] = (closes - closes.shift(1)) / closes.shift(1) * 100.0
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        day = str(row["date"])[:10]
        move = row.get("realized_1d_pct")
        rows.append(
            {
                "date": day,
                "close": round(float(row["close"]), 2),
                "realized_1d_pct": round(float(move), 3) if pd.notna(move) else None,
            }
        )
    return rows


def explain_nifty_day(day: str, *, history_days: int = 365) -> dict[str, Any]:
    """Return factor drivers and calendar context for one trading date."""
    target = str(day).strip()[:10]
    frame = load_aligned_factor_history(days=history_days)
    if frame.empty or "close" not in frame.columns:
        return {"status": "error", "message": "No aligned factor history"}

    frame = frame.sort_values("date").reset_index(drop=True)
    frame["date_str"] = frame["date"].astype(str).str[:10]
    matches = frame.index[frame["date_str"] == target].tolist()
    if not matches:
        return {"status": "not_found", "date": target, "message": "Date not in history window"}

    idx = int(matches[0])
    row = frame.iloc[idx]
    prev_idx = idx - 1 if idx > 0 else idx

    exclude = {"date", "date_str", "close", "open", "high", "low", "volume", "target"}
    feature_cols = [
        c
        for c in frame.columns
        if c not in exclude and pd.api.types.is_numeric_dtype(frame[c])
    ]

    def _row_factors(r: pd.Series) -> dict[str, float]:
        out: dict[str, float] = {}
        for col in feature_cols:
            val = r.get(col)
            if val is not None and pd.notna(val):
                try:
                    out[col] = float(val)
                except (TypeError, ValueError):
                    continue
        return out

    factors_today = _row_factors(row)
    factors_prev = _row_factors(frame.iloc[prev_idx])
    try:
        as_of = date.fromisoformat(target)
    except ValueError:
        as_of = date.today()

    move_pct = None
    if idx > 0:
        prev_close = float(frame.iloc[idx - 1]["close"])
        cur_close = float(row["close"])
        if prev_close > 0:
            move_pct = round((cur_close - prev_close) / prev_close * 100.0, 3)

    drivers = _factor_drivers(factors_today, factors_prev, limit=10)
    calendar = _calendar_events_for_date(as_of)
    index_headlines = _fetch_index_headlines(target, limit=5)
    constituent_headlines = collect_constituent_headlines_for_day(target, limit=6)

    narrative: list[str] = []
    for d in drivers[:5]:
        label = d.get("label") or d.get("factor")
        prev_v = d.get("prev")
        cur_v = d.get("current")
        if prev_v is not None and cur_v is not None:
            narrative.append(f"{label} moved from {prev_v} → {cur_v} ({d.get('change_pct'):+.1f}% d/d)")

    causal_hypotheses = build_causal_hypotheses(
        factor_drivers=drivers,
        realized_1d_pct=move_pct,
        calendar_events=calendar,
        index_headlines=index_headlines,
        constituent_headlines=constituent_headlines,
    )

    return {
        "status": "ok",
        "date": target,
        "close": round(float(row["close"]), 2),
        "realized_1d_pct": move_pct,
        "factor_drivers": drivers,
        "calendar_events": calendar,
        "narrative": narrative,
        "causal_hypotheses": causal_hypotheses,
        "index_headlines": index_headlines,
        "constituent_headlines": constituent_headlines,
        "labels": _FACTOR_LABELS,
    }
