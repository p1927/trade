"""Enrich existing factor snapshots with missing prediction inputs."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from trade_integrations.dataflows.index_research.constituents import load_nifty50_constituents
from trade_integrations.dataflows.index_research.factor_store import (
    get_factor_data_dir,
    upsert_daily_factors,
)
from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history
from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
    build_rolling_sum_series,
    fetch_mrchartist_flow_frame,
    merge_flow_derivatives_frame,
)
from trade_integrations.dataflows.index_research.sources.rbi_repo_schedule import repo_rate_on

logger = logging.getLogger(__name__)

_SENTIMENT_SCALE = 10.0


def fetch_fii_history_frame() -> pd.DataFrame:
    """Load daily FII/DII/PCR history (real rows only, no seeded continuity)."""
    return fetch_mrchartist_flow_frame(include_seeded=False)


def fetch_fii_daily_net_series(start: str, end: str) -> pd.Series:
    """Best-effort FII daily net (₹ crore) from merged public history."""
    frame = merge_flow_derivatives_frame(start, end)
    if frame.empty or "fii_net" not in frame.columns:
        return pd.Series(dtype=float)
    series = pd.Series(frame["fii_net"].astype(float).values, index=frame["date"].astype(str))
    return series.sort_index()


def fetch_dii_daily_net_series(start: str, end: str) -> pd.Series:
    """Best-effort DII daily net (₹ crore) from merged public history."""
    frame = merge_flow_derivatives_frame(start, end)
    if frame.empty or "dii_net" not in frame.columns:
        return pd.Series(dtype=float)
    series = pd.Series(frame["dii_net"].astype(float).values, index=frame["date"].astype(str))
    return series.sort_index()


def build_fii_net_5d_series(trading_dates: list[str], start: str, end: str) -> pd.Series:
    """Rolling 5-session FII net sum aligned to Nifty trading dates."""
    frame = merge_flow_derivatives_frame(start, end)
    return build_rolling_sum_series(frame, "fii_net", trading_dates, window=5)


def build_dii_net_5d_series(trading_dates: list[str], start: str, end: str) -> pd.Series:
    """Rolling 5-session DII net sum aligned to Nifty trading dates."""
    frame = merge_flow_derivatives_frame(start, end)
    return build_rolling_sum_series(frame, "dii_net", trading_dates, window=5)


def build_nifty_pe_proxy_series(nifty: pd.DataFrame) -> pd.Series:
    """Scale current trailing PE by historical Nifty close ratio."""
    import yfinance as yf

    if nifty.empty or "close" not in nifty.columns:
        return pd.Series(dtype=float)

    info = yf.Ticker("^NSEI").info or {}
    current_pe = info.get("trailingPE")
    if current_pe is None:
        env_raw = __import__("os").getenv("NIFTY_TRAILING_PE", "22.0").strip()
        try:
            current_pe = float(env_raw)
        except ValueError:
            current_pe = 22.0

    latest_close = float(nifty["close"].iloc[-1])
    if latest_close <= 0:
        return pd.Series(dtype=float)

    closes = nifty["close"].astype(float)
    pe_series = float(current_pe) * (closes / latest_close)
    return pd.Series(pe_series.values, index=nifty["date"].astype(str))


def _yfinance_symbol(symbol: str) -> str:
    sym = symbol.strip().upper()
    if sym.endswith(".NS") or sym.endswith(".BO"):
        return sym
    return f"{sym}.NS"


def build_constituent_momentum_series(
    trading_dates: list[str],
    *,
    start: str,
    end: str,
) -> pd.Series:
    """Weighted 7d Nifty 50 momentum (%) per trading day via yfinance."""
    import yfinance as yf

    constituents = load_nifty50_constituents()
    if not constituents:
        return pd.Series(dtype=float)

    start_d = date.fromisoformat(start[:10]) - timedelta(days=14)
    end_d = date.fromisoformat(end[:10]) + timedelta(days=2)
    date_index = pd.to_datetime(trading_dates)

    symbol_returns: dict[str, pd.Series] = {}
    for row in constituents:
        yf_sym = _yfinance_symbol(row.symbol)
        try:
            hist = yf.Ticker(yf_sym).history(
                start=start_d.isoformat(),
                end=end_d.isoformat(),
                auto_adjust=True,
            )
        except Exception as exc:
            logger.debug("momentum history failed for %s: %s", row.symbol, exc)
            continue
        if hist is None or hist.empty or len(hist) < 8:
            continue
        close_col = "Close" if "Close" in hist.columns else "close"
        closes = hist[close_col].astype(float)
        closes.index = pd.to_datetime(closes.index).tz_localize(None)
        ret_7d = (closes / closes.shift(7) - 1.0) * 100.0
        symbol_returns[row.symbol.upper()] = ret_7d

    if not symbol_returns:
        return pd.Series(dtype=float)

    out: dict[str, float] = {}
    for day in trading_dates:
        ts = pd.Timestamp(day)
        weighted = 0.0
        total_weight = 0.0
        for constituent in constituents:
            series = symbol_returns.get(constituent.symbol.upper())
            if series is None:
                continue
            eligible = series.index[series.index <= ts]
            if len(eligible) == 0:
                continue
            value = series.loc[eligible[-1]]
            if value is None or (isinstance(value, float) and np.isnan(value)):
                continue
            weighted += constituent.weight * float(value)
            total_weight += constituent.weight
        if total_weight > 0:
            out[day] = weighted / total_weight
    return pd.Series(out)


def _sentiment_proxy_from_momentum(momentum_pct: float) -> float:
    return float(np.clip(momentum_pct / _SENTIMENT_SCALE, -1.0, 1.0))


def enrich_factor_history(*, days: int = 365) -> dict[str, int | str]:
    """Merge missing factors (repo_rate, FII, PE, momentum, sentiment proxies) into daily store."""
    nifty = load_nifty_history(days=days)
    if nifty.empty:
        return {"days_enriched": 0, "reason": "no_nifty_history"}

    trading_dates = nifty["date"].astype(str).tolist()
    start = trading_dates[0]
    end = trading_dates[-1]

    fii_5d = build_fii_net_5d_series(trading_dates, start, end)
    dii_5d = build_dii_net_5d_series(trading_dates, start, end)
    pe_series = build_nifty_pe_proxy_series(nifty)
    momentum = build_constituent_momentum_series(trading_dates, start=start, end=end)
    flow_frame = merge_flow_derivatives_frame(start, end)

    days_enriched = 0
    for _, row in nifty.iterrows():
        day = str(row["date"])
        rows: list[dict] = [
            {
                "factor": "repo_rate",
                "value": repo_rate_on(day),
                "source": "backfill_rbi_schedule",
            }
        ]

        if day in fii_5d and not pd.isna(fii_5d[day]):
            rows.append(
                {
                    "factor": "fii_net_5d",
                    "value": float(fii_5d[day]),
                    "source": "backfill_fii_history",
                }
            )

        if day in dii_5d and not pd.isna(dii_5d[day]):
            rows.append(
                {
                    "factor": "dii_net_5d",
                    "value": float(dii_5d[day]),
                    "source": "backfill_dii_history",
                }
            )

        if not flow_frame.empty:
            flow_day = flow_frame[flow_frame["date"] == day]
            if not flow_day.empty:
                flow_row = flow_day.iloc[0]
                pcr = flow_row.get("nifty_pcr")
                if pcr is not None and not pd.isna(pcr) and float(pcr) > 0:
                    rows.append(
                        {
                            "factor": "nifty_pcr",
                            "value": float(pcr),
                            "source": "backfill_fii_history",
                        }
                    )
                fut_ratio = flow_row.get("fii_fut_long_short_ratio")
                if fut_ratio is not None and not pd.isna(fut_ratio) and float(fut_ratio) > 0:
                    rows.append(
                        {
                            "factor": "fii_fut_long_short_ratio",
                            "value": float(fut_ratio),
                            "source": "backfill_fii_derivatives",
                        }
                    )
                sentiment = flow_row.get("fii_sentiment_score")
                if sentiment is not None and not pd.isna(sentiment):
                    normalized = float(np.clip((float(sentiment) - 50.0) / 50.0, -1.0, 1.0))
                    rows.extend(
                        [
                            {
                                "factor": "index_sentiment",
                                "value": normalized,
                                "source": "backfill_fii_sentiment",
                            },
                            {
                                "factor": "sector_breadth_mean_sentiment",
                                "value": normalized,
                                "source": "backfill_fii_sentiment",
                            },
                        ]
                    )

        if day in pe_series.index and not pd.isna(pe_series[day]):
            rows.append(
                {
                    "factor": "nifty_pe",
                    "value": float(pe_series[day]),
                    "source": "backfill_pe_proxy",
                }
            )

        if day in momentum.index and not pd.isna(momentum[day]):
            mom = float(momentum[day])
            rows.append(
                {
                    "factor": "constituent_momentum_7d",
                    "value": mom,
                    "source": "backfill_constituent_momentum",
                }
            )
            if not any(r["factor"] == "index_sentiment" for r in rows):
                sentiment = _sentiment_proxy_from_momentum(mom)
                rows.extend(
                    [
                        {
                            "factor": "index_sentiment",
                            "value": sentiment,
                            "source": "backfill_momentum_proxy",
                        },
                        {
                            "factor": "sector_breadth_mean_sentiment",
                            "value": sentiment,
                            "source": "backfill_momentum_proxy",
                        },
                    ]
                )

        upsert_daily_factors(day, rows)
        days_enriched += 1

    return {
        "days_enriched": days_enriched,
        "start": start,
        "end": end,
        "fii_days": int(fii_5d.notna().sum()) if not fii_5d.empty else 0,
        "dii_days": int(dii_5d.notna().sum()) if not dii_5d.empty else 0,
        "pcr_days": int(flow_frame["nifty_pcr"].notna().sum()) if "nifty_pcr" in flow_frame.columns else 0,
        "momentum_days": int(momentum.notna().sum()) if not momentum.empty else 0,
    }


def list_factor_snapshot_dates() -> list[str]:
    """Return sorted YYYY-MM-DD stems for existing daily factor files."""
    out_dir = get_factor_data_dir()
    if not out_dir.is_dir():
        return []
    dates: list[str] = []
    for path in out_dir.iterdir():
        if path.suffix in {".parquet", ".csv"} and len(path.stem) == 10 and path.stem[4] == "-":
            dates.append(path.stem)
    return sorted(dates)
