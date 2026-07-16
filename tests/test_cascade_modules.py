"""Per-module unit tests for the cascade package and simulate integration."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from trade_integrations.dataflows.index_research.cascade.blender import blend_all_rules, blend_rules
from trade_integrations.dataflows.index_research.cascade.calibration_store import (
    load_cascade_calibration,
    load_calibration_from_doc,
    save_cascade_calibration,
)
from trade_integrations.dataflows.index_research.cascade.engine import build_cascade_overrides
from trade_integrations.dataflows.index_research.cascade.event_presets import overrides_from_event_preset
from trade_integrations.dataflows.index_research.cascade.heuristic_rules import (
    HEURISTIC_CASCADE_RULES,
    heuristic_rules_for,
)
from trade_integrations.dataflows.index_research.cascade.irf_converter import (
    var_rules_from_fit,
    var_rules_to_serializable,
)
from trade_integrations.dataflows.index_research.cascade.regime_scaler import (
    classify_cascade_regime,
    regime_scale,
    scale_rules,
)
from trade_integrations.dataflows.index_research.cascade.rule_provider import (
    CalibratedRuleProvider,
    HeuristicRuleProvider,
    build_rule_provider,
)
from trade_integrations.dataflows.index_research.cascade.shock_math import (
    apply_secondary_shock,
    shock_primary_value,
)
from trade_integrations.dataflows.index_research.cascade.types import (
    CascadeCalibration,
    CascadeSecondaryRule,
)
from trade_integrations.dataflows.index_research.cascade.var_estimator import (
    VarFitResult,
    fit_var1,
    impulse_response,
    prepare_var_matrix,
)
from trade_integrations.dataflows.index_research.simulate import (
    build_forecast_path,
    resolve_factor_overrides,
)


# ---------------------------------------------------------------------------
# types
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cascade_calibration_round_trip():
    cal = CascadeCalibration(
        as_of="2026-07-16",
        status="ok",
        rules={"oil_brent": [{"secondary": "usd_inr", "multiplier": 0.2, "mode": "relative"}]},
        diagnostics={"n_obs": 78},
    )
    restored = CascadeCalibration.from_dict(cal.to_dict())
    assert restored is not None
    assert restored.as_of == "2026-07-16"
    assert restored.rules["oil_brent"][0]["secondary"] == "usd_inr"
    assert restored.diagnostics["n_obs"] == 78


@pytest.mark.unit
def test_cascade_calibration_from_dict_handles_empty():
    assert CascadeCalibration.from_dict(None) is None
    assert CascadeCalibration.from_dict({}) is None
    parsed = CascadeCalibration.from_dict({"as_of": "2026-07-16"})
    assert parsed is not None
    assert parsed.as_of == "2026-07-16"


# ---------------------------------------------------------------------------
# heuristic_rules
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_heuristic_rules_oil_brent_edges():
    rules = heuristic_rules_for("oil_brent")
    secondaries = {r.secondary for r in rules}
    assert secondaries == {"usd_inr", "india_vix", "gold"}
    assert all(r.source == "heuristic" for r in rules)


@pytest.mark.unit
def test_heuristic_rules_unknown_primary_empty():
    assert heuristic_rules_for("unknown_factor") == []


@pytest.mark.unit
def test_heuristic_rules_cover_all_primaries():
    for primary, edges in HEURISTIC_CASCADE_RULES.items():
        assert edges, f"{primary} has no edges"
        rules = heuristic_rules_for(primary)
        assert len(rules) == len(edges)


# ---------------------------------------------------------------------------
# shock_math
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "base,shock,factor,expected",
    [
        (80.0, 10.0, "oil_brent", 88.0),
        (14.0, 10.0, "india_vix", 15.4),
        (0.0, 5.0, "oil_brent", 0.05),
    ],
)
def test_shock_primary_value(base, shock, factor, expected):
    assert shock_primary_value(base, shock, factor) == pytest.approx(expected)


@pytest.mark.unit
@pytest.mark.parametrize(
    "base,shock,mult,mode,expected",
    [
        (83.0, 10.0, 0.15, "relative", 83.0 * 1.015),
        (14.0, 10.0, 0.15, "absolute", 15.5),
        (0.0, 10.0, 0.15, "relative", 0.015),
    ],
)
def test_apply_secondary_shock(base, shock, mult, mode, expected):
    assert apply_secondary_shock(base, shock, mult, mode) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# var_estimator
# ---------------------------------------------------------------------------


def _synthetic_factor_frame(n: int = 80) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    oil = np.cumsum(rng.normal(0, 0.5, n)) + 80
    return pd.DataFrame(
        {
            "oil_brent": oil,
            "usd_inr": 83 + 0.1 * (oil - oil[0]) + rng.normal(0, 0.05, n),
            "india_vix": 14 + 0.05 * (oil - oil[0]) + rng.normal(0, 0.1, n),
            "sp500": 5000 + rng.normal(0, 5, n),
            "fii_net_5d": rng.normal(0, 500, n),
            "us_10y": 4.2 + rng.normal(0, 0.02, n),
            "nifty_pcr": 1.0 + rng.normal(0, 0.05, n),
        }
    )


@pytest.mark.unit
def test_prepare_var_matrix_drops_sparse_columns():
    frame = _synthetic_factor_frame()
    matrix = prepare_var_matrix(frame)
    assert not matrix.empty
    assert "oil_brent" in matrix.columns


@pytest.mark.unit
def test_fit_var1_insufficient_obs_returns_none():
    frame = _synthetic_factor_frame(n=10)
    matrix = prepare_var_matrix(frame)
    assert fit_var1(matrix) is None


@pytest.mark.unit
def test_fit_var1_and_impulse_response():
    matrix = prepare_var_matrix(_synthetic_factor_frame())
    fit = fit_var1(matrix)
    assert fit is not None
    assert fit.n_obs >= 30
    paths = impulse_response(fit, shock_factor="oil_brent", shock_size=1.0, horizon=2)
    assert "usd_inr" in paths
    assert len(paths["usd_inr"]) == 2


@pytest.mark.unit
def test_impulse_response_unknown_factor_empty():
    matrix = prepare_var_matrix(_synthetic_factor_frame())
    fit = fit_var1(matrix)
    assert fit is not None
    assert impulse_response(fit, shock_factor="gold") == {}


# ---------------------------------------------------------------------------
# irf_converter
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_var_rules_from_fit_and_serialize():
    matrix = prepare_var_matrix(_synthetic_factor_frame())
    fit = fit_var1(matrix)
    assert fit is not None
    rules = var_rules_from_fit(fit, primaries=("oil_brent",))
    assert "oil_brent" in rules
    assert rules["oil_brent"][0].source == "var"
    payload = var_rules_to_serializable(rules)
    assert payload["oil_brent"][0]["secondary"] in {"usd_inr", "india_vix", "gold"}


# ---------------------------------------------------------------------------
# blender
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_blend_rules_without_var_keeps_heuristic():
    blended = blend_rules("oil_brent", var_rules=None)
    heur = heuristic_rules_for("oil_brent")
    assert len(blended) == len(heur)
    assert all(r.source == "heuristic" for r in blended)


@pytest.mark.unit
def test_blend_all_rules_covers_primaries():
    all_blended = blend_all_rules({}, alpha=0.5)
    assert set(all_blended.keys()) == set(HEURISTIC_CASCADE_RULES.keys())


# ---------------------------------------------------------------------------
# regime_scaler
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "vix,expected",
    [(None, "calm"), (12, "calm"), (17, "elevated"), (22, "crisis")],
)
def test_classify_cascade_regime(vix, expected):
    assert classify_cascade_regime(india_vix=vix) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "regime,scale",
    [("calm", 1.0), ("elevated", 1.1), ("crisis", 1.25)],
)
def test_regime_scale(regime, scale):
    assert regime_scale(regime) == pytest.approx(scale)


@pytest.mark.unit
def test_scale_rules_calm_unchanged():
    rules = heuristic_rules_for("oil_brent")
    assert scale_rules(rules, "calm") == rules


# ---------------------------------------------------------------------------
# rule_provider
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_rule_provider_defaults_heuristic():
    provider = build_rule_provider(None)
    assert isinstance(provider, HeuristicRuleProvider)


@pytest.mark.unit
def test_build_rule_provider_uses_calibration_when_ok():
    cal = CascadeCalibration(as_of="2026-07-16", status="ok", rules={"oil_brent": []})
    provider = build_rule_provider(cal)
    assert isinstance(provider, CalibratedRuleProvider)


@pytest.mark.unit
def test_build_rule_provider_force_heuristic_overrides_calibration():
    cal = CascadeCalibration(
        as_of="2026-07-16",
        status="ok",
        rules={"oil_brent": [{"secondary": "usd_inr", "multiplier": 0.99, "mode": "relative"}]},
    )
    provider = build_rule_provider(cal, force_heuristic=True)
    assert isinstance(provider, HeuristicRuleProvider)


@pytest.mark.unit
def test_calibrated_provider_falls_back_for_unknown_primary():
    cal = CascadeCalibration(as_of="2026-07-16", status="ok", rules={"oil_brent": []})
    provider = CalibratedRuleProvider(cal)
    rules = provider.rules_for("repo_rate")
    assert len(rules) >= 1


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_engine_cascade_disabled_and_zero_shock():
    macro = {"oil_brent": 80.0, "usd_inr": 83.0}
    overrides, applied = build_cascade_overrides("oil_brent", 10.0, macro, cascade=False)
    assert "usd_inr" not in overrides
    overrides0, applied0 = build_cascade_overrides("oil_brent", 0.0, macro, cascade=True)
    assert len(applied0) == 1


# ---------------------------------------------------------------------------
# event_presets
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_event_preset_missing_returns_empty():
    overrides, applied = overrides_from_event_preset([], "missing|id", {})
    assert overrides == {}
    assert applied == []


@pytest.mark.unit
def test_event_preset_partial_progress():
    macro = {"oil_brent": 100.0, "india_vix": 14.0}
    curves = [
        {
            "event": "oil_spike",
            "outcome": "supply_shock",
            "factor_shocks": {"oil_brent": 0.10, "india_vix": 2.0},
        }
    ]
    overrides, _ = overrides_from_event_preset(curves, "oil_spike|supply_shock", macro, progress=0.5)
    assert overrides["oil_brent"] == pytest.approx(105.0)
    assert overrides["india_vix"] == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# calibration_store
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_calibration_store_save_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    cal = CascadeCalibration(
        as_of="2026-07-16",
        status="ok",
        rules={"oil_brent": [{"secondary": "usd_inr", "multiplier": 0.2, "mode": "relative"}]},
    )
    path = save_cascade_calibration(cal, ticker="NIFTY")
    assert path.is_file()
    loaded = load_cascade_calibration("NIFTY")
    assert loaded is not None
    assert loaded.status == "ok"
    assert loaded.rules["oil_brent"][0]["multiplier"] == 0.2


@pytest.mark.unit
def test_load_calibration_from_doc_prefers_embedded(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    embedded = {"as_of": "2026-07-16", "status": "ok", "rules": {}}
    doc = SimpleNamespace(ticker="NIFTY", cascade_calibration=embedded)
    parsed = load_calibration_from_doc(doc)
    assert parsed is not None
    assert parsed.as_of == "2026-07-16"


@pytest.mark.unit
def test_load_calibration_from_doc_falls_back_to_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    cal = CascadeCalibration(as_of="2026-07-16", status="ok", rules={})
    save_cascade_calibration(cal, ticker="NIFTY")
    doc = SimpleNamespace(ticker="NIFTY", cascade_calibration={})
    parsed = load_calibration_from_doc(doc)
    assert parsed is not None
    assert parsed.as_of == "2026-07-16"


# ---------------------------------------------------------------------------
# calibrator (offline job)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_cascade_calibration_insufficient_data(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    with patch(
        "trade_integrations.dataflows.index_research.cascade.calibrator.load_aligned_factor_history",
        return_value=pd.DataFrame(),
    ):
        from trade_integrations.dataflows.index_research.cascade.calibrator import (
            run_cascade_calibration,
        )

        cal = run_cascade_calibration(ticker="NIFTY")
    assert cal.status == "insufficient_data"
    assert load_cascade_calibration("NIFTY") is not None


@pytest.mark.unit
def test_run_cascade_calibration_ok_with_synthetic_history(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(tmp_path))
    aligned = _synthetic_factor_frame(120)
    aligned.insert(0, "date", pd.date_range("2026-01-01", periods=120, freq="D").strftime("%Y-%m-%d"))
    aligned["close"] = 24000 + np.arange(120)

    with patch(
        "trade_integrations.dataflows.index_research.cascade.calibrator.load_aligned_factor_history",
        return_value=aligned,
    ):
        from trade_integrations.dataflows.index_research.cascade.calibrator import (
            run_cascade_calibration,
        )

        cal = run_cascade_calibration(ticker="NIFTY", india_vix=22.0)

    assert cal.status == "ok"
    assert cal.regime == "crisis"
    assert len(cal.rules) > 0
    saved = json.loads((tmp_path / "NIFTY" / "index_research" / "cascade_calibration.json").read_text())
    assert saved["status"] == "ok"


# ---------------------------------------------------------------------------
# simulate integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_forecast_path_length():
    path = build_forecast_path(
        spot=24000.0,
        baseline_return_pct=1.0,
        scenario_return_pct=2.0,
        horizon_days=14,
    )
    assert len(path) == 15
    assert path[0]["day"] == 0
    assert path[-1]["day"] == 14
    assert path[-1]["scenario_level"] > path[-1]["baseline_level"]


@pytest.mark.unit
def test_resolve_factor_overrides_with_calibration():
    macro = {"oil_brent": 80.0, "usd_inr": 83.0, "india_vix": 14.0}
    cal = CascadeCalibration(
        as_of="2026-07-16",
        status="ok",
        rules={
            "oil_brent": [
                {
                    "secondary": "usd_inr",
                    "multiplier": 0.25,
                    "mode": "relative",
                    "source": "blended",
                    "heuristic_multiplier": 0.15,
                    "var_multiplier": 0.35,
                }
            ]
        },
    )
    overrides, applied = resolve_factor_overrides(
        macro,
        primary_factor="oil_brent",
        primary_shock_pct=10.0,
        cascade=True,
        cascade_calibration=cal,
        india_vix=14.0,
    )
    assert overrides["oil_brent"] == pytest.approx(88.0)
    assert overrides["usd_inr"] > 83.0
    usd_row = next(r for r in applied if r["factor"] == "usd_inr")
    assert usd_row.get("source") == "blended"


@pytest.mark.unit
def test_resolve_factor_overrides_event_preset_priority():
    macro = {"oil_brent": 80.0, "india_vix": 14.0}
    curves = [
        {
            "event": "oil_spike",
            "outcome": "supply_shock",
            "factor_shocks": {"oil_brent": 0.10, "india_vix": 1.0},
        }
    ]
    overrides, applied = resolve_factor_overrides(
        macro,
        primary_factor="oil_brent",
        primary_shock_pct=10.0,
        event_preset_id="oil_spike|supply_shock",
        event_impact_curves=curves,
    )
    assert overrides["oil_brent"] == pytest.approx(88.0)
    assert overrides["india_vix"] == pytest.approx(15.0)
    assert len(applied) == 2


@pytest.mark.unit
def test_simulate_index_prediction_cascade_metadata():
    pytest.importorskip("sklearn")
    from trade_integrations.dataflows.index_research.simulate import simulate_index_prediction

    macro = {
        "oil_brent": 80.0,
        "usd_inr": 83.0,
        "india_vix": 14.0,
        "sp500": 5200.0,
        "index_sentiment": 0.1,
    }
    cal = CascadeCalibration(
        as_of="2026-07-16",
        status="ok",
        rules={
            "oil_brent": [
                {
                    "secondary": "usd_inr",
                    "multiplier": 0.20,
                    "mode": "relative",
                    "source": "blended",
                    "heuristic_multiplier": 0.15,
                    "var_multiplier": 0.25,
                }
            ]
        },
    )
    result = simulate_index_prediction(
        macro_factors=macro,
        spot=24500.0,
        bottom_up_return_pct=0.5,
        horizon_days=14,
        primary_factor="oil_brent",
        primary_shock_pct=10.0,
        cascade=True,
        cascade_calibration=cal,
        india_vix=14.0,
    )
    assert result["cascade_method"] == "data_calibrated"
    assert result["cascade_regime"] == "calm"
    assert result["cascade_calibration_as_of"] == "2026-07-16"
    assert len(result["forecast_path"]) == 15
    assert len(result["cascade_applied"]) >= 2


@pytest.mark.unit
def test_simulate_force_heuristic_cascade_metadata():
    pytest.importorskip("sklearn")
    from trade_integrations.dataflows.index_research.simulate import simulate_index_prediction

    macro = {"oil_brent": 80.0, "usd_inr": 83.0, "india_vix": 14.0}
    cal = CascadeCalibration(as_of="2026-07-16", status="ok", rules={"oil_brent": []})
    result = simulate_index_prediction(
        macro_factors=macro,
        spot=24500.0,
        bottom_up_return_pct=0.5,
        horizon_days=14,
        primary_factor="oil_brent",
        primary_shock_pct=10.0,
        cascade_calibration=cal,
        force_heuristic_cascade=True,
    )
    assert result["cascade_method"] == "heuristic"
