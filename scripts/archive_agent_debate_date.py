#!/usr/bin/env python3
"""Copy agent debate JSON into dated history/ archive (no TradingAgents re-run)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from trade_integrations.context.hub import get_hub_dir, save_agent_debate  # noqa: E402


def _debate_dir(ticker: str) -> Path:
    return get_hub_dir() / ticker.strip().upper() / "agent_debate"


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive agent debate payload under history/{date}.json")
    parser.add_argument("--ticker", default="NIFTY")
    parser.add_argument("--date", required=True, help="ISO date for history key (YYYY-MM-DD)")
    parser.add_argument(
        "--from-file",
        help="JSON file to archive (default: hub agent_debate/latest.json)",
    )
    args = parser.parse_args()

    ticker = args.ticker.strip().upper()
    day = args.date.strip()[:10]
    src = Path(args.from_file) if args.from_file else _debate_dir(ticker) / "latest.json"
    if not src.is_file():
        print(f"Source not found: {src}", file=sys.stderr)
        return 1

    payload = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        print("Payload must be a JSON object", file=sys.stderr)
        return 1

    payload = dict(payload)
    payload["as_of"] = day
    payload["date"] = day
    out = save_agent_debate(ticker, payload)
    history_path = out.parent / "history" / f"{day}.json"
    print(f"Archived {ticker} debate → {history_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
