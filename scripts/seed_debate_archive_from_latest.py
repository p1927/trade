#!/usr/bin/env python3
"""Seed debate history from latest.json for walk-forward dev (no agent re-run)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from trade_integrations.context.hub import get_hub_dir, save_agent_debate  # noqa: E402


def _debate_latest_path(ticker: str) -> Path:
    return get_hub_dir() / ticker.strip().upper() / "agent_debate" / "latest.json"


def seed_debate_archive_from_latest(
    *,
    ticker: str,
    days: int,
    step: int = 1,
    from_file: Path | None = None,
) -> dict[str, int | str]:
    """Copy latest debate payload onto synthetic trading dates going backward."""
    src = from_file or _debate_latest_path(ticker)
    if not src.is_file():
        raise FileNotFoundError(f"Source not found: {src}")

    payload = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a JSON object")

    ticker = ticker.strip().upper()
    step = max(1, int(step))
    count = max(1, int(days))
    written = 0
    day = date.today()
    for _ in range(count):
        copy = dict(payload)
        iso = day.isoformat()
        copy["as_of"] = iso
        copy["date"] = iso
        copy["seeded_from_latest"] = True
        save_agent_debate(ticker, copy)
        written += 1
        day -= timedelta(days=step)

    return {"ticker": ticker, "written": written, "source": str(src)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed debate archive dates from latest.json")
    parser.add_argument("--ticker", default="NIFTY")
    parser.add_argument("--days", type=int, default=60, help="Number of archive dates to write")
    parser.add_argument("--step", type=int, default=1, help="Calendar days between archive dates")
    parser.add_argument("--from-file", help="Optional JSON source (default hub latest.json)")
    args = parser.parse_args()

    try:
        result = seed_debate_archive_from_latest(
            ticker=args.ticker,
            days=args.days,
            step=args.step,
            from_file=Path(args.from_file) if args.from_file else None,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
