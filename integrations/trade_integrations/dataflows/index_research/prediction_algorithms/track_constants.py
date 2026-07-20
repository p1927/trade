"""Track IDs and backtest eligibility (shared — avoids registry ↔ _helpers cycle)."""

from __future__ import annotations

# All live forecast tracks in the lab registry.
CANONICAL_TRACK_IDS: tuple[str, ...] = (
    "quant_ridge",
    "quant_ridge_no_overlay",
    "macro_only",
    "macro_only_no_overlay",
    "bottom_up",
    "scenario_anchor",
    "event_overlay",
    "naive_zero",
    "naive_momentum",
    "debate_numeric",
    "headline_legacy",
)

# Walk-forward backtest runs these (debate skipped — no historical debate JSON).
BACKTEST_TRACK_IDS: tuple[str, ...] = tuple(
    tid for tid in CANONICAL_TRACK_IDS if tid != "debate_numeric"
)

# Tracks used by 3-way combiners (macro without overlay + scenario + standalone overlay).
COMBINER_THREE_TRACK_IDS: tuple[str, ...] = (
    "macro_only_no_overlay",
    "scenario_anchor",
    "event_overlay",
)

# Tracks used by 2-way combiners (macro with overlay baked in + scenario).
COMBINER_TWO_TRACK_IDS: tuple[str, ...] = (
    "macro_only",
    "scenario_anchor",
)

BACKTEST_COMBINER_IDS: tuple[str, ...] = (
    "quant_only",
    "equal_weight_2",
    "equal_weight_3",
    "inverse_mae_w6",
    "inverse_mae_w12",
    "shrinkage_50",
    "alignment_grid",
    "stress_conditional",
    "fixed_legacy",
)

TRACK_BACKTEST_ELIGIBLE: dict[str, bool] = {
    "quant_ridge": True,
    "quant_ridge_no_overlay": True,
    "macro_only": True,
    "macro_only_no_overlay": True,
    "bottom_up": False,
    "scenario_anchor": True,
    "event_overlay": True,
    "naive_zero": True,
    "naive_momentum": True,
    "debate_numeric": False,
    "headline_legacy": True,
}

_DEBATE_ARCHIVE_MIN_DATES = 60


def debate_backtest_eligible(ticker: str = "NIFTY") -> bool:
    """True when dated debate history has enough coverage for walk-forward."""
    try:
        from trade_integrations.context.hub import count_agent_debate_history

        return count_agent_debate_history(ticker.strip().upper()) >= _DEBATE_ARCHIVE_MIN_DATES
    except Exception:
        return False


def walk_forward_track_ids(*, ticker: str = "NIFTY") -> tuple[str, ...]:
    """Backtest track list; includes debate_numeric when archive threshold met."""
    if debate_backtest_eligible(ticker):
        return CANONICAL_TRACK_IDS
    return BACKTEST_TRACK_IDS

TRACK_IMPLEMENTATION_NOTES: dict[str, str] = {
    "quant_ridge": "predict_nifty() — bottom-up + macro Ridge + overlay shrink",
    "quant_ridge_no_overlay": "predict_nifty(apply_event_overlay=False) — hybrid without news shock in macro",
    "macro_only": "Ridge macro delta + event overlay + scenario shrink (backtest parity path)",
    "macro_only_no_overlay": "Ridge macro delta without overlay — pairs with event_overlay in combiners",
    "bottom_up": "Constituent sentiment/momentum rollup; hybrid when company_research/history exists",
    "scenario_anchor": "Probability-weighted scenario table (build_index_scenarios)",
    "event_overlay": "Calibrated news_shock_calibration × topic intensity (needs shock calibration topics)",
    "naive_zero": "Intentional 0% return baseline — flat horizon forecast from anchor spot",
    "naive_momentum": "nifty_return_7d or 14d from factor snapshot",
    "debate_numeric": "Live only — agent_debate/latest.json (no historical archive)",
    "headline_legacy": "predict → scenario reconcile → finalize → optional debate merge",
}

INVERSE_MAE_WINDOWS: dict[str, int] = {
    "inverse_mae_w6": 6,
    "inverse_mae_w12": 12,
}

SCOREBOARD_SCHEMA_VERSION = 4
