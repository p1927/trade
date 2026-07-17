#!/usr/bin/env python3
"""Verify OpenAlgo host, API key, broker session, and Vibe MCP env parity."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations"))

from trade_integrations.env import ensure_openalgo_env, trade_repo_root


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"OK: {msg}")


def main() -> int:
    from trade_integrations.env import ensure_vibe_stack_heal

    ensure_vibe_stack_heal()
    cfg = ensure_openalgo_env(root=trade_repo_root())
    host = cfg["host"]
    api_key = cfg["api_key"]
    if not api_key:
        _fail("OPENALGO_API_KEY missing in trade .env")

    try:
        import requests

        ping = requests.post(
            f"{host}/api/v1/ping",
            json={"apikey": api_key},
            timeout=15,
        )
        ping_body = ping.json() if ping.content else {}
    except Exception as exc:
        _fail(f"cannot reach OpenAlgo at {host}: {exc}")

    if not ping.ok:
        msg = ping_body.get("message") if isinstance(ping_body, dict) else str(ping_body)
        _fail(f"ping HTTP {ping.status_code}: {msg}")
    _ok(f"ping {host}")

    try:
        quotes = requests.post(
            f"{host}/api/v1/quotes",
            json={"apikey": api_key, "symbol": "NIFTY", "exchange": "NSE_INDEX"},
            timeout=15,
        )
        qbody = quotes.json() if quotes.content else {}
    except Exception as exc:
        _fail(f"quotes probe failed: {exc}")

    if not quotes.ok:
        msg = qbody.get("message") if isinstance(qbody, dict) else str(qbody)
        code = qbody.get("error_code") if isinstance(qbody, dict) else None
        hint = " (re-login broker in OpenAlgo UI)" if "apikey" in str(msg).lower() or code else ""
        _fail(f"quotes HTTP {quotes.status_code}: {msg}{hint}")
    _ok("NIFTY quotes (broker session live)")

    from datetime import date, timedelta

    start = (date.today() - timedelta(days=10)).isoformat()
    end = date.today().isoformat()
    try:
        history = requests.post(
            f"{host}/api/v1/history",
            json={
                "apikey": api_key,
                "symbol": "NIFTY",
                "exchange": "NSE_INDEX",
                "interval": "D",
                "start_date": start,
                "end_date": end,
            },
            timeout=30,
        )
        hbody = history.json() if history.content else {}
    except Exception as exc:
        _fail(f"history probe failed: {exc}")

    if not history.ok:
        msg = hbody.get("message") if isinstance(hbody, dict) else str(hbody)
        _fail(f"history HTTP {history.status_code}: {msg}")
    rows = hbody.get("data") or []
    if not rows:
        _fail("history returned no rows for NIFTY")
    last_ts = rows[-1].get("timestamp")
    if not last_ts or int(last_ts) < 1_000_000_000:
        _fail(f"history timestamp looks invalid: {last_ts}")
    _ok(f"NIFTY daily history ({len(rows)} bars, last ts={last_ts})")

    agent_json = Path.home() / ".vibe-trading" / "agent.json"
    if agent_json.is_file():
        try:
            payload = json.loads(agent_json.read_text(encoding="utf-8"))
            mcp_args = (
                payload.get("mcpServers", {})
                .get("openalgo", {})
                .get("args")
                or []
            )
            if len(mcp_args) >= 1 and str(mcp_args[0]) != api_key:
                _fail("MCP agent.json openalgo key differs from root .env — run: python scripts/setup_vibe.py --force-env")
            _ok("MCP agent.json key matches root .env")
        except (OSError, json.JSONDecodeError):
            pass

    print("OpenAlgo health check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
