#!/usr/bin/env python3
"""End-to-end prediction pipeline verification (ingest audit + backtest + pytest)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], *, label: str) -> dict:
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, check=False)
    out = {
        "label": label,
        "returncode": proc.returncode,
        "cmd": cmd,
    }
    if proc.stdout.strip():
        out["stdout_tail"] = proc.stdout.strip()[-2000:]
    if proc.stderr.strip():
        out["stderr_tail"] = proc.stderr.strip()[-1000:]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify prediction pipeline wiring")
    parser.add_argument("--skip-backtest", action="store_true")
    parser.add_argument("--skip-pytest", action="store_true")
    args = parser.parse_args()

    py = sys.executable
    steps: list[dict] = []

    steps.append(_run([py, str(ROOT / "scripts" / "audit_prediction_data.py"), "--days", "500", "--write"], label="audit"))
    if not args.skip_backtest:
        steps.append(
            _run(
                [py, str(ROOT / "scripts" / "run_track_backtest.py"), "--ticker", "NIFTY", "--days", "365", "--eval-step", "5"],
                label="backtest_run_1",
            )
        )
        steps.append(
            _run(
                [py, str(ROOT / "scripts" / "run_track_backtest.py"), "--ticker", "NIFTY", "--days", "365", "--eval-step", "5"],
                label="backtest_run_2",
            )
        )
    if not args.skip_pytest:
        steps.append(
            _run(
                [
                    py,
                    "-m",
                    "pytest",
                    "tests/test_index_explain.py",
                    "tests/test_prediction_review_fixes.py",
                    "tests/test_prediction_data_consistency.py",
                    "tests/test_history_panel.py",
                    "-q",
                    "--timeout=120",
                ],
                label="pytest_explain_panel",
            )
        )
        steps.append(
            _run(
                [
                    py,
                    "-m",
                    "pytest",
                    "tests/test_enrich_macro_panel_news.py",
                    "tests/test_prediction_pipeline_lab.py",
                    "tests/test_calibrate_bottom_up.py",
                    "tests/test_phase_i_coverage.py",
                    "tests/test_spread_features.py",
                    "tests/test_data_router_ohlcv.py",
                    "tests/test_data_router_worker.py",
                    "-q",
                    "--timeout=120",
                ],
                label="pytest_prediction_core",
            )
        )
        steps.append(
            _run(
                [
                    py,
                    "-m",
                    "pytest",
                    "tests/test_prediction_algorithms_combiners.py",
                    "tests/test_prediction_algorithms_tracks.py",
                    "tests/test_debate_synthesis.py",
                    "-k",
                    "debate or seed_debate",
                    "-q",
                    "--timeout=120",
                ],
                label="pytest_debate",
            )
        )

    promotion_status: dict[str, object] = {"loaded": False}
    scoreboard_eval_count = 0
    try:
        if str(ROOT / "integrations") not in sys.path:
            sys.path.insert(0, str(ROOT / "integrations"))
        from trade_integrations.dataflows.index_research.prediction_algorithms.evaluator.scoreboard import (
            load_scoreboard,
        )
        from trade_integrations.dataflows.index_research.prediction_algorithms.promotion import (
            evaluate_promotion,
        )

        board = load_scoreboard("NIFTY")
        scoreboard_eval_count = int((board or {}).get("eval_count") or 0)
        promotion_status = evaluate_promotion(board or {}, ticker="NIFTY")
        promotion_status["loaded"] = True
        promotion_status["scoreboard_eval_count"] = scoreboard_eval_count
    except Exception as exc:
        promotion_status = {
            "loaded": False,
            "error": str(exc),
            "scoreboard_eval_count": scoreboard_eval_count,
        }

    backtest_steps = [s for s in steps if str(s.get("label", "")).startswith("backtest_run")]
    backtest_any_ok = any(s.get("returncode") == 0 for s in backtest_steps)
    if not args.skip_backtest and backtest_steps and backtest_any_ok and scoreboard_eval_count <= 0:
        steps.append(
            {
                "label": "scoreboard_eval_count",
                "returncode": 1,
                "cmd": ["scoreboard_eval_count_check"],
                "stderr_tail": "track scoreboard eval_count is 0 after backtest — walk-forward produced no OOS rows",
            }
        )

    failed = [s for s in steps if s["returncode"] != 0]
    report = {
        "steps": steps,
        "failed": len(failed),
        "ok": len(failed) == 0,
        "promotion_status": promotion_status,
    }
    print(json.dumps(report, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
