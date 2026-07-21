"""Shared helpers for realistic autonomous E2E scripts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
VENV_NAUTILUS = ROOT / ".venv-nautilus" / "bin" / "python"

if str(INTEGRATIONS) not in sys.path:
    sys.path.insert(0, str(INTEGRATIONS))


@dataclass(frozen=True)
class E2EMarket:
    mode: str  # "us" | "in"
    symbol: str

    @property
    def is_us(self) -> bool:
        return self.mode == "us"


DEFENDER_REFUSAL_MARKERS = (
    "defender posture",
    "cross-market drift",
    "ledger-write attempted",
    "fabricated",
    "exfiltration",
    "drift-injection",
    "drift injection",
    "prompt-injection",
    "prompt injection",
    "instance #",
    "mandate grounds",
    "declined on mandate",
    "refusing to execute",
)


def turn_looks_like_defender_refusal(text: str) -> bool:
    lower = (text or "").lower()
    return any(marker in lower for marker in DEFENDER_REFUSAL_MARKERS)


def assert_turn_not_defender_refusal(
    assistant_text: str,
    *,
    fail: Callable[[str, str], None],
    step: str = "llm turn",
) -> bool:
    if turn_looks_like_defender_refusal(assistant_text):
        fail(step, f"model refused integration turn: {assistant_text[:200].replace(chr(10), ' ')}")
        return False
    return True


def load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        val = value.strip().strip('"').strip("'")
        if val:
            os.environ[key.strip()] = val


def vibe_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("VIBE_API_AUTH_KEY") or os.getenv("API_AUTH_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def vibe_base() -> str:
    return os.getenv("VIBE_BACKEND_URL", "http://127.0.0.1:8899").rstrip("/")


def vibe_post(path: str, payload: dict[str, Any] | None = None, *, timeout: int = 180) -> Any:
    url = f"{vibe_base()}{path}"
    data = json.dumps(payload or {}).encode("utf-8")
    last_exc: Exception | None = None
    for attempt in range(12):
        try:
            if not wait_for_http(f"{vibe_base()}/health", attempts=3, delay_sec=1.0):
                raise urllib.error.URLError("vibe health check failed")
            req = urllib.request.Request(url, data=data, headers=vibe_headers(), method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            last_exc = exc
            time.sleep(min(2.0 + attempt, 8.0))
    raise RuntimeError(f"Vibe POST {path} failed: {last_exc}") from last_exc


def vibe_get(path: str, *, timeout: int = 60) -> Any:
    url = f"{vibe_base()}{path}"
    last_exc: Exception | None = None
    for attempt in range(12):
        try:
            if not wait_for_http(f"{vibe_base()}/health", attempts=3, delay_sec=1.0):
                raise urllib.error.URLError("vibe health check failed")
            req = urllib.request.Request(url, headers=vibe_headers(), method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            last_exc = exc
            time.sleep(min(2.0 + attempt, 8.0))
    raise RuntimeError(f"Vibe GET {path} failed: {last_exc}") from last_exc


def wait_for_http(url: str, *, attempts: int = 20, delay_sec: float = 2.0) -> bool:
    for _ in range(attempts):
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(delay_sec)
    return False


def require_stack_ready(*, fail: Callable[[str, str], None], market: E2EMarket) -> Any:
    """Block until Vibe + market backend (Alpaca US or OpenAlgo IN) are ready."""
    if not wait_for_http(f"{vibe_base()}/health"):
        fail("preflight vibe", f"unreachable at {vibe_base()}")
        return None

    if market.is_us:
        from trade_integrations.dataflows.alpaca import (
            alpaca_configured,
            fetch_alpaca_account,
            fetch_alpaca_quote,
        )

        if not alpaca_configured():
            fail("preflight alpaca", "ALPACA_API_KEY / ALPACA_API_SECRET not set")
            return None
        for attempt in range(15):
            try:
                acct = fetch_alpaca_account()
                quote = fetch_alpaca_quote(market.symbol)
                if acct.get("status") and quote and quote.get("ltp"):
                    return {"market": market, "quote": quote, "account": acct}
            except Exception:
                pass
            time.sleep(2.0)
        fail("preflight alpaca", f"account or {market.symbol} quote not ready")
        return None

    openalgo_host = os.getenv("OPENALGO_HOST", "http://127.0.0.1:5001").rstrip("/")
    if not wait_for_http(f"{openalgo_host}/"):
        fail("preflight openalgo", f"unreachable at {openalgo_host}")
        return None

    from nautilus_openalgo_bridge.openalgo_client import get_openalgo_client

    client = get_openalgo_client()
    for attempt in range(15):
        try:
            if client.ensure_analyzer_mode():
                client.get_funds()
                client.get_quote("NIFTY", exchange="NSE_INDEX")
                return client
        except Exception:
            pass
        time.sleep(2.0)
    fail("preflight openalgo", "analyzer mode or quotes not ready after retries")
    return None


def alpaca_spy_qty(positions: list[dict[str, Any]], symbol: str) -> float:
    sym = symbol.upper()
    for row in positions:
        if str(row.get("symbol") or "").upper() == sym:
            try:
                return float(row.get("qty") or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def verified_flatten_us(
    symbol: str,
    *,
    fail: Callable[[str, str], None],
) -> bool:
    from trade_integrations.dataflows.alpaca import close_alpaca_position, list_alpaca_positions

    last_exc: Exception | None = None
    for attempt in range(8):
        try:
            positions = list_alpaca_positions()
            sym = symbol.upper()
            open_rows = [p for p in positions if str(p.get("symbol") or "").upper() == sym]
            if not open_rows:
                return True
            close_alpaca_position(sym)
            time.sleep(2.0)
            remaining = alpaca_spy_qty(list_alpaca_positions(), sym)
            if remaining == 0:
                return True
            last_exc = RuntimeError(f"{remaining} shares of {sym} still open")
        except Exception as exc:
            last_exc = exc
        time.sleep(3.0)
    fail("flatten alpaca", str(last_exc or "positions remain"))
    return False


def verified_flatten(
    agent_id: str,
    client: Any,
    *,
    fail: Callable[[str, str], None],
    strategy: str = "realistic_e2e",
    market: E2EMarket | None = None,
) -> bool:
    if market and market.is_us:
        return verified_flatten_us(market.symbol, fail=fail)
    """EXIT all bridge positions; fail unless OpenAlgo confirms flat book."""
    from nautilus_openalgo_bridge.config import is_bridge_market_open
    from nautilus_openalgo_bridge.execute import execute_intent
    from nautilus_openalgo_bridge.models import ExecutionIntent, IntentAction
    from nautilus_openalgo_bridge.reconcile import open_positions_from_book

    openalgo_host = os.getenv("OPENALGO_HOST", "http://127.0.0.1:5001").rstrip("/")
    if not wait_for_http(f"{openalgo_host}/", attempts=10, delay_sec=2.0):
        fail("flatten preflight", "OpenAlgo unreachable before cleanup")
        return False

    last_exc: Exception | None = None
    for attempt in range(8):
        try:
            result = execute_intent(
                ExecutionIntent(
                    action=IntentAction.EXIT,
                    agent_id=agent_id,
                    rationale="E2E verified flatten",
                    underlying="NIFTY",
                    strategy=strategy,
                ),
                client=client,
                skip_preflight=not is_bridge_market_open(),
            )
            remaining = open_positions_from_book(client.get_position_book())
            if result.get("status") in {"executed", "skipped"} and not remaining:
                return True
            if remaining:
                last_exc = RuntimeError(f"{len(remaining)} positions still open after EXIT")
            else:
                return True
        except Exception as exc:
            last_exc = exc
        time.sleep(3.0)

    fail("flatten", str(last_exc or "positions remain after retries"))
    return False


def build_fireable_watch_spec(*, symbol: str, ltp: float, vix_ltp: float | None = None) -> dict[str, Any]:
    from trade_integrations.auto_paper.mandate_config import _watch_exchange_for_symbol

    exchange = _watch_exchange_for_symbol(symbol)
    move_threshold = 0.5 if exchange == "US" else 0.001
    rules: list[dict[str, Any]] = [
        {
            "symbol": symbol,
            "metric": "spot_move_pct",
            "threshold": move_threshold,
            "direction": "either",
            "exchange": exchange,
            "baseline_ltp": ltp * (1.0002 if exchange != "US" else 1.006),
            "label": f"{symbol} move (armed)",
        },
    ]
    if vix_ltp is not None:
        rules.append(
            {
                "symbol": "INDIAVIX",
                "metric": "level_above",
                "threshold": max(1.0, vix_ltp - 0.05),
                "label": "VIX level (armed)",
            },
        )
    return {
        "rules": rules,
        "gate": {"skip_if_unchanged_minutes": 1},
        "review_triggers": ["watch_rule_fired", "thesis_break"],
    }


def dispatch_us_watch_alert(
    agent_id: str,
    *,
    symbol: str,
    ltp: float,
    message: str,
) -> dict[str, Any]:
    """Bridge alert → Vibe (US symbols — Nautilus OpenAlgo feed not used)."""
    from nautilus_openalgo_bridge.models import BridgeSignal, QuoteSnapshot, WatchAlert, WatchRule
    from nautilus_openalgo_bridge.vibe_trigger import dispatch_watch_alert_sync
    from trade_integrations.autonomous_agents.store import get_agent

    agent = get_agent(agent_id) or {}
    watch_spec = dict(agent.get("watch_spec") or {})
    threshold = 0.5
    for rule in watch_spec.get("rules") or []:
        if str(rule.get("symbol") or "").upper() == symbol.upper():
            threshold = float(rule.get("threshold") or threshold)
            break

    alert = WatchAlert(
        signal=BridgeSignal.REVIEW_NEEDED,
        rule=WatchRule(symbol=symbol, metric="spot_move_pct", threshold=threshold, exchange="US"),
        symbol=symbol,
        message=message,
        ltp=ltp,
    )
    quotes = {symbol: QuoteSnapshot(symbol=symbol, exchange="US", ltp=ltp)}
    result = dispatch_watch_alert_sync(agent_id, alert, quotes=quotes)
    return result


def mechanical_us_entry(symbol: str, *, orders: int = 2, qty_each: float = 1.0) -> float:
    """Place multiple Alpaca market buys; return last known LTP."""
    from trade_integrations.dataflows.alpaca import fetch_alpaca_quote, submit_alpaca_market_order

    ltp = 0.0
    for i in range(orders):
        submit_alpaca_market_order(symbol, side="buy", qty=qty_each)
        time.sleep(1.5)
        q = fetch_alpaca_quote(symbol)
        if q and q.get("ltp"):
            ltp = float(q["ltp"])
    if ltp <= 0:
        q = fetch_alpaca_quote(symbol)
        ltp = float((q or {}).get("ltp") or 0)
    if ltp <= 0:
        raise RuntimeError(f"no Alpaca quote for {symbol} after entry")
    return ltp


def mechanical_us_partial_exit(symbol: str, *, qty: float = 1.0) -> None:
    from trade_integrations.dataflows.alpaca import submit_alpaca_market_order

    submit_alpaca_market_order(symbol, side="sell", qty=qty)
    time.sleep(1.5)


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


def mechanical_straddle_entry(agent_id: str, client: Any, *, strategy: str) -> tuple[list[Any], float]:
    from nautilus_openalgo_bridge.execute import execute_intent
    from nautilus_openalgo_bridge.handoff import load_handoff, save_handoff
    from nautilus_openalgo_bridge.models import (
        ExecutionIntent,
        ExecutionLeg,
        IntentAction,
        PositionHandoff,
        StopRules,
    )
    from nautilus_openalgo_bridge.reconcile import sync_handoff_from_position_book

    chain = client.get_option_chain("NIFTY", strike_count=3)
    ce, pe, atm = _pick_atm_straddle(chain)
    spot = float(chain.get("underlying_ltp") or 0)
    lot = int(ce.get("lotsize") or pe.get("lotsize") or 25)
    legs = [
        ExecutionLeg(symbol=str(ce["symbol"]), exchange="NFO", action="SELL", quantity=lot),
        ExecutionLeg(symbol=str(pe["symbol"]), exchange="NFO", action="SELL", quantity=lot),
    ]
    result = execute_intent(
        ExecutionIntent(
            action=IntentAction.ENTER,
            agent_id=agent_id,
            rationale="E2E mechanical straddle entry",
            underlying="NIFTY",
            legs=legs,
            strategy=strategy,
            confidence=70,
        ),
        client=client,
        skip_preflight=True,
    )
    if result.get("status") != "executed":
        raise RuntimeError(f"ENTER failed: {json.dumps(result)[:200]}")

    handoff = load_handoff(agent_id) or PositionHandoff(
        agent_id=agent_id,
        widget_id=None,
        underlying="NIFTY",
        legs=legs,
        entry_spot=spot,
        stop_rules=StopRules(max_loss_inr=2500, flatten_at_close=False),
    )
    handoff.legs = legs
    handoff.entry_spot = spot
    save_handoff(handoff)
    sync_handoff_from_position_book(agent_id, client=client, underlying="NIFTY")
    return legs, spot


def assert_handoff_active(agent_id: str, *, require_legs: bool = False) -> dict[str, Any]:
    """M3 checkpoint: bridge handoff file exists (optionally with open legs)."""
    from nautilus_openalgo_bridge.handoff import load_handoff

    handoff = load_handoff(agent_id)
    if handoff is None:
        raise AssertionError(f"handoff missing for {agent_id}")
    if require_legs and not handoff.legs:
        raise AssertionError(f"handoff for {agent_id} has no legs")
    return {
        "agent_id": agent_id,
        "underlying": handoff.underlying,
        "entry_spot": handoff.entry_spot,
        "leg_count": len(handoff.legs or []),
        "watch_rules": len(handoff.watch_spec.rules or []),
    }


def assert_m3_bridge_loop_ready(agent_id: str, client: Any) -> dict[str, Any]:
    """Verify handoff + OpenAlgo positionbook alignment before watch/EXIT phases."""
    from nautilus_openalgo_bridge.handoff import load_handoff
    from nautilus_openalgo_bridge.reconcile import open_positions_from_book

    handoff = load_handoff(agent_id)
    if handoff is None:
        raise AssertionError(f"M3 preflight: no handoff for {agent_id}")
    rows = open_positions_from_book(client.get_position_book())
    return {
        "handoff": assert_handoff_active(agent_id, require_legs=bool(handoff.legs)),
        "open_positions": len(rows),
    }


def mechanical_partial_exit(agent_id: str, client: Any, legs: list[Any], *, strategy: str) -> None:
    from nautilus_openalgo_bridge.execute import execute_intent
    from nautilus_openalgo_bridge.models import ExecutionIntent, ExecutionLeg, IntentAction

    if not legs:
        return
    leg = legs[0]
    execute_intent(
        ExecutionIntent(
            action=IntentAction.ADJUST,
            agent_id=agent_id,
            rationale="E2E partial exit — close one short leg",
            underlying="NIFTY",
            legs=[
                ExecutionLeg(
                    symbol=leg.symbol,
                    exchange=leg.exchange,
                    action="BUY",
                    quantity=leg.quantity,
                )
            ],
            strategy=strategy,
        ),
        client=client,
        skip_preflight=True,
    )


def start_watch_node(agent_id: str) -> subprocess.Popen[Any]:
    env = os.environ.copy()
    env["NAUTILUS_WATCH_ENABLE"] = "true"
    env["NAUTILUS_BRIDGE_ALERT_OUTSIDE_HOURS"] = "true"
    env["NAUTILUS_ALERT_COOLDOWN_SEC"] = "15"
    env["PYTHONPATH"] = f"{INTEGRATIONS}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env["TRADE_INTEGRATIONS_SKIP_APPLY"] = "1"
    py = str(VENV_NAUTILUS) if VENV_NAUTILUS.is_file() else sys.executable
    return subprocess.Popen(
        [py, "-m", "nautilus_openalgo_bridge.runtime.run_watch_node", "--agent-id", agent_id],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_watch_node(proc: subprocess.Popen[Any] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def stop_auto_paper_session() -> None:
    try:
        from trade_integrations.auto_paper.mcp_actions import stop_auto_paper

        stop_auto_paper()
    except Exception:
        pass


def stop_agent(agent_id: str) -> None:
    try:
        vibe_post(f"/autonomous-agents/{agent_id}/stop")
    except Exception:
        pass


def wait_for_agent_idle(agent_id: str, *, timeout_sec: int = 120) -> bool:
    from trade_integrations.autonomous_agents.store import get_agent

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        agent = get_agent(agent_id) or {}
        if not agent.get("streaming"):
            return True
        time.sleep(2)
    return False


def _default_memory_dir() -> Path:
    return Path.home() / ".vibe-trading" / "memory"


def purge_e2e_poisoned_memory(*, memory_dir: Path | None = None) -> int:
    """Remove injection-pattern memories written during prior E2E runs; rebuild index."""
    mem_dir = memory_dir or _default_memory_dir()
    if not mem_dir.is_dir():
        return 0

    poison_markers = (
        "prompt-injection",
        "prompt_injection",
        "phase-2_prompt-injection",
        "phase-3_prompt-injection",
        "drift-injection",
    )
    removed = 0
    for path in sorted(mem_dir.glob("*.md")):
        if path.name == "MEMORY.md":
            continue
        blob = f"{path.name} {path.read_text(encoding='utf-8', errors='replace')[:800]}".lower()
        if any(marker in blob for marker in poison_markers):
            path.unlink(missing_ok=True)
            removed += 1

    index_path = mem_dir / "MEMORY.md"
    lines: list[str] = []
    for path in sorted(mem_dir.glob("*.md")):
        if path.name == "MEMORY.md":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        title = path.stem
        desc = title
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                for row in parts[1].splitlines():
                    if row.strip().startswith("name:"):
                        title = row.split(":", 1)[1].strip()
                    if row.strip().startswith("description:"):
                        desc = row.split(":", 1)[1].strip()
        lines.append(f"- [{title}]({path.name}) — {desc}")
    index_path.write_text("\n".join(lines[:200]), encoding="utf-8")
    return removed


def classify_refusal(text: str) -> str | None:
    lower = (text or "").lower()
    if turn_looks_like_defender_refusal(text):
        return "injection_refusal"
    if "confidence" in lower and ("threshold" in lower or "<" in lower):
        return "confidence_gate"
    if "market_open" in lower or "market closed" in lower or "outside_market" in lower:
        return "market_hours"
    if "watch spec" in lower and "drift" in lower:
        return "watch_drift"
    if "agent not found" in lower:
        return "agent_not_found"
    if "no open position" in lower or "position_count=0" in lower:
        return "no_position"
    return None


def log_e2e_turn_result(
    *,
    phase: str,
    agent_id: str,
    turn_kind: str,
    assistant_text: str,
    outcome: str,
    log_path: Path | None = None,
) -> None:
    path = log_path or (ROOT / "log" / "e2e_refusal_taxonomy.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phase": phase,
        "agent_id": agent_id,
        "turn_kind": turn_kind,
        "outcome": outcome,
        "refusal_class": classify_refusal(assistant_text),
        "preview": (assistant_text or "")[:400].replace("\n", " "),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def wait_for_session_attempt(
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


def dispatch_production_turn(
    agent_id: str,
    *,
    turn_kind: str = "research",
    timeout_sec: int = 900,
) -> tuple[str, dict[str, Any]]:
    """Dispatch the same prompt autonomous jobs use — no E2E user-message overrides."""
    from trade_integrations.autonomous_agents.store import get_agent
    from trade_integrations.autonomous_agents.turns import build_full_reasoning_prompt

    agent = get_agent(agent_id) or {}
    session_id = str(agent.get("vibe_session_id") or "").strip()
    if not session_id:
        raise RuntimeError(f"agent {agent_id} has no vibe_session_id")
    prompt = build_full_reasoning_prompt(agent=agent, turn_kind=turn_kind)
    dispatch = vibe_post(f"/sessions/{session_id}/messages", {"content": prompt})
    attempt_id = str(dispatch.get("attempt_id") or "")
    if not attempt_id:
        raise RuntimeError(f"turn dispatch failed: {json.dumps(dispatch)[:200]}")
    msg = wait_for_session_attempt(session_id, attempt_id, timeout_sec=timeout_sec)
    return attempt_id, msg


def create_paper_agent(*, name: str, mandate: str, symbols: list[str] | None = None) -> tuple[str, str]:
    from trade_integrations.autonomous_agents.market import symbol_execution_market
    from trade_integrations.autonomous_agents.proposals import propose_autonomous_agent
    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    syms = symbols or ["NIFTY"]
    if symbol_execution_market(syms[0]) == "US":
        stop_auto_paper_session()

    from trade_integrations.auto_paper.mandate_config import resolve_allowed_instruments

    exec_market = symbol_execution_market(syms[0])
    instruments = resolve_allowed_instruments(syms, mandate, execution_market=exec_market)
    if instruments is None:
        instruments = ("equity",) if exec_market == "US" else ("options",)

    e2e = bool(os.getenv("REALISTIC_E2E_MARKET"))
    proposal = propose_autonomous_agent(
        symbols=syms,
        name=name,
        mandate=mandate,
        mode="paper",
        confidence_threshold=0 if e2e else 60,
        budget_inr=25_000,
        max_daily_loss_inr=2_500,
        watch_interval_min=5,
        allowed_instruments=list(instruments),
    )
    if str(proposal.get("status") or "") != "ready":
        raise RuntimeError(
            f"proposal not ready: status={proposal.get('status')} "
            f"missing={proposal.get('missing_fields')} errors={proposal.get('routing_errors')}"
        )
    commit = vibe_post(
        "/autonomous-agents/commit",
        {"proposal_id": proposal["proposal_id"], "consent_ack": True},
    )
    agent_id = commit.get("agent_id") or (commit.get("agent") or {}).get("id")
    session_id = commit.get("vibe_session_id") or (commit.get("agent") or {}).get("vibe_session_id")
    if not agent_id or not session_id:
        raise RuntimeError(f"commit failed: {json.dumps(commit)[:200]}")
    agent = get_agent(agent_id) or {}
    mc = dict(agent.get("mandate_config") or {})
    mc["market_hours_only"] = False
    constraints = dict(agent.get("constraints") or {})
    if e2e:
        mc["confidence_threshold"] = 0
        constraints["confidence_threshold"] = 0
        agent["e2e_harness"] = True
    agent["mandate_config"] = mc
    agent["constraints"] = constraints
    save_agent(agent)
    return str(agent_id), str(session_id)


def assert_turn_tools_or_fail(
    session_id: str,
    attempt_id: str,
    *,
    required_tools: set[str],
    fail: Callable[[str, str], None],
    timeout_sec: int = 900,
) -> str:
    """Wait for attempt; fail if model refused or required MCP tools were not called."""
    deadline = time.time() + timeout_sec
    assistant_text = ""
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
                if status not in {"completed", "failed", "cancelled"}:
                    break
                assistant_text = str(msg.get("content") or "")
                if status == "failed":
                    fail("agent turn", assistant_text[:200] or "failed")
                    return assistant_text
                if turn_looks_like_defender_refusal(assistant_text):
                    fail(
                        "agent turn",
                        "model returned defender/refusal prose instead of executing MCP tools",
                    )
                    return assistant_text
                called = set(meta.get("tools_called") or [])
                missing = required_tools - called
                if missing and required_tools:
                    fail("agent turn", f"missing tool calls: {sorted(missing)}")
                return assistant_text
        time.sleep(5)
    fail("agent turn", f"timeout after {timeout_sec}s")
    return assistant_text
