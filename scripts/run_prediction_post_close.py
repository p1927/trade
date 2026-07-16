#!/usr/bin/env python3
"""Post-close enrichment: flows, backtest, counterfactual, data audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

import trade_integrations  # noqa: F401


def main() -> int:
    parser = argparse.ArgumentParser(description="Post-close prediction pipeline refresh")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--horizon-days", type=int, default=14)
    parser.add_argument("--skip-fao", action="store_true", help="Skip NSE FAO archive backfill")
    args = parser.parse_args()

    results: dict[str, object] = {"status": "ok"}

    from trade_integrations.dataflows.index_research.nse_browser_refresh import (
        refresh_nse_browser_for_prediction,
    )
    from trade_integrations.dataflows.index_research.factor_backfill_enrichment import (
        enrich_factor_history,
    )
    from trade_integrations.dataflows.index_research.hub_data_audit import run_and_save_data_audit
    from trade_integrations.dataflows.index_research.backtest_runner import (
        run_and_save_backtest,
    )
    from trade_integrations.dataflows.index_research.prediction_counterfactual import (
        run_and_save_counterfactual,
    )

    results["nse_browser"] = refresh_nse_browser_for_prediction(
        days=args.days,
        refresh=True,
    )

    if args.skip_fao:
        from trade_integrations.dataflows.index_research.sources.history_loader import load_nifty_history
        from trade_integrations.dataflows.index_research.sources.nse_flow_derivatives_backfill import (
            merge_flow_derivatives_frame,
            upsert_flow_cash_cache,
        )

        nifty = load_nifty_history(days=args.days)
        if not nifty.empty:
            start = str(nifty["date"].iloc[0])[:10]
            end = str(nifty["date"].iloc[-1])[:10]
            flow = merge_flow_derivatives_frame(start, end)
            if not flow.empty:
                upsert_flow_cash_cache(flow.to_dict("records"))
        results["factor_enrichment"] = enrich_factor_history(days=args.days)
    else:
        results["factor_enrichment"] = enrich_factor_history(days=args.days)

    results["backtest"] = run_and_save_backtest(days=args.days, horizon_days=args.horizon_days)
    results["counterfactual"] = run_and_save_counterfactual(days=args.days, horizon_days=args.horizon_days)
    results["data_audit"] = run_and_save_data_audit(days=args.days, horizon_days=args.horizon_days)

    print(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
