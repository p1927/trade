#!/usr/bin/env python3
"""Scan reports/hub and write _data/manifest.json (data lake inventory)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def _load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Hub data lake inventory")
    parser.add_argument("--write", action="store_true", help="Write _data/manifest.json")
    parser.add_argument("--json", action="store_true", help="Print full manifest JSON")
    args = parser.parse_args()

    _load_env()
    from trade_integrations.context.hub import get_hub_dir
    from trade_integrations.hub_analytics.manifest import build_manifest, write_hub_manifest

    if args.write:
        result = write_hub_manifest(sync_executions=True)
        print(f"Wrote {result['path']}")
        manifest = build_manifest(get_hub_dir())
    else:
        manifest = build_manifest(get_hub_dir())

    if args.json or not args.write:
        print(json.dumps(manifest, indent=2, default=str))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
