#!/usr/bin/env python3
"""One-time: migrate agent.watch_spec rows into unified watch registry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy agent watch_spec into watch registry")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create registry watches (default is dry-run)",
    )
    parser.add_argument(
        "--sync-nautilus",
        action="store_true",
        help="After migration, rebuild log/nautilus-watch.agents.json",
    )
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env

    load_trade_env()

    if not args.apply:
        from trade_integrations.autonomous_agents.store import list_agents
        from trade_integrations.watch_registry.store import list_watches, migrate_agent_watch_spec_to_registry

        candidates: list[str] = []
        for agent in list_agents():
            agent_id = str(agent.get("id") or "").strip()
            if not agent_id.startswith("aa_"):
                continue
            existing = list_watches(owner_kind="autonomous_agent", owner_id=agent_id, active_only=True)
            if existing:
                continue
            raw = agent.get("watch_spec") or (agent.get("mandate_config") or {}).get("watch_spec")
            if isinstance(raw, dict) and raw.get("rules"):
                candidates.append(agent_id)
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "would_migrate": candidates,
                    "count": len(candidates),
                    "message": "pass --apply to create registry watches",
                },
                indent=2,
            )
        )
        return 0

    from trade_integrations.watch_registry.store import (
        migrate_all_agent_watch_specs_to_registry,
        sync_nautilus_registry_from_watches,
    )

    summary = migrate_all_agent_watch_specs_to_registry()
    if args.sync_nautilus:
        summary["nautilus_sync"] = sync_nautilus_registry_from_watches(restart_if_changed=False)
    print(json.dumps({"status": "ok", **summary}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
