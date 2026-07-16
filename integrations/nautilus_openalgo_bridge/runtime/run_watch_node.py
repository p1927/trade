"""CLI entry: Nautilus TradingNode watch (default) or legacy poll loop."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

INTEGRATIONS = Path(__file__).resolve().parents[2]
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenAlgo → Nautilus TradingNode watch bridge")
    parser.add_argument("--agent-id", default=None, help="Autonomous agent id (aa_…) ")
    parser.add_argument("--once", action="store_true", help="Single poll then exit (legacy poll only)")
    parser.add_argument("--trigger-vibe", action="store_true", help="Dispatch Vibe turn on alert")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Poll OpenAlgo once and print JSON (legacy poll; no NAUTILUS_WATCH_ENABLE required)",
    )
    parser.add_argument(
        "--legacy-poll",
        action="store_true",
        help="Use Python poll loop instead of Nautilus TradingNode",
    )
    parser.add_argument("--no-process-intents", action="store_true", help="Skip intent queue processing")
    args = parser.parse_args(argv)

    trigger = args.trigger_vibe or bool(args.agent_id)

    if args.dry_run or args.legacy_poll or args.once:
        from nautilus_openalgo_bridge.runtime.poll_loop import run_poll_loop

        return run_poll_loop(
            agent_id=args.agent_id,
            once=args.once or args.dry_run,
            trigger_vibe=trigger and not args.dry_run,
            dry_run=args.dry_run,
            process_intents=not args.no_process_intents,
        )

    from nautilus_openalgo_bridge.node import NAUTILUS_AVAILABLE, nautilus_import_error, run_trading_node

    if not NAUTILUS_AVAILABLE:
        logging.basicConfig(level=logging.WARNING)
        logging.warning(
            "nautilus_trader unavailable (%s) — falling back to legacy poll loop",
            nautilus_import_error(),
        )
        from nautilus_openalgo_bridge.runtime.poll_loop import run_poll_loop

        return run_poll_loop(
            agent_id=args.agent_id,
            once=False,
            trigger_vibe=trigger,
            dry_run=False,
            process_intents=not args.no_process_intents,
        )

    return run_trading_node(
        agent_id=args.agent_id,
        trigger_vibe=trigger,
    )


if __name__ == "__main__":
    raise SystemExit(main())
