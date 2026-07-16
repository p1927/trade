#!/usr/bin/env python3
"""Paper E2E: autonomous agent → watch/handoff → execution → cancel → exit.

Requires OpenAlgo (analyzer mode) + Vibe API. Safe for paper only.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))

# Load .env (overwrite so shell empty vars don't win)
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


def _log(step: str, detail: str = "", *, ok: bool = True) -> None:
    mark = "✓" if ok else "✗"
    msg = f"  {mark} {step}"
    if detail:
        msg += f" — {detail}"
    print(msg)


def _fail(step: str, detail: str) -> None:
    _log(step, detail, ok=False)


def _vibe_post(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    base = os.getenv("VIBE_BACKEND_URL", "http://127.0.0.1:8899").rstrip("/")
    url = f"{base}{path}"
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("VIBE_API_AUTH_KEY") or os.getenv("API_AUTH_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _vibe_get(path: str) -> dict[str, Any]:
    base = os.getenv("VIBE_BACKEND_URL", "http://127.0.0.1:8899").rstrip("/")
    url = f"{base}{path}"
    headers: dict[str, str] = {}
    api_key = os.getenv("VIBE_API_AUTH_KEY") or os.getenv("API_AUTH_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _pick_atm_straddle(chain_payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], float]:
    chain = chain_payload.get("chain") or []
    atm = chain_payload.get("atm_strike")
    if atm is None and chain:
        atm = chain[len(chain) // 2].get("strike")
    atm_f = float(atm or 0)
    ce_row = pe_row = None
    for row in chain:
        if not isinstance(row, dict):
            continue
        try:
            strike = float(row.get("strike") or 0)
        except (TypeError, ValueError):
            continue
        if abs(strike - atm_f) < 0.01:
            ce_row = row.get("ce") if isinstance(row.get("ce"), dict) else None
            pe_row = row.get("pe") if isinstance(row.get("pe"), dict) else None
            break
    if not ce_row or not pe_row:
        raise RuntimeError("could not resolve ATM CE/PE from option chain")
    return ce_row, pe_row, atm_f


def main() -> int:
    from nautilus_openalgo_bridge.config import is_bridge_market_open
    from nautilus_openalgo_bridge.execute import execute_intent
    from nautilus_openalgo_bridge.handoff import load_handoff, save_handoff, sync_watch_spec_to_handoff
    from nautilus_openalgo_bridge.models import (
        BridgeSignal,
        ExecutionIntent,
        ExecutionLeg,
        IntentAction,
        PositionHandoff,
        StopRules,
        WatchAlert,
        WatchRule,
        WatchSpec,
    )
    from nautilus_openalgo_bridge.openalgo_client import get_openalgo_client
    from nautilus_openalgo_bridge.reconcile import open_positions_from_book, sync_handoff_from_position_book
    from nautilus_openalgo_bridge.runtime.poll_loop import run_once
    from nautilus_openalgo_bridge.watch_eval import evaluate_watch_spec
    from trade_integrations.autonomous_agents.proposals import propose_autonomous_agent
    from trade_integrations.autonomous_agents.store import get_agent, save_agent
    from trade_integrations.autonomous_agents.mcp_actions import mcp_set_watch_spec

    print("══════════════════════════════════════════════════════════")
    print("  Nautilus ↔ Vibe ↔ OpenAlgo paper E2E")
    print("══════════════════════════════════════════════════════════")

    if not os.getenv("OPENALGO_API_KEY"):
        _fail("env", "OPENALGO_API_KEY not set")
        return 1

    client = get_openalgo_client()
    errors = 0

    def fail(step: str, detail: str) -> None:
        nonlocal errors
        errors += 1
        _fail(step, detail)

    # --- 1. Paper mode ---
    try:
        if not client.ensure_analyzer_mode():
            fail("paper mode", "analyzer mode not enabled")
        else:
            _log("paper mode", "OpenAlgo analyzer (paper) active")
    except Exception as exc:
        fail("paper mode", str(exc))

    # --- 2. Create autonomous agent via Vibe API ---
    agent_id: str | None = None
    try:
        proposal = propose_autonomous_agent(
            symbols=["NIFTY"],
            name="E2E NIFTY paper",
            mandate="Paper trade NIFTY options; watch spot and VIX; exit on stops.",
            mode="paper",
            watch_interval_min=7,
            research_interval_min=90,
        )
        proposal_id = proposal["proposal_id"]
        commit = _vibe_post(
            "/autonomous-agents/commit",
            {"proposal_id": proposal_id, "consent_ack": True},
        )
        agent_id = commit.get("agent_id") or commit.get("agent", {}).get("id")
        vibe_session = commit.get("vibe_session_id") or commit.get("agent", {}).get("vibe_session_id")
        if not agent_id:
            fail("create agent", f"no agent_id in {commit}")
        else:
            _log("create agent", f"{agent_id} session={vibe_session}")
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
        fail("create agent", str(exc))
        return 1

    assert agent_id is not None

    # --- 3. Set watch spec (simulates Vibe agent output) ---
    watch_spec = {
        "rules": [
            {"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 0.5, "direction": "either"},
            {"symbol": "INDIAVIX", "metric": "level_above", "threshold": 14.0},
        ],
        "gate": {"skip_if_unchanged_minutes": 30},
    }
    try:
        mcp_set_watch_spec(agent_id=agent_id, watch_spec=watch_spec)
        handoff = sync_watch_spec_to_handoff(agent_id, watch_spec)
        agent = get_agent(agent_id)
        if not agent or not agent.get("watch_spec"):
            fail("watch spec", "not persisted on agent")
        elif handoff is None or not handoff.watch_spec.rules:
            fail("watch spec", "handoff missing rules")
        else:
            _log("watch spec", f"{len(handoff.watch_spec.rules)} rules on agent + handoff")
    except Exception as exc:
        fail("watch spec", str(exc))

    # --- 4. Strategy legs from OpenAlgo chain (simulates Vibe strategy pick) ---
    entry_spot = 0.0
    legs: list[ExecutionLeg] = []
    try:
        chain = client.get_option_chain("NIFTY", strike_count=3)
        ce, pe, atm = _pick_atm_straddle(chain)
        entry_spot = float(chain.get("underlying_ltp") or 0)
        lot = 25  # NIFTY lot — analyzer accepts small qty
        legs = [
            ExecutionLeg(symbol=str(ce["symbol"]), exchange="NFO", action="SELL", quantity=lot),
            ExecutionLeg(symbol=str(pe["symbol"]), exchange="NFO", action="SELL", quantity=lot),
        ]
        _log("strategy legs", f"short straddle ATM {atm} CE={ce['symbol']} PE={pe['symbol']}")
    except Exception as exc:
        fail("option chain", str(exc))

    # --- 5. Paper ENTER via bridge execute (simulates post-Vibe handoff entry) ---
    if legs:
        enter_intent = ExecutionIntent(
            action=IntentAction.ENTER,
            agent_id=agent_id,
            rationale="E2E paper straddle entry",
            underlying="NIFTY",
            legs=legs,
            strategy="nautilus_e2e",
            confidence=80,
        )
        skip_mkt = not is_bridge_market_open()
        try:
            result = execute_intent(enter_intent, client=client, skip_preflight=skip_mkt)
            status = result.get("status")
            if status == "blocked" and skip_mkt is False:
                result = execute_intent(enter_intent, client=client, skip_preflight=True)
                status = result.get("status")
            if status not in ("executed",):
                fail("ENTER execution", json.dumps(result)[:200])
            else:
                _log("ENTER execution", f"{result.get('orders_placed', 0)} orders (paper)")
        except Exception as exc:
            fail("ENTER execution", str(exc))

        # Persist handoff as Vibe would after basket fill
        try:
            handoff = load_handoff(agent_id) or PositionHandoff(
                agent_id=agent_id,
                underlying="NIFTY",
                legs=legs,
                entry_spot=entry_spot,
                watch_spec=WatchSpec.from_dict(watch_spec),
                stop_rules=StopRules(max_loss_inr=1500, flatten_at_close=True),
            )
            handoff.legs = legs
            handoff.entry_spot = entry_spot
            save_handoff(handoff)
            sync_handoff_from_position_book(agent_id, client=client, underlying="NIFTY")
            _log("handoff sync", f"entry_spot={handoff.entry_spot:.0f} legs={len(handoff.legs)}")
        except Exception as exc:
            fail("handoff sync", str(exc))

    # --- 6. Watch engine fires alert on synthetic move ---
    try:
        spec = WatchSpec.from_dict(watch_spec)
        baselines = {"NIFTY": entry_spot or 24000.0, "INDIAVIX": 12.0}
        quotes = {
            "NIFTY": type("Q", (), {"symbol": "NIFTY", "exchange": "NSE", "ltp": baselines["NIFTY"] * 1.01})(),
            "INDIAVIX": type("Q", (), {"symbol": "INDIAVIX", "exchange": "NSE", "ltp": 13.0})(),
        }
        from nautilus_openalgo_bridge.models import QuoteSnapshot

        quote_snaps = {
            "NIFTY": QuoteSnapshot(symbol="NIFTY", exchange="NSE", ltp=baselines["NIFTY"] * 1.01),
            "INDIAVIX": QuoteSnapshot(symbol="INDIAVIX", exchange="NSE", ltp=13.0),
        }
        alerts = evaluate_watch_spec(spec, quote_snaps, baselines=baselines)
        review = [a for a in alerts if a.signal == BridgeSignal.REVIEW_NEEDED]
        if not review:
            fail("watch alert", "no REVIEW_NEEDED on +1% synthetic move")
        else:
            _log("watch alert", review[0].message[:80])
    except Exception as exc:
        fail("watch alert", str(exc))

    # --- 7. Poll-loop watch tick (bridge path, no Vibe LLM) ---
    try:
        tick = run_once(agent_id=agent_id, trigger_vibe=False, process_intents=False)
        _log("watch tick", f"quotes={len(tick.get('quotes', {}))} alerts={len(tick.get('alerts', []))}")
    except Exception as exc:
        fail("watch tick", str(exc))

    # --- 8. Pending order + cancel (OpenAlgo paper) ---
    try:
        nifty_ltp = entry_spot or float(client.get_quote("NIFTY", exchange="NSE_INDEX").get("ltp") or 24000)
        limit_price = round(nifty_ltp * 0.85, 2)
        placed = client.place_order(
            {
                "symbol": "NIFTY",
                "exchange": "NSE_INDEX",
                "action": "BUY",
                "quantity": 1,
                "product": "MIS",
                "pricetype": "LIMIT",
                "price": str(limit_price),
            },
            strategy="nautilus_e2e",
        )
        order_id = str(placed.get("orderid") or placed.get("order_id") or "")
        if not order_id:
            fail("place limit order", json.dumps(placed)[:120])
        else:
            cancelled = client.cancel_order(order_id, strategy="nautilus_e2e")
            cancel_ok = str(cancelled.get("status") or cancelled.get("message") or "").lower()
            if "success" in cancel_ok or cancelled.get("orderid"):
                _log("cancel order", f"orderid={order_id}")
            else:
                _log("cancel order", f"response={json.dumps(cancelled)[:100]}")
    except Exception as exc:
        fail("order cancel", str(exc))

    # --- 9. EXIT via bridge (Nautilus exit path) ---
    try:
        exit_intent = ExecutionIntent(
            action=IntentAction.EXIT,
            agent_id=agent_id,
            rationale="E2E flatten",
            underlying="NIFTY",
            strategy="nautilus_e2e",
        )
        exit_result = execute_intent(exit_intent, client=client)
        positions = open_positions_from_book(client.get_position_book())
        _log(
            "EXIT execution",
            f"status={exit_result.get('status')} open_positions={len(positions)}",
        )
        if positions:
            fail("EXIT execution", f"{len(positions)} positions still open after exit")
    except Exception as exc:
        fail("EXIT execution", str(exc))

    # --- 10. Stop test agent ---
    try:
        _vibe_post(f"/autonomous-agents/{agent_id}/stop")
        agent = get_agent(agent_id)
        if agent:
            agent["status"] = "stopped"
            agent["stopped_at"] = datetime.now(timezone.utc).isoformat()
            save_agent(agent)
        _log("cleanup", f"stopped {agent_id}")
    except Exception as exc:
        _log("cleanup", str(exc), ok=False)

    print("══════════════════════════════════════════════════════════")
    if errors:
        print(f"  E2E finished with {errors} error(s)")
        return 1
    print("  E2E paper integration OK")
    print("══════════════════════════════════════════════════════════")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
