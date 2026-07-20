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
    parser.add_argument("--write", action="store_true", help="Write JSON audit artifact")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env
    from trade_integrations.dataflows.index_research.data_completeness import measure_flow_coverage
    from trade_integrations.dataflows.index_research.history_panel import build_history_panel
    from trade_integrations.dataflows.index_research.phase_i_coverage import audit_phase_i_coverage
    from trade_integrations.dataflows.index_research.prediction_audit_extensions import audit_news_pipeline
    from trade_integrations.dataflows.index_research.prediction_data_requirements import (
        audit_prediction_panel_coverage,
    )

    load_trade_env()

    panel = build_history_panel(panel_name=args.panel_name)
    audit = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "panel_name": args.panel_name,
        "panel_rows": len(panel),
        "panel_coverage": audit_prediction_panel_coverage(panel),
        "phase_i_coverage": audit_phase_i_coverage(panel),
        "flow_coverage": measure_flow_coverage(days=args.days, allow_live_fetch=False),
        "news_pipeline": audit_news_pipeline(ticker="NIFTY"),
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
