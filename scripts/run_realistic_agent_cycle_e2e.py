#!/usr/bin/env python3
"""Realistic autonomous cycle E2E (paper only).

Uses production autonomous turn prompts (build_full_reasoning_prompt) — no custom
E2E user-message overrides that trigger injection false-positives.

Flow:
  1. Research turn (analysis)
  2. Research turn with harness hint — agent enters paper position
  3. Arm watch rules
  4. Bridge alert → strategy revision turn
  5. Strategy revision turn — agent closes position

Usage:
  REALISTIC_E2E_MARKET=us REALISTIC_E2E_SYMBOL=SPY python3 scripts/run_realistic_agent_cycle_e2e.py
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
VENV_NAUTILUS = ROOT / ".venv-nautilus" / "bin" / "python"

if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

env_file = ROOT / ".env"
if env_file.is_file():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        val = value.strip().strip('"').strip("'")
        if val:
            os.environ[key.strip()] = val

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


def count_bridge_alert_messages(session_id: str) -> int:
    sys.path.insert(0, str(ROOT / "scripts"))
    import realistic_e2e_lib as lib

    messages = lib.vibe_get(f"/sessions/{session_id}/messages?limit=100")
    if not isinstance(messages, list):
        return 0
    return sum(
        1
        for m in messages
        if isinstance(m, dict)
        and (
            "Nautilus watch alert" in str(m.get("content") or "")
            or "Watch alert (US Alpaca)" in str(m.get("content") or "")
        )
    )


def _run_production_turn(
    lib: Any,
    *,
    agent_id: str,
    phase: str,
    turn_kind: str,
    timeout_sec: int,
) -> str:
    lib.wait_for_agent_idle(agent_id, timeout_sec=min(timeout_sec, 180))
    _, msg = lib.dispatch_production_turn(agent_id, turn_kind=turn_kind, timeout_sec=timeout_sec)
    content = str(msg.get("content") or "")
    preview = content[:100].replace("\n", " ")
    status = str((msg.get("metadata") or {}).get("status", "?"))
    _log(f"{phase} turn", f"{status} — {preview}")
    refusal = lib.classify_refusal(content)
    lib.log_e2e_turn_result(
        phase=phase,
        agent_id=agent_id,
        turn_kind=turn_kind,
        assistant_text=content,
        outcome="refusal" if refusal else "completed",
    )
    return content


def main() -> int:
    parser = argparse.ArgumentParser(description="Realistic autonomous agent cycle E2E")
    parser.add_argument("--skip-cleanup", action="store_true")
    parser.add_argument("--skip-flatten", action="store_true")
    parser.add_argument("--allow-mechanical", action="store_true", help="Allow mechanical order fallback (debug only)")
    parser.add_argument("--metric-delay-sec", type=int, default=20)
    parser.add_argument("--turn-timeout", type=int, default=900)
    parser.add_argument("--market", choices=("in", "us"), default=os.getenv("REALISTIC_E2E_MARKET", "us"))
    parser.add_argument("--symbol", default=os.getenv("REALISTIC_E2E_SYMBOL", "SPY"))
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT / "scripts"))
    import realistic_e2e_lib as lib

    lib.load_env()
    e2e_market = lib.E2EMarket(mode=args.market, symbol=args.symbol.upper())
    sym = e2e_market.symbol

    from trade_integrations.autonomous_agents.mcp_actions import mcp_set_watch_spec
    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    if e2e_market.is_us:
        from trade_integrations.dataflows.alpaca import fetch_alpaca_quote, list_alpaca_positions
    else:
        from nautilus_openalgo_bridge.reconcile import open_positions_from_book, sync_handoff_from_position_book

    title = f"US {sym} Alpaca" if e2e_market.is_us else f"IN {sym} OpenAlgo"
    print("══════════════════════════════════════════════════════════", flush=True)
    print(f"  Realistic cycle ({title}) — production turns → watch → react", flush=True)
    print("══════════════════════════════════════════════════════════", flush=True)

    if not args.skip_cleanup:
        print("\n── Cleanup ──", flush=True)
        subprocess.run([sys.executable, str(ROOT / "scripts/cleanup_autonomous_agents.py")], check=False)
    else:
        n = lib.purge_e2e_poisoned_memory()
        if n:
            _log("memory purge", f"removed {n} poisoned file(s)")

    stack = lib.require_stack_ready(fail=_fail, market=e2e_market)
    if stack is None:
        return 1
    _log("stack preflight", f"Alpaca + Vibe OK ({sym})" if e2e_market.is_us else "OpenAlgo + Vibe OK")

    print("\n── Phase 0: Create agent ──", flush=True)
    mandate = (
        f"Paper trade US {sym} via Alpaca. React to watch alerts by buying or selling shares."
        if e2e_market.is_us
        else f"Paper trade {sym} options autonomously. React to watch alerts by adjusting positions."
    )
    agent_id, session_id = lib.create_paper_agent(
        name=f"Realistic cycle E2E ({sym})",
        mandate=mandate,
        symbols=[sym],
    )
    _log("agent", f"{agent_id} session={session_id}")
    if not get_agent(agent_id):
        _fail("agent create", f"agent {agent_id} not in store after commit")
        return 1

    try:
        lib.ensure_agent_plan_approved(agent_id)
        _log("plan approval", "approved via API")
    except Exception as exc:
        _fail("plan approval", str(exc))
        return 1

    print("\n── Phase 1: Research turn (analysis) ──", flush=True)
    try:
        _run_production_turn(
            lib,
            agent_id=agent_id,
            phase="phase1_analysis",
            turn_kind="research",
            timeout_sec=args.turn_timeout,
        )
    except Exception as exc:
        _fail("analysis turn", str(exc))

    print("\n── Phase 2: Research turn (enter position) ──", flush=True)
    alerts_before = count_bridge_alert_messages(session_id)
    try:
        _run_production_turn(
            lib,
            agent_id=agent_id,
            phase="phase2_entry",
            turn_kind="research",
            timeout_sec=args.turn_timeout,
        )
    except Exception as exc:
        _fail("execution turn", str(exc))

    entry_ltp = 0.0
    client = stack if not e2e_market.is_us else None
    legs: list[Any] = []
    mechanical_used = False

    if e2e_market.is_us:
        qty = lib.alpaca_spy_qty(list_alpaca_positions(), sym)
        if qty >= 1:
            _log("alpaca position", f"{sym} qty={qty}")
            q = fetch_alpaca_quote(sym)
            entry_ltp = float((q or {}).get("ltp") or 0)
        elif args.allow_mechanical:
            mechanical_used = True
            _log("alpaca position", "flat — mechanical fallback (debug)", ok=False)
            try:
                entry_ltp = lib.mechanical_us_entry(sym, orders=2, qty_each=1.0)
                lib.mechanical_us_partial_exit(sym, qty=1.0)
                qty = lib.alpaca_spy_qty(list_alpaca_positions(), sym)
                _log("mechanical US entry", f"{sym} qty={qty} ltp={entry_ltp:.2f}")
            except Exception as exc:
                _fail("mechanical US fallback", str(exc))
                return 1
        else:
            _fail("LLM execution", f"no {sym} position after production entry turn")
    else:
        from nautilus_openalgo_bridge.handoff import load_handoff

        positions = open_positions_from_book(client.get_position_book())
        if positions:
            _log("positions after LLM", f"{len(positions)} open")
            handoff = load_handoff(agent_id)
            if handoff:
                legs = list(handoff.legs or [])
                entry_ltp = float(handoff.entry_spot or 0)
        elif args.allow_mechanical:
            _log("positions after LLM", "none — mechanical fallback (debug)", ok=False)
            try:
                legs, entry_ltp = lib.mechanical_straddle_entry(agent_id, client, strategy="realistic_e2e")
                lib.mechanical_partial_exit(agent_id, client, legs, strategy="realistic_e2e")
                positions = open_positions_from_book(client.get_position_book())
                _log("mechanical entry", f"legs={len(legs)} open_positions={len(positions)}")
            except Exception as exc:
                _fail("mechanical fallback", str(exc))
                return 1
        else:
            _fail("LLM execution", "no open positions after production entry turn")

    if mechanical_used:
        _fail("LLM execution", "mechanical fallback used (--allow-mechanical)")

    print(f"\n── Phase 3: Wait {args.metric_delay_sec}s then arm watch metrics ──", flush=True)
    watch_proc: subprocess.Popen[Any] | None = None
    if not e2e_market.is_us and VENV_NAUTILUS.is_file():
        watch_proc = lib.start_watch_node(agent_id)
        _log("nautilus watch", f"started pid={watch_proc.pid}")

    for remaining in range(args.metric_delay_sec, 0, -5):
        print(f"    … {remaining}s until metrics armed", flush=True)
        time.sleep(min(5, remaining))

    if e2e_market.is_us:
        q = fetch_alpaca_quote(sym)
        ltp = float((q or {}).get("ltp") or entry_ltp or 0)
        armed_spec = lib.build_fireable_watch_spec(symbol=sym, ltp=ltp)
        mcp_set_watch_spec(agent_id=agent_id, watch_spec=armed_spec)
        _log(
            "armed watch_spec",
            f"{sym} baseline={ltp * 1.006:.2f} thr={armed_spec['rules'][0]['threshold']}%",
        )
    else:
        from nautilus_openalgo_bridge.reconcile import sync_handoff_from_position_book

        nifty_q2 = client.get_quote("NIFTY", exchange="NSE_INDEX")
        vix_q2 = client.get_quote("INDIAVIX", exchange="NSE_INDEX")
        nifty_ltp = float(nifty_q2.get("ltp") or entry_ltp)
        vix_ltp = float(vix_q2.get("ltp") or 12.0)
        armed_spec = lib.build_fireable_watch_spec(symbol="NIFTY", ltp=nifty_ltp, vix_ltp=vix_ltp)
        mcp_set_watch_spec(agent_id=agent_id, watch_spec=armed_spec)
        sync_handoff_from_position_book(agent_id, client=client, underlying="NIFTY")
        _log("armed watch_spec", f"NIFTY baseline={nifty_ltp * 1.0002:.2f} thr={armed_spec['rules'][0]['threshold']}%")

    print("\n── Phase 4: Watch alert → revision turn ──", flush=True)
    agent = get_agent(agent_id) or {}
    agent["streaming"] = False
    save_agent(agent)

    if e2e_market.is_us:
        q = fetch_alpaca_quote(sym)
        ltp = float((q or {}).get("ltp") or entry_ltp)
        result = lib.dispatch_us_watch_alert(
            agent_id,
            symbol=sym,
            ltp=ltp,
            message=f"{sym} spot move exceeded watch threshold",
        )
        _log("bridge dispatch", str(result.get("status")))
        if result.get("status") == "skipped":
            _fail("bridge dispatch", str(result.get("reason") or "skipped"))
    else:
        from nautilus_openalgo_bridge.runtime.poll_loop import run_once

        tick = run_once(agent_id=agent_id, trigger_vibe=True, process_intents=True)
        _log("watch tick", f"alerts={len(tick.get('alerts') or [])} dispatches={len(tick.get('dispatches') or [])}")

    deadline = time.time() + 600
    while time.time() < deadline:
        if count_bridge_alert_messages(session_id) > alerts_before:
            _log("bridge alert in session", "received")
            break
        if not (get_agent(agent_id) or {}).get("streaming"):
            time.sleep(3)
            if count_bridge_alert_messages(session_id) > alerts_before:
                break
        time.sleep(5)
    else:
        _fail("alert revision", "no bridge alert message in session within 10m")

    lib.wait_for_agent_idle(agent_id, timeout_sec=600)
    _log("agent revision", "idle after alert")

    if e2e_market.is_us and not args.skip_flatten:
        print("\n── Phase 5: Revision turn (close position) ──", flush=True)
        qty_open = lib.alpaca_spy_qty(list_alpaca_positions(), sym)
        if qty_open >= 1:
            try:
                _run_production_turn(
                    lib,
                    agent_id=agent_id,
                    phase="phase5_exit",
                    turn_kind="strategy_revision",
                    timeout_sec=args.turn_timeout,
                )
                qty_after = lib.alpaca_spy_qty(list_alpaca_positions(), sym)
                if qty_after >= 1:
                    _fail("agent exit", f"still holding {qty_after} {sym} after revision turn")
                else:
                    _log("agent exit", f"flat — LLM closed {sym}")
            except Exception as exc:
                _fail("exit turn", str(exc))
        else:
            _log("agent exit", "already flat before phase 5")

    if watch_proc and watch_proc.poll() is None:
        lib.stop_watch_node(watch_proc)

    if not args.skip_flatten:
        print("\n── Cleanup flatten (verified) ──", flush=True)
        if lib.verified_flatten(agent_id, client, fail=_fail, strategy="realistic_e2e", market=e2e_market):
            _log("flatten verified", "0 open positions")

    lib.stop_agent(agent_id)
    _log("stop agent", agent_id)

    print("\n══════════════════════════════════════════════════════════", flush=True)
    if FAILURES:
        for item in FAILURES:
            print(f"  ✗ {item}", flush=True)
        print("══════════════════════════════════════════════════════════", flush=True)
        return 1
    print("  Realistic cycle E2E OK", flush=True)
    print("══════════════════════════════════════════════════════════", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
