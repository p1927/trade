#!/usr/bin/env python3
"""Autonomous India options paper trading agent (Vibe + OpenAlgo).

This is the recommended entry point for a full trading agent — not a rules-only bot.
It mirrors Vibe LiveRunner: persistent loop, agent turns with MCP tools, lifecycle + Plan B.

Requires:
  - OpenAlgo sandbox running (./start.sh --openalgo-only or full stack)
  - Vibe backend for LLM agent turns (./start.sh or --vibe-url http://127.0.0.1:8899)
  - Without Vibe, falls back to deterministic hub-ranked entries each tick.

Usage:
  python scripts/run_paper_trading_agent.py --ticker NIFTY --budget 20000
  python scripts/run_paper_trading_agent.py --once
  python scripts/run_paper_trading_agent.py --vibe-url http://127.0.0.1:8899
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if INTEGRATIONS.is_dir() and str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from trade_integrations.auto_paper.config import get_auto_paper_config
from trade_integrations.auto_paper.engine import is_market_session_open
from trade_integrations.auto_paper.mcp_actions import start_auto_paper, stop_auto_paper
from trade_integrations.auto_paper.openalgo_client import OpenAlgoClient
from trade_integrations.auto_paper.runner import ensure_vibe_session, resolve_runner
from trade_integrations.auto_paper.session_store import load_session, stop_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("paper_trading_agent")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autonomous paper trading agent (India options)")
    parser.add_argument("--ticker", default="NIFTY", help="Primary underlying")
    parser.add_argument("--budget", type=float, default=None, help="Paper budget INR")
    parser.add_argument("--watchlist", type=str, default=None, help="Comma-separated underlyings")
    parser.add_argument(
        "--vibe-url",
        default=os.getenv("VIBE_BACKEND_URL", f"http://127.0.0.1:{os.getenv('VIBE_BACKEND_PORT', '8899')}"),
        help="Vibe API base URL for agent turns",
    )
    parser.add_argument("--interval", type=int, default=None, help="Poll interval seconds")
    parser.add_argument("--once", action="store_true", help="Single agent turn then exit")
    parser.add_argument("--stop", action="store_true", help="Stop session and exit")
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="Create Vibe UI session and inject kickoff prompt (recommended)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume active session in Vibe UI (fresh attempt, continuity in prompt)",
    )
    parser.add_argument("--no-agent", action="store_true", help="Deterministic mode only (no Vibe)")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    if args.stop:
        stop_auto_paper()
        stop_session()
        logger.info("Paper trading agent stopped")
        return 0

    cfg = get_auto_paper_config()
    budget = args.budget if args.budget is not None else cfg.budget_inr
    watchlist = (
        [s.strip().upper() for s in args.watchlist.split(",") if s.strip()]
        if args.watchlist
        else list(cfg.watchlist)
    )
    ticker = args.ticker.strip().upper()
    if ticker not in watchlist:
        watchlist.insert(0, ticker)

    if args.resume or args.bootstrap:
        import urllib.error
        import urllib.request

        vibe_url = args.vibe_url.rstrip("/")
        endpoint = "/trade/auto-paper/resume" if args.resume else "/trade/auto-paper/bootstrap"
        payload = {
            "ticker": ticker,
            "budget_inr": budget,
            "watchlist": watchlist,
            "dispatch": True,
            "fresh_session": args.resume,
        }
        if not args.resume:
            payload["prompt"] = None
        req = urllib.request.Request(
            f"{vibe_url}{endpoint}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            logger.info("Bootstrap/resume: %s", json.dumps(result, default=str)[:2000])
            ui = result.get("ui_url")
            if ui:
                logger.info("Open in Vibe UI: %s", ui)
            if args.once:
                return 0
        except urllib.error.HTTPError as exc:
            logger.error("Bootstrap/resume failed: %s", exc.read().decode())
            return 1

    if args.resume and args.once:
        return 0

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

    start_auto_paper(ticker=ticker, budget_inr=budget, watchlist=watchlist, agent_mode=True)
    logger.info("Paper agent session started — %s, budget ₹%.0f", ticker, budget)

    vibe_url = None if args.no_agent else args.vibe_url
    poll_ms = (args.interval or int(cfg.poll_interval_ms / 1000)) * 1000

    if vibe_url and not args.no_agent:
        try:
            sid = ensure_vibe_session(ticker=ticker, base_url=vibe_url)
            logger.info("Vibe agent session: %s (%s)", sid, vibe_url)
        except RuntimeError as exc:
            logger.warning("Vibe unavailable (%s) — will use deterministic fallback", exc)

    runner = resolve_runner(vibe_url=vibe_url, poll_ms=poll_ms)

    if args.once:
        result = await runner.run_once()
        logger.info("Turn: %s", json.dumps(result.to_dict(), default=str))
        return 0 if result.outcome not in {"halted", "error", "reconcile_unsafe"} else 2

    while True:
        session = load_session()
        if not session.get("enabled"):
            break
        if session.get("halted"):
            logger.warning("Halted: %s", session.get("halt_reason"))
            return 2

        if is_market_session_open(cfg):
            result = await runner.run_once()
            logger.info("Turn: %s (%s)", result.outcome, result.reason or "ok")
            if result.outcome in {"halted", "reconcile_unsafe"}:
                return 2
        else:
            logger.info("Outside market hours — waiting")

        await asyncio.sleep(max(30, poll_ms / 1000.0))


def main() -> int:
    args = _parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        logger.info("Interrupted")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
