"""CLI entry: Nautilus TradingNode watch (OpenAlgo feed → WatchActor → signals)."""

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
    parser.add_argument(
        "--registry",
        action="store_true",
        help="Load agent list from log/nautilus-watch.agents.json",
    )
    parser.add_argument("--trigger-vibe", action="store_true", help="Dispatch Vibe turn on alert")
    args = parser.parse_args(argv)

    trigger = args.trigger_vibe or bool(args.agent_id) or args.registry

    if args.registry and not args.agent_id:
        try:
            from trade_integrations.autonomous_agents.nautilus_watch import get_registry_agent_ids

            reg_ids = get_registry_agent_ids()
            if not reg_ids:
                logging.basicConfig(level=logging.ERROR)
                logging.error("registry mode but no agents in log/nautilus-watch.agents.json")
                return 1
        except Exception as exc:
            logging.basicConfig(level=logging.ERROR)
            logging.error("failed to load watch registry: %s", exc)
            return 1

    from nautilus_openalgo_bridge.node import NAUTILUS_AVAILABLE, nautilus_import_error, run_trading_node

    if not NAUTILUS_AVAILABLE:
        logging.basicConfig(level=logging.ERROR)
        logging.error(
            "nautilus_trader unavailable (%s) — install .venv-nautilus via ./scripts/setup_nautilus.sh",
            nautilus_import_error(),
        )
        return 1

    return run_trading_node(
        agent_id=args.agent_id,
        trigger_vibe=trigger,
        use_registry=args.registry,
    )


if __name__ == "__main__":
    raise SystemExit(main())
