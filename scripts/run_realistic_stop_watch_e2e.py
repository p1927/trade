#!/usr/bin/env python3
"""Scenario B: mechanical portfolio + spot stop → flatten.

IN: OpenAlgo options straddle + bridge EXIT
US: Alpaca SPY shares + spot stop → Alpaca close
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import realistic_e2e_lib as lib  # noqa: E402

lib.load_env()

FAILURES: list[str] = []


def _log(step: str, detail: str = "", *, ok: bool = True) -> None:
    mark = "✓" if ok else "✗"
    msg = f"  {mark} {step}"
    if detail:
        msg += f" — {detail}"
    print(msg, flush=True)


def _fail(step: str, detail: str) -> None:
    FAILURES.append(f"{step}: {detail}")
    _log(step, detail, ok=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Mechanical stop-watch realistic E2E")
    parser.add_argument("--metric-delay-sec", type=int, default=20)
    parser.add_argument("--market", choices=("in", "us"), default=os.getenv("REALISTIC_E2E_MARKET", "us"))
    parser.add_argument("--symbol", default=os.getenv("REALISTIC_E2E_SYMBOL", "SPY"))
    args = parser.parse_args()

    fail: Callable[[str, str], None] = _fail
    e2e = lib.E2EMarket(mode=args.market, symbol=args.symbol.upper())
    sym = e2e.symbol
    strategy = "realistic_stop_e2e"

    label = f"US {sym} Alpaca" if e2e.is_us else f"IN {sym} OpenAlgo"
    print("══════════════════════════════════════════════════════════", flush=True)
    print(f"  Scenario B ({label}) — entry → spot stop → flatten", flush=True)
    print("══════════════════════════════════════════════════════════", flush=True)

    stack = lib.require_stack_ready(fail=fail, market=e2e)
    if stack is None:
        return 1
    _log("stack preflight", f"ready ({label})")

    agent_id, _session_id = lib.create_paper_agent(
        name=f"Realistic stop-watch E2E ({sym})",
        mandate=f"Paper {sym}; honor stop alerts and flatten when breached.",
        symbols=[sym],
    )
    _log("agent", agent_id)

    watch_proc = None
    if not e2e.is_us and lib.VENV_NAUTILUS.is_file():
        watch_proc = lib.start_watch_node(agent_id)
        _log("nautilus watch", f"pid={watch_proc.pid}")

    if e2e.is_us:
        from trade_integrations.dataflows.alpaca import fetch_alpaca_quote, list_alpaca_positions

        entry_ltp = lib.mechanical_us_entry(sym, orders=2, qty_each=1.0)
        lib.mechanical_us_partial_exit(sym, qty=1.0)
        qty = lib.alpaca_spy_qty(list_alpaca_positions(), sym)
        _log("after partial exit", f"{sym} qty={qty}")
        if qty < 1:
            _fail("partial exit", f"expected ≥1 share of {sym}")
            return 1
    else:
        from nautilus_openalgo_bridge.reconcile import open_positions_from_book

        client = stack
        legs, spot = lib.mechanical_straddle_entry(agent_id, client, strategy=strategy)
        lib.mechanical_partial_exit(agent_id, client, legs, strategy=strategy)
        positions = open_positions_from_book(client.get_position_book())
        _log("after partial exit", f"open_positions={len(positions)}")
        if not positions:
            _fail("partial exit", "expected at least one open leg")
            return 1
        entry_ltp = spot

    print(f"\n── Wait {args.metric_delay_sec}s then arm spot stop ──", flush=True)
    for remaining in range(args.metric_delay_sec, 0, -5):
        print(f"    … {remaining}s", flush=True)
        time.sleep(min(5, remaining))

    from nautilus_openalgo_bridge.handoff import load_handoff, save_handoff
    from nautilus_openalgo_bridge.models import PositionHandoff, QuoteSnapshot, StopRules
    from nautilus_openalgo_bridge.stop_eval import evaluate_stop_rules

    if e2e.is_us:
        from trade_integrations.dataflows.alpaca import fetch_alpaca_quote

        ltp = float((fetch_alpaca_quote(sym) or {}).get("ltp") or entry_ltp)
        handoff = PositionHandoff(
            agent_id=agent_id,
            widget_id=None,
            underlying=sym,
            legs=[],
            entry_spot=ltp * 1.002,
            stop_rules=StopRules(max_loss_inr=50_000, spot_stop_pct=0.001, flatten_at_close=False),
        )
        save_handoff(handoff)
        quote = QuoteSnapshot(symbol=sym, exchange="US", ltp=ltp)
        stop_alert = evaluate_stop_rules(handoff, {sym: quote}, unrealized_pnl_inr=0.0)
    else:
        from nautilus_openalgo_bridge.reconcile import open_positions_from_book
        from nautilus_openalgo_bridge.runtime.poll_loop import run_once
        from nautilus_openalgo_bridge.signal_actions import dispatch_exit_intent

        client = stack
        nifty_ltp = float(client.get_quote("NIFTY", exchange="NSE_INDEX").get("ltp") or entry_ltp)
        handoff = load_handoff(agent_id) or PositionHandoff(
            agent_id=agent_id, underlying="NIFTY", legs=legs, entry_spot=entry_ltp
        )
        handoff.entry_spot = nifty_ltp * 1.002
        handoff.stop_rules = StopRules(max_loss_inr=50_000, spot_stop_pct=0.001, flatten_at_close=False)
        save_handoff(handoff)
        quote = QuoteSnapshot(symbol="NIFTY", exchange="NSE", ltp=nifty_ltp)
        stop_alert = evaluate_stop_rules(handoff, {"NIFTY": quote}, unrealized_pnl_inr=0.0)

    if stop_alert is None:
        _fail("stop eval", "expected EXIT_NOW alert on armed handoff")
    else:
        _log("stop alert", stop_alert.message[:80])

    print("\n── Flatten on stop ──", flush=True)
    if e2e.is_us:
        if not lib.verified_flatten_us(sym, fail=fail):
            return 1
        _log("flatten verified", f"0 {sym} shares")
    else:
        tick = run_once(agent_id=agent_id, trigger_vibe=False, process_intents=True)
        _log("watch tick", f"alerts={len(tick.get('alerts') or [])}")
        if stop_alert:
            from nautilus_openalgo_bridge.signal_actions import dispatch_exit_intent

            exit_result = dispatch_exit_intent(agent_id, stop_alert, underlying="NIFTY")
            _log("EXIT dispatch", str(exit_result.get("status")))
        lib.verified_flatten(agent_id, client, fail=fail, strategy=strategy, market=e2e)

    lib.stop_watch_node(watch_proc)
    lib.stop_agent(agent_id)
    _log("stop agent", agent_id)

    print("\n══════════════════════════════════════════════════════════", flush=True)
    if FAILURES:
        for item in FAILURES:
            print(f"  ✗ {item}", flush=True)
        return 1
    print("  Scenario B OK", flush=True)
    print("══════════════════════════════════════════════════════════", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
