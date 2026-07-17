"""Legacy poll-loop fallback (used for --dry-run and --legacy-poll)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from nautilus_openalgo_bridge.config import get_bridge_config, is_bridge_market_open, is_watch_enabled
from nautilus_openalgo_bridge.data_feed import OpenAlgoQuoteFeed
from nautilus_openalgo_bridge.handoff import handoff_mtime, load_agent_watch_spec, load_handoff
from nautilus_openalgo_bridge.intent_queue import process_pending_intents
from nautilus_openalgo_bridge.models import BridgeSignal, WatchSpec
from nautilus_openalgo_bridge.openalgo_client import get_openalgo_client
from nautilus_openalgo_bridge.reconcile import open_positions_from_book, total_unrealized_pnl
from nautilus_openalgo_bridge.stop_eval import evaluate_stop_rules
from nautilus_openalgo_bridge.thesis_eval import evaluate_thesis_for_agent
from nautilus_openalgo_bridge.watch_eval import evaluate_watch_spec

logger = logging.getLogger(__name__)


def _agent_mtime(agent_id: str) -> float | None:
    from nautilus_openalgo_bridge.hub_paths import agent_json_path

    path = agent_json_path(agent_id)
    if not path.is_file():
        return None
    return path.stat().st_mtime


def _default_watch_spec() -> WatchSpec:
    cfg = get_bridge_config()
    rules = [
        {"symbol": symbol, "metric": "spot_move_pct", "threshold": 0.5, "direction": "either"}
        for symbol in cfg.watch_symbols
        if symbol != "INDIAVIX"
    ]
    rules.append({"symbol": "INDIAVIX", "metric": "level_above", "threshold": 14.0})
    return WatchSpec.from_dict({"rules": rules})


def _resolve_watch_spec(agent_id: str | None) -> WatchSpec:
    if agent_id:
        handoff = load_handoff(agent_id)
        if handoff and handoff.watch_spec.rules:
            return handoff.watch_spec
        raw = load_agent_watch_spec(agent_id)
        if raw:
            return WatchSpec.from_dict(raw)
    return _default_watch_spec()


def maybe_reload_watch_spec(
    agent_id: str | None,
    spec: WatchSpec,
    *,
    last_handoff_mtime: float | None,
    last_agent_mtime: float | None,
) -> tuple[WatchSpec, float | None, float | None]:
    """Re-read watch spec when handoff or agent JSON changes on disk."""
    if not agent_id:
        return spec, last_handoff_mtime, last_agent_mtime

    mt = handoff_mtime(agent_id)
    am = _agent_mtime(agent_id)
    if mt is not None and mt != last_handoff_mtime:
        return _resolve_watch_spec(agent_id), mt, am if am is not None else last_agent_mtime
    if am is not None and am != last_agent_mtime:
        return _resolve_watch_spec(agent_id), last_handoff_mtime, am
    return spec, last_handoff_mtime, last_agent_mtime


def _dispatch_alerts(
    agent_id: str,
    alerts: list,
    *,
    quotes: dict,
    trigger_vibe: bool,
    market: str | None = None,
) -> list[dict]:
    dispatch_results: list[dict] = []
    if not trigger_vibe:
        return dispatch_results
    from nautilus_openalgo_bridge.config import allow_vibe_alert_outside_market_hours
    from nautilus_openalgo_bridge.market_hours import agent_market, is_market_open_for_market
    from nautilus_openalgo_bridge.signal_actions import dispatch_exit_intent
    from nautilus_openalgo_bridge.vibe_trigger import dispatch_thesis_alert_sync, dispatch_watch_alert_sync

    agent_market_code = (market or agent_market(agent_id)).upper()
    outside_hours = not allow_vibe_alert_outside_market_hours()

    for alert in alerts:
        if alert.signal == BridgeSignal.EXIT_NOW:
            dispatch_results.append(dispatch_exit_intent(agent_id, alert))
        elif alert.signal == BridgeSignal.THESIS_BROKEN:
            if outside_hours and not is_market_open_for_market(agent_market_code):
                dispatch_results.append({"status": "skipped", "reason": "outside_market_hours"})
                continue
            dispatch_results.append(dispatch_thesis_alert_sync(agent_id, alert, quotes=quotes))
        elif alert.signal == BridgeSignal.REVIEW_NEEDED:
            if outside_hours and not is_market_open_for_market(agent_market_code):
                dispatch_results.append({"status": "skipped", "reason": "outside_market_hours"})
                continue
            dispatch_results.append(dispatch_watch_alert_sync(agent_id, alert, quotes=quotes))
    return dispatch_results


def run_once(
    *,
    agent_id: str | None = None,
    baselines: dict[str, float] | None = None,
    trigger_vibe: bool = False,
    process_intents: bool = False,
) -> dict[str, Any]:
    cfg = get_bridge_config()
    feed = OpenAlgoQuoteFeed()
    spec = _resolve_watch_spec(agent_id)
    quotes = feed.poll()
    alerts = evaluate_watch_spec(spec, quotes, baselines=baselines or {})

    handoff = load_handoff(agent_id) if agent_id else None
    if handoff:
        try:
            client = get_openalgo_client(cfg)
            rows = open_positions_from_book(client.get_position_book())
            pnl = total_unrealized_pnl(rows)
            stop_alert = evaluate_stop_rules(handoff, quotes, unrealized_pnl_inr=pnl, config=cfg)
            if stop_alert is not None:
                alerts.insert(0, stop_alert)
        except RuntimeError as exc:
            logger.debug("stop rule eval skipped: %s", exc)

    if agent_id:
        focus = handoff.underlying if handoff else None
        if not focus:
            try:
                from trade_integrations.autonomous_agents.store import get_agent

                agent = get_agent(agent_id) or {}
                syms = list(agent.get("symbols") or ["NIFTY"])
                focus = syms[0]
            except Exception:
                focus = "NIFTY"
        quote = quotes.get(str(focus).upper()) if focus else None
        live_spot = quote.ltp if quote else None
        pnl = None
        if handoff:
            try:
                client = get_openalgo_client(cfg)
                rows = open_positions_from_book(client.get_position_book())
                pnl = total_unrealized_pnl(rows)
            except RuntimeError:
                pass
        thesis_alert = evaluate_thesis_for_agent(agent_id, live_spot=live_spot, position_pnl=pnl)
        if thesis_alert is not None:
            alerts.insert(0, thesis_alert)

    intent_results: list[dict] = []

    if process_intents:
        try:
            intent_results = process_pending_intents(client=get_openalgo_client(cfg))
        except RuntimeError as exc:
            logger.warning("intent queue processing failed: %s", exc)

    dispatch_results = (
        _dispatch_alerts(
            agent_id,
            alerts,
            quotes=quotes,
            trigger_vibe=bool(trigger_vibe and agent_id),
            market="IN",
        )
        if agent_id
        else []
    )

    return {
        "quotes": {k: v.to_dict() for k, v in quotes.items()},
        "alerts": [a.to_dict() for a in alerts],
        "dispatches": dispatch_results,
        "intents_processed": intent_results,
    }


def run_once_alpaca(
    *,
    agent_id: str | None = None,
    baselines: dict[str, float] | None = None,
    trigger_vibe: bool = False,
    process_intents: bool = False,
) -> dict[str, Any]:
    from nautilus_openalgo_bridge.alpaca_quote_feed import AlpacaQuoteFeed

    _ = process_intents
    cfg = get_bridge_config()
    feed = AlpacaQuoteFeed()
    spec = _resolve_watch_spec(agent_id)
    try:
        from trade_integrations.autonomous_agents.store import get_agent

        agent = get_agent(agent_id) if agent_id else None
        symbols = [str(s).upper() for s in (agent.get("symbols") or ["SPY"])] if agent else ["SPY"]
    except Exception:
        symbols = ["SPY"]
    quotes = feed.poll(symbols)
    alerts = evaluate_watch_spec(spec, quotes, baselines=baselines or {})

    handoff = load_handoff(agent_id) if agent_id else None
    if handoff:
        try:
            from trade_integrations.dataflows.alpaca import list_alpaca_positions

            rows = list_alpaca_positions()
            pnl = 0.0
            found = False
            for row in rows:
                raw = row.get("unrealized_pl")
                if raw is None:
                    continue
                pnl += float(raw)
                found = True
            stop_alert = evaluate_stop_rules(
                handoff,
                quotes,
                unrealized_pnl_inr=pnl if found else None,
                config=cfg,
            )
            if stop_alert is not None:
                alerts.insert(0, stop_alert)
        except Exception as exc:
            logger.debug("US stop rule eval skipped: %s", exc)

    if agent_id:
        focus = symbols[0] if symbols else "SPY"
        quote = quotes.get(focus)
        thesis_alert = evaluate_thesis_for_agent(agent_id, live_spot=quote.ltp if quote else None)
        if thesis_alert is not None:
            alerts.insert(0, thesis_alert)

    dispatch_results = (
        _dispatch_alerts(
            agent_id,
            alerts,
            quotes=quotes,
            trigger_vibe=bool(trigger_vibe and agent_id),
            market="US",
        )
        if agent_id
        else []
    )

    return {
        "quotes": {k: v.to_dict() for k, v in quotes.items()},
        "alerts": [a.to_dict() for a in alerts],
        "dispatches": dispatch_results,
        "intents_processed": [],
    }


def _poll_loop_market(agent_id: str | None) -> str:
    try:
        from nautilus_openalgo_bridge.market_hours import agent_market

        return agent_market(agent_id)
    except Exception:
        return "IN"


def run_poll_loop(
    *,
    agent_id: str | None = None,
    once: bool = False,
    trigger_vibe: bool = False,
    dry_run: bool = False,
    process_intents: bool = True,
) -> int:
    if not dry_run and not is_watch_enabled():
        logger.error("NAUTILUS_WATCH_ENABLE=0 — use --dry-run or set NAUTILUS_WATCH_ENABLE=true")
        return 1

    cfg = get_bridge_config()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if dry_run:
        logger.info("dry-run: testing OpenAlgo quote feed only")
        try:
            client = get_openalgo_client(cfg)
            _ = client.get_funds()
        except RuntimeError as exc:
            logger.error("OpenAlgo unreachable: %s", exc)
            return 1
        result = run_once(agent_id=agent_id, trigger_vibe=False, process_intents=False)
        print(json.dumps(result, indent=2))
        return 0

    if trigger_vibe and agent_id:
        from nautilus_openalgo_bridge.vibe_trigger import ping_vibe_backend

        vibe_health = ping_vibe_backend(cfg)
        logger.info("Vibe backend probe: %s", vibe_health.get("status"))
        if vibe_health.get("status") == "unreachable":
            logger.error("Vibe backend unreachable at %s", cfg.vibe_backend_url)
            return 1

    feed = OpenAlgoQuoteFeed()
    spec = _resolve_watch_spec(agent_id)
    baselines: dict[str, float] = {}
    last_alert_at: dict[str, float] = {}
    last_handoff_mtime: float | None = handoff_mtime(agent_id) if agent_id else None
    last_agent_mtime: float | None = _agent_mtime(agent_id) if agent_id else None
    last_vibe_dispatch_at: float = 0.0

    logger.info(
        "Poll-loop watch bridge (legacy) poll=%sms agent=%s trigger_vibe=%s",
        cfg.quote_poll_ms,
        agent_id or "default",
        trigger_vibe and bool(agent_id),
    )

    from nautilus_openalgo_bridge.market_hours import closed_market_poll_interval_sec, is_market_open_for_market

    loop_market = _poll_loop_market(agent_id)

    while True:
        if not is_market_open_for_market(loop_market):
            if once:
                logger.info("market closed (%s) — exiting poll loop", loop_market)
                return 0
            time.sleep(closed_market_poll_interval_sec())
            continue

        if agent_id:
            spec, last_handoff_mtime, last_agent_mtime = maybe_reload_watch_spec(
                agent_id,
                spec,
                last_handoff_mtime=last_handoff_mtime,
                last_agent_mtime=last_agent_mtime,
            )

        if process_intents:
            try:
                for row in process_pending_intents(client=get_openalgo_client(cfg), max_count=3):
                    logger.info("intent queue: %s → %s", row.get("intent_id"), row.get("status"))
            except RuntimeError as exc:
                logger.debug("intent queue tick skipped: %s", exc)

        quotes = feed.poll()
        for symbol, snap in quotes.items():
            baselines.setdefault(symbol, snap.ltp)

        alerts = evaluate_watch_spec(spec, quotes, baselines=baselines)
        handoff = load_handoff(agent_id) if agent_id else None
        if handoff:
            try:
                client = get_openalgo_client(cfg)
                rows = open_positions_from_book(client.get_position_book())
                pnl = total_unrealized_pnl(rows)
                stop_alert = evaluate_stop_rules(handoff, quotes, unrealized_pnl_inr=pnl, config=cfg)
                if stop_alert is not None:
                    alerts.insert(0, stop_alert)
            except RuntimeError:
                pass

        now = time.time()
        gate_minutes = spec.gate.skip_if_unchanged_minutes if spec.gate else 30

        for alert in alerts:
            key = f"{alert.symbol}:{alert.rule.metric if alert.rule else alert.signal.value}"
            if now - last_alert_at.get(key, 0.0) < cfg.alert_cooldown_sec:
                continue
            last_alert_at[key] = now
            logger.warning("WATCH ALERT: %s", alert.message)

            if alert.signal == BridgeSignal.EXIT_NOW and agent_id:
                from nautilus_openalgo_bridge.signal_actions import dispatch_exit_intent

                result = dispatch_exit_intent(agent_id, alert, underlying=handoff.underlying if handoff else None)
                logger.info("EXIT intent: %s", result.get("status"))
                continue

            if not trigger_vibe or not agent_id:
                continue
            if alert.signal == BridgeSignal.THESIS_BROKEN:
                from nautilus_openalgo_bridge.vibe_trigger import dispatch_thesis_alert_sync

                result = dispatch_thesis_alert_sync(agent_id, alert, quotes=quotes)
                if result.get("status") == "dispatched":
                    last_vibe_dispatch_at = now
                continue

            if alert.signal != BridgeSignal.REVIEW_NEEDED:
                continue
            if spec.gate and (now - last_vibe_dispatch_at) < gate_minutes * 60:
                continue
            if not is_bridge_market_open(cfg):
                continue
            from nautilus_openalgo_bridge.vibe_trigger import dispatch_watch_alert_sync

            result = dispatch_watch_alert_sync(agent_id, alert, quotes=quotes)
            if result.get("status") == "dispatched":
                last_vibe_dispatch_at = now

        if once:
            return 0
        time.sleep(cfg.quote_poll_ms / 1000.0)
