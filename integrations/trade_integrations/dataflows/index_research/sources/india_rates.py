"""India G-Sec / T-Bill yield helpers for term spread and ERP."""

from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Typical India 10Y–repo spread when no G-Sec series (documented proxy only).
_DEFAULT_10Y_REPO_SPREAD = 0.65

_RBI_COLUMNS = frozenset({"india_91d_tbill", "india_10y"})
_CREDIT_SPREAD_DATASET = "india_credit_spread_daily"


def _env_float(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def cold_tier_rbi_rate_series(trading_dates: list[str], column: str) -> pd.Series:
    """Merge-asof weekly RBI WSS rate column onto trading dates."""
    if column not in _RBI_COLUMNS:
        return pd.Series(dtype=float)
    from trade_integrations.dataflows.index_research.history_store import load_history_dataset

    frame = load_history_dataset("india_rbi_wss_weekly")
    if frame.empty or column not in frame.columns:
        return pd.Series(dtype=float)
    daily = frame[["date", column]].copy()
    daily["date"] = pd.to_datetime(daily["date"].astype(str).str[:10])
    daily[column] = pd.to_numeric(daily[column], errors="coerce")
    daily = daily.dropna(subset=[column]).sort_values("date").drop_duplicates("date", keep="last")
    if daily.empty:
        return pd.Series(dtype=float)
    trading = pd.DataFrame({"date": pd.to_datetime(trading_dates)})
    merged = pd.merge_asof(
        trading.sort_values("date"),
        daily.sort_values("date"),
        on="date",
        direction="backward",
    )
    return pd.Series(merged[column].values, index=trading_dates)


def cold_tier_rbi_latest(column: str) -> float | None:
    """Most recent non-null RBI WSS value for a rate column."""
    if column not in _RBI_COLUMNS:
        return None
    from trade_integrations.dataflows.index_research.history_store import load_history_dataset

    frame = load_history_dataset("india_rbi_wss_weekly")
    if frame.empty or column not in frame.columns:
        return None
    series = pd.to_numeric(frame[column], errors="coerce").dropna()
    if series.empty:
        return None
    return float(series.iloc[-1])


def _credit_spread_column(frame: pd.DataFrame) -> str | None:
    for col in ("india_credit_spread", "credit_spread", "baa_aaa_spread", "spread"):
        if col in frame.columns:
            return col
    return None


def cold_tier_credit_spread_series(trading_dates: list[str]) -> pd.Series:
    """Merge-asof CRISIL / corporate credit spread onto trading dates when cold tier exists."""
    from trade_integrations.dataflows.index_research.history_store import load_history_dataset

    frame = load_history_dataset(_CREDIT_SPREAD_DATASET)
    if frame.empty:
        return pd.Series(dtype=float)
    col = _credit_spread_column(frame)
    if col is None or "date" not in frame.columns:
        return pd.Series(dtype=float)
    daily = frame[["date", col]].copy()
    daily["date"] = pd.to_datetime(daily["date"].astype(str).str[:10])
    daily[col] = pd.to_numeric(daily[col], errors="coerce")
    daily = daily.dropna(subset=[col]).sort_values("date").drop_duplicates("date", keep="last")
    if daily.empty:
        return pd.Series(dtype=float)
    trading = pd.DataFrame({"date": pd.to_datetime(trading_dates)})
    merged = pd.merge_asof(
        trading.sort_values("date"),
        daily.sort_values("date"),
        on="date",
        direction="backward",
    )
    return pd.Series(merged[col].values, index=trading_dates)


def cold_tier_credit_spread_latest() -> float | None:
    """Most recent non-null credit spread from cold tier."""
    from trade_integrations.dataflows.index_research.history_store import load_history_dataset

    frame = load_history_dataset(_CREDIT_SPREAD_DATASET)
    if frame.empty:
        return None
    col = _credit_spread_column(frame)
    if col is None:
        return None
    series = pd.to_numeric(frame[col], errors="coerce").dropna()
    if series.empty:
        return None
    return float(series.iloc[-1])


def _fetch_fred_latest(series_id: str) -> float | None:
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from trade_integrations.http import get

        resp = get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 5,
            },
            timeout=15,
        )
        resp.raise_for_status()
        for obs in resp.json().get("observations") or []:
            val = obs.get("value")
            if val in (None, ".", ""):
                continue
            try:
                return float(val)
            except ValueError:
                continue
    except Exception as exc:
        logger.debug("FRED %s fetch failed: %s", series_id, exc)
    return None


