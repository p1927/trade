#!/usr/bin/env python3
"""Full Vibe LLM turn E2E for autonomous agent (paper only).

Creates agent → dispatches research turn → waits for completion →
verifies watch_spec / handoff / decision → optional EXIT cleanup.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INTEGRATIONS = ROOT / "integrations"
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


def _log(step: str, detail: str = "", *, ok: bool = True) -> None:
    mark = "✓" if ok else "✗"
    msg = f"  {mark} {step}"
    if detail:
        msg += f" — {detail}"
    print(msg, flush=True)


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("VIBE_API_AUTH_KEY") or os.getenv("API_AUTH_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _vibe_base() -> str:
    return os.getenv("VIBE_BACKEND_URL", "http://127.0.0.1:8899").rstrip("/")


def vibe_post(path: str, payload: dict[str, Any] | None = None, *, timeout: int = 120) -> Any:
    url = f"{_vibe_base()}{path}"
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def vibe_get(path: str, *, timeout: int = 60) -> Any:
    url = f"{_vibe_base()}{path}"
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_for_attempt(
    session_id: str,
    attempt_id: str,
    *,
    timeout_sec: int = 900,
    poll_sec: float = 5.0,
) -> dict[str, Any]:
    """Poll session messages until the assistant reply for attempt_id completes."""
    deadline = time.time() + timeout_sec
    last_assistant = None
    while time.time() < deadline:
        messages = vibe_get(f"/sessions/{session_id}/messages?limit=50")
        if not isinstance(messages, list):
            time.sleep(poll_sec)
            continue
        for msg in reversed(messages):
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "assistant":
                continue
            if msg.get("linked_attempt_id") != attempt_id:
                continue
            meta = msg.get("metadata") or {}
            status = str(meta.get("status") or "").lower()
            if status in {"completed", "failed", "cancelled"}:
                return msg
            last_assistant = msg
            break
        else:
            # running — show progress snippet
            if last_assistant is None:
                for msg in reversed(messages):
                    if msg.get("role") == "assistant" and msg.get("linked_attempt_id") == attempt_id:
                        preview = str(msg.get("content") or "")[:120].replace("\n", " ")
                        if preview:
                            print(f"    … turn in progress: {preview}", flush=True)
                        break
        time.sleep(poll_sec)
    raise TimeoutError(f"attempt {attempt_id} did not complete within {timeout_sec}s")


def main() -> int:
    parser = argparse.ArgumentParser(description="Full Vibe LLM autonomous agent paper E2E")
    parser.add_argument("--timeout", type=int, default=900, help="Max seconds to wait for LLM turn")
    parser.add_argument("--agent-id", default=None, help="Reuse running agent instead of creating new")
    parser.add_argument("--skip-cleanup", action="store_true")
    parser.add_argument("--skip-exit", action="store_true")
    args = parser.parse_args()

    from nautilus_openalgo_bridge.config import get_bridge_config
    from nautilus_openalgo_bridge.execute import execute_intent
    from nautilus_openalgo_bridge.handoff import handoff_path, load_handoff
    from nautilus_openalgo_bridge.models import ExecutionIntent, IntentAction
    from nautilus_openalgo_bridge.openalgo_client import get_openalgo_client
    from nautilus_openalgo_bridge.reconcile import open_positions_from_book
    from trade_integrations.autonomous_agents.proposals import propose_autonomous_agent
    from trade_integrations.autonomous_agents.store import get_agent, save_agent
    from trade_integrations.autonomous_agents.turns import build_full_reasoning_prompt

    print("══════════════════════════════════════════════════════════", flush=True)
    print("  Vibe LLM full turn — autonomous agent (paper)", flush=True)
    print("══════════════════════════════════════════════════════════", flush=True)

    errors = 0

    def fail(step: str, detail: str) -> None:
        nonlocal errors
        errors += 1
        _log(step, detail, ok=False)

    # Paper mode gate
    try:
        client = get_openalgo_client()
        if not client.ensure_analyzer_mode():
            fail("paper mode", "OpenAlgo analyzer mode not active")
        else:
            _log("paper mode", "analyzer active")
    except Exception as exc:
        fail("paper mode", str(exc))
        return 1

    agent_id = args.agent_id
    session_id: str | None = None

    if not agent_id:
        try:
            proposal = propose_autonomous_agent(
                symbols=["NIFTY"],
                name="Vibe LLM E2E paper",
                mandate=(
                    "Paper trade NIFTY options autonomously. On this first research turn: "
                    "load options research, pick ONE low-risk paper strategy (prefer ATM short straddle "
                    "or iron fly with 1 lot), show charges, execute via execute_auto_paper_basket if "
                    "confidence ≥ threshold, then call set_agent_watch_spec with spot/VIX rules."
                ),
                mode="paper",
                confidence_threshold=65,
                budget_inr=20_000,
                max_daily_loss_inr=2_000,
                watch_interval_min=7,
            )
            commit = vibe_post(
                "/autonomous-agents/commit",
                {"proposal_id": proposal["proposal_id"], "consent_ack": True},
            )
            agent_id = commit.get("agent_id") or (commit.get("agent") or {}).get("id")
            session_id = commit.get("vibe_session_id") or (commit.get("agent") or {}).get("vibe_session_id")
            _log("create agent", f"{agent_id} session={session_id}")
        except Exception as exc:
            fail("create agent", str(exc))
            return 1
    else:
        agent = get_agent(agent_id)
        if not agent:
            fail("load agent", agent_id)
            return 1
        session_id = str(agent.get("vibe_session_id") or "")
        _log("reuse agent", f"{agent_id} session={session_id}")

    assert agent_id and session_id

    agent = get_agent(agent_id) or {}
    # Allow paper tool use outside regular hours for integration test
    mc = dict(agent.get("mandate_config") or {})
    mc["market_hours_only"] = False
    agent["mandate_config"] = mc
    constraints = dict(agent.get("constraints") or {})
    constraints["confidence_threshold"] = 65
    agent["constraints"] = constraints
    save_agent(agent)

    # Reconcile stale execution ledger vs OpenAlgo positionbook
    try:
        from trade_integrations.auto_paper.market_feedback import build_market_feedback

        fb = build_market_feedback(ticker=str(agent.get("symbols", ["NIFTY"])[0]))
        _log("ledger reconcile", f"open_positions={len(fb.get('open_positions') or [])}")
    except Exception as exc:
        _log("ledger reconcile", str(exc), ok=False)

    prompt = build_full_reasoning_prompt(agent=agent, turn_kind="research")
    prompt += (
        "\n\n## E2E integration test (paper only)\n"
        "This is an automated integration run. You MUST:\n"
        "1. Call `get_autonomous_agent_status` and OpenAlgo MCP tools (browse/chain/widget).\n"
        "2. If viable, enter a **1-lot** paper position via `execute_auto_paper_basket`.\n"
        "3. Call `set_agent_watch_spec` with at least NIFTY spot_move_pct and INDIAVIX level rules.\n"
        "4. Call `record_autonomous_decision` with ENTER/HOLD and rationale.\n"
        "Do not ask the user for confirmation — paper mode only.\n"
    )

    # --- Dispatch LLM turn ---
    try:
        ping = vibe_get("/health")
        _log("vibe api", str(ping.get("status", "ok")))
    except Exception:
        _log("vibe api", "health check skipped")

    attempt_id: str | None = None
    try:
        _log("dispatch turn", "POST /sessions/.../messages (may take several minutes)")
        dispatch = vibe_post(
            f"/sessions/{session_id}/messages",
            {"content": prompt},
            timeout=180,
        )
        attempt_id = dispatch.get("attempt_id")
        if not attempt_id:
            fail("dispatch turn", json.dumps(dispatch)[:200])
        else:
            _log("attempt started", attempt_id)
    except Exception as exc:
        fail("dispatch turn", str(exc))
        return 1

    assert attempt_id

    # --- Wait for completion ---
    try:
        assistant = wait_for_attempt(session_id, attempt_id, timeout_sec=args.timeout)
        meta = assistant.get("metadata") or {}
        status = meta.get("status", "?")
        preview = str(assistant.get("content") or "")[:240].replace("\n", " ")
        _log("turn complete", f"status={status} | {preview}")
        if str(status).lower() == "failed":
            fail("turn result", preview or "attempt failed")
    except TimeoutError as exc:
        fail("turn wait", str(exc))
    except Exception as exc:
        fail("turn wait", str(exc))

    # --- Verify agent artifacts ---
    agent = get_agent(agent_id) or {}
    watch_rules = (agent.get("watch_spec") or {}).get("rules") or []
    if watch_rules:
        _log("watch_spec on agent", f"{len(watch_rules)} rules")
    else:
        fail("watch_spec on agent", "missing — set_agent_watch_spec not called?")

    handoff = load_handoff(agent_id)
    if handoff_path(agent_id).is_file():
        _log("handoff file", f"legs={len(handoff.legs if handoff else [])} underlying={getattr(handoff, 'underlying', '?')}")
    else:
        _log("handoff file", "not created (OK if HOLD only)")

    last_decision = agent.get("last_decision") or {}
    if last_decision:
        _log("last decision", f"{last_decision.get('decision')} — {str(last_decision.get('rationale') or '')[:80]}")
    else:
        _log("last decision", "none recorded (agent may have HOLD without record_autonomous_decision)")

    positions = open_positions_from_book(client.get_position_book())
    _log("open positions", str(len(positions)))

    # --- EXIT cleanup if positions ---
    if positions and not args.skip_exit:
        try:
            result = execute_intent(
                ExecutionIntent(
                    action=IntentAction.EXIT,
                    agent_id=agent_id,
                    rationale="E2E cleanup flatten",
                    underlying="NIFTY",
                    strategy="vibe_e2e_cleanup",
                ),
                client=client,
            )
            remaining = open_positions_from_book(client.get_position_book())
            _log("cleanup EXIT", f"status={result.get('status')} remaining={len(remaining)}")
        except Exception as exc:
            fail("cleanup EXIT", str(exc))

    if not args.skip_cleanup:
        try:
            vibe_post(f"/autonomous-agents/{agent_id}/stop")
            _log("stop agent", agent_id)
        except Exception as exc:
            _log("stop agent", str(exc), ok=False)

    print("══════════════════════════════════════════════════════════", flush=True)
    if errors:
        print(f"  Finished with {errors} error(s)", flush=True)
        return 1
    print("  Vibe LLM E2E OK", flush=True)
    print("══════════════════════════════════════════════════════════", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
