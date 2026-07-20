"""India G-Sec / T-Bill yield helpers for term spread and ERP."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Typical India 10Y–repo spread when no G-Sec series (documented proxy only).
_DEFAULT_10Y_REPO_SPREAD = 0.65


def _env_float(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _fetch_fred_latest(series_id: str) -> float | None:
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import requests

        resp = requests.get(
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


def resolve_india_91d_tbill(*, repo_rate: float | None = None) -> float | None:
    """91-day T-Bill yield — env override or repo proxy until RBI series wired."""
    override = _env_float("INDEX_INDIA_91D_TBILL")
    if override is not None:
        return override
    if repo_rate is not None:
        return float(repo_rate)
    return None


def resolve_india_10y(*, repo_rate: float | None = None) -> float | None:
    """India 10Y G-Sec yield — env, FRED, or repo+spread proxy."""
    override = _env_float("INDEX_INDIA_10Y")
    if override is not None:
        return override
    # OECD / FRED long-term government bond yield for India (monthly; best-effort).
    fred = _fetch_fred_latest("IRSTCI01INM156N")
    if fred is not None:
        return fred
    if repo_rate is not None:
        return float(repo_rate) + _DEFAULT_10Y_REPO_SPREAD
    return None


def resolve_india_credit_spread(*, repo_rate: float | None = None) -> float | None:
    """Corporate credit spread — env override or term-spread proxy (no CRISIL CSV yet)."""
    override = _env_float("INDEX_INDIA_CREDIT_SPREAD")
    if override is not None:
        return override
    tbill = resolve_india_91d_tbill(repo_rate=repo_rate)
    ten_y = resolve_india_10y(repo_rate=repo_rate)
    if ten_y is not None and tbill is not None:
        from trade_integrations.dataflows.index_research.spread_features import compute_credit_spread_proxy

        proxy = compute_credit_spread_proxy(ten_y - tbill)
        return float(proxy) if proxy is not None and not (isinstance(proxy, float) and proxy != proxy) else None
    return None


def india_rate_factor_rows(*, repo_rate: float | None = None) -> list[dict[str, Any]]:
    """Live snapshot rows for India rate inputs."""
    rows: list[dict[str, Any]] = []
    tbill = resolve_india_91d_tbill(repo_rate=repo_rate)
    ten_y = resolve_india_10y(repo_rate=repo_rate)
    credit = resolve_india_credit_spread(repo_rate=repo_rate)

    if tbill is not None:
        rows.append({"factor": "india_91d_tbill", "value": tbill, "source": "india_rates_proxy"})
    if ten_y is not None:
        rows.append({"factor": "india_10y", "value": ten_y, "source": "india_rates_proxy"})
    if credit is not None:
        rows.append(
            {
                "factor": "india_credit_spread",
                "value": credit,
                "source": "india_rates_env"
                if _env_float("INDEX_INDIA_CREDIT_SPREAD") is not None
                else "india_rates_proxy",
            }
        )
    if ten_y is not None and tbill is not None:
        rows.append(
            {
                "factor": "india_term_spread",
                "value": round(ten_y - tbill, 4),
                "source": "india_rates_derived",
            }
        )
    return rows
