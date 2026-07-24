"""CLI entrypoints for bash stack — single Python authority for Nautilus lifecycle."""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nautilus watch lifecycle (stack authority)")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("stack-start", help="Start or adopt Nautilus watch under lifecycle lock")
    start.add_argument("--skip-adopt", action="store_true")
    start.add_argument("--agent-id", default="")

    sub.add_parser("stack-purge", help="Purge all Nautilus watch processes; exit 1 if survivors remain")

    sub.add_parser(
        "stack-reconcile-claim",
        help="Align pidfile, registry node_pid, and stack claim with live watch process",
    )

    args = parser.parse_args(argv)

    if args.command == "stack-start":
        from trade_integrations.autonomous_agents.nautilus_watch import run_stack_nautilus_start

        agent_id = str(args.agent_id or "").strip() or None
        result = run_stack_nautilus_start(agent_id=agent_id, skip_adopt=bool(args.skip_adopt))
        status = str(result.get("status") or "")
        if status == "ok":
            pid = result.get("pid")
            if result.get("adopted"):
                print(f"[stack] Nautilus watch already running (pid {pid}, registry mode)")
            else:
                print("[stack] starting Nautilus watch node ...")
                print(f"[stack] Nautilus watch running (pid {pid})")
            return 0
        if status == "skipped":
            reason = str(result.get("reason") or "")
            if reason == "no_agents":
                print("[stack] skip Nautilus watch — no agents in registry yet")
            elif reason == "disabled":
                print("[stack] NAUTILUS_WATCH_ENABLE=0 — skip Nautilus watch node")
            return 0
        print(json.dumps(result), file=sys.stderr)
        if result.get("reason") == "purge_incomplete":
            return 2
        return 1

    if args.command == "stack-purge":
        from trade_integrations.autonomous_agents.nautilus_watch import purge_nautilus_watch_processes

        result = purge_nautilus_watch_processes()
        if result.get("survivors"):
            print(
                f"[stack] Nautilus purge incomplete — survivors: {result.get('survivors')}",
                file=sys.stderr,
            )
            return 1
        return 0

    if args.command == "stack-reconcile-claim":
        from trade_integrations.autonomous_agents.nautilus_watch import reconcile_nautilus_service_claim

        result = reconcile_nautilus_service_claim()
        pid = result.get("pid")
        if result.get("status") == "ok" and pid:
            print(f"[stack] Nautilus watch claim reconciled (pid {pid}, source={result.get('source')})")
            return 0
        if result.get("status") == "error":
            print(json.dumps(result), file=sys.stderr)
            return 1
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
