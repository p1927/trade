#!/usr/bin/env python3
"""Remove legacy JSON sidecars from raw/sources/news/ and raw/sources/research/."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge JSON sidecars from LLM-Wiki raw source dirs")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env
    from trade_integrations.dataflows.hub_wiki.compile import purge_json_wiki_sidecars

    load_trade_env()
    result = purge_json_wiki_sidecars(dry_run=args.dry_run)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
