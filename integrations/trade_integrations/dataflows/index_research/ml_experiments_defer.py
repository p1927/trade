"""Phase 4 ML experiments — only if Phase 3 OOS gate fails (+3pp direction).

Do NOT import QuantMuse, LSTM clones, or book RAG as primary knowledge.
"""

from __future__ import annotations

PHASE3_OOS_BASELINE_DIRECTION_PCT = 44.4
PHASE3_OOS_GATE_DELTA_PP = 3.0
PHASE3_OOS_GATE_DIRECTION_PCT = PHASE3_OOS_BASELINE_DIRECTION_PCT + PHASE3_OOS_GATE_DELTA_PP

DEFERRED_EXPERIMENTS: tuple[dict[str, str], ...] = (
    {
        "id": "lightgbm_ensemble",
        "when": "Ridge+TA close but direction OOS below gate",
        "skip_if": "Phase 3 ablation passes +3pp gate",
    },
    {
        "id": "lstm_auxiliary_2_3d",
        "when": "User wants tactical horizon and rule-based TA insufficient",
        "skip_if": "OOS rejects LSTM auxiliary on walk-forward",
    },
    {
        "id": "curated_rag_rbi_nse",
        "when": "Playbook + quant reviewer insufficient for open Q&A",
        "skip_if": "Phase 1 agent literacy covers user needs",
    },
    {
        "id": "quantmuse_import",
        "when": "never",
        "skip_if": "always — wrong market/product; OpenAlgo is execution authority",
    },
)


def phase3_gate_passed(direction_oos_pct: float) -> bool:
    """Return True when walk-forward direction hit rate meets +3pp gate."""
    return direction_oos_pct >= PHASE3_OOS_GATE_DIRECTION_PCT


def should_run_experiment(experiment_id: str, *, direction_oos_pct: float) -> bool:
    """Gate deferred ML experiments behind documented Phase 3 OOS failure."""
    if experiment_id == "quantmuse_import":
        return False
    return not phase3_gate_passed(direction_oos_pct)
