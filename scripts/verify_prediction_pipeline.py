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

    steps.append(_run([py, str(ROOT / "scripts" / "audit_prediction_data.py"), "--write"], label="audit"))
    if not args.skip_backtest:
        steps.append(
            _run(
                [py, str(ROOT / "scripts" / "run_track_backtest.py"), "--ticker", "NIFTY", "--days", "60", "--eval-step", "5"],
                label="backtest_run_1",
            )
        )
        steps.append(
            _run(
                [py, str(ROOT / "scripts" / "run_track_backtest.py"), "--ticker", "NIFTY", "--days", "60", "--eval-step", "5"],
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
                    "tests/test_enrich_macro_panel_news.py",
                    "tests/test_prediction_pipeline_lab.py",
                    "tests/test_prediction_algorithms_combiners.py",
                    "tests/test_calibrate_bottom_up.py",
                    "-q",
                    "--timeout=120",
                ],
                label="pytest",
            )
        )

    failed = [s for s in steps if s["returncode"] != 0]
    report = {"steps": steps, "failed": len(failed), "ok": len(failed) == 0}
    print(json.dumps(report, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
