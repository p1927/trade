"""CLI: process pending bridge intent queue files."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

INTEGRATIONS = Path(__file__).resolve().parents[2]
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

from nautilus_openalgo_bridge.intent_queue import list_pending_intents, process_pending_intents  # noqa: E402

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process pending Nautilus bridge execution intents")
    parser.add_argument("--max", type=int, default=10, help="Max intents to process")
    parser.add_argument("--list", action="store_true", help="List pending intents only")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.list:
        pending = list_pending_intents()
        print(json.dumps([str(path.name) for path in pending], indent=2))
        return 0

    results = process_pending_intents(max_count=args.max)
    print(json.dumps(results, indent=2, default=str))
    return 0 if results or not list_pending_intents() else 0


if __name__ == "__main__":
    raise SystemExit(main())
