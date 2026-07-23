#!/usr/bin/env python3
"""Dedicated live E2E: create → bootstrap → approve → watch → natural alert → user revise → re-approve."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "vibetrading" / "agent"))

import realistic_e2e_lib as lib  # noqa: E402

LOG_PATH = ROOT / "log" / "dedicated_e2e_run.jsonl"


def _log(phase: str, status: str, **detail: Any) -> None:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "status": status,
        **detail,
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
    mark = "PASS" if status == "pass" else ("FAIL" if status == "fail" else "INFO")
    msg = detail.get("detail") or detail.get("error") or ""
    print(f"[{mark}] {phase}: {msg}", flush=True)


def _session_text(session_id: str, limit: int = 100) -> list[dict]:
    msgs = lib.vibe_get(f"/sessions/{session_id}/messages?limit={limit}")
    return msgs if isinstance(msgs, list) else []


def _find_bridge_alert(msgs: list[dict], *, exclude: set[str] | None = None) -> dict | None:
    exclude = exclude or set()
    for msg in reversed(msgs):
        c = str(msg.get("content") or "")
        if "Nautilus watch alert" not in c:
            continue
        if any(x in c for x in exclude):
            continue
        return msg
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap-timeout", type=int, default=900)
    parser.add_argument("--alert-timeout", type=int, default=180)
    parser.add_argument("--reuse-agent", type=str, default="")
    parser.add_argument("--skip-cleanup", action="store_true")
    parser.add_argument("--cleanup-only", action="store_true")
    args = parser.parse_args()

    lib.load_env()
    failures = 0

    if not args.skip_cleanup:
        if not lib.ensure_stack_healthy():
            _log("stack_preflight", "fail", error="OpenAlgo or Vibe API not healthy")
            return 1
        _log("stack_preflight", "pass")
        cleanup = lib.cleanup_all_autonomous_agents()
        _log("cleanup", "pass", deleted=cleanup.get("deleted"), errors=cleanup.get("errors"))
        if args.cleanup_only:
            print(json.dumps(cleanup, indent=2))
            return 0 if not cleanup.get("errors") else 1

    # Phase 0 — create
    if args.reuse_agent:
        from trade_integrations.autonomous_agents.store import get_agent

        agent_id = args.reuse_agent.strip()
        agent = get_agent(agent_id) or {}
        session_id = str(agent.get("vibe_session_id") or "")
        _log("create", "pass", agent_id=agent_id, session_id=session_id, detail="reused agent")
    else:
        try:
            agent_id, session_id = lib.create_paper_agent(
                name="Dedicated E2E NIFTY hold-cash",
                mandate=(
                    "Watch NIFTY 50 index outlook only. Hold cash — no trades. "
                    "Report spot moves and outlook after each watch tick."
                ),
                symbols=["NIFTY"],
            )
            _log("create", "pass", agent_id=agent_id, session_id=session_id)
        except Exception as exc:
            _log("create", "fail", error=str(exc))
            return 1

    from trade_integrations.autonomous_agents.store import get_agent, save_agent

    agent = get_agent(agent_id) or {}
    schedules = dict(agent.get("schedules") or {})
    schedules["watch_ms"] = 60_000
    schedules["research_ms"] = 3_600_000
    agent["schedules"] = schedules
    mc = dict(agent.get("mandate_config") or {})
    mc["market_hours_only"] = False
    agent["mandate_config"] = mc
    save_agent(agent)

    from src.scheduled_research.autonomous_agent_jobs import register_agent_jobs

    register_agent_jobs(get_agent(agent_id) or agent)
    lib.ensure_agent_running_and_bootstrap(agent_id)

    # Phase 1 — bootstrap → awaiting_plan_approval
    try:
        agent = lib.wait_for_plan_approval_gate(agent_id, timeout_sec=args.bootstrap_timeout)
        _log(
            "bootstrap",
            "pass",
            bootstrap_status=agent.get("bootstrap_status"),
            widget=agent.get("active_trade_plan_widget_id"),
        )
    except Exception as exc:
        agent = get_agent(agent_id) or {}
        _log(
            "bootstrap",
            "fail",
            error=str(exc),
            bootstrap_status=agent.get("bootstrap_status"),
        )
        failures += 1
        if agent.get("bootstrap_status") != "awaiting_plan_approval":
            return 1

    # Phase 2 — approve
    widget_id = str(agent.get("active_trade_plan_widget_id") or "")
    try:
        lib.approve_agent_plan_via_api(agent_id, widget_id=widget_id or None)
        agent = get_agent(agent_id) or {}
        watches = lib.wait_for_registry_watches(agent_id, min_count=1, timeout_sec=30)
        _log(
            "approve",
            "pass",
            plan_approved_at=agent.get("plan_approved_at"),
            bootstrap_status=agent.get("bootstrap_status"),
            registry_watches=len(watches),
        )
        if not watches:
            _log("watchers_ui", "fail", error="no registry watches after approve")
            failures += 1
        else:
            _log("watchers_ui", "pass", watch_ids=[w.get("watch_id") for w in watches])
    except Exception as exc:
        _log("approve", "fail", error=str(exc))
        failures += 1
        return 1

    if not lib.ensure_nautilus_watch_running(timeout_sec=90):
        _log("nautilus_post_approve", "fail", error="Nautilus watch not running after approve")
        failures += 1
    else:
        _log("nautilus_post_approve", "pass")

    # Phase 3 — Nautilus registry
    reg_path = ROOT / "log" / "nautilus-watch.agents.json"
    in_registry = False
    if reg_path.is_file():
        reg = json.loads(reg_path.read_text())
        in_registry = any(
            str(a.get("agent_id")) == agent_id for a in (reg.get("agents") or [])
        )
    _log("nautilus_registry", "pass" if in_registry else "fail", in_registry=in_registry)
    if not in_registry:
        failures += 1

    # Phase 4 — arm fireable watch for natural Nautilus alert
    from trade_integrations.openalgo.market_data import fetch_openalgo_quote
    from trade_integrations.autonomous_agents.mcp_actions import activate_watch_spec_for_agent
    from trade_integrations.execution.profile import resolve_profile

    quote = fetch_openalgo_quote("NIFTY") or {}
    ltp = float(quote.get("ltp") or quote.get("last_price") or 0)
    if ltp <= 0:
        _log("simulator_quote", "fail", error=f"empty quote: {quote}")
        failures += 1
        ltp = 22365.0
    else:
        _log("simulator_quote", "pass", ltp=ltp, simulated=quote.get("simulated"))

    watch_spec = lib.build_fireable_watch_spec(symbol="NIFTY", ltp=ltp)
    agent = get_agent(agent_id) or {}
    agent["watch_spec"] = watch_spec
    save_agent(agent)
    profile = resolve_profile(agent=agent)
    activate_watch_spec_for_agent(agent_id, agent, watch_spec, profile=profile)
    _log("arm_watch", "pass", baseline=watch_spec["rules"][0].get("baseline_ltp"), threshold=0.001)

    # Phase 5 — wait for natural bridge alert (replay clock / Nautilus poll)
    baseline_msgs = _session_text(session_id)
    baseline_count = len(baseline_msgs)
    deadline = time.time() + args.alert_timeout
    natural_alert = None
    while time.time() < deadline:
        msgs = _session_text(session_id)
        natural_alert = _find_bridge_alert(
            msgs,
            exclude={"Live E2E", "synthetic E2E", "Verify integration"},
        )
        if natural_alert:
            break
        time.sleep(10)
    if natural_alert:
        _log(
            "natural_alert",
            "pass",
            detail=str(natural_alert.get("content") or "")[:200],
        )
    else:
        _log("natural_alert", "fail", error=f"no alert in {args.alert_timeout}s")
        failures += 1

    # Phase 5b — scheduled watch tick (system summary)
    from src.scheduled_research.store import ScheduledResearchJobStore

    job = ScheduledResearchJobStore().get(f"{agent_id}-watch")
    before_lw = (get_agent(agent_id) or {}).get("last_watch_at")
    time.sleep(max(15, 0))
    deadline = time.time() + 90
    watch_summary = False
    while time.time() < deadline:
        msgs = _session_text(session_id)
        for m in msgs:
            if "[autonomous_watch]" in str(m.get("content") or ""):
                watch_summary = True
        agent_now = get_agent(agent_id) or {}
        if watch_summary or agent_now.get("last_watch_at") != before_lw:
            _log(
                "scheduled_watch",
                "pass",
                last_watch_at=agent_now.get("last_watch_at"),
                job_status=getattr(job, "status", None),
            )
            break
        time.sleep(10)
    else:
        _log("scheduled_watch", "fail", error="no watch summary in 90s")
        failures += 1

    # Phase 6 — user guidance: change watch
    guidance = (
        "User guidance: simplify to hold cash — use a 1% spot-move watch only, "
        "remove option legs. Call set_agent_watch_spec(strategy=hold_cash) and acknowledge."
    )
    try:
        lib.vibe_post(f"/sessions/{session_id}/messages", {"content": guidance})
        _log("user_guidance", "pass", detail=guidance[:100])
    except Exception as exc:
        _log("user_guidance", "fail", error=str(exc))
        failures += 1

    # Phase 7 — wait for assistant reply
    start = len(_session_text(session_id))
    deadline = time.time() + 180
    reply = ""
    while time.time() < deadline:
        msgs = _session_text(session_id)
        if len(msgs) > start:
            for m in reversed(msgs):
                if m.get("role") == "assistant":
                    c = str(m.get("content") or "")
                    if len(c) > 100:
                        reply = c[:400]
                        break
        if reply and not (get_agent(agent_id) or {}).get("streaming"):
            break
        time.sleep(5)
    _log("agent_guidance_reply", "pass" if reply else "fail", detail=reply[:200] if reply else "timeout")

    agent = get_agent(agent_id) or {}
    ws = agent.get("watch_spec") or {}
    _log(
        "watch_spec_after_guidance",
        "info",
        bootstrap=agent.get("bootstrap_status"),
        rules=ws.get("rules"),
    )

    # Phase 8 — re-approve if plan gate re-opened
    if agent.get("bootstrap_status") == "awaiting_plan_approval":
        wid = str(agent.get("active_trade_plan_widget_id") or widget_id)
        try:
            lib.approve_agent_plan_via_api(agent_id, widget_id=wid or None)
            agent = get_agent(agent_id) or {}
            _log("re_approve", "pass", widget_id=wid)
        except Exception as exc:
            _log("re_approve", "fail", error=str(exc))
            failures += 1
    else:
        _log("re_approve", "info", detail="not required")

    # Final snapshot
    agent = get_agent(agent_id) or {}
    summary = {
        "agent_id": agent_id,
        "session_id": session_id,
        "bootstrap_status": agent.get("bootstrap_status"),
        "plan_approved_at": agent.get("plan_approved_at"),
        "watch_spec_rules": len((agent.get("watch_spec") or {}).get("rules") or []),
        "failures": failures,
    }
    _log("summary", "pass" if failures == 0 else "fail", **summary)
    print(json.dumps(summary, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
