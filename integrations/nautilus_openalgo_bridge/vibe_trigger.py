"""Trigger Vibe autonomous agent turns on Nautilus watch alerts."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from nautilus_openalgo_bridge.config import BridgeConfig, get_bridge_config
from nautilus_openalgo_bridge.hub_paths import load_agent_json, save_agent_json
from nautilus_openalgo_bridge.models import QuoteSnapshot, WatchAlert

logger = logging.getLogger(__name__)


def get_agent(agent_id: str) -> dict[str, Any]:
    """Load agent JSON without importing trade_integrations (Nautilus venv safe)."""
    return load_agent_json(agent_id)


def save_agent(agent: dict[str, Any]) -> dict[str, Any]:
    return save_agent_json(agent)


def _vibe_api_key(config: BridgeConfig | None = None) -> str | None:
    cfg = config or get_bridge_config()
    key = (
        os.getenv("VIBE_API_AUTH_KEY")
        or os.getenv("API_AUTH_KEY")
        or cfg.vibe_api_key
        or ""
    ).strip()
    return key or None


def build_bridge_alert_block(
    alert: WatchAlert,
    quotes: dict[str, QuoteSnapshot] | None = None,
) -> str:
    quote_rows = {}
    if quotes:
        for symbol, snap in quotes.items():
            quote_rows[symbol] = {"ltp": snap.ltp, "exchange": snap.exchange, "fetched_at": snap.fetched_at}
    payload = {
        "source": "nautilus_openalgo_bridge",
        "alert": alert.to_dict(),
        "quotes": quote_rows,
    }
    exchange = str(alert.rule.exchange or "").upper()
    title = "Watch alert (US Alpaca)" if exchange == "US" else "Nautilus watch alert (bridge)"
    return (
        f"## {title}\n"
        f"```json\n{json.dumps(payload, indent=2)}\n```\n\n"
        f"**Alert:** {alert.message}\n"
        "Re-evaluate thesis and decide ENTER / ADJUST / EXIT / HOLD.\n"
    )


def build_minimal_revision_prompt(agent: dict[str, Any]) -> str:
    symbols = list(agent.get("symbols") or ["NIFTY"])
    focus = symbols[0] if symbols else "NIFTY"
    constraints = dict(agent.get("constraints") or {})
    threshold = int(constraints.get("confidence_threshold") or 75)
    mandate = str(agent.get("mandate") or "Paper trade autonomously.")
    return (
        f"\n# Autonomous strategy revision (bridge)\n\n"
        f"- Symbols: {', '.join(symbols)}\n"
        f"- Focus: {focus}\n"
        f"- Confidence threshold: {threshold}%\n"
        f"- Mandate: {mandate}\n\n"
        "Re-evaluate thesis after the alert. Output ENTER | ADJUST | EXIT | HOLD with rationale.\n"
        "Call `record_autonomous_decision` and update watch/handoff if needed.\n"
    )


def build_full_reasoning_prompt(*, agent: dict[str, Any], turn_kind: str = "research") -> str:
    """Prefer trade_integrations prompt when available; minimal fallback in Nautilus venv."""
    try:
        from trade_integrations.autonomous_agents.turns import (
            build_full_reasoning_prompt as _build,
        )

        return _build(agent=agent, turn_kind=turn_kind)
    except Exception:
        if turn_kind == "strategy_revision":
            return build_minimal_revision_prompt(agent)
        symbols = list(agent.get("symbols") or ["NIFTY"])
        return (
            f"\n# Autonomous agent turn ({turn_kind})\n"
            f"- Symbols: {', '.join(symbols)}\n"
            f"- Mandate: {agent.get('mandate') or 'Paper trade autonomously.'}\n"
        )


def build_alert_turn_prompt(
    *,
    agent: dict[str, Any],
    alert: WatchAlert,
    quotes: dict[str, QuoteSnapshot] | None = None,
) -> str:
    return build_bridge_alert_block(alert, quotes) + build_full_reasoning_prompt(
        agent=agent,
        turn_kind="strategy_revision",
    )


def make_vibe_message_client(
    config: BridgeConfig | None = None,
):
    """Return async (session_id, content) -> dict caller."""
    cfg = config or get_bridge_config()
    base = cfg.vibe_backend_url.rstrip("/")
    api_key = _vibe_api_key(cfg)

    async def _call(session_id: str, content: str) -> dict[str, Any]:
        url = f"{base}/sessions/{session_id}/messages"
        body = json.dumps({"content": content}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        def _post() -> dict[str, Any]:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=600) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Vibe API {exc.code}: {detail}") from exc

        return await asyncio.to_thread(_post)

    return _call


async def dispatch_watch_alert(
    agent_id: str,
    alert: WatchAlert,
    *,
    quotes: dict[str, QuoteSnapshot] | None = None,
    config: BridgeConfig | None = None,
) -> dict[str, Any]:
    """Send alert summary + full reasoning prompt to the agent's Vibe session."""
    agent = get_agent(agent_id)
    if not agent:
        return {"status": "error", "error": f"agent not found: {agent_id}"}
    if str(agent.get("status")) != "running":
        return {"status": "skipped", "reason": "agent_not_running"}

    session_id = str(agent.get("vibe_session_id") or "").strip()
    if not session_id:
        return {"status": "error", "error": "agent has no vibe_session_id"}

    if agent.get("streaming"):
        return {"status": "skipped", "reason": "turn_in_flight"}

    prompt = build_alert_turn_prompt(agent=agent, alert=alert, quotes=quotes)
    caller = make_vibe_message_client(config)

    agent["streaming"] = True
    agent["last_bridge_alert_at"] = alert.fired_at
    agent["last_bridge_alert"] = alert.to_dict()
    agent["last_revision_at"] = alert.fired_at
    save_agent(agent)

    try:
        result = await caller(session_id, prompt)
        return {"status": "dispatched", "session_id": session_id, "result": result}
    except RuntimeError as exc:
        logger.warning("Vibe dispatch failed for %s: %s", agent_id, exc)
        return {"status": "error", "error": str(exc)}
    finally:
        latest = get_agent(agent_id) or agent
        latest["streaming"] = False
        save_agent(latest)


def dispatch_watch_alert_sync(
    agent_id: str,
    alert: WatchAlert,
    *,
    quotes: dict[str, QuoteSnapshot] | None = None,
    config: BridgeConfig | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        dispatch_watch_alert(agent_id, alert, quotes=quotes, config=config)
    )


def ping_vibe_backend(config: BridgeConfig | None = None) -> dict[str, Any]:
    """Health check — GET /health or /sessions without creating a turn."""
    cfg = config or get_bridge_config()
    base = cfg.vibe_backend_url.rstrip("/")
    api_key = _vibe_api_key(cfg)
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    for path in ("/health", "/sessions"):
        url = f"{base}{path}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return {"status": "ok", "path": path, "code": resp.status, "body_preview": body[:200]}
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403) and not api_key:
                return {"status": "auth_required", "path": path, "code": exc.code}
            if exc.code == 404 and path == "/health":
                continue
            return {"status": "error", "path": path, "code": exc.code, "detail": exc.read().decode()[:200]}
        except urllib.error.URLError as exc:
            return {"status": "unreachable", "url": url, "error": str(exc.reason)}
    return {"status": "unknown"}
