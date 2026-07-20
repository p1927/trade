#!/usr/bin/env python3
"""Audit prediction pipeline data coverage — panel, Phase I, flow gate."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit prediction data coverage")
    parser.add_argument("--panel-name", default="NIFTY_2006_present")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--long-days", type=int, default=3650, help="Long-window flow gate days")
    parser.add_argument("--write", action="store_true", help="Write JSON audit artifact")
    parser.add_argument(
        "--refresh-diagnostics",
        action="store_true",
        help="Run equation diagnostics before audit (populates Phase I ablation summary)",
    )
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env
    from trade_integrations.dataflows.index_research.data_completeness import measure_flow_coverage
    from trade_integrations.dataflows.index_research.history_panel import build_history_panel
    from trade_integrations.dataflows.index_research.phase_i_coverage import audit_phase_i_coverage
    from trade_integrations.dataflows.index_research.prediction_audit_extensions import (
        audit_flow_parity,
        audit_ltim_status,
        audit_news_pipeline,
    )
    from trade_integrations.dataflows.index_research.prediction_data_requirements import (
        audit_prediction_panel_coverage,
    )

    load_trade_env()

    diagnostics_report: dict | None = None
    if args.refresh_diagnostics:
        from trade_integrations.dataflows.index_research.equation_diagnostics import run_and_save_diagnostics

        diagnostics_report = run_and_save_diagnostics(days=args.days, ticker="NIFTY")

    panel = build_history_panel(panel_name=args.panel_name)
    audit = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "panel_name": args.panel_name,
        "panel_rows": len(panel),
        "panel_coverage": audit_prediction_panel_coverage(panel),
        "phase_i_coverage": audit_phase_i_coverage(panel),
        "flow_coverage": measure_flow_coverage(days=args.days, allow_live_fetch=False),
        "flow_coverage_long": measure_flow_coverage(days=args.long_days, allow_live_fetch=False),
        "flow_parity": audit_flow_parity(panel, days=args.days, allow_live_fetch=False),
        "ltim_status": audit_ltim_status(),
        "news_pipeline": audit_news_pipeline(ticker="NIFTY"),
    }
    if diagnostics_report is not None:
        audit["equation_diagnostics"] = {
            "status": diagnostics_report.get("status"),
            "baseline_direction_hit_rate": diagnostics_report.get("baseline_direction_hit_rate"),
            "block_count": len(diagnostics_report.get("block_ablation") or []),
        }

    if args.write:
        out_path = ROOT / "reports" / "hub" / "_data" / "history" / "prediction_data_audit.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")
        audit["written_to"] = str(out_path)

    print(json.dumps(audit, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
