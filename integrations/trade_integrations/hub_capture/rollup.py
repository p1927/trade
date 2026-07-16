"""Roll capture ledgers into index factor parquet and prune stale files."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from trade_integrations.dataflows.index_research.factor_store import save_daily_factors, upsert_daily_factors
from trade_integrations.hub_capture.registry import capture_base_dir, get_entity, load_registry
from trade_integrations.hub_capture.writers import prune_capture_series
from trade_integrations.hub_storage.parquet_io import read_dataframe

logger = logging.getLogger(__name__)


def _collect_day_rows(entity_id: str, series: str, day: str) -> pd.DataFrame:
    path = capture_base_dir(entity_id) / series / f"{day[:10]}.parquet"
    return read_dataframe(path)


def rollup_capture_to_factors(*, entity_id: str = "NIFTY", as_of_date: str | None = None) -> dict[str, Any]:
    """Merge capture summaries into index factor daily parquet for the given date."""
    entity = get_entity(entity_id)
    if not entity or not entity.get("capture_enabled"):
        return {"status": "skipped", "reason": "capture_disabled"}

    day = (as_of_date or datetime.now(timezone.utc).date().isoformat())[:10]
    rows: list[dict[str, Any]] = []

    chain_dir = capture_base_dir(entity_id) / "derivatives_chain" / f"{day}.parquet"
    chain_frame = read_dataframe(chain_dir)
    if not chain_frame.empty and "series" in chain_frame.columns:
        pcr_rows = chain_frame[chain_frame["series"] == "pcr_summary"]
        if not pcr_rows.empty:
            pcr_val = pcr_rows.iloc[-1].get("nifty_pcr")
            if pcr_val is not None and pd.notna(pcr_val):
                rows.append(
                    {
                        "factor": "nifty_pcr",
                        "value": float(pcr_val),
                        "source": "hub_capture",
                        "entity_id": entity_id.upper(),
                    }
                )

    flows_frame = _collect_day_rows(entity_id, "flows", day)
    if not flows_frame.empty:
        last = flows_frame.iloc[-1].to_dict()
        for col, factor in (
            ("fii_net_5d", "fii_net_5d"),
            ("dii_net_5d", "dii_net_5d"),
            ("fii_net", "fii_net_5d"),
            ("dii_net", "dii_net_5d"),
            ("fii_fut_long_short_ratio", "fii_fut_long_short_ratio"),
        ):
            if col in last and last[col] is not None and pd.notna(last[col]):
                rows.append(
                    {
                        "factor": factor,
                        "value": float(last[col]),
                        "source": str(last.get("source") or "hub_capture"),
                        "entity_id": entity_id.upper(),
                    }
                )

    vix_frame = _collect_day_rows(entity_id, "vix", day)
    if not vix_frame.empty:
        vix_val = vix_frame.iloc[-1].get("india_vix")
        if vix_val is not None and pd.notna(vix_val):
            rows.append(
                {
                    "factor": "india_vix",
                    "value": float(vix_val),
                    "source": "hub_capture",
                    "entity_id": entity_id.upper(),
                }
            )

    if not rows:
        return {"status": "empty", "day": day, "rows": 0}

    upsert_daily_factors(day, rows)
    return {"status": "ok", "day": day, "rows": len(rows), "factors": [r["factor"] for r in rows]}


def run_capture_rollup(*, as_of_date: str | None = None) -> dict[str, Any]:
    """Roll up all enabled entities and prune old capture files."""
    reg = load_registry(create=False)
    summary: dict[str, Any] = {"entities": {}, "status": "ok"}
    for entity in reg.get("entities") or []:
        if not entity.get("capture_enabled"):
            continue
        eid = str(entity.get("id") or "NIFTY").upper()
        summary["entities"][eid] = {
            "rollup": rollup_capture_to_factors(entity_id=eid, as_of_date=as_of_date),
            "prune": prune_capture_series(eid),
        }
    return summary


def capture_coverage_stats(*, entity_id: str = "NIFTY", trading_days: int = 252) -> dict[str, Any]:
    """Per-series fill rate for DuckDB capture_coverage view."""
    base = capture_base_dir(entity_id)
    out: dict[str, Any] = {"entity_id": entity_id, "series": {}}
    for series in ("derivatives_chain", "flows", "vix"):
        directory = base / series
        days = 0
        if directory.is_dir():
            days = sum(1 for p in directory.glob("*.parquet") if p.stem[:4].isdigit())
        out["series"][series] = {
            "days_captured": days,
            "trading_days_window": trading_days,
            "fill_rate_pct": round(100.0 * days / trading_days, 2) if trading_days else 0.0,
        }
    poi = capture_base_dir(entity_id).parent.parent / "participant_oi"
    if poi.is_dir():
        poi_days = sum(1 for p in poi.glob("*.json"))
        out["series"]["participant_oi"] = {
            "days_captured": poi_days,
            "trading_days_window": trading_days,
            "fill_rate_pct": round(100.0 * poi_days / trading_days, 2) if trading_days else 0.0,
        }
    return out
