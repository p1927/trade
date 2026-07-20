"""Cold-tier datasets and factors required by NIFTY prediction — nothing unused."""

from __future__ import annotations

from typing import Any

import pandas as pd

from trade_integrations.dataflows.index_research.factor_matrix import MACRO_FACTOR_KEYS
from trade_integrations.dataflows.index_research.news_event_features import NEWS_EVENT_FACTOR_KEYS

# Only SP500 is used as a global index proxy (Fed / US risk channel). Nikkei, DAX, FTSE, etc. are not
# in MACRO_FACTOR_KEYS or any track.
GLOBAL_INDEX_FACTORS: tuple[str, ...] = ("sp500",)

REQUIRED_COLD_DATASETS: dict[str, dict[str, Any]] = {
    "nifty_ohlcv_daily": {
        "source": "yfinance ^NSEI",
        "factors": (
            "close (target)",
            "nifty_return_7d/14d",
            "nifty_rsi_14",
            "nifty_realized_vol_20d",
            "nifty_ma*",
            "nifty_macd_*",
            "nifty_bb_*",
            "nifty_stoch_*",
            "nifty_williams_r",
            "nifty_cci_20",
            "nifty_adx_14",
            "nifty_atr_pct",
            "nifty_golden_cross_signal",
        ),
        "tracks": ("quant_ridge", "macro_only", "naive_momentum", "walk_forward"),
    },
    "macro_daily": {
        "source": "yfinance + FRED (oil, FX, gold, sp500, us_10y)",
        "factors": GLOBAL_INDEX_FACTORS
        + ("oil_brent", "oil_wti", "usd_inr", "gold", "us_10y"),
        "tracks": ("quant_ridge", "macro_only", "event_overlay", "cause_layer"),
    },
    "flow_cash_daily": {
        "source": "NSE repo + NiftyInvest + MrChartist",
        "factors": ("fii_net_5d", "dii_net_5d", "institutional_net_5d", "dii_absorption_ratio"),
        "tracks": ("quant_ridge", "macro_only", "bottom_up"),
    },
    "flow_derivatives_daily": {
        "source": "NSE F&O archive + flow merge",
        "factors": ("nifty_pcr", "fii_fut_long_short_ratio"),
        "tracks": ("quant_ridge", "macro_only"),
    },
    "india_vix_daily": {
        "source": "nselib / yfinance ^INDIAVIX",
        "factors": ("india_vix", "india_vix_velocity_3d"),
        "tracks": ("quant_ridge", "event_overlay", "cause_stress_index"),
    },
    "news_events_daily": {
        "source": "major_events calendar + optional GDELT",
        "factors": NEWS_EVENT_FACTOR_KEYS,
        "tracks": ("event_overlay", "scenario_anchor", "quant_ridge when news ridge on"),
    },
    "india_cpi_monthly_yoy": {
        "source": "MoSPI CPI via historic_data repo",
        "factors": ("cpi_yoy_proxy",),
        "tracks": ("quant_ridge", "macro_only"),
    },
    "india_rbi_wss_weekly": {
        "source": "RBI WSS Table 5 FBIL G-Sec yields",
        "factors": ("india_10y", "india_91d_tbill", "india_term_spread"),
        "tracks": ("quant_ridge", "macro_only"),
    },
    "india_credit_spread_daily": {
        "source": "CRISIL / corporate BAA-AAA spread CSV (optional; proxy until present)",
        "factors": ("india_credit_spread",),
        "tracks": ("quant_ridge", "macro_only"),
    },
    "nifty50_valuation_daily": {
        "source": "Nifty50 PE/PB/DIV CSV + curated ingest",
        "factors": ("nifty_pe", "nifty_pb", "nifty_dividend_yield", "equity_risk_premium"),
        "tracks": ("quant_ridge", "macro_only"),
    },
    "india_news_sentiment_daily": {
        "source": "News sentiment CSV + indic_finance extension",
        "factors": ("index_sentiment",),
        "tracks": ("quant_ridge", "bottom_up", "event_overlay"),
    },
    "india_macro_annual": {
        "source": "historic_data/India_Stock_Market_Data.xlsx",
        "factors": ("gdp_growth_pct", "inflation_pct", "sensex_return_pct"),
        "tracks": ("macro_only", "cause_layer"),
    },
}

