"""Tests for track walk-forward scoreboard."""

from __future__ import annotations

import pandas as pd


def test_walk_forward_sets_target_and_produces_eval_rows(monkeypatch):
    from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator import walk_forward as wf

    dates = pd.date_range("2024-01-02", periods=80, freq="B")
    closes = [100 + i * 0.2 for i in range(len(dates))]
    frame = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "close": closes, "india_vix": 14.0})

    class FakeArtifact:
        feature_names = ["india_vix"]
        mae = 1.5
        coefficients = {"india_vix": 0.1}

    def fake_load(days=365, **kwargs):
        return frame.copy()

    def fake_train(_train, _horizon):
        return FakeArtifact()

    def fake_build_scenarios(*_a, **_k):
        return [{"event": "test", "probability": 1.0, "range": [99, 101]}]

    def fake_scenario_anchor(*_a, **_k):
        return 0.5

    def fake_signals(_day, _factors=None):
        return []

    def fake_legacy(**_k):
        return {"expected_return_pct": 0.3, "view": "neutral"}

    def fake_run_all_tracks(ctx, track_ids=None):
        from trade_integrations.dataflows.index_research.prediction_algorithms.types import ForecastTrack

        ids = track_ids or ["quant_ridge"]
        return {
            tid: ForecastTrack(track_id=tid, expected_return_pct=0.4, view="neutral")
            for tid in ids
        }

    monkeypatch.setattr(wf, "load_aligned_factor_history", fake_load)
    monkeypatch.setattr(wf, "load_constituent_signals_for_day", fake_signals)
    monkeypatch.setattr(wf, "replay_legacy_headline", fake_legacy)
    monkeypatch.setattr(wf, "run_all_tracks", fake_run_all_tracks)
    monkeypatch.setattr(wf, "save_scoreboard", lambda *_a, **_k: None)

    import trade_integrations.dataflows.index_research.predictor as pred

    monkeypatch.setattr(pred, "train_macro_ridge", fake_train)

    import trade_integrations.dataflows.index_research.scenarios as scn

    monkeypatch.setattr(scn, "build_index_scenarios", fake_build_scenarios)
    monkeypatch.setattr(scn, "scenario_weighted_return_pct", fake_scenario_anchor)

    report = wf.run_track_walk_forward(
        ticker="NIFTY",
        days=365,
        horizon_days=14,
        min_train_rows=20,
        eval_step=10,
    )

    assert report["status"] == "ok"
    assert report["eval_count"] > 0
    assert report["chart"]["eval_dates"]
    assert report["daily_evaluations"]
