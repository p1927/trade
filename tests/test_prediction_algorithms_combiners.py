"""Unit tests for forecast lab combiners."""

from __future__ import annotations

import pytest

from trade_integrations.dataflows.index_research.prediction_algorithms.combiners import run_combiner
from trade_integrations.dataflows.index_research.prediction_algorithms.combiners._math import (
    equal_weight_combine,
    inverse_mae_combine,
)
from trade_integrations.dataflows.index_research.prediction_algorithms.promotion import evaluate_promotion
from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack


def _track(track_id: str, value: float, available: bool = True) -> ForecastTrack:
    view = "bullish" if value > 0.3 else "bearish" if value < -0.3 else "neutral"
    return ForecastTrack(track_id=track_id, expected_return_pct=value, view=view, available=available)


@pytest.mark.unit
def test_equal_weight_combine_averages():
    tracks = [_track("a", 1.0), _track("b", -1.0)]
    value, weights = equal_weight_combine(tracks)
    assert value == pytest.approx(0.0)
    assert weights == {"a": 0.5, "b": 0.5}


@pytest.mark.unit
def test_inverse_mae_prefers_lower_mae():
    tracks = [_track("a", 2.0), _track("b", 0.0)]
    value, weights = inverse_mae_combine(tracks, {"a": 4.0, "b": 1.0})
    assert weights["b"] > weights["a"]
    assert value == pytest.approx(2.0 * weights["a"] + 0.0 * weights["b"])


@pytest.mark.unit
def test_quant_only_combiner():
    tracks = {
        "quant_ridge": _track("quant_ridge", 0.8),
        "macro_only": _track("macro_only", 0.2),
    }
    result = run_combiner("quant_only", tracks)
    assert result.expected_return_pct == pytest.approx(0.8)
    assert result.tracks_used == ["quant_ridge"]


@pytest.mark.unit
def test_equal_weight_2_combiner():
    tracks = {
        "macro_only": _track("macro_only", 1.0),
        "scenario_anchor": _track("scenario_anchor", -1.0),
    }
    result = run_combiner("equal_weight_2", tracks)
    assert result.expected_return_pct == pytest.approx(0.0)


@pytest.mark.unit
def test_stress_conditional_high_stress():
    tracks = {
        "quant_ridge": _track("quant_ridge", 0.5),
        "macro_only": _track("macro_only", 1.0),
        "scenario_anchor": _track("scenario_anchor", -0.5),
        "event_overlay": _track("event_overlay", 0.25),
    }
    calm = run_combiner("stress_conditional", tracks, cause_stress_index=20.0)
    stressed = run_combiner("stress_conditional", tracks, cause_stress_index=70.0)
    assert calm.combiner_id == "quant_only" or calm.tracks_used == ["quant_ridge"]
    assert stressed.combiner_id == "stress_conditional"
    assert len(stressed.tracks_used) >= 2


@pytest.mark.unit
def test_promotion_requires_three_pp_margin():
    board = {
        "eval_count": 65,
        "tracks": {"quant_ridge": {"direction_hit_rate": 0.50, "view_hit_rate": 0.50}},
        "combiners": {
            "equal_weight_2": {"direction_hit_rate": 0.54, "view_hit_rate": 0.54},
            "shrinkage_50": {"direction_hit_rate": 0.56, "view_hit_rate": 0.56},
        },
        "promotion_run_history": [
            {"promoted": ["equal_weight_2", "shrinkage_50"]},
            {"promoted": ["equal_weight_2", "shrinkage_50"]},
        ],
        "combiner_weight_history": [
            {"combiner_id": "shrinkage_50", "weights": {"macro_only_no_overlay": 0.33, "scenario_anchor": 0.33, "event_overlay": 0.34}},
            {"combiner_id": "shrinkage_50", "weights": {"macro_only_no_overlay": 0.34, "scenario_anchor": 0.33, "event_overlay": 0.33}},
        ],
    }
    promo = evaluate_promotion(board)
    assert promo["verdicts"]["equal_weight_2"]["promoted"] is True
    assert promo["verdicts"]["shrinkage_50"]["promoted"] is True
    assert "shrinkage_50" in promo["promoted_combiners"]