# Derived at panel materialize time (no separate cold dataset).
PANEL_DERIVED_FACTORS: tuple[str, ...] = (
    "repo_rate",
    "india_10y",
    "india_91d_tbill",
    "india_term_spread",
    "india_credit_spread",
    "nifty_pe",
    "nifty_earnings_yield",
    "equity_risk_premium",
    "usd_inr_momentum_5d",
    "us_10y_velocity_3d",
    "fii_net_5d_momentum",
    "index_sentiment",
    "cpi_yoy_proxy",
    "days_to_monthly_expiry",
    "is_budget_week",
    "is_results_season",
)

try:
    from trade_integrations.dataflows.index_research.ml_adapters.macro_lag_features import (
        MACRO_LAG_FACTOR_KEYS,
    )

    PANEL_DERIVED_FACTORS = PANEL_DERIVED_FACTORS + MACRO_LAG_FACTOR_KEYS
except ImportError:
    pass

# Explicitly out of scope for index prediction cold tier.
EXCLUDED_DATA: dict[str, str] = {
    "nikkei_dax_ftse_hsi": "Not in MACRO_FACTOR_KEYS; SP500 is the sole global index input",
    "sensex_banknifty_history": "Prediction target is NIFTY 50 only",
    "us_cpi_unemployment_fedfunds": "US macro beyond us_10y not in Ridge factor keys",
    "nse_equity_bhavcopy": "Per-stock EOD not used by index polynomial / macro tracks",
    "mcx_commodity_spot": "Oil already via Brent/WTI in macro_daily",
    "fred_us_macro_open_data_repo": "Redundant — FRED API wired for us_10y + oil",
    "indian_market_data_clone": "Use pip/API only if NSE PE archive needed later; PE proxy suffices today",
}

_PINNED_FOR_AUDIT: frozenset[str] = frozenset(
    {
        "oil_brent",
        "usd_inr",
        "sp500",
        "us_10y",
        "india_vix",
        "fii_net_5d",
        "dii_net_5d",
        "nifty_pcr",
        "repo_rate",
        "nifty_pe",
    }
)


def required_factor_keys() -> tuple[str, ...]:
    """Union of Ridge macro keys + news overlay keys used by prediction lab."""
    keys = list(MACRO_FACTOR_KEYS)
    for key in NEWS_EVENT_FACTOR_KEYS:
        if key not in keys:
            keys.append(key)
    return tuple(keys)


def load_panel_for_audit(panel_name: str = "NIFTY_2006_present") -> pd.DataFrame:
    from trade_integrations.dataflows.index_research.history_store import load_panel

    return load_panel(panel_name)


def audit_prediction_panel_coverage(frame: pd.DataFrame) -> dict[str, Any]:
    """Report non-null coverage for factors prediction tracks consume."""
    if frame.empty:
        return {"status": "empty", "factors": [], "pinned_missing": list(_PINNED_FOR_AUDIT)}

    rows = len(frame)
    factors: list[dict[str, Any]] = []
    pinned_missing: list[str] = []

    for key in required_factor_keys():
        if key not in frame.columns:
            factors.append({"factor": key, "coverage_pct": 0.0, "present": False, "pinned": key in _PINNED_FOR_AUDIT})
            if key in _PINNED_FOR_AUDIT:
                pinned_missing.append(key)
            continue
        non_null = int(pd.to_numeric(frame[key], errors="coerce").notna().sum())
        pct = round(100.0 * non_null / rows, 1) if rows else 0.0
        factors.append(
            {
                "factor": key,
                "coverage_pct": pct,
                "present": True,
                "non_null": non_null,
                "pinned": key in _PINNED_FOR_AUDIT,
            }
        )
        if key in _PINNED_FOR_AUDIT and pct < 45.0:
            pinned_missing.append(key)

    return {
        "status": "ok",
        "rows": rows,
        "start": str(frame["date"].iloc[0]) if "date" in frame.columns else None,
        "end": str(frame["date"].iloc[-1]) if "date" in frame.columns else None,
        "factors": factors,
        "pinned_missing_or_sparse": pinned_missing,
        "excluded_data": EXCLUDED_DATA,
    }
