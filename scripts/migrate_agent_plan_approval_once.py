#!/usr/bin/env python3
"""One-time: backfill plan approval widget fields on all hub autonomous agents."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill plan approval fields on autonomous agents")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist backfills (default is dry-run count only)",
    )
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env
    from trade_integrations.autonomous_agents.plan_approval import (
        ensure_plan_approval_record,
        normalize_legacy_plan_approval,
    )
    from trade_integrations.autonomous_agents.store import list_agents, load_agent

    load_trade_env()

    updated: list[str] = []
    for agent in list_agents():
        agent_id = str(agent.get("id") or "").strip()
        if not agent_id:
            continue
        raw = load_agent(agent_id)
        if not raw:
            continue
        _, changed = normalize_legacy_plan_approval(raw)
        if not changed:
            continue
        updated.append(agent_id)
        if args.apply:
            ensure_plan_approval_record(raw, persist=True)

    payload = {"status": "ok" if args.apply else "dry_run", "updated": updated, "count": len(updated)}
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
