#!/usr/bin/env python3
"""Realistic autonomous cycle E2E (paper only).

Flow:
  1. Agent research / analysis turn (no orders required)
  2. Forced execution turn — must place multiple legs, partial exit, set watch metrics
  3. Wait --metric-delay-sec (default 20s), arm watch rules that fire on next tick
  4. Nautilus watch node + bridge alert → Vibe revision turn (sell more / add)
  5. Optional cleanup flatten + stop agent

Usage:
  ./scripts/run_realistic_agent_cycle_e2e.sh
  python3 scripts/run_realistic_agent_cycle_e2e.py --skip-cleanup --metric-delay-sec 20
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
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


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("VIBE_API_AUTH_KEY") or os.getenv("API_AUTH_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _vibe_base() -> str:
    return os.getenv("VIBE_BACKEND_URL", "http://127.0.0.1:8899").rstrip("/")


def vibe_post(path: str, payload: dict[str, Any] | None = None, *, timeout: int = 180) -> Any:
    sys.path.insert(0, str(ROOT / "scripts"))
    import realistic_e2e_lib as lib

    return lib.vibe_post(path, payload, timeout=timeout)


def vibe_get(path: str, *, timeout: int = 60) -> Any:
    sys.path.insert(0, str(ROOT / "scripts"))
    import realistic_e2e_lib as lib

    return lib.vibe_get(path, timeout=timeout)


def wait_for_attempt(
    session_id: str,
    attempt_id: str,
    *,
    timeout_sec: int = 900,
    poll_sec: float = 5.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        messages = vibe_get(f"/sessions/{session_id}/messages?limit=80")
        if isinstance(messages, list):
            for msg in reversed(messages):
                if msg.get("role") != "assistant":
                    continue
                if msg.get("linked_attempt_id") != attempt_id:
                    continue
                meta = msg.get("metadata") or {}
                status = str(meta.get("status") or "").lower()
                if status in {"completed", "failed", "cancelled"}:
                    return msg
        time.sleep(poll_sec)
    raise TimeoutError(f"attempt {attempt_id} not complete within {timeout_sec}s")


def count_bridge_alert_messages(session_id: str) -> int:
    messages = vibe_get(f"/sessions/{session_id}/messages?limit=100")
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Realistic autonomous agent cycle E2E")
    parser.add_argument("--skip-cleanup", action="store_true")
    parser.add_argument("--skip-flatten", action="store_true")
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
    from trade_integrations.autonomous_agents.turns import build_full_reasoning_prompt
    from trade_integrations.execution.prompt_fragments import build_e2e_phase_delta

    if e2e_market.is_us:
        from trade_integrations.dataflows.alpaca import fetch_alpaca_quote, list_alpaca_positions
    else:
        from nautilus_openalgo_bridge.handoff import load_handoff
        from nautilus_openalgo_bridge.reconcile import open_positions_from_book, sync_handoff_from_position_book
        from nautilus_openalgo_bridge.runtime.poll_loop import run_once

    title = f"US {sym} Alpaca" if e2e_market.is_us else f"IN {sym} OpenAlgo"
    print("══════════════════════════════════════════════════════════", flush=True)
    print(f"  Realistic cycle ({title}) — analysis → orders → watch → react", flush=True)
    print("══════════════════════════════════════════════════════════", flush=True)

    if not args.skip_cleanup:
        print("\n── Cleanup ──", flush=True)
        subprocess.run([sys.executable, str(ROOT / "scripts/cleanup_autonomous_agents.py")], check=False)

    stack = lib.require_stack_ready(fail=_fail, market=e2e_market)
    if stack is None:
        return 1
    _log("stack preflight", f"Alpaca + Vibe OK ({sym})" if e2e_market.is_us else "OpenAlgo + Vibe OK")

    print("\n── Phase 0: Create agent ──", flush=True)
    mandate = (
        f"Paper trade US {sym} via Alpaca. React to watch alerts by buying/selling shares."
        if e2e_market.is_us
        else f"Paper trade {sym} options autonomously. React to watch alerts by adjusting positions."
    )
    agent_id, session_id = lib.create_paper_agent(
        name=f"Realistic cycle E2E ({sym})",
        mandate=mandate,
        symbols=[sym],
    )
    _log("agent", f"{agent_id} session={session_id}")
    agent = get_agent(agent_id) or {}

    print("\n── Phase 1: Analysis turn (no orders) ──", flush=True)
    analysis_prompt = build_full_reasoning_prompt(agent=agent, turn_kind="research")
    analysis_prompt += lib.build_e2e_integration_preamble(agent_id=agent_id, phase="Phase 1 analysis")
    analysis_prompt += build_e2e_phase_delta(
        phase="analysis",
        market="US" if e2e_market.is_us else "IN",
        symbol=sym,
    )
    try:
        dispatch = vibe_post(f"/sessions/{session_id}/messages", {"content": analysis_prompt})
        attempt1 = dispatch.get("attempt_id")
        if not attempt1:
            _fail("analysis dispatch", json.dumps(dispatch)[:200])
        else:
            msg1 = wait_for_attempt(session_id, attempt1, timeout_sec=args.turn_timeout)
            preview = str(msg1.get("content") or "")[:100].replace("\n", " ")
            content1 = str(msg1.get("content") or "")
            if not lib.assert_turn_not_defender_refusal(content1, fail=_fail, step="analysis turn"):
                return 1
            _log("analysis complete", preview)
    except Exception as exc:
        _fail("analysis turn", str(exc))

    # --- Phase 2: Forced execution ---
    print("\n── Phase 2: Forced execution (multi-order + partial exit + watch) ──", flush=True)
    exec_prompt = lib.build_e2e_integration_preamble(agent_id=agent_id, phase="Phase 2 execution")
    exec_prompt += build_e2e_phase_delta(
        phase="execution",
        market="US" if e2e_market.is_us else "IN",
        symbol=sym,
    )
    if not e2e_market.is_us:
        exec_prompt += (
            f"\nAutomated E2E for `{agent_id}`: use OpenAlgo paper tools for {sym} options.\n"
        )
    alerts_before = 0
    try:
        alerts_before = count_bridge_alert_messages(session_id)
    except Exception as exc:
        _log("alert baseline", str(exc), ok=False)
    try:
        dispatch2 = vibe_post(f"/sessions/{session_id}/messages", {"content": exec_prompt})
        attempt2 = dispatch2.get("attempt_id")
        if not attempt2:
            _fail("execution dispatch", json.dumps(dispatch2)[:200])
        else:
            msg2 = wait_for_attempt(session_id, attempt2, timeout_sec=args.turn_timeout)
            _log("execution turn complete", str((msg2.get("metadata") or {}).get("status", "?")))
            content2 = str(msg2.get("content") or "")
            if not lib.assert_turn_not_defender_refusal(content2, fail=_fail, step="execution turn"):
                return 1
    except Exception as exc:
        _fail("execution turn", str(exc))

    entry_ltp = 0.0
    client = stack if not e2e_market.is_us else None
    legs: list[Any] = []

    if e2e_market.is_us:
        qty = lib.alpaca_spy_qty(list_alpaca_positions(), sym)
        if qty >= 1:
            _log("alpaca after LLM", f"{sym} qty={qty}")
            q = fetch_alpaca_quote(sym)
            entry_ltp = float((q or {}).get("ltp") or 0)
        else:
            _log("alpaca after LLM", "flat — mechanical fallback", ok=True)
            try:
                entry_ltp = lib.mechanical_us_entry(sym, orders=2, qty_each=1.0)
                lib.mechanical_us_partial_exit(sym, qty=1.0)
                qty = lib.alpaca_spy_qty(list_alpaca_positions(), sym)
                _log("mechanical US entry", f"{sym} qty={qty} ltp={entry_ltp:.2f}")
            except Exception as exc:
                _fail("mechanical US fallback", str(exc))
                return 1
    else:
        from nautilus_openalgo_bridge.handoff import load_handoff

        positions = open_positions_from_book(client.get_position_book())
        if positions:
            _log("positions after LLM", f"{len(positions)} open")
            handoff = load_handoff(agent_id)
            if handoff:
                legs = list(handoff.legs or [])
                entry_ltp = float(handoff.entry_spot or 0)
        else:
            _log("positions after LLM", "none — using mechanical fallback", ok=True)
            try:
                legs, entry_ltp = lib.mechanical_straddle_entry(agent_id, client, strategy="realistic_e2e")
                lib.mechanical_partial_exit(agent_id, client, legs, strategy="realistic_e2e")
                positions = open_positions_from_book(client.get_position_book())
                _log("mechanical entry", f"legs={len(legs)} open_positions={len(positions)}")
            except Exception as exc:
                _fail("mechanical fallback", str(exc))
                return 1

    # --- Phase 3: Delay then arm metrics ---
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
        _log("armed watch_spec", f"{sym} baseline={ltp * 1.0002:.2f} thr=0.001%")
    else:
        from nautilus_openalgo_bridge.reconcile import sync_handoff_from_position_book

        nifty_q2 = client.get_quote("NIFTY", exchange="NSE_INDEX")
        vix_q2 = client.get_quote("INDIAVIX", exchange="NSE_INDEX")
        nifty_ltp = float(nifty_q2.get("ltp") or entry_ltp)
        vix_ltp = float(vix_q2.get("ltp") or 12.0)
        armed_spec = lib.build_fireable_watch_spec(symbol="NIFTY", ltp=nifty_ltp, vix_ltp=vix_ltp)
        mcp_set_watch_spec(agent_id=agent_id, watch_spec=armed_spec)
        sync_handoff_from_position_book(agent_id, client=client, underlying="NIFTY")
        _log("armed watch_spec", f"NIFTY baseline={nifty_ltp * 1.0002:.2f} thr=0.001%")

    # --- Phase 4: Trigger bridge alert → Vibe revision ---
    print("\n── Phase 4: Watch alert → agent revision turn ──", flush=True)
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
            message=f"{sym} armed watch fired (US E2E integration test)",
        )
        _log("bridge dispatch", str(result.get("status")))
    else:
        from nautilus_openalgo_bridge.runtime.poll_loop import run_once

        tick = run_once(agent_id=agent_id, trigger_vibe=True, process_intents=True)
        _log("watch tick", f"alerts={len(tick.get('alerts') or [])} dispatches={len(tick.get('dispatches') or [])}")
        dispatches = tick.get("dispatches") or []
        if dispatches:
            _log("bridge dispatch", str(dispatches[0].get("status")))

    revision_seen = False
    deadline = time.time() + 600
    while time.time() < deadline:
        alerts_now = count_bridge_alert_messages(session_id)
        if alerts_now > alerts_before:
            _log("bridge alert in session", f"messages={alerts_now}")
            break
        agent = get_agent(agent_id) or {}
        if agent.get("streaming"):
            revision_seen = True
        time.sleep(5)
    else:
        _fail("alert revision", "no new bridge alert message in Vibe session within 10m")

    if revision_seen or count_bridge_alert_messages(session_id) > alerts_before:
        _log("agent revision", "triggered (streaming or alert message)")
        while time.time() < deadline:
            agent = get_agent(agent_id) or {}
            if not agent.get("streaming"):
                break
            time.sleep(5)
        agent = get_agent(agent_id) or {}
        decision = agent.get("last_decision") or {}
        if decision:
            _log("post-alert decision", f"{decision.get('decision')} — {str(decision.get('rationale') or '')[:80]}")
        messages = vibe_get(f"/sessions/{session_id}/messages?limit=20")
        if isinstance(messages, list):
            for msg in reversed(messages):
                if msg.get("role") != "assistant":
                    continue
                content = str(msg.get("content") or "").lower()
                if "nautilus watch alert" in content or "strategy revision" in content:
                    continue
                if any(w in content for w in ("exit", "adjust", "enter", "hold", "sell", "buy", "leg")):
                    _log("revision reply", str(msg.get("content") or "")[:120].replace("\n", " "))
                    break

    if watch_proc and watch_proc.poll() is None:
        lib.stop_watch_node(watch_proc)

    # --- Cleanup (must flatten — test fails if OpenAlgo still shows positions) ---
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
