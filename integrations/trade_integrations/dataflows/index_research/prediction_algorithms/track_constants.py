"""Track IDs and backtest eligibility (shared — avoids registry ↔ _helpers cycle)."""

from __future__ import annotations

# All live forecast tracks in the lab registry.
CANONICAL_TRACK_IDS: tuple[str, ...] = (
    "quant_ridge",
    "quant_ridge_no_overlay",
    "macro_only",
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

BACKTEST_COMBINER_IDS: tuple[str, ...] = (
    "quant_only",
    "equal_weight_2",
    "equal_weight_3",
    "inverse_mae_w6",
    "shrinkage_50",
    "alignment_grid",
    "stress_conditional",
    "fixed_legacy",
)

TRACK_BACKTEST_ELIGIBLE: dict[str, bool] = {
    "quant_ridge": True,
    "quant_ridge_no_overlay": True,
    "macro_only": True,
    "bottom_up": True,
    "scenario_anchor": True,
    "event_overlay": True,
    "naive_zero": True,
    "naive_momentum": True,
    "debate_numeric": False,
    "headline_legacy": True,
}

TRACK_IMPLEMENTATION_NOTES: dict[str, str] = {
    "quant_ridge": "predict_nifty() — bottom-up + macro Ridge + overlay shrink",
    "quant_ridge_no_overlay": "predict_nifty(apply_event_overlay=False) — hybrid without news shock in macro",
    "macro_only": "Ridge macro delta + event overlay + scenario shrink (backtest parity path)",
    "bottom_up": "Constituent sentiment/momentum rollup; hybrid when company_research/history exists",
    "scenario_anchor": "Probability-weighted scenario table (build_index_scenarios)",
    "event_overlay": "Calibrated news_shock_calibration × topic intensity",
    "naive_zero": "Constant 0% baseline",
    "naive_momentum": "nifty_return_7d or 14d from factor snapshot",
    "debate_numeric": "Live only — agent_debate/latest.json (no historical archive)",
    "headline_legacy": "predict → scenario reconcile → finalize (no debate in backtest)",
}

SCOREBOARD_SCHEMA_VERSION = 3
