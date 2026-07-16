"""Persistent paper-trading agent runner (LiveRunner-style for India OpenAlgo)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

from trade_integrations.auto_paper.agent_mandate import build_agent_turn_prompt, is_agent_session_active
from trade_integrations.auto_paper.audit import write_paper_action
from trade_integrations.auto_paper.config import get_auto_paper_config
from trade_integrations.auto_paper.engine import is_market_session_open, run_auto_paper_tick
from trade_integrations.auto_paper.lifecycle import sync_lifecycle_from_positions
from trade_integrations.auto_paper.market_feedback import build_market_feedback
from trade_integrations.auto_paper.openalgo_client import OpenAlgoClient
from trade_integrations.auto_paper.reconcile import reconcile_paper_state
from trade_integrations.auto_paper.session_store import load_session, save_session

logger = logging.getLogger(__name__)

TICK_HALTED = "halted"
TICK_NO_SESSION = "no_session"
TICK_OUTSIDE_HOURS = "outside_hours"
TICK_RECONCILE_UNSAFE = "reconcile_unsafe"
TICK_INVOKED = "invoked"
TICK_FALLBACK = "deterministic_fallback"
TICK_ERROR = "error"

AgentCaller = Callable[[str, str], Awaitable[Mapping[str, Any]]]
_DEFAULT_POLL_MS = 300_000  # 5 min


@dataclass(frozen=True)
class PaperTickResult:
    outcome: str
    reason: str = ""
    agent_result: Mapping[str, Any] | None = None
    audit_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "agent_result": dict(self.agent_result) if self.agent_result else None,
            "audit_id": self.audit_id,
        }


def _check_daily_loss_halt(session: dict[str, Any]) -> str | None:
    cfg = get_auto_paper_config()
    try:
        client = OpenAlgoClient()
        funds = client.get_funds()
    except RuntimeError:
        return None
    available = funds.get("availablecash") or funds.get("available_balance")
    if available is None:
        return None
    try:
        current = float(available)
    except (TypeError, ValueError):
        return None
    starting = session.get("starting_balance")
    if starting is None:
        session["starting_balance"] = current
        save_session(session)
        return None
    try:
        loss = float(starting) - current
    except (TypeError, ValueError):
        return None
    if loss >= cfg.max_daily_loss_inr:
        return f"daily_loss_limit_{loss:.0f}_inr"
    return None


class PaperTradingAgentRunner:
    """Autonomous paper-trading runner: reconcile → lifecycle → agent turn."""

    def __init__(
        self,
        *,
        agent_caller: AgentCaller | None = None,
        poll_interval_ms: int = _DEFAULT_POLL_MS,
        fallback_deterministic: bool = True,
    ) -> None:
        self._agent_caller = agent_caller
        self._poll_interval_ms = max(30_000, poll_interval_ms)
        self._fallback_deterministic = fallback_deterministic
        self._running = False

    async def run_once(self) -> PaperTickResult:
        session = load_session()
        if not is_agent_session_active():
            return PaperTickResult(TICK_NO_SESSION, "session_inactive_or_halted")

        cfg = get_auto_paper_config()
        if not is_market_session_open(cfg):
            return PaperTickResult(TICK_OUTSIDE_HOURS, "outside_market_hours")

        halt_reason = _check_daily_loss_halt(session)
        if halt_reason:
            session["halted"] = True
            session["halt_reason"] = halt_reason
            save_session(session)
            write_paper_action("halt", outcome="blocked", detail={"reason": halt_reason})
            return PaperTickResult(TICK_HALTED, halt_reason)

        reconcile = reconcile_paper_state()
        if reconcile.requires_halt:
            session["halted"] = True
            session["halt_reason"] = "; ".join(reconcile.messages)
            save_session(session)
            write_paper_action("halt", outcome="blocked", detail={"reconcile": reconcile.__dict__})
            return PaperTickResult(TICK_RECONCILE_UNSAFE, session["halt_reason"])

        session = load_session()
        sync_lifecycle_from_positions(session)
        save_session(session)

        ticker = str(session.get("primary_ticker") or (session.get("watchlist") or ["NIFTY"])[0])
        feedback = build_market_feedback(ticker=ticker)
        session["last_market_feedback"] = feedback.get("summary")
        save_session(session)

        from dataclasses import asdict

        prompt = build_agent_turn_prompt(
            ticker=ticker,
            reconcile_report=asdict(reconcile),
            market_feedback=feedback,
        )

        vibe_session_id = session.get("vibe_session_id")
        if self._agent_caller and vibe_session_id:
            try:
                result = await self._agent_caller(str(vibe_session_id), prompt)
                audit = write_paper_action(
                    "turn_completed",
                    detail={"ticker": ticker, "summary": feedback.get("summary")},
                )
                session["last_agent_turn_at"] = datetime.now(timezone.utc).isoformat()
                save_session(session)
                return PaperTickResult(TICK_INVOKED, agent_result=result, audit_id=audit.get("audit_id"))
            except Exception as exc:
                logger.exception("agent turn failed: %s", exc)
                if not self._fallback_deterministic:
                    return PaperTickResult(TICK_ERROR, str(exc))
                write_paper_action("turn_failed", outcome="error", detail={"error": str(exc)})

        if self._fallback_deterministic:
            tick = await asyncio.to_thread(run_auto_paper_tick)
            audit = write_paper_action("deterministic_tick", detail={"status": tick.get("status")})
            return PaperTickResult(TICK_FALLBACK, tick.get("reason", ""), audit_id=audit.get("audit_id"))

        return PaperTickResult(TICK_ERROR, "no_agent_caller_configured")

    async def run_loop(self) -> None:
        self._running = True
        logger.info("Paper trading agent runner started (poll=%sms)", self._poll_interval_ms)
        while self._running:
            session = load_session()
            if not session.get("enabled"):
                logger.info("Paper session disabled — stopping runner")
                break
            if session.get("halted"):
                logger.warning("Paper session halted: %s", session.get("halt_reason"))
                break

            result = await self.run_once()
            logger.info("Paper tick: %s (%s)", result.outcome, result.reason or "ok")

            if result.outcome in {TICK_HALTED, TICK_RECONCILE_UNSAFE}:
                break

            await asyncio.sleep(self._poll_interval_ms / 1000.0)

    def stop(self) -> None:
        self._running = False


def make_vibe_agent_caller(base_url: str, *, api_key: str | None = None) -> AgentCaller:
    """HTTP caller for Vibe SessionService.send_message."""
    import urllib.error
    import urllib.request

    base = base_url.rstrip("/")

    async def _call(session_id: str, prompt: str) -> dict[str, Any]:
        import json

        url = f"{base}/sessions/{session_id}/messages"
        body = json.dumps({"content": prompt}).encode("utf-8")
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


def make_inprocess_agent_caller() -> AgentCaller | None:
    """Use loaded api_server SessionService when Vibe backend is in-process."""
    import sys

    host = sys.modules.get("api_server") or sys.modules.get("agent.api_server")
    if host is None:
        return None
    svc = host._get_session_service()
    if svc is None:
        return None

    async def _call(session_id: str, prompt: str) -> dict[str, Any]:
        return await svc.send_message(session_id, prompt)

    return _call


def ensure_vibe_session(*, ticker: str, base_url: str | None = None) -> str:
    """Create or reuse persistent Vibe session for paper agent."""
    import json
    import urllib.error
    import urllib.request

    session = load_session()
    existing = session.get("vibe_session_id")
    if existing:
        return str(existing)

    if base_url:
        from trade_integrations.auto_paper.vibe_research import paper_session_vibe_config

        url = f"{base_url.rstrip('/')}/sessions"
        body = json.dumps(
            {
                "title": f"paper-trading-agent:{ticker}",
                "config": paper_session_vibe_config(ticker=ticker),
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                sid = str(payload.get("session_id") or payload.get("id") or "")
                if sid:
                    session["vibe_session_id"] = sid
                    save_session(session)
                    return sid
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Could not create Vibe session: {exc.read().decode()}") from exc

    host = __import__("sys").modules.get("api_server")
    if host is not None:
        svc = host._get_session_service()
        if svc is not None:
            from trade_integrations.auto_paper.vibe_research import paper_session_vibe_config

            vibe_session = svc.create_session(
                title=f"paper-trading-agent:{ticker}",
                config=paper_session_vibe_config(ticker=ticker),
            )
            session["vibe_session_id"] = vibe_session.session_id
            save_session(session)
            return vibe_session.session_id

    raise RuntimeError("Vibe session service unavailable — start Vibe backend or pass --vibe-url")


def resolve_runner(*, vibe_url: str | None = None, poll_ms: int | None = None) -> PaperTradingAgentRunner:
    cfg = get_auto_paper_config()
    poll = poll_ms or int(getattr(cfg, "poll_interval_ms", 0) or _DEFAULT_POLL_MS)

    caller = make_inprocess_agent_caller()
    if caller is None and vibe_url:
        caller = make_vibe_agent_caller(vibe_url)

    return PaperTradingAgentRunner(
        agent_caller=caller,
        poll_interval_ms=poll,
        fallback_deterministic=True,
    )