@pytest.mark.unit
def test_promotion_requires_two_distinct_runs():
    board_one_run = {
        "eval_count": 65,
        "tracks": {"quant_ridge": {"view_hit_rate": 0.50}},
        "combiners": {"equal_weight_2": {"view_hit_rate": 0.56}},
        "promotion_run_history": [{"promoted": ["equal_weight_2"]}],
    }
    assert evaluate_promotion(board_one_run)["promoted_combiners"] == []

    board_two_runs = {
        **board_one_run,
        "promotion_run_history": [
            {"promoted": ["equal_weight_2"]},
            {"promoted": ["equal_weight_2"]},
        ],
    }
    assert "equal_weight_2" in evaluate_promotion(board_two_runs)["promoted_combiners"]


@pytest.mark.unit
def test_finalize_scoreboard_promotion_appends_once(monkeypatch):
    from trade_integrations.dataflows.index_research.prediction_algorithms import promotion as promo_mod

    def _fake_load(_ticker="NIFTY"):
        return {"promotion_run_history": [{"promoted": ["equal_weight_2"]}]}

    monkeypatch.setattr(promo_mod, "load_scoreboard", _fake_load)

    report = {
        "eval_count": 65,
        "tracks": {"quant_ridge": {"view_hit_rate": 0.50}},
        "combiners": {"equal_weight_2": {"view_hit_rate": 0.56}},
    }
    out = promo_mod.finalize_scoreboard_promotion(report, ticker="NIFTY")
    assert len(out["promotion_run_history"]) == 2
    assert out["promotion_run_history"][-1]["promoted"] == ["equal_weight_2"]
    assert "equal_weight_2" in out["promotion"]["promoted_combiners"]


@pytest.mark.unit
def test_promotion_blocks_first_run_without_history():
    board = {
        "eval_count": 65,
        "tracks": {"quant_ridge": {"view_hit_rate": 0.50}},
        "combiners": {"shrinkage_50": {"view_hit_rate": 0.56}},
        "promotion_run_history": [],
    }
    promo = evaluate_promotion(board)
    assert promo["verdicts"]["shrinkage_50"]["promoted"] is True
    assert promo["promoted_combiners"] == []


@pytest.mark.unit
def test_combiner_blocks_macro_only_plus_event_overlay():
    from trade_integrations.dataflows.index_research.prediction_algorithms.combiners import (
        _validate_track_set,
    )

    assert _validate_track_set(["macro_only", "event_overlay"]) is not None


@pytest.mark.unit
def test_inverse_mae_window_differs():
    from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.scoreboard import (
        summarize_track_metrics,
    )

    eval_rows = [
        {"date": f"2026-01-{i:02d}", "track_id": "macro_only_no_overlay", "error_pct": float(i), "direction_hit": True}
        for i in range(1, 14)
    ]
    mae6 = summarize_track_metrics(eval_rows, "macro_only_no_overlay", window=6, before_date="2026-01-14")["mae_pct"]
    mae12 = summarize_track_metrics(eval_rows, "macro_only_no_overlay", window=12, before_date="2026-01-14")["mae_pct"]
    assert mae6 != mae12


@pytest.mark.unit
def test_combiner_no_double_count():
    from trade_integrations.dataflows.index_research.prediction_algorithms.combiners import (
        _validate_track_set,
        run_combiner,
    )

    assert _validate_track_set(["quant_ridge", "bottom_up"]) is not None
    assert _validate_track_set(["macro_only", "scenario_anchor"]) is None

    tracks = {
        "quant_ridge": _track("quant_ridge", 0.8),
        "bottom_up": _track("bottom_up", 0.2),
    }
    result = run_combiner("quant_only", tracks)
    assert result.tracks_used == ["quant_ridge"]

    tracks2 = {
        "quant_ridge": _track("quant_ridge", 0.5),
        "scenario_anchor": _track("scenario_anchor", -0.2),
    }
    aligned = run_combiner("alignment_grid", tracks2)
    assert "validation_error" not in (aligned.provenance or {})


@pytest.mark.unit
def test_inverse_mae_no_lookahead():
    from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.scoreboard import (
        summarize_track_metrics,
    )

    eval_rows = [
        {"date": "2026-01-01", "track_id": "macro_only", "error_pct": 1.0, "direction_hit": True},
        {"date": "2026-01-02", "track_id": "macro_only", "error_pct": 5.0, "direction_hit": False},
    ]
    prior = [r for r in eval_rows if r["date"] < "2026-01-02"]
    metrics = summarize_track_metrics(prior, "macro_only")
    assert metrics["eval_count"] == 1
    assert metrics["mae_pct"] == pytest.approx(1.0)
