#!/usr/bin/env python3
"""One-time: migrate legacy orchestrator.json active session into draft agents."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "integrations") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate orchestrator.json orphan sessions to draft agents")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Run migration (default is dry-run status only)",
    )
    args = parser.parse_args()

    from trade_integrations.env import load_trade_env

    load_trade_env()

    active_sid = None
    try:
        from trade_integrations.autonomous_agents.store import get_active_orchestrator_session_id

        active_sid = get_active_orchestrator_session_id()
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1

    if not active_sid:
        print(json.dumps({"status": "ok", "backfilled": None, "message": "no active orchestrator session"}))
        return 0

    if not args.apply:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "active_orchestrator_session_id": active_sid,
                    "message": "pass --apply to migrate orphan into draft agent",
                },
                indent=2,
            )
        )
        return 0

    try:
        agent_src = ROOT / "vibetrading" / "agent"
        if str(agent_src) not in sys.path:
            sys.path.insert(0, str(agent_src))
        from src.api.autonomous_routes import _session_service

        session_service = _session_service()
    except Exception:
        session_service = None

    if session_service is None:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "session runtime unavailable — start API or pass session service manually",
                }
            )
        )
        return 2

    from trade_integrations.autonomous_agents.store import backfill_orphan_orchestrator_session

    result = backfill_orphan_orchestrator_session(session_service=session_service)
    if not result:
        print(
            json.dumps(
                {
                    "status": "skipped",
                    "backfilled": None,
                    "message": "no orphan orchestrator session to migrate",
                },
                indent=2,
            )
        )
        return 0
    print(json.dumps({"status": "ok", "backfilled": result}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
