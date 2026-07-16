#!/usr/bin/env python3
"""Smoke test for hub research + TradingAgents debate integration."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_SRC = ROOT / "vibetrading" / "agent"
sys.path.insert(0, str(ROOT / "integrations"))
sys.path.insert(0, str(AGENT_SRC))


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")
    raise SystemExit(1)


def test_symbol_detect() -> None:
    from src.trade.symbol_detect import detect_finalize_intent, extract_primary_ticker

    assert extract_primary_ticker("NIFTY options strategy") == "NIFTY"
    assert extract_primary_ticker("Should I buy RELIANCE stock?") == "RELIANCE"
    assert extract_primary_ticker("hello world") is None
    assert detect_finalize_intent("please finalize this plan")
    ok("symbol detection")


def test_hub_bridge() -> None:
    os.environ.setdefault("TRADE_STACK_ROOT", str(ROOT))
    from src.trade.hub_bridge import (
        ensure_trade_stack_path,
        load_debate_artifact,
        load_hub_plan_artifact,
        trade_repo_root,
    )

    assert trade_repo_root() == ROOT
    ensure_trade_stack_path()
    plan = load_hub_plan_artifact("NIFTY", "options")
    if plan is None:
        fail("NIFTY options plan missing from hub — run: python scripts/run_options_research.py NIFTY")
    assert plan.get("underlying") or plan.get("ticker")
    ok(f"hub plan loaded for NIFTY ({plan.get('recommended_name') or 'plan'})")
    debate = load_debate_artifact("NIFTY")
    if debate:
        ok(f"cached agent debate for NIFTY (rating={debate.get('rating')})")
    else:
        ok("no cached agent debate for NIFTY (expected until finalize)")


def _get(url: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode() if exc.fp else "{}"
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"detail": body}
        return exc.code, payload


def test_vibe_api(base: str = "http://127.0.0.1:8899") -> None:
    status, health = _get(f"{base}/health")
    if status != 200:
        fail(f"Vibe API not reachable at {base} (status={status})")
    ok(f"Vibe API health: {health.get('status', health)}")

    status, plan = _get(f"{base}/trade/hub-plan?ticker=NIFTY&asset=options")
    if status != 200:
        fail(f"GET /trade/hub-plan failed: {status} {plan}")
    if plan.get("status") != "ok" or not plan.get("artifact"):
        fail(f"hub-plan unexpected payload: {plan}")
    ok("GET /trade/hub-plan?ticker=NIFTY")

    status, debate = _get(f"{base}/trade/agent-debate?ticker=NIFTY")
    if status != 200:
        fail(f"GET /trade/agent-debate failed: {status} {debate}")
    ok(f"GET /trade/agent-debate (status={debate.get('status')})")


def wait_for_api(base: str, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _ = _get(f"{base}/health")
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def main() -> int:
    print("=== Research integration smoke test ===\n")
    print("[1] Unit checks")
    test_symbol_detect()
    test_hub_bridge()

    base = os.getenv("VIBE_API_BASE", "http://127.0.0.1:8899")
    print(f"\n[2] Live API ({base})")
    if not wait_for_api(base, timeout=3):
        print("  ⚠ Vibe API not running — start with: ./start.sh  OR  vibe-trading serve --port 8899")
        print("  Skipping live API checks.")
        return 0

    test_vibe_api(base)
    print("\nAll smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
