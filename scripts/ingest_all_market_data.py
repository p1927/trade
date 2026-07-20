#!/usr/bin/env python3
"""Orchestrate full market-data load: INDmoney/OpenAlgo, repo seeds, external datasets."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> dict[str, object]:
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip()[:8000],
        "stderr": (proc.stderr or "").strip()[:4000],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Load all market data (INDmoney first, then other sources)")
    parser.add_argument(
        "--skip-openalgo",
        action="store_true",
        help="Skip INDmoney/OpenAlgo bulk OHLCV fetch",
    )
    parser.add_argument(
        "--skip-historical",
        action="store_true",
        help="Skip repo seeds + cold tier + hub sync",
    )
    parser.add_argument(
        "--skip-external",
        action="store_true",
        help="Skip GitHub/HF external dataset ingest",
    )
    parser.add_argument(
        "--skip-fundamentals",
        action="store_true",
        help="Skip Nifty100 financial intel ingest",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Historical ingest without live HTTP (repo/offline only)",
    )
    parser.add_argument("--years", type=int, default=10, help="OpenAlgo history depth (max 10)")
    parser.add_argument("--sleep", type=float, default=0.45, help="Seconds between OpenAlgo API calls")
    parser.add_argument("--force-openalgo", action="store_true", help="Re-fetch OpenAlgo even when cached")
    parser.add_argument(
        "--bundles",
        default="indices_extended,nifty50,nifty100",
        help="Comma-separated OpenAlgo symbol bundles",
    )
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env

    load_trade_env()

    py = sys.executable
    report: dict[str, object] = {"steps": {}}

    if not args.skip_openalgo:
        from trade_integrations.openalgo.bulk_history_persist import persist_all_openalgo_bundles

        bundles = [b.strip() for b in args.bundles.split(",") if b.strip()]
        report["steps"]["openalgo"] = persist_all_openalgo_bundles(
            bundles=bundles,
            years=min(args.years, 10),
            sleep_s=args.sleep,
            force=args.force_openalgo,
            sync_cold_tier=True,
        )

    if not args.skip_historical:
        hist_cmd = [py, "scripts/ingest_historical_data.py", "--all"]
        if args.offline:
            hist_cmd.append("--offline")
        report["steps"]["historical"] = _run(hist_cmd)

    if not args.skip_external:
        ext_cmd = [
            py,
            "scripts/ingest_github_datasets.py",
            "--ingest-gaps",
            "--slow-fetch",
            "--skip-kaggle",
        ]
        report["steps"]["external"] = _run(ext_cmd)

    if not args.skip_fundamentals:
        fund_cmd = [py, "scripts/ingest_nifty100_financial_intel.py", "--force-fetch"]
        report["steps"]["fundamentals"] = _run(fund_cmd)

    out_path = ROOT / "data" / "nse" / "historic_data" / "openalgo" / "ingest_all_manifest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))

    failed = False
    for name, step in report["steps"].items():
        if name == "openalgo":
            summary = (step or {}).get("bundles", {})
            for bundle_report in summary.values():
                err = (bundle_report or {}).get("summary", {}).get("error", 0)
                if err:
                    failed = True
        elif isinstance(step, dict) and step.get("returncode", 0) != 0:
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
