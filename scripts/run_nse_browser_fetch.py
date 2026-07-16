#!/usr/bin/env python3
"""Run NSE/NSDL browser fetch missions (nodriver + optional agent fallback)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import trade_integrations  # noqa: F401

from trade_integrations.nse_browser.chrome_bootstrap import ensure_chrome_or_warn
from trade_integrations.nse_browser.missions import list_missions, run_all_missions, run_mission


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch NSE/NSDL data via nodriver browser module")
    parser.add_argument(
        "--mission",
        choices=["fii_dii_history", "fpi_nsdl", "market_archives", "all"],
        default="fii_dii_history",
        help="Which mission to run (default: fii_dii_history)",
    )
    parser.add_argument(
        "--refresh-cookies",
        action="store_true",
        help="Bootstrap a fresh nodriver session and persist cookies",
    )
    parser.add_argument(
        "--agent-fallback",
        action="store_true",
        help="Use MiniMax M3 agent when deterministic fetch fails",
    )
    parser.add_argument(
        "--install-chrome",
        action="store_true",
        help="Ensure Google Chrome is installed before running missions",
    )
    parser.add_argument("--list", action="store_true", help="List available missions and exit")
    args = parser.parse_args()

    if args.install_chrome:
        from trade_integrations.nse_browser.chrome_bootstrap import ensure_chrome

        path = ensure_chrome(auto_install=True)
        print(json.dumps({"chrome": path}))
    else:
        ensure_chrome_or_warn()

    if args.list:
        print(json.dumps(list_missions(), indent=2))
        return 0

    if args.mission == "all":
        summary = run_all_missions(
            refresh_cookies=args.refresh_cookies,
            agent_fallback=args.agent_fallback,
        )
    else:
        summary = run_mission(
            args.mission,
            refresh_cookies=args.refresh_cookies,
            agent_fallback=args.agent_fallback,
        )
    print(json.dumps(summary, indent=2, default=str))
    status = summary.get("status") if isinstance(summary, dict) else "error"
    if args.mission == "all":
        return 0 if summary.get("ok_count", 0) > 0 else 1
    return 0 if status in {"ok", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
