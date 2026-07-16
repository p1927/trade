#!/usr/bin/env python3
"""Long-running automated intraday paper trading loop.

Polls the market on a fixed interval during NSE hours, researches options,
enters ranked strategies within budget, and exits on thesis break.

Usage:
  python scripts/run_auto_paper_trading.py
  python scripts/run_auto_paper_trading.py --once --dry-run
  python scripts/run_auto_paper_trading.py --budget 20000 --watchlist NIFTY,BANKNIFTY
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if INTEGRATIONS.is_dir() and str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from trade_integrations.auto_paper.config import get_auto_paper_config
from trade_integrations.auto_paper.engine import is_market_session_open, run_auto_paper_tick
from trade_integrations.auto_paper.openalgo_client import OpenAlgoClient
from trade_integrations.auto_paper.session_store import start_session, stop_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("auto_paper")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Automated intraday paper trading")
    parser.add_argument("--budget", type=float, default=None, help="Paper budget in INR")
    parser.add_argument("--watchlist", type=str, default=None, help="Comma-separated underlyings")
    parser.add_argument("--interval", type=int, default=300, help="Poll interval seconds")
    parser.add_argument("--once", action="store_true", help="Run a single tick and exit")
    parser.add_argument("--dry-run", action="store_true", help="Skip order placement")
    parser.add_argument("--stop", action="store_true", help="Stop session and exit")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    cfg = get_auto_paper_config()

    if args.stop:
        from trade_integrations.auto_paper.mcp_actions import stop_auto_paper

        stop_auto_paper()
        logger.info("Auto paper session stopped (scheduler jobs removed if present)")
        return 0

    budget = args.budget if args.budget is not None else cfg.budget_inr
    watchlist = (
        [item.strip().upper() for item in args.watchlist.split(",") if item.strip()]
        if args.watchlist
        else list(cfg.watchlist)
    )

    os.environ.setdefault("AUTO_PAPER_TRADING_ENABLED", "true")

    try:
        client = OpenAlgoClient()
        if client.ensure_analyzer_mode():
            logger.info("OpenAlgo analyzer (paper) mode active")
        funds = client.get_funds()
        logger.info("Sandbox funds: %s", json.dumps(funds, default=str))
    except RuntimeError as exc:
        logger.error("OpenAlgo unavailable: %s", exc)
        return 1

    start_session(budget_inr=budget, watchlist=watchlist)
    logger.info("Auto paper started — budget ₹%.0f, watchlist %s", budget, watchlist)

    try:
        while True:
            if is_market_session_open(cfg):
                result = run_auto_paper_tick(dry_run=args.dry_run)
                logger.info("Tick: %s", json.dumps(result, default=str))
                if result.get("halted"):
                    logger.warning("Halted: %s", result.get("halt_reason"))
                    return 2
            else:
                logger.info("Outside market hours — waiting")

            if args.once:
                return 0

            time.sleep(max(30, args.interval))
    except KeyboardInterrupt:
        logger.info("Interrupted — stopping session")
        stop_session()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
