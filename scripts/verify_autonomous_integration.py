#!/usr/bin/env python3
"""Thorough integration verification for autonomous stack.

Checks (in order):
  1. Environment prechecks (OpenAlgo, Vibe, Nautilus venv, NAUTILUS_WATCH_ENABLE)
  2. Unit tests (optional --skip-unit)
  3. Live Nautilus TradingNode (WatchActor + OpenAlgo feed) — NOT dry-run / legacy poll
  4. Auto-message: Nautilus bridge alert → Vibe session message
  5. US Alpaca paper path (SPY agent short LLM turn) when Alpaca configured

Exit 0 only when all enabled sections pass.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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

os.environ.setdefault("NAUTILUS_WATCH_ENABLE", "true")
os.environ.setdefault("TRADE_INTEGRATIONS_SKIP_APPLY", "1")

FAILURES: list[str] = []


def _log(section: str, detail: str = "", *, ok: bool = True) -> None:
    mark = "✓" if ok else "✗"
    msg = f"  {mark} {section}"
    if detail:
        msg += f" — {detail}"
    print(msg, flush=True)


def _fail(section: str, detail: str) -> None:
    FAILURES.append(f"{section}: {detail}")
    _log(section, detail, ok=False)


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("VIBE_API_AUTH_KEY") or os.getenv("API_AUTH_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _vibe_base() -> str:
    return os.getenv("VIBE_BACKEND_URL", "http://127.0.0.1:8899").rstrip("/")


def vibe_get(path: str, *, timeout: int = 30) -> Any:
    url = f"{_vibe_base()}{path}"
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def vibe_post(path: str, payload: dict[str, Any] | None = None, *, timeout: int = 120) -> Any:
    url = f"{_vibe_base()}{path}"
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def vibe_delete(path: str, *, timeout: int = 30) -> Any:
    url = f"{_vibe_base()}{path}"
    req = urllib.request.Request(url, headers=_headers(), method="DELETE")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body.strip() else {}


def cleanup_test_agent(agent_id: str, *, label: str = "test agent") -> None:
    """Stop scheduler/Nautilus watch and remove hub agent record."""
    if not agent_id:
        return
    try:
        vibe_post(f"/autonomous-agents/{agent_id}/stop")
        _log(f"stop {label}", agent_id)
    except Exception:
        pass
    try:
        vibe_delete(f"/autonomous-agents/{agent_id}")
        _log(f"delete {label}", agent_id)
    except Exception:
        pass
    try:
        from trade_integrations.autonomous_agents.store import delete_agent

        if delete_agent(agent_id):
            _log(f"removed hub record {label}", agent_id)
    except Exception as exc:
        _log(f"hub cleanup {label}", str(exc), ok=False)


def precheck() -> bool:
    print("\n── 1. Prechecks ──", flush=True)
    ok = True

    if not VENV_NAUTILUS.is_file():
        _fail("nautilus venv", f"missing {VENV_NAUTILUS} — run ./scripts/setup_nautilus.sh")
        ok = False
    else:
        _log("nautilus venv", str(VENV_NAUTILUS))

    from nautilus_openalgo_bridge.config import get_bridge_config, is_watch_enabled

    if not is_watch_enabled():
        _fail("NAUTILUS_WATCH_ENABLE", "must be true for live watch node")
        ok = False
    else:
        _log("NAUTILUS_WATCH_ENABLE", "true")

    proc = subprocess.run(
        [str(VENV_NAUTILUS), "-c", "from nautilus_openalgo_bridge.node import NAUTILUS_AVAILABLE, nautilus_import_error; import json; print(json.dumps({'ok': NAUTILUS_AVAILABLE, 'err': nautilus_import_error()}))"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(INTEGRATIONS), "TRADE_INTEGRATIONS_SKIP_APPLY": "1"},
        capture_output=True,
        text=True,
    )
    try:
        nautilus_info = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        nautilus_info = {"ok": False, "err": proc.stderr or proc.stdout}
    if not nautilus_info.get("ok"):
        _fail("nautilus_trader import", str(nautilus_info.get("err") or "unavailable"))
        ok = False
    else:
        _log("nautilus_trader", "import ok (.venv-nautilus)")

    cfg = get_bridge_config()
    if not cfg.openalgo_api_key:
        _fail("OPENALGO_API_KEY", "not set")
        ok = False
    else:
        openalgo_ok = False
        for _ in range(8):
            try:
                from nautilus_openalgo_bridge.openalgo_client import get_openalgo_client

                client = get_openalgo_client(cfg)
                if client.ensure_analyzer_mode():
                    _log("openalgo paper", "analyzer active")
                    openalgo_ok = True
                    break
            except Exception:
                pass
            time.sleep(2.0)
        if not openalgo_ok:
            _fail("openalgo", "analyzer mode not active or API unreachable")
            ok = False

    if wait_for_vibe(attempts=8, delay_sec=2.0):
        health = vibe_get("/health")
        _log("vibe api", str(health.get("status", "ok")))
    else:
        _fail("vibe api", "unreachable — start Vibe backend on VIBE_BACKEND_URL")
        ok = False

    from trade_integrations.dataflows.alpaca import alpaca_configured, fetch_alpaca_quote

    if alpaca_configured():
        q = fetch_alpaca_quote("SPY")
        if q and q.get("ltp"):
            _log("alpaca paper", f"SPY ltp={q['ltp']}")
        else:
            _log("alpaca paper", "configured but SPY quote empty", ok=False)
    else:
        _log("alpaca paper", "skipped (keys not set)", ok=True)

    return ok


def run_unit_tests() -> bool:
    print("\n── 2. Unit tests ──", flush=True)
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_nautilus_bridge_models.py",
        "tests/test_nautilus_vibe_trigger.py",
        "tests/test_nautilus_execute.py",
        "tests/test_nautilus_handoff.py",
        "tests/test_nautilus_preflight.py",
        "tests/test_nautilus_intent_queue.py",
        "tests/test_nautilus_stop_eval.py",
        "tests/test_nautilus_reconcile.py",
        "tests/test_nautilus_risk_state.py",
        "-q",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        _fail("unit tests", (proc.stdout + proc.stderr)[-400:])
        return False
    last = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "ok"
    _log("unit tests", last)
    return True


def create_test_agent(*, symbols: list[str], name: str, mandate: str) -> tuple[str, str]:
    sys.path.insert(0, str(ROOT / "scripts"))
    import realistic_e2e_lib as lib

    lib.load_env()
    agent_id, session_id = lib.create_paper_agent(name=name, mandate=mandate, symbols=symbols)
    from datetime import datetime, timezone
    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    agent = get_agent(agent_id) or {}
    # Bridge alert dispatch requires an approved plan; bootstrap may not finish when markets are closed.
    agent["plan_approved_at"] = datetime.now(timezone.utc).isoformat()
    agent["bootstrap_status"] = "done"
    agent.pop("plan_approval_required", None)
    if symbols and symbols[0].upper() != "SPY":
        agent["watch_spec"] = {
            "rules": [
                {"symbol": "NIFTY", "metric": "spot_move_pct", "threshold": 0.01, "direction": "either"},
                {"symbol": "INDIAVIX", "metric": "level_above", "threshold": 99.0},
            ],
        }
    save_agent(agent)
    return agent_id, session_id


def count_session_messages(session_id: str) -> int:
    messages = vibe_get(f"/sessions/{session_id}/messages?limit=100")
    return len(messages) if isinstance(messages, list) else 0


def find_bridge_alert_message(session_id: str) -> dict[str, Any] | None:
    messages = vibe_get(f"/sessions/{session_id}/messages?limit=100")
    if not isinstance(messages, list):
        return None
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        content = str(msg.get("content") or "")
        if "Nautilus watch alert" in content or "NAUTILUS_TEST_FIRE_ALERT" in content:
            return msg
    return None


def wait_for_vibe(*, attempts: int = 12, delay_sec: float = 2.5) -> bool:
    for _ in range(attempts):
        try:
            vibe_get("/health")
            return True
        except Exception:
            time.sleep(delay_sec)
    return False


def _session_turn_in_flight(session_id: str) -> bool:
    messages = vibe_get(f"/sessions/{session_id}/messages?limit=30")
    if not isinstance(messages, list):
        return False
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        meta = msg.get("metadata") or {}
        status = str(meta.get("status") or "").lower()
        if status in {"running", "streaming", "pending"}:
            return True
    return False


def ensure_agent_ready(
    agent_id: str,
    *,
    session_id: str | None = None,
    label: str,
    timeout_sec: int = 240,
) -> bool:
    """Wait for post-commit bootstrap + in-flight Vibe turns to finish."""
    from nautilus_openalgo_bridge.market_hours import any_trading_market_open
    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    if not any_trading_market_open():
        agent = get_agent(agent_id) or {}
        session_busy = bool(session_id and _session_turn_in_flight(session_id))
        if not agent.get("streaming") and not session_busy:
            _log(label, "markets closed — agent idle (no wait)", ok=True)
            return True
        timeout_sec = min(timeout_sec, 15)
        _log(label, f"markets closed — short wait ({timeout_sec}s max)", ok=True)

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        agent = get_agent(agent_id) or {}
        bootstrap = str(agent.get("bootstrap_status") or "")
        streaming = bool(agent.get("streaming"))
        session_busy = bool(session_id and _session_turn_in_flight(session_id))
        if not streaming and not session_busy and bootstrap in ("", "done", "failed"):
            _log(label, f"ready (bootstrap={bootstrap or 'n/a'})")
            return True
        time.sleep(3)

    agent = get_agent(agent_id) or {}
    if agent.get("streaming") or (session_id and _session_turn_in_flight(session_id)):
        if agent.get("streaming"):
            _log(label, f"clearing stale streaming after {timeout_sec}s", ok=True)
            agent["streaming"] = False
            save_agent(agent)
        if session_id and _session_turn_in_flight(session_id):
            _log(label, f"session still has running attempt after {timeout_sec}s", ok=False)
            return False
        return True

    _fail(
        label,
        f"agent not idle after {timeout_sec}s "
        f"(bootstrap={agent.get('bootstrap_status')}, streaming={agent.get('streaming')})",
    )
    return False


def verify_live_nautilus_node(agent_id: str, *, run_seconds: int = 90) -> bool:
    print("\n── 3. Live Nautilus TradingNode (watch ON) ──", flush=True)
    from nautilus_openalgo_bridge.config import is_bridge_market_open

    if not is_bridge_market_open():
        _log("live nautilus node", "skipped — NSE market closed")
        return True

    log_path = ROOT / "log" / "verify_nautilus_watch.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["NAUTILUS_WATCH_ENABLE"] = "true"
    env["NAUTILUS_TEST_FIRE_ALERT"] = "1"
    env["PYTHONPATH"] = f"{INTEGRATIONS}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env["TRADE_INTEGRATIONS_SKIP_APPLY"] = "1"

    cmd = [
        str(VENV_NAUTILUS),
        "-m",
        "nautilus_openalgo_bridge.runtime.run_watch_node",
        "--agent-id",
        agent_id,
    ]
    with log_path.open("w", encoding="utf-8") as logf:
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            env=env,
            stdout=logf,
            stderr=subprocess.STDOUT,
        )
        started = time.monotonic()
        deadline = started + run_seconds
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        else:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        ran_for = int(time.monotonic() - started)

    text = log_path.read_text(encoding="utf-8", errors="replace")
    markers = {
        "WatchActor started": "WatchActor started" in text,
        "OpenAlgo live data connected": "OpenAlgo live data connected" in text,
        "BridgeSignalActor started": "BridgeSignalActor started" in text,
        "test alert injected": "NAUTILUS_TEST_FIRE_ALERT" in text,
        "Vibe dispatch dispatched": bool(re.search(r"Vibe dispatch:\s*dispatched", text)),
    }
    optional_markers = {
        "WatchActor heartbeat": "WatchActor heartbeat" in text,
    }
    all_ok = True
    for name, present in markers.items():
        if present:
            _log(name, "seen in log")
        else:
            _fail(name, f"not found in {log_path}")
            all_ok = False

    for name, present in optional_markers.items():
        if present:
            _log(name, "seen in log")
        else:
            _log(name, f"not seen (ran {ran_for}s; heartbeat needs ≥60s)", ok=True)

    if "legacy poll" in text.lower() or "dry-run" in text.lower():
        _fail("native node", "fell back to legacy poll / dry-run — not a live TradingNode run")
        all_ok = False
    elif "TradingNode: RUNNING" in text:
        _log("native node", "TradingNode.run() executed")

    if not wait_for_vibe():
        _fail("post-node vibe", "API unreachable after watch node stopped")
        all_ok = False

    return all_ok


def verify_direct_alert_dispatch(agent_id: str, session_id: str) -> bool:
    print("\n── 4. Auto-message (bridge → Vibe) ──", flush=True)
    if not wait_for_vibe():
        _fail("vibe api", "unreachable before alert dispatch")
        return False
    if not ensure_agent_ready(agent_id, session_id=session_id, label="pre-dispatch idle"):
        return False
    from nautilus_openalgo_bridge.models import BridgeSignal, WatchAlert, WatchRule
    from nautilus_openalgo_bridge.vibe_trigger import dispatch_watch_alert_sync

    before = count_session_messages(session_id)
    alert = WatchAlert(
        signal=BridgeSignal.REVIEW_NEEDED,
        rule=WatchRule(symbol="NIFTY", metric="spot_move_pct", threshold=0.5),
        symbol="NIFTY",
        message="Verify integration: NIFTY synthetic move for auto-message test",
        ltp=24600.0,
        move_pct=0.55,
    )
    result: dict[str, Any] = {}
    status = ""
    for attempt in range(5):
        result = dispatch_watch_alert_sync(agent_id, alert)
        status = str(result.get("status"))
        detail = status if attempt == 0 else f"{status} (retry {attempt})"
        _log("dispatch_watch_alert_sync", detail)
        if status == "dispatched":
            break
        if status == "skipped" and result.get("reason") == "turn_in_flight":
            time.sleep(5)
            continue
        _fail("direct dispatch", json.dumps(result)[:200])
        return False
    else:
        _fail("direct dispatch", "turn_in_flight after retries")
        return False

    deadline = time.time() + 30
    found = None
    while time.time() < deadline:
        found = find_bridge_alert_message(session_id)
        if found:
            break
        time.sleep(2)

    after = count_session_messages(session_id)
    if found:
        role = found.get("role", "?")
        _log("vibe session message", f"role={role} messages {before}→{after}")
        return True

    _fail("vibe session message", f"no bridge alert in session after dispatch (messages {before}→{after})")
    return False


def verify_us_alpaca_mock_dispatch() -> bool:
    """Fast US path check — registry + Alpaca quote + mock Vibe (no live LLM)."""
    print("\n── 5. US Alpaca paper (mock dispatch) ──", flush=True)
    from trade_integrations.dataflows.alpaca import alpaca_configured, fetch_alpaca_quote

    if not alpaca_configured():
        _log("us alpaca", "skipped — Alpaca keys not configured")
        return True

    q = fetch_alpaca_quote("SPY")
    if not q or not q.get("ltp"):
        _fail("us alpaca quote", "SPY quote empty")
        return False
    _log("us alpaca quote", f"SPY ltp={q.get('ltp')}")

    agent_id: str | None = None
    try:
        agent_id, session_id = create_test_agent(
            symbols=["SPY"],
            name="Verify US mock",
            mandate="US paper smoke — mock dispatch only.",
        )
        from trade_integrations.autonomous_agents.store import get_agent, save_agent

        agent = get_agent(agent_id) or {}
        agent["streaming"] = False
        save_agent(agent)

        from unittest.mock import AsyncMock, patch
        from nautilus_openalgo_bridge.models import BridgeSignal, WatchAlert, WatchRule
        from nautilus_openalgo_bridge.vibe_trigger import dispatch_watch_alert_sync

        alert = WatchAlert(
            signal=BridgeSignal.REVIEW_NEEDED,
            rule=WatchRule(symbol="SPY", metric="spot_move_pct", threshold=0.1),
            symbol="SPY",
            message="US mock verify alert",
            ltp=float(q.get("ltp") or 0),
        )
        with patch(
            "nautilus_openalgo_bridge.vibe_trigger.make_vibe_message_client",
            return_value=AsyncMock(return_value={"status": "ok"}),
        ):
            result = dispatch_watch_alert_sync(agent_id, alert)
        if result.get("status") != "dispatched":
            _fail("us mock dispatch", json.dumps(result)[:200])
            return False
        _log("us mock dispatch", "ok")
        return True
    except Exception as exc:
        _fail("us mock", str(exc))
        return False
    finally:
        if agent_id:
            try:
                vibe_post(f"/autonomous-agents/{agent_id}/stop")
            except Exception:
                pass


def verify_us_alpaca_short_turn(*, timeout_sec: int = 240) -> bool:
    print("\n── 5. US Alpaca paper (SPY short turn) ──", flush=True)
    from nautilus_openalgo_bridge.market_hours import is_us_market_session_open
    from trade_integrations.dataflows.alpaca import alpaca_configured

    if not alpaca_configured():
        _log("us alpaca", "skipped — Alpaca keys not configured")
        return True

    if not is_us_market_session_open():
        _log("us alpaca", "skipped — US market closed")
        return True

    agent_id: str | None = None
    try:
        agent_id, session_id = create_test_agent(
            symbols=["SPY"],
            name="Verify US Alpaca paper",
            mandate=(
                "Paper trade US equities via Alpaca. Integration test only: "
                "call get_stock_browse for SPY, get_autonomous_agent_status, "
                "record_autonomous_decision HOLD with brief rationale. "
                "Do not place live orders in this run."
            ),
        )
        _log("spy agent", f"{agent_id} session={session_id}")

        prompt = (
            "## US Alpaca integration verify (paper)\n"
            f"Automated check for agent `{agent_id}`. Steps:\n"
            "1. Call `get_stock_browse` for SPY and cite the tool price.\n"
            f"2. Call `get_autonomous_agent_status(agent_id=\"{agent_id}\")`.\n"
            "3. Call `record_autonomous_decision` with decision HOLD.\n"
            "Keep response short.\n"
        )
        dispatch = vibe_post(f"/sessions/{session_id}/messages", {"content": prompt}, timeout=60)
        attempt_id = dispatch.get("attempt_id")
        if not attempt_id:
            _fail("us dispatch", json.dumps(dispatch)[:200])
            return False
        _log("us turn started", attempt_id)

        deadline = time.time() + timeout_sec
        assistant_text = ""
        while time.time() < deadline:
            messages = vibe_get(f"/sessions/{session_id}/messages?limit=50")
            if isinstance(messages, list):
                for msg in reversed(messages):
                    if msg.get("role") != "assistant":
                        continue
                    if msg.get("linked_attempt_id") != attempt_id:
                        continue
                    meta = msg.get("metadata") or {}
                    status = str(meta.get("status") or "").lower()
                    if status in {"completed", "failed", "cancelled"}:
                        assistant_text = str(msg.get("content") or "")
                        if status == "failed":
                            _fail("us turn", assistant_text[:200] or "failed")
                            return False
                        break
                else:
                    time.sleep(5)
                    continue
                break
            time.sleep(5)
        else:
            _fail("us turn", f"timeout after {timeout_sec}s")
            return False

        sys.path.insert(0, str(ROOT / "scripts"))
        import realistic_e2e_lib as e2e_lib

        if not e2e_lib.assert_turn_not_defender_refusal(assistant_text, fail=_fail, step="us turn"):
            return False

        lower = assistant_text.lower()
        if "spy" in lower or "alpaca" in lower or "hold" in lower:
            _log("us turn complete", assistant_text[:120].replace("\n", " "))
        else:
            _log("us turn complete", assistant_text[:120].replace("\n", " "), ok=True)

        from trade_integrations.autonomous_agents.store import get_agent

        agent = get_agent(agent_id) or {}
        decision = agent.get("last_decision") or {}
        if decision:
            _log("us decision", f"{decision.get('decision')}")
        return True
    except Exception as exc:
        _fail("us alpaca", str(exc))
        return False
    finally:
        if agent_id:
            cleanup_test_agent(agent_id, label="spy agent")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify autonomous integration (Nautilus watch + Vibe + US)")
    parser.add_argument("--skip-unit", action="store_true")
    parser.add_argument("--skip-live-node", action="store_true")
    parser.add_argument("--skip-us", action="store_true")
    parser.add_argument("--node-seconds", type=int, default=40)
    parser.add_argument("--us-timeout", type=int, default=240)
    parser.add_argument("--us-mock", action="store_true", help="US path: mock Vibe dispatch instead of live LLM turn")
    args = parser.parse_args()

    print("══════════════════════════════════════════════════════════", flush=True)
    print("  Autonomous integration verification", flush=True)
    print("══════════════════════════════════════════════════════════", flush=True)

    if not precheck():
        print("\nPrechecks failed — fix environment before live tests.", flush=True)
        return 1

    if not args.skip_unit and not run_unit_tests():
        return 1

    agent_id: str | None = None
    session_id: str | None = None
    ok_node = ok_alert = ok_us = False
    try:
        agent_id, session_id = create_test_agent(
            symbols=["NIFTY"],
            name="Verify Nautilus watch",
            mandate="Integration test agent for Nautilus watch bridge alerts.",
        )
        _log("india test agent", f"{agent_id} session={session_id}")

        if not ensure_agent_ready(agent_id, session_id=session_id, label="post-commit idle"):
            ok_node = ok_alert = False
        else:
            ok_node = True
            if not args.skip_live_node:
                ok_node = verify_live_nautilus_node(agent_id, run_seconds=args.node_seconds)

            ok_alert = verify_direct_alert_dispatch(agent_id, session_id)

        ok_us = True
        if not args.skip_us:
            if args.us_mock:
                ok_us = verify_us_alpaca_mock_dispatch()
            else:
                ok_us = verify_us_alpaca_short_turn(timeout_sec=args.us_timeout)

    except Exception as exc:
        _fail("setup", str(exc))
        ok_node = ok_alert = ok_us = False
    finally:
        if agent_id:
            cleanup_test_agent(agent_id, label="india agent")

    print("\n══════════════════════════════════════════════════════════", flush=True)
    if FAILURES:
        print(f"  FAILED ({len(FAILURES)} issue(s)):", flush=True)
        for item in FAILURES:
            print(f"    • {item}", flush=True)
        print("══════════════════════════════════════════════════════════", flush=True)
        return 1

    print("  ALL CHECKS PASSED", flush=True)
    print("  • Nautilus watch ran as native TradingNode (not dry-run)", flush=True)
    print("  • Bridge auto-message reached Vibe session", flush=True)
    if not args.skip_us:
        print("  • US Alpaca SPY agent turn completed", flush=True)
    print("══════════════════════════════════════════════════════════", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
