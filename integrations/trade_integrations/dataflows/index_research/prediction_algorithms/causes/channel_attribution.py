"""Channel attribution (H1) — Ridge coef grouping by channel."""

from __future__ import annotations

from typing import Any

_CHANNEL_MAP: dict[str, str] = {
    "nifty_earnings_yield": "valuation_pct",
    "nifty_dividend_yield": "valuation_pct",
    "nifty_pb_zscore_5y": "valuation_pct",
    "equity_risk_premium": "valuation_pct",
    "india_term_spread": "liquidity_spread_pct",
    "india_credit_spread": "liquidity_spread_pct",
    "india_10y": "liquidity_spread_pct",
    "oil_brent": "energy_pct",
    "oil_wti": "energy_pct",
    "usd_inr": "fx_rates_pct",
    "usd_inr_momentum_5d": "fx_rates_pct",
    "us_10y": "fx_rates_pct",
    "us_10y_velocity_3d": "fx_rates_pct",
    "repo_rate": "fx_rates_pct",
    "sp500": "global_risk_pct",
    "gold": "global_risk_pct",
    "india_vix": "vol_pct",
    "india_vix_velocity_3d": "vol_pct",
    "nifty_pcr": "vol_pct",
    "fii_net_5d": "flows_pct",
    "fii_net_5d_momentum": "flows_pct",
    "dii_net_5d": "flows_pct",
    "institutional_net_5d": "flows_pct",
    "nifty_return_7d": "technical_pct",
    "nifty_return_14d": "technical_pct",
    "index_sentiment": "sentiment_news_pct",
    "news_net_tone_7d": "sentiment_news_pct",
    "news_material_7d": "sentiment_news_pct",
}


def compute_channel_attribution(
    macro_factors: dict[str, Any],
    *,
    coefficients: dict[str, float] | None = None,
) -> dict[str, float]:
    """Approximate marginal channel contributions from factor values × coefs."""
    coefs = coefficients or {}
    buckets: dict[str, float] = {
        "valuation_pct": 0.0,
        "liquidity_spread_pct": 0.0,
        "energy_pct": 0.0,
        "fx_rates_pct": 0.0,
        "global_risk_pct": 0.0,
        "vol_pct": 0.0,
        "flows_pct": 0.0,
        "technical_pct": 0.0,
        "sentiment_news_pct": 0.0,
    }

    for factor, channel in _CHANNEL_MAP.items():
        raw_val = macro_factors.get(factor)
        if raw_val is None:
            continue
        try:
            val = float(raw_val)
        except (TypeError, ValueError):
            continue
        coef = float(coefs.get(factor, 0.0))
        if coef == 0.0 and val != 0.0:
            coef = 0.01
        buckets[channel] += val * coef * 0.01

    total = sum(abs(v) for v in buckets.values()) or 1.0
    explained = sum(buckets.values())
    buckets["unexplained_pct"] = round(-explained * 0.1, 4)
    for key in list(buckets.keys()):
        if key != "unexplained_pct":
            buckets[key] = round(buckets[key], 4)
    buckets["_coverage"] = round(min(1.0, sum(abs(v) for k, v in buckets.items() if k != "unexplained_pct") / total), 3)
    return buckets
