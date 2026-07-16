"""Historical FII/DII cash flows and derivatives positioning for factor backfill."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

_FII_LIVE_URL = "https://fii-diidata.mrchartist.com/api/data"
_FII_HISTORY_URL = "https://fii-diidata.mrchartist.com/api/history-full"
_SEEDED_SOURCES = frozenset({"seeded", "estimate", "estimated", "synthetic"})

_FLOW_COLS = ("fii_net", "dii_net")
_DERIV_COLS = (
    "nifty_pcr",
    "fii_sentiment_score",
    "fii_idx_fut_long",
    "fii_idx_fut_short",
    "fii_idx_put_oi",
    "fii_idx_call_oi",
    "fii_fut_long_short_ratio",
)


def _parse_api_date(raw: str) -> str | None:
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(raw).strip()[:11], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _float_or_none(raw) -> float | None:
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if pd.isna(val):
        return None
    return val


def _is_seeded_row(item: dict) -> bool:
    source = str(item.get("_source") or item.get("source") or "").strip().lower()
    if source in _SEEDED_SOURCES:
        return True
    if source and source != "fetch-pipeline":
        return "seed" in source or "estimat" in source
    return False


def fetch_mrchartist_flow_frame(*, include_seeded: bool = False) -> pd.DataFrame:
    """Load FII/DII/PCR/F&O OI from Mr. Chartist history-full JSON."""
    try:
        import requests

        response = requests.get(_FII_HISTORY_URL, timeout=45)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.debug("Mr. Chartist history-full unavailable: %s", exc)
        return pd.DataFrame()

    if not isinstance(payload, list) or not payload:
        return pd.DataFrame()

    rows: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if not include_seeded and _is_seeded_row(item):
            continue
        day = _parse_api_date(str(item.get("d") or item.get("date") or ""))
        if not day:
            continue
        row: dict = {"date": day, "source": str(item.get("_source") or "mrchartist")}
        for src, dest in (
            ("fn", "fii_net"),
            ("fii_net", "fii_net"),
            ("dn", "dii_net"),
            ("dii_net", "dii_net"),
            ("pcr", "nifty_pcr"),
            ("sentiment_score", "fii_sentiment_score"),
            ("fii_idx_fut_long", "fii_idx_fut_long"),
            ("fii_idx_fut_short", "fii_idx_fut_short"),
            ("fii_idx_opt_put_short", "fii_idx_put_oi"),
            ("fii_idx_opt_call_short", "fii_idx_call_oi"),
        ):
            val = _float_or_none(item.get(src))
            if val is not None:
                row[dest] = val
        rows.append(row)

    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")
    long_oi = frame.get("fii_idx_fut_long")
    short_oi = frame.get("fii_idx_fut_short")
    if long_oi is not None and short_oi is not None:
        frame["fii_fut_long_short_ratio"] = long_oi / short_oi.replace(0, pd.NA)
    if "fii_idx_put_oi" in frame.columns and "fii_idx_call_oi" in frame.columns:
        frame["nifty_pcr"] = frame["nifty_pcr"].combine_first(
            frame["fii_idx_put_oi"] / frame["fii_idx_call_oi"].replace(0, pd.NA)
        )
    return frame


def fetch_mrchartist_latest_session() -> pd.DataFrame:
    """Latest FII/DII + derivatives from Mr. Chartist (NSE-sourced, post-close)."""
    try:
        import requests

        response = requests.get(_FII_LIVE_URL, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.debug("Mr. Chartist /api/data unavailable: %s", exc)
        return pd.DataFrame()

    if not isinstance(payload, dict):
        return pd.DataFrame()

    day = _parse_api_date(str(payload.get("d") or payload.get("date") or ""))
    if not day:
        day = datetime.now().date().isoformat()

    row: dict = {"date": day, "source": str(payload.get("_source") or "mrchartist_live")}
    for src, dest in (
        ("fn", "fii_net"),
        ("fii_net", "fii_net"),
        ("dn", "dii_net"),
        ("dii_net", "dii_net"),
        ("pcr", "nifty_pcr"),
        ("sentiment_score", "fii_sentiment_score"),
        ("fii_idx_fut_long", "fii_idx_fut_long"),
        ("fii_idx_fut_short", "fii_idx_fut_short"),
        ("fii_idx_opt_put_short", "fii_idx_put_oi"),
        ("fii_idx_opt_call_short", "fii_idx_call_oi"),
    ):
        val = _float_or_none(payload.get(src))
        if val is not None:
            row[dest] = val
    if row.get("fii_idx_fut_long") is not None and row.get("fii_idx_fut_short"):
        row["fii_fut_long_short_ratio"] = row["fii_idx_fut_long"] / max(row["fii_idx_fut_short"], 1e-9)
    return pd.DataFrame([row])


def _fii_net_column(frame: pd.DataFrame) -> str | None:
    for column in frame.columns:
        label = str(column).lower()
        if "fii" in label and "net" in label:
            return column
    return None


def _dii_net_column(frame: pd.DataFrame) -> str | None:
    for column in frame.columns:
        label = str(column).lower()
        if "dii" in label and "net" in label:
            return column
    return None


def _date_column(frame: pd.DataFrame) -> str | None:
    for column in frame.columns:
        label = str(column).lower()
        if "date" in label or label in {"tradedate", "trade date"}:
            return column
    return None


def fetch_nselib_fii_dii_frame(start: str, end: str) -> pd.DataFrame:
    """Fetch latest FII/DII cash from NSE public API (same-day; historical via Mr. Chartist)."""
    try:
        import requests
    except ImportError:
        return pd.DataFrame()

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com/reports/fii-dii",
        }
    )
    try:
        session.get("https://www.nseindia.com", timeout=15)
        response = session.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.debug("NSE fiidiiTradeReact unavailable: %s", exc)
        return pd.DataFrame()

    if not isinstance(payload, list):
        return pd.DataFrame()

    rows: list[dict] = []
    day = end[:10]
    fii_net = None
    dii_net = None
    for item in payload:
        if not isinstance(item, dict):
            continue
        cat = str(item.get("category") or "").upper()
        net = _float_or_none(item.get("netValue"))
        raw_date = str(item.get("date") or "")
        parsed = _parse_api_date(raw_date)
        if parsed:
            day = parsed
        if cat == "FII" and net is not None:
            fii_net = net
        if cat == "DII" and net is not None:
            dii_net = net
    if fii_net is None and dii_net is None:
        return pd.DataFrame()
    row: dict = {"date": day, "source": "nse_fiidii_react"}
    if fii_net is not None:
        row["fii_net"] = fii_net
    if dii_net is not None:
        row["dii_net"] = dii_net
    rows.append(row)
    frame = pd.DataFrame(rows)
    return frame[(frame["date"] >= start[:10]) & (frame["date"] <= end[:10])]


def _row_dict(frame: pd.DataFrame, day: str) -> dict:
    hits = frame[frame["date"].astype(str) == day[:10]]
    if hits.empty:
        return {}
    return hits.iloc[-1].to_dict()


def merge_flow_derivatives_frame(start: str, end: str) -> pd.DataFrame:
    """Merge Mr. Chartist history (full range) with NSE today + latest session."""
    mr = fetch_mrchartist_flow_frame(include_seeded=False)
    latest = fetch_mrchartist_latest_session()
    nse = fetch_nselib_fii_dii_frame(start, end)

    frames = [f for f in (mr, latest, nse) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values("date").drop_duplicates("date", keep="last")

    dates = set(combined["date"].astype(str).tolist())
    rows: list[dict] = []
    for day in sorted(dates):
        if day < start[:10] or day > end[:10]:
            continue
        day_rows = combined[combined["date"].astype(str) == day[:10]]
        if day_rows.empty:
            continue
        merged = day_rows.iloc[-1].to_dict()
        merged["date"] = day[:10]
        rows.append(merged)

    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")
    if "fii_idx_fut_long" in frame.columns and "fii_idx_fut_short" in frame.columns:
        frame["fii_fut_long_short_ratio"] = frame["fii_fut_long_short_ratio"].combine_first(
            frame["fii_idx_fut_long"] / frame["fii_idx_fut_short"].replace(0, pd.NA)
        )
    return frame.reset_index(drop=True)


def build_rolling_sum_series(
    frame: pd.DataFrame,
    column: str,
    trading_dates: list[str],
    *,
    window: int = 5,
) -> pd.Series:
    """Align a daily column to Nifty trading dates with rolling sum."""
    if frame.empty or column not in frame.columns:
        return pd.Series(dtype=float)

    daily = pd.Series(frame[column].astype(float).values, index=frame["date"].astype(str))
    daily.index = pd.to_datetime(daily.index)
    rolling = daily.sort_index().rolling(window, min_periods=1).sum()
    out: dict[str, float] = {}
    for day in trading_dates:
        ts = pd.Timestamp(day)
        eligible = rolling.index[rolling.index <= ts]
        if len(eligible) == 0:
            continue
        val = rolling.loc[eligible[-1]]
        if not pd.isna(val):
            out[day] = float(val)
    return pd.Series(out)


def flow_backfill_summary(*, days: int = 365) -> dict[str, int | str]:
    """Dry-run summary of merged flow coverage."""
    from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history

    nifty = load_nifty_history(days=days)
    if nifty.empty:
        return {"status": "error", "reason": "no_nifty_history"}
    start = str(nifty["date"].iloc[0])[:10]
    end = str(nifty["date"].iloc[-1])[:10]
    frame = merge_flow_derivatives_frame(start, end)
    return {
        "status": "ok",
        "start": start,
        "end": end,
        "rows": len(frame),
        "fii_net_days": int(frame["fii_net"].notna().sum()) if "fii_net" in frame.columns else 0,
        "dii_net_days": int(frame["dii_net"].notna().sum()) if "dii_net" in frame.columns else 0,
        "pcr_days": int(frame["nifty_pcr"].notna().sum()) if "nifty_pcr" in frame.columns else 0,
        "fut_ratio_days": int(frame["fii_fut_long_short_ratio"].notna().sum())
        if "fii_fut_long_short_ratio" in frame.columns
        else 0,
        "primary_source": "mrchartist_history_full",
        "fii_cash_limit_note": "FII/DII cash capped at ~111 sessions (Mr. Chartist); NSE API is today-only.",
    }
