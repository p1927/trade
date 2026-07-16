"""Horizon-aware technical feature selection for index Ridge."""

from __future__ import annotations

from trade_integrations.dataflows.index_research.horizon import HorizonProfile

_BASE_TECH_KEYS: tuple[str, ...] = (
    "nifty_return_7d",
    "nifty_return_14d",
    "nifty_rsi_14",
    "nifty_realized_vol_20d",
    "nifty_ma20_distance_pct",
)

_HORIZON_EXTRA: dict[str, tuple[str, ...]] = {
    "A": (
        "nifty_macd_line",
        "nifty_macd_signal",
        "nifty_macd_histogram",
        "nifty_bb_percent_b",
        "nifty_bb_width_pct",
        "nifty_stoch_k",
        "nifty_williams_r",
        "nifty_atr_pct",
    ),
    "B": (
        "nifty_macd_histogram",
        "nifty_bb_percent_b",
        "nifty_bb_width_pct",
        "nifty_stoch_k",
        "nifty_stoch_d",
        "nifty_cci_20",
        "nifty_ma50_distance_pct",
        "nifty_adx_14",
    ),
    "C": (
        "nifty_ma50_distance_pct",
        "nifty_ma200_distance_pct",
        "nifty_golden_cross_signal",
        "nifty_adx_14",
    ),
}

_DERIVATIVES_KEYS: tuple[str, ...] = (
    "qfinindia_skew",
    "qfinindia_expected_move",
    "qfinindia_tail_risk",
)


def technical_keys_for_horizon(horizon: HorizonProfile) -> tuple[str, ...]:
    """Return ordered technical factor keys active for this horizon profile."""
    extras = _HORIZON_EXTRA.get(horizon.name, _HORIZON_EXTRA["B"])
    keys: list[str] = list(_BASE_TECH_KEYS)
    for key in extras:
        if key not in keys:
            keys.append(key)
    return tuple(keys)


def extended_macro_keys_for_horizon(horizon: HorizonProfile) -> tuple[str, ...]:
    """Technical + derivatives keys to prefer when building feature matrix."""
    tech = technical_keys_for_horizon(horizon)
    out: list[str] = list(tech)
    for key in _DERIVATIVES_KEYS:
        if key not in out:
            out.append(key)
    return tuple(out)
