#!/usr/bin/env python3
"""M1 smoke: synthetic watch alert → Vibe dispatch (or dry-run evaluate)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))


def _synthetic_alert(agent_id: str):
    from nautilus_openalgo_bridge.models import BridgeSignal, QuoteSnapshot, WatchAlert, WatchRule

    rule = WatchRule(symbol="NIFTY", metric="spot_move_pct", threshold=0.1, direction="either")
    return WatchAlert(
        signal=BridgeSignal.REVIEW_NEEDED,
        rule=rule,
        symbol="NIFTY",
        message="M1 smoke: NIFTY moved +0.75% (threshold 0.1%)",
        ltp=24500.0,
        move_pct=0.75,
    )


def _dry_run_evaluate(agent_id: str) -> dict:
    from nautilus_openalgo_bridge.models import QuoteSnapshot, WatchSpec
    from nautilus_openalgo_bridge.runtime.poll_loop import run_once

    baselines = {"NIFTY": 24300.0}
    quotes = {
        "NIFTY": QuoteSnapshot(symbol="NIFTY", ltp=24500.0, exchange="NSE_INDEX", fetched_at=time.time()),
    }
    from nautilus_openalgo_bridge.handoff import sync_watch_spec_to_handoff
    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    agent = get_agent(agent_id)
    if agent:
        sync_watch_spec_to_handoff(
            agent_id,
            {
                "rules": [{"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 0.1, "direction": "either"}],
                "gate": {"skip_if_unchanged_minutes": 0},
            },
        )

    from unittest.mock import patch

    with patch("nautilus_openalgo_bridge.runtime.poll_loop.OpenAlgoQuoteFeed") as feed_cls:
        feed_cls.return_value.poll.return_value = quotes
        result = run_once(
            agent_id=agent_id,
            baselines=baselines,
            trigger_vibe=False,
            process_intents=False,
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="M1 Nautilus watch alert smoke")
    parser.add_argument("--agent-id", required=True, help="Autonomous agent id (aa_*)")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate alert only; do not POST to Vibe")
    parser.add_argument("--mock-vibe", action="store_true", help="Mock Vibe HTTP; verify dispatch path only")
    args = parser.parse_args()

    agent_id = args.agent_id.strip()
    os.environ.setdefault("NAUTILUS_BRIDGE_ALERT_OUTSIDE_HOURS", "true")

    from trade_integrations.autonomous_agents.store import get_agent

    agent = get_agent(agent_id)
    if not agent:
        print(f"FAIL: agent not found: {agent_id}", file=sys.stderr)
        return 1

    if args.dry_run:
        result = _dry_run_evaluate(agent_id)
        alerts = result.get("alerts") or []
        print(json.dumps({"mode": "dry_run", "alerts": alerts}, indent=2))
        if not alerts:
            print("FAIL: no alerts fired — check watch_spec / baselines", file=sys.stderr)
            return 1
        print("PASS: watch evaluation fired alert(s)")
        return 0

    alert = _synthetic_alert(agent_id)
    quotes = {
        "NIFTY": type("Q", (), {"ltp": 24500.0, "exchange": "NSE_INDEX", "fetched_at": time.time(), "to_dict": lambda self: {"ltp": 24500.0}})(),
    }

    if args.mock_vibe:
        from unittest.mock import AsyncMock, patch

        async def _fake_call(session_id: str, content: str) -> dict:
            return {"status": "ok", "session_id": session_id, "preview": content[:120]}

        with patch("nautilus_openalgo_bridge.vibe_trigger.make_vibe_message_client", return_value=_fake_call):
            from nautilus_openalgo_bridge.vibe_trigger import dispatch_watch_alert_sync

            result = dispatch_watch_alert_sync(agent_id, alert, quotes=None)
        print(json.dumps({"mode": "mock_vibe", "result": result}, indent=2, default=str))
        if result.get("status") != "dispatched":
            print(f"FAIL: dispatch status={result.get('status')} reason={result.get('reason') or result.get('error')}", file=sys.stderr)
            return 1
        print("PASS: alert dispatched (mock Vibe)")
        return 0

    from nautilus_openalgo_bridge.vibe_trigger import dispatch_watch_alert_sync, ping_vibe_backend

    vibe = ping_vibe_backend()
    if vibe.get("status") == "unreachable":
        print(f"FAIL: Vibe unreachable — {vibe}", file=sys.stderr)
        return 1

    result = dispatch_watch_alert_sync(agent_id, alert, quotes=None)
    print(json.dumps({"mode": "live_vibe", "vibe_probe": vibe, "result": result}, indent=2, default=str))
    if result.get("status") not in {"dispatched", "skipped"}:
        print(f"FAIL: {result}", file=sys.stderr)
        return 1
    if result.get("status") == "skipped" and result.get("reason") == "turn_in_flight":
        print("WARN: turn already in flight — alert path reachable but skipped")
        return 0
    print("PASS: alert dispatched to Vibe session")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
