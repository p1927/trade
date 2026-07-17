"""HTTP fetchers for third-party FII/DII flow data (no browser required where possible)."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

_NIFTYINVEST_MONTH_API = "https://niftyinvest.com/fii-dii-data/api/v1/month"
_USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _parse_session_row(session: dict[str, Any]) -> dict[str, Any] | None:
    day = str(session.get("date") or "")[:10]
    if not day:
        return None
    cash = session.get("cash") if isinstance(session.get("cash"), dict) else {}
    fii = cash.get("fii") if isinstance(cash.get("fii"), dict) else {}
    dii = cash.get("dii") if isinstance(cash.get("dii"), dict) else {}
    row: dict[str, Any] = {
        "date": day,
        "source": "niftyinvest_api",
        "variant": "cash",
        "granularity": "daily",
    }
    mapping = (
        (fii.get("buy"), "fii_buy"),
        (fii.get("sell"), "fii_sell"),
        (fii.get("net"), "fii_net"),
        (dii.get("buy"), "dii_buy"),
        (dii.get("sell"), "dii_sell"),
        (dii.get("net"), "dii_net"),
    )
    for raw, dest in mapping:
        if raw is None:
            continue
        try:
            row[dest] = float(raw)
        except (TypeError, ValueError):
            continue
    if "fii_net" not in row and "dii_net" not in row:
        return None
    fut = session.get("future") if isinstance(session.get("future"), dict) else {}
    fii_fut = fut.get("fii") if isinstance(fut.get("fii"), dict) else {}
    if fii_fut.get("idxNetOi") is not None:
        try:
            row["fii_idx_fut_long"] = float(fii_fut["idxNetOi"])
        except (TypeError, ValueError):
            pass
    return row


def parse_niftyinvest_month_payload(payload: dict[str, Any]) -> pd.DataFrame:
    """Parse Nifty Invest /api/v1/month JSON into daily cash rows."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return pd.DataFrame()
    sessions = data.get("sessions")
    if not isinstance(sessions, list):
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for session in sessions:
        if not isinstance(session, dict):
            continue
        row = _parse_session_row(session)
        if row:
            rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")


def _fetch_niftyinvest_month(year_month: str, *, sleep_s: float = 0.25) -> pd.DataFrame:
    try:
        import requests

        response = requests.get(
            _NIFTYINVEST_MONTH_API,
            params={"yearMonth": year_month},
            timeout=30,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.debug("Nifty Invest month %s failed: %s", year_month, exc)
        return pd.DataFrame()
    finally:
        if sleep_s > 0:
            time.sleep(sleep_s)
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return pd.DataFrame()
    return parse_niftyinvest_month_payload(payload)


def list_niftyinvest_calendar_months() -> list[str]:
    """Discover available yearMonth keys from Nifty Invest API."""
    try:
        import requests

        response = requests.get(
            _NIFTYINVEST_MONTH_API,
            timeout=30,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.debug("Nifty Invest calendar fetch failed: %s", exc)
        return []
    data = payload.get("data") if isinstance(payload, dict) else {}
    calendar = data.get("calendar") if isinstance(data, dict) else {}
    months = calendar.get("yearMonths") if isinstance(calendar, dict) else []
    if isinstance(months, list):
        return [str(m) for m in months if m]
    err_months = data.get("calendarYearMonths") if isinstance(data, dict) else []
    if isinstance(err_months, list):
        return [str(m) for m in err_months if m]
    return []


def _months_for_range(
    months: list[str],
    *,
    start: str | None = None,
    end: str | None = None,
) -> list[str]:
    """Keep only yearMonth keys that can overlap ``[start, end]``."""
    if not months:
        return []
    if not start and not end:
        return months
    start_ym = (start or "1900-01")[:7]
    end_ym = (end or "2999-12")[:7]
    return [ym for ym in months if start_ym[:7] <= str(ym)[:7] <= end_ym[:7]]


def fetch_niftyinvest_flow_frame(
    *,
    months: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    sleep_s: float = 0.25,
    allow_live_fetch: bool = True,
) -> pd.DataFrame:
    """Fetch daily FII/DII cash (+ partial F&O) from Nifty Invest public API."""
    if not allow_live_fetch:
        return pd.DataFrame()
    available = months or list_niftyinvest_calendar_months()
    available = _months_for_range(available, start=start, end=end)
    if not available:
        return pd.DataFrame()
    frames: list[pd.DataFrame] = []
    for ym in available:
        frame = _fetch_niftyinvest_month(ym, sleep_s=sleep_s)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined["date"] = combined["date"].astype(str).str[:10]
    combined = combined.sort_values("date").drop_duplicates("date", keep="last")
    if start:
        combined = combined[combined["date"] >= start[:10]]
    if end:
        combined = combined[combined["date"] <= end[:10]]
    return combined.reset_index(drop=True)


def seed_niftyinvest_flow_to_repo(*, days: int = 365) -> dict[str, Any]:
    """Fetch recent Nifty Invest months and upsert into fii_dii repo."""
    from datetime import date, timedelta

    from trade_integrations.nse_browser.parsers.fii_dii import merge_fii_dii_variants
    from trade_integrations.nse_browser.repository import load_repo_dataset, save_raw_file, upsert_repo_parquet

    end = date.today().isoformat()
    start = (date.today() - timedelta(days=max(30, days))).isoformat()
    months = _months_for_range(list_niftyinvest_calendar_months(), start=start, end=end)
    frame = fetch_niftyinvest_flow_frame(months=months, start=start, end=end)
    if frame.empty:
        return {"status": "error", "error": "no_rows", "months": months, "start": start, "end": end}
    save_raw_file(
        frame.to_csv(index=False),
        dataset="web_flow",
        name=f"niftyinvest_api_{datetime.utcnow():%Y%m%d}.csv",
    )
    existing = load_repo_dataset("fii_dii")
    merged = merge_fii_dii_variants(existing, frame) if not existing.empty else frame
    upsert_repo_parquet(merged, dataset_id="fii_dii", source="niftyinvest_api")
    return {
        "status": "ok",
        "rows": len(frame),
        "months": len(months),
        "date_range": {"start": str(frame["date"].min()), "end": str(frame["date"].max())},
    }
