#!/usr/bin/env python3
"""Remove deprecated llm-wiki layout after Hub News → raw/sources/news cutover."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove legacy llm-wiki/wiki/events and sources/ trees")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env
    from trade_integrations.dataflows.hub_wiki.bootstrap import cleanup_legacy_wiki_artifacts

    load_trade_env()
    result = cleanup_legacy_wiki_artifacts(dry_run=args.dry_run)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
