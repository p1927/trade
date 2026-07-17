#!/usr/bin/env python3
"""Rank Alpha Zoo equity_in alphas on the Nifty 50 universe (IC/IR bench).

Writes ``reports/hub/_data/index_factors/alpha_zoo_ic_rankings.json`` for
consensus-basket selection in the index alpha_bridge config.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VIBE_AGENT = _REPO_ROOT / "vibetrading" / "agent"
if str(_VIBE_AGENT) not in sys.path:
    sys.path.insert(0, str(_VIBE_AGENT))

_DEFAULT_OUTPUT = (
    _REPO_ROOT / "reports" / "hub" / "_data" / "index_factors" / "alpha_zoo_ic_rankings.json"
)
_EQUITY_IN_ZOOS = ("alpha101", "qlib158")


def _run_zoo_bench(zoo: str, *, universe: str, period: str, top: int) -> dict:
    from src.tools.alpha_bench_tool import run_alpha_bench

    return run_alpha_bench(
        zoo=zoo,
        universe=universe,
        period=period,
        top=top,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Bench Alpha Zoo on Nifty 50 and save IC rankings")
    parser.add_argument("--universe", default="nifty50", help="Bench universe (default: nifty50)")
    parser.add_argument("--period", default="2020-2025", help="YYYY-YYYY or YYYY-MM-DD/YYYY-MM-DD")
    parser.add_argument("--top", type=int, default=30, help="Top-N alphas per zoo to retain")
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT, help="Output JSON path")
    parser.add_argument(
        "--zoos",
        nargs="+",
        default=list(_EQUITY_IN_ZOOS),
        help="Zoos to bench (default: alpha101 qlib158)",
    )
    args = parser.parse_args()

    all_rows: list[dict] = []
    zoo_summaries: dict[str, dict] = {}
    errors: list[str] = []

    for zoo in args.zoos:
        envelope = _run_zoo_bench(zoo, universe=args.universe, period=args.period, top=args.top)
        if envelope.get("status") != "ok":
            errors.append(f"{zoo}: {envelope.get('error', 'unknown error')}")
            continue
        top_rows = list(envelope.get("top") or [])
        for row in top_rows:
            all_rows.append({**row, "zoo": zoo, "universe": args.universe, "period": args.period})
        zoo_summaries[zoo] = {
            "n_alphas_tested": envelope.get("n_alphas_tested"),
            "n_skipped": envelope.get("n_skipped"),
            "report_path": envelope.get("report_path"),
        }

    all_rows.sort(key=lambda r: float(r.get("ir") or 0.0), reverse=True)

    payload = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "universe": args.universe,
        "period": args.period,
        "zoos": args.zoos,
        "rankings": all_rows[: max(args.top * len(args.zoos), args.top)],
        "zoo_summaries": zoo_summaries,
        "errors": errors,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {len(payload['rankings'])} ranked alphas → {args.output}")
    if errors:
        print("Errors:", "; ".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
