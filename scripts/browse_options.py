#!/usr/bin/env python3
"""Quick options chain browse (no full research pipeline)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tradingagents"))


def _load_trade_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    import os

    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_trade_env()

import trade_integrations  # noqa: F401

from trade_integrations.openalgo.market_data import (
    fetch_option_chain,
    fetch_option_expiry_dates,
)
from trade_integrations.openalgo.symbols import normalize_openalgo_expiry
from trade_integrations.dataflows.options_research.browse_summary import (
    build_browse_summary,
    format_browse_markdown,
)
from trade_integrations.dataflows.options_research.market import resolve_options_instrument


def main() -> int:
    parser = argparse.ArgumentParser(description="Browse live India options chain")
    parser.add_argument("ticker", help="Underlying, e.g. NIFTY or RELIANCE")
    parser.add_argument("--expiry", help="Expiry DDMMMYY (default: nearest from broker)")
    parser.add_argument("--strikes", type=int, default=10, help="Strikes around ATM")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    args = parser.parse_args()

    instrument = resolve_options_instrument(args.ticker)
    expiries = fetch_option_expiry_dates(
        instrument.underlying_symbol,
        instrument.options_exchange,
    )
    expiry = normalize_openalgo_expiry(args.expiry) if args.expiry else None
    if not expiry and expiries:
        expiry = normalize_openalgo_expiry(expiries[0])
    chain = fetch_option_chain(
        instrument.underlying_symbol,
        instrument.underlying_exchange,
        expiry_date=expiry,
        strike_count=args.strikes,
    )
    chain["expiries"] = [normalize_openalgo_expiry(e) for e in expiries]
    summary = build_browse_summary(chain)

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(format_browse_markdown(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