def _resolve_india_91d_tbill_with_source(*, repo_rate: float | None = None) -> tuple[float | None, str]:
    override = _env_float("INDEX_INDIA_91D_TBILL")
    if override is not None:
        return override, "india_rates_env"
    cold = cold_tier_rbi_latest("india_91d_tbill")
    if cold is not None:
        return cold, "india_rbi_wss_cold_tier"
    if repo_rate is not None:
        return float(repo_rate), "india_rates_proxy"
    return None, "missing"


def _resolve_india_10y_with_source(*, repo_rate: float | None = None) -> tuple[float | None, str]:
    override = _env_float("INDEX_INDIA_10Y")
    if override is not None:
        return override, "india_rates_env"
    cold = cold_tier_rbi_latest("india_10y")
    if cold is not None:
        return cold, "india_rbi_wss_cold_tier"
    fred = _fetch_fred_latest("IRSTCI01INM156N")
    if fred is not None:
        return fred, "fred"
    if repo_rate is not None:
        return float(repo_rate) + _DEFAULT_10Y_REPO_SPREAD, "india_rates_proxy"
    return None, "missing"


def resolve_india_91d_tbill(*, repo_rate: float | None = None) -> float | None:
    """91-day T-Bill yield — env override, RBI cold tier, or repo proxy."""
    value, _source = _resolve_india_91d_tbill_with_source(repo_rate=repo_rate)
    return value


def resolve_india_10y(*, repo_rate: float | None = None) -> float | None:
    """India 10Y G-Sec yield — env, RBI cold tier, FRED, or repo+spread proxy."""
    value, _source = _resolve_india_10y_with_source(repo_rate=repo_rate)
    return value


def fetch_india_10y_fred_series(start: str, end: str) -> pd.Series:
    """Historical India 10Y from FRED IRSTCI01INM156N (monthly, merge_asof in panel)."""
    from trade_integrations.dataflows.index_research.sources.historical_macro import _fetch_fred_series

    return _fetch_fred_series("IRSTCI01INM156N", start, end)


def resolve_india_credit_spread(*, repo_rate: float | None = None) -> float | None:
    """Corporate credit spread — env, cold tier, or term-spread proxy (CRISIL CSV optional)."""
    override = _env_float("INDEX_INDIA_CREDIT_SPREAD")
    if override is not None:
        return override
    cold = cold_tier_credit_spread_latest()
    if cold is not None:
        return cold
    tbill = resolve_india_91d_tbill(repo_rate=repo_rate)
    ten_y = resolve_india_10y(repo_rate=repo_rate)
    if ten_y is not None and tbill is not None:
        from trade_integrations.dataflows.index_research.spread_features import compute_credit_spread_proxy

        proxy = compute_credit_spread_proxy(ten_y - tbill)
        return float(proxy) if proxy is not None and not (isinstance(proxy, float) and proxy != proxy) else None
    return None


def _data_quality_for_source(source: str) -> str:
    if source == "india_rbi_wss_cold_tier":
        return "cold_tier"
    if source in {"india_rates_env", "fred"}:
        return "observed"
    if source == "india_rates_derived":
        return "derived"
    return "proxy"


def india_rate_factor_rows(*, repo_rate: float | None = None) -> list[dict[str, Any]]:
    """Live snapshot rows for India rate inputs."""
    rows: list[dict[str, Any]] = []
    tbill, tbill_source = _resolve_india_91d_tbill_with_source(repo_rate=repo_rate)
    ten_y, ten_y_source = _resolve_india_10y_with_source(repo_rate=repo_rate)
    credit = resolve_india_credit_spread(repo_rate=repo_rate)
    credit_cold = cold_tier_credit_spread_latest()

    if tbill is not None:
        rows.append(
            {
                "factor": "india_91d_tbill",
                "value": tbill,
                "source": tbill_source,
                "data_quality": _data_quality_for_source(tbill_source),
            }
        )
    if ten_y is not None:
        rows.append(
            {
                "factor": "india_10y",
                "value": ten_y,
                "source": ten_y_source,
                "data_quality": _data_quality_for_source(ten_y_source),
            }
        )
    if credit is not None:
        if _env_float("INDEX_INDIA_CREDIT_SPREAD") is not None:
            credit_source = "india_rates_env"
            credit_quality = "observed"
        elif credit_cold is not None:
            credit_source = "india_credit_spread_cold_tier"
            credit_quality = "cold_tier"
        else:
            credit_source = "india_rates_proxy"
            credit_quality = "proxy"
        rows.append(
            {
                "factor": "india_credit_spread",
                "value": credit,
                "source": credit_source,
                "data_quality": credit_quality,
            }
        )
    if ten_y is not None and tbill is not None:
        rows.append(
            {
                "factor": "india_term_spread",
                "value": round(ten_y - tbill, 4),
                "source": "india_rates_derived",
                "data_quality": "derived",
            }
        )
    return rows
